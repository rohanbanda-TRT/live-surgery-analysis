"""
Live surgery monitoring service using outlier comparison analysis approach.

This combines V1 live monitoring infrastructure with the chunked video comparison
analysis methodology. Key differences from standard live_surgery.py:

1. Uses same prompts and extraction methods as chunked_video_comparison.py
2. Analyzes video chunks with rolling history (all previous chunk analyses)
3. Direct video data analysis (not GCS URLs)
4. Same UI integration as V1 (WebSocket, all_steps, analysis_update)
5. Supports both standard and outlier procedures with comparison-style prompts
"""
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
import asyncio
import tempfile
import subprocess

from app.services.gemini_client import GeminiClient
from app.db.collections import MASTER_PROCEDURES, SURGICAL_STEPS, LIVE_SESSIONS, SESSION_ALERTS, OUTLIER_PROCEDURES
from app.core.logging import logger
from app.prompts.outlier_prompts import build_outlier_resolution_context
from app.services.outlier_analysis import OutlierAnalysisParser, CheckpointTracker
from app.services.procedure_cache import ProcedureCache
from app.services.comparison_analysis_schemas import (
    OutlierComparisonChunkAnalysis,
    StandardComparisonChunkAnalysis
)
import json
import traceback


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to H:MM:SS or MM:SS format for prompts."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _build_chunk_history_text(previous_analyses: List[Dict[str, Any]]) -> str:
    """
    Build rolling history for the prompt from all unique (non-repeat) chunks.

    - Repeat chunks (is_repeat=True) are excluded — they add no new information.
    - Every other chunk is included in full so the AI has complete context.
    - A detected-phases summary is prepended for quick orientation.
    """
    if not previous_analyses:
        return ""

    unique_chunks = [p for p in previous_analyses if not p.get("is_repeat", False)]
    if not unique_chunks:
        return ""

    parts = []
    detected_summary = []

    for prev in unique_chunks:
        if prev.get("detected_phase"):
            detected_summary.append(
                f"Phase {prev['detected_phase']} (Chunk {prev['chunk_index'] + 1})"
            )
        parts.append(
            f"=== Chunk {prev['chunk_index'] + 1} ({prev.get('time_range', '')}) ==="
            f"\n{prev.get('analysis', '')}"
        )

    header = ""
    if detected_summary:
        header = "**DETECTED PHASES ACROSS ALL CHUNKS:**\n" + ", ".join(detected_summary) + "\n\n"

    return header + "\n\n".join(parts)


class LiveSurgeryOutlierComparisonService:
    """
    Live surgery monitoring using outlier comparison analysis approach.
    
    Analyzes video chunks with rolling history and comparison-style prompts,
    while maintaining V1's WebSocket-based UI integration.
    """
    
    def __init__(self, db: AsyncDatabase, session_id: str):
        """
        Initialize live surgery outlier comparison service.
        
        Args:
            db: MongoDB database instance
            session_id: Unique session identifier
        """
        self.db = db
        self.session_id = session_id
        self.gemini_client = GeminiClient()
        self.procedure_cache = ProcedureCache()
        
        # Session state
        self.master_procedure: Optional[Dict[str, Any]] = None
        self.outlier_procedure: Optional[Dict[str, Any]] = None
        self.procedure_source: str = "standard"
        self.procedure_steps: List[Dict[str, Any]] = []
        self.session_doc_id: Optional[ObjectId] = None
        self.alert_callback: Optional[Callable] = None
        self.analysis_callback: Optional[Callable] = None
        
        # Chunk history for rolling context (stores full analysis text)
        self.chunk_history: List[Dict[str, Any]] = []  # List of {chunk_index, time_range, analysis, start_frame, end_frame}
        self.chunk_counter: int = 0
        
        # Cumulative step/phase tracking
        self.detected_steps_cumulative: set = set()
        self.step_status: Dict[int, str] = {}
        self.phase_number_to_index: Dict[str, int] = {}
        
        # Outlier mode: checkpoint tracking
        self.checkpoint_tracker: Optional[CheckpointTracker] = None
        self.outlier_parser = OutlierAnalysisParser()
        
        # Video chunk processing
        self.frame_buffer: List[bytes] = []
        self.frame_count: int = 0
        self.chunk_size: int = 5  # frames at 1 FPS
        self.chunk_overlap: int = 1
        self.chunk_queue: asyncio.Queue = asyncio.Queue()
        self.is_processing_chunks: bool = False
        self.chunk_task: Optional[asyncio.Task] = None

        # ── Ordered Integrator (LangGraph-style shared state) ─────────────────
        # Workers run concurrently (up to MAX_CONCURRENT) but results are
        # applied to shared state IN CHUNK ORDER so that later workers always
        # start with the most up-to-date history snapshot available.
        #
        #  Queue → Dispatcher → Workers 1..N (each snapshots history at start)
        #                           ↓  post raw result to _result_buffer
        #                      Ordered Integrator (serial, drains in order 0→1→2…)
        #                           ↓  appends to chunk_history  ← future workers see this
        #                      _process_json_analysis_response → cumulative state + callback
        #
        self.MAX_CONCURRENT: int = 4
        self._analysis_semaphore: asyncio.Semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        # slot index → raw result dict; filled by workers as they complete
        self._result_buffer: Dict[int, Dict[str, Any]] = {}
        # next chunk index the integrator should process (in-order)
        self._next_integrate_index: int = 0
        # prevents two coroutines from running the integrator simultaneously
        self._integration_lock: asyncio.Lock = asyncio.Lock()
        
        logger.info("live_surgery_outlier_comparison_initialized", session_id=session_id)
    
    async def start_session(
        self,
        procedure_id: str,
        surgeon_id: str,
        procedure_source: str = "standard",
        alert_callback: Optional[Callable] = None,
        analysis_callback: Optional[Callable] = None
    ):
        """
        Start a new live surgery monitoring session.
        
        Args:
            procedure_id: ID of the master procedure or outlier procedure
            surgeon_id: ID of the surgeon
            procedure_source: "standard" for master_procedures, "outlier" for outlier_procedures
            alert_callback: Callback for sending alerts
            analysis_callback: Callback for sending real-time analysis updates
        """
        try:
            # Start chunk processing task
            self.is_processing_chunks = True
            self.chunk_task = asyncio.create_task(self._process_chunk_queue())
            
            self.procedure_source = procedure_source
            
            logger.info(
                "starting_live_session_outlier_comparison",
                session_id=self.session_id,
                procedure_id=procedure_id,
                surgeon_id=surgeon_id,
                procedure_source=procedure_source
            )
            
            # Load procedure using cache
            procedure, procedure_steps = await self.procedure_cache.load_procedure(
                self.db, procedure_id, procedure_source
            )
            
            if procedure_source == "outlier":
                self.outlier_procedure = procedure
                self.procedure_steps = procedure_steps
                
                # Create phase_number to index mapping
                self.phase_number_to_index = {
                    phase["phase_number"]: i 
                    for i, phase in enumerate(self.outlier_procedure.get("phases", []))
                }
                
                # Initialize checkpoint tracker
                self.checkpoint_tracker = CheckpointTracker()
                for phase in self.outlier_procedure.get("phases", []):
                    self.checkpoint_tracker.initialize_phase_checkpoints(phase)
                
                logger.info(
                    "outlier_procedure_loaded",
                    session_id=self.session_id,
                    procedure_name=self.outlier_procedure.get("procedure_name"),
                    phases_count=len(self.procedure_steps)
                )
            else:
                self.master_procedure = procedure
                self.procedure_steps = procedure_steps
                
                logger.info(
                    "master_procedure_loaded",
                    session_id=self.session_id,
                    procedure_name=self.master_procedure.get("procedure_name"),
                    steps_count=len(self.procedure_steps)
                )
            
            # Initialize all steps as pending
            for i in range(len(self.procedure_steps)):
                self.step_status[i] = "pending"
            
            # Reset cumulative tracking
            self.detected_steps_cumulative.clear()
            
            # Create session document
            procedure_name = (
                self.outlier_procedure.get("procedure_name") if self.procedure_source == "outlier"
                else self.master_procedure.get("procedure_name")
            )
            
            session_doc = {
                "session_id": self.session_id,
                "procedure_id": ObjectId(procedure_id),
                "surgeon_id": surgeon_id,
                "start_time": datetime.utcnow(),
                "end_time": None,
                "current_step": 0,
                "status": "active",
                "procedure_source": self.procedure_source,
                "metadata": {
                    "procedure_name": procedure_name,
                    "total_steps": len(self.procedure_steps),
                    "analysis_mode": "outlier_comparison"
                }
            }
            
            result = await self.db[LIVE_SESSIONS].insert_one(session_doc)
            self.session_doc_id = result.inserted_id
            
            # Store callbacks
            self.alert_callback = alert_callback
            self.analysis_callback = analysis_callback
            
            logger.info(
                "live_session_started_outlier_comparison",
                session_id=self.session_id,
                session_doc_id=str(self.session_doc_id),
                total_steps=len(self.procedure_steps),
                chunk_size=self.chunk_size
            )
            
        except Exception as e:
            logger.error(
                "failed_to_start_session_outlier_comparison",
                session_id=self.session_id,
                error=str(e)
            )
            raise
    
    async def process_frame(self, frame_data: bytes):
        """
        Process incoming video frame - accumulate into chunks for analysis.
        
        Args:
            frame_data: Raw frame data as bytes
        """
        try:
            self.frame_count += 1
            self.frame_buffer.append(frame_data)
            
            # When we have enough frames for a chunk, queue it for analysis
            if len(self.frame_buffer) >= self.chunk_size:
                # Create video chunk from buffered frames
                chunk_frames = self.frame_buffer[:self.chunk_size]
                
                # Calculate elapsed time
                start_frame = self.frame_count - len(chunk_frames) + 1
                end_frame = self.frame_count
                start_sec = (start_frame - 1)  # 1 FPS
                end_sec = end_frame
                
                # Add to processing queue
                await self.chunk_queue.put({
                    "frames": chunk_frames,
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "chunk_index": self.chunk_counter
                })
                
                self.chunk_counter += 1
                
                logger.debug(
                    "chunk_queued_outlier_comparison",
                    session_id=self.session_id,
                    chunk_index=self.chunk_counter - 1,
                    chunk_frames=len(chunk_frames),
                    queue_size=self.chunk_queue.qsize()
                )
                
                # Keep overlap frames for next chunk
                self.frame_buffer = self.frame_buffer[self.chunk_size - self.chunk_overlap:]
            
        except Exception as e:
            logger.error(
                "frame_processing_failed_outlier_comparison",
                session_id=self.session_id,
                frame_count=self.frame_count,
                error=str(e)
            )
    
    async def _process_chunk_queue(self):
        """
        Background task to dispatch video chunks for analysis.

        Each chunk is dispatched as an independent asyncio Task protected by
        _analysis_semaphore (max 2 concurrent Gemini calls).  This keeps us
        from falling behind real-time when analysis takes longer than the
        chunk interval.
        """
        active_tasks: List[asyncio.Task] = []

        async def _run_chunk(chunk_data: Dict[str, Any]):
            async with self._analysis_semaphore:
                await self._analyze_video_chunk(chunk_data)

        try:
            while self.is_processing_chunks:
                try:
                    chunk_data = await asyncio.wait_for(
                        self.chunk_queue.get(),
                        timeout=1.0
                    )

                    logger.info(
                        "processing_chunk_outlier_comparison",
                        session_id=self.session_id,
                        chunk_index=chunk_data["chunk_index"],
                        start_frame=chunk_data["start_frame"],
                        end_frame=chunk_data["end_frame"],
                        queue_remaining=self.chunk_queue.qsize()
                    )

                    task = asyncio.create_task(_run_chunk(chunk_data))
                    active_tasks.append(task)

                    # Prune completed tasks to avoid memory leak
                    active_tasks = [t for t in active_tasks if not t.done()]

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(
                        "chunk_processing_error_outlier_comparison",
                        session_id=self.session_id,
                        error=str(e)
                    )

        except asyncio.CancelledError:
            # Wait for in-flight analyses to finish cleanly
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            logger.info("chunk_processing_cancelled_outlier_comparison", session_id=self.session_id)
        except Exception as e:
            logger.error("chunk_queue_handler_failed_outlier_comparison", session_id=self.session_id, error=str(e))
    
    async def _create_video_from_frames(self, frames: List[bytes]) -> bytes:
        """
        Async video creation from frames using ffmpeg.

        Uses -preset ultrafast for minimal encoding latency.
        Non-blocking: runs ffmpeg via asyncio subprocess so the event loop
        is free to dispatch other work while encoding.
        """
        import tempfile

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                for i, frame_data in enumerate(frames):
                    with open(f"{temp_dir}/frame_{i:04d}.jpg", 'wb') as f:
                        f.write(frame_data)

                output_path = f"{temp_dir}/chunk.mp4"
                proc = await asyncio.create_subprocess_exec(
                    'ffmpeg', '-y',
                    '-framerate', '1',
                    '-i', f'{temp_dir}/frame_%04d.jpg',
                    '-c:v', 'libx264',
                    '-preset', 'ultrafast',   # fastest encoding, ~3x faster than default
                    '-tune', 'zerolatency',   # minimise buffering delay
                    '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart',
                    output_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(f"ffmpeg exited with code {proc.returncode}")

                with open(output_path, 'rb') as f:
                    return f.read()
                    
        except Exception as e:
            logger.error(
                "video_creation_failed_outlier_comparison",
                session_id=self.session_id,
                error=str(e)
            )
            raise
    
    async def _analyze_video_chunk(self, chunk_data: Dict[str, Any]):
        """
        Worker: encode video, snapshot history, call Gemini, post result to buffer.

        Does NOT touch shared state directly — that is the integrator's job.
        Taking a snapshot of chunk_history at start means this worker automatically
        gets context from all chunks that were already integrated before it started.
        """
        chunk_index = chunk_data["chunk_index"]
        try:
            if not self.is_processing_chunks:
                return

            start_sec = chunk_data["start_sec"]
            end_sec   = chunk_data["end_sec"]
            time_range = f"{_format_timestamp(start_sec)} – {_format_timestamp(end_sec)}"

            # ── 1. Encode video (async, non-blocking) ───────────────────────────
            video_data = await self._create_video_from_frames(chunk_data["frames"])

            # ── 2. Snapshot shared state at this exact moment ───────────────────
            #    Any chunks integrated BEFORE this worker started are visible here.
            #    Chunks whose workers are still in-flight are NOT yet visible — that
            #    is the inherent trade-off of concurrent processing.
            history_snapshot   = list(self.chunk_history)
            cumulative_snapshot = set(self.detected_steps_cumulative)

            history_text = _build_chunk_history_text(history_snapshot)

            # Build detected phases info from snapshot
            detected_phases_info = ""
            if self.procedure_source == "outlier" and cumulative_snapshot:
                detected_list = [
                    f"Phase {self.procedure_steps[idx].get('phase_number')}: "
                    f"{self.procedure_steps[idx].get('phase_name')}"
                    for idx in sorted(cumulative_snapshot)
                    if idx < len(self.procedure_steps)
                ]
                if detected_list:
                    detected_phases_info = (
                        "\n**PHASES ALREADY MARKED AS DETECTED:**\n"
                        + "\n".join(f"✓ {p}" for p in detected_list)
                        + "\n"
                    )

            # ── 3. Build prompt ─────────────────────────────────────────────────
            if self.procedure_source == "outlier":
                prompt = self._build_outlier_chunk_prompt(
                    chunk_index=chunk_index,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    history_text=history_text,
                    detected_phases_info=detected_phases_info,
                    history_snapshot=history_snapshot,
                )
            else:
                prompt = self._build_standard_chunk_prompt(
                    chunk_index=chunk_index,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    history_text=history_text,
                )

            logger.info(
                "chunk_prompt_built_outlier_comparison",
                session_id=self.session_id,
                chunk_index=chunk_index,
                prompt_length=len(prompt),
                history_chunks_in_snapshot=len(history_snapshot),
                concurrent_workers=self.MAX_CONCURRENT - self._analysis_semaphore._value,
            )

            # ── 4. Call Gemini ──────────────────────────────────────────────────
            response_schema = (
                OutlierComparisonChunkAnalysis
                if self.procedure_source == "outlier"
                else StandardComparisonChunkAnalysis
            )

            analysis_json = await self.gemini_client.analyze_video_chunk(
                video_data=video_data,
                prompt=prompt,
                temperature=0.1,
                response_schema=response_schema,
            )

            analysis_data = json.loads(analysis_json)

            # Log the FULL raw JSON response for debugging
            logger.info(
                "gemini_raw_json_response",
                session_id=self.session_id,
                chunk_index=chunk_index,
                raw_json=analysis_json,
            )

            logger.info(
                "gemini_json_response_received",
                session_id=self.session_id,
                chunk_index=chunk_index,
                is_repeat=analysis_data.get("is_repeat", False),
                phases_count=len(analysis_data.get("phases", [])),
                error_codes_count=len(analysis_data.get("error_codes", [])),
            )

            # ── 5. Post raw result to ordered buffer, then trigger integrator ───
            self._result_buffer[chunk_index] = {
                "analysis_data": analysis_data,
                "analysis_text": analysis_data.get("analysis_text", ""),
                "chunk_data":    chunk_data,
                "time_range":    time_range,
                "is_repeat":     analysis_data.get("is_repeat", False),
            }

            # Trigger integrator — it will drain whatever is ready in order
            await self._integrate_pending_results()

        except Exception as e:
            logger.error(
                "chunk_analysis_failed_outlier_comparison",
                session_id=self.session_id,
                chunk_index=chunk_index,
                error=str(e),
                traceback=traceback.format_exc(),
            )
            # Post a sentinel so the integrator doesn't stall on this slot
            self._result_buffer[chunk_index] = None  # type: ignore
            await self._integrate_pending_results()

    async def _integrate_pending_results(self):
        """
        Ordered integrator — the LangGraph-style state manager.

        Drains _result_buffer starting from _next_integrate_index, processing
        consecutive completed slots in sequence.  Serialised by _integration_lock
        so only one coroutine runs this at a time.

        After each result is integrated:
        - chunk_history is updated (future workers' snapshots see it)
        - cumulative detection state is updated
        - analysis_callback is fired
        """
        async with self._integration_lock:
            while self._next_integrate_index in self._result_buffer:
                idx    = self._next_integrate_index
                result = self._result_buffer.pop(idx)
                self._next_integrate_index += 1

                if result is None:
                    # Worker failed for this slot — skip but keep order intact
                    logger.warning(
                        "integrator_skipping_failed_chunk",
                        session_id=self.session_id,
                        chunk_index=idx,
                    )
                    continue

                analysis_data = result["analysis_data"]
                analysis_text = result["analysis_text"]
                chunk_data    = result["chunk_data"]
                time_range    = result["time_range"]
                is_repeat     = result["is_repeat"]

                # Build chunk_record and append to shared history
                chunk_record = {
                    "chunk_index": idx,
                    "time_range":  time_range,
                    "analysis":    analysis_text,
                    "is_repeat":   is_repeat,
                    "start_frame": chunk_data["start_frame"],
                    "end_frame":   chunk_data["end_frame"],
                    "start_sec":   chunk_data["start_sec"],
                    "end_sec":     chunk_data["end_sec"],
                }
                if self.procedure_source == "outlier":
                    for phase in analysis_data.get("phases", []):
                        if phase.get("detected") and phase.get("phase_number"):
                            chunk_record["detected_phase"]       = phase["phase_number"]
                            chunk_record["detected_phase_index"] = self.phase_number_to_index.get(phase["phase_number"])
                            break

                # Append BEFORE processing so next worker snapshot includes it
                self.chunk_history.append(chunk_record)

                unique_count = sum(1 for c in self.chunk_history if not c.get("is_repeat"))
                logger.info(
                    "chunk_integrated",
                    session_id=self.session_id,
                    chunk_index=idx,
                    is_repeat=is_repeat,
                    history_size=len(self.chunk_history),
                    unique_in_history=unique_count,
                    buffer_pending=len(self._result_buffer),
                    next_integrate=self._next_integrate_index,
                )

                # Apply state update and fire callback
                await self._process_json_analysis_response(
                    analysis_data, analysis_text, chunk_data, is_repeat=is_repeat
                )
    
    def _build_standard_chunk_prompt(
        self,
        chunk_index: int,
        start_sec: float,
        end_sec: float,
        history_text: str
    ) -> str:
        """Build prompt for a chunk in standard mode (comparison approach)."""
        procedure_name = self.master_procedure.get("procedure_name", "Unknown Procedure")
        time_range = f"{_format_timestamp(start_sec)} – {_format_timestamp(end_sec)}"
        
        # Build steps list
        steps_list = []
        for i, step in enumerate(self.procedure_steps, 1):
            step_info = (
                f"Step {step.get('step_number', i)}: {step['step_name']}\n"
                f"- Description: {step.get('description', 'N/A')}\n"
                f"- Expected Duration: {step.get('expected_duration_min', 'N/A')}-"
                f"{step.get('expected_duration_max', 'N/A')} minutes\n"
                f"- Critical: {'YES' if step.get('is_critical') else 'No'}\n"
                f"- Required Instruments: "
                f"{', '.join(step.get('instruments_required', [])) or 'Not specified'}\n"
                f"- Anatomical Landmarks: "
                f"{', '.join(step.get('anatomical_landmarks', [])) or 'Not specified'}"
            )
            steps_list.append(step_info)
        steps_context = "\n\n".join(steps_list)
        
        prompt = f"""You are analyzing CHUNK {chunk_index + 1} from a LIVE surgical video of: {procedure_name}

**VIDEO SEGMENT:**
- Time window: {time_range} (elapsed time from surgery start)
- This is chunk {chunk_index + 1} of the ongoing live surgery

**IMPORTANT TIMESTAMP INSTRUCTIONS:**
- All timestamps you report should be relative to the surgery start time
- The video segment you see is from {_format_timestamp(start_sec)} to {_format_timestamp(end_sec)}

**MASTER PROCEDURE STEPS:**
{steps_context}
"""
        
        if history_text:
            prompt += f"""
**ANALYSIS FROM PREVIOUS CHUNKS:**
The following is the complete analysis from prior chunks. Use this context to:
1. Avoid re-reporting steps already fully detected in previous chunks
2. Continue tracking steps that were partially detected
3. Maintain continuity of the overall procedure analysis

{history_text}
"""
        else:
            prompt += """
**NOTE:** This is the FIRST chunk — no prior analysis history is available.
"""
        
        prompt += f"""
**YOUR TASK:**
Analyze this video segment ({time_range}) and determine which steps from the master procedure are visible.

For EACH step you can observe in THIS chunk, provide:

Step [number]: [name]
Detected: [YES/NO]
Evidence: [specific observations from this video segment]
Timestamp: [time range in the surgery, e.g., "{_format_timestamp(start_sec)}-{_format_timestamp(end_sec)}"]
Completion: [COMPLETED/PARTIAL/NOT_PERFORMED]
Notes: [any deviations, continuations from previous chunks, or observations]

---

After analyzing all visible steps, provide:

**CHUNK SUMMARY:**
- Chunk: {chunk_index + 1}
- Time Window: {time_range}
- Steps Detected in This Chunk: [number]
- Steps Completed in This Chunk: [number]

**CRITICAL OBSERVATIONS:**
[Any critical deviations, skipped steps, or safety concerns observed in this chunk]
"""
        return prompt
    
    def _build_outlier_chunk_prompt(
        self,
        chunk_index: int,
        start_sec: float,
        end_sec: float,
        history_text: str,
        detected_phases_info: str = "",
        history_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build prompt for a chunk in outlier mode (comparison approach)."""
        time_range = f"{_format_timestamp(start_sec)} – {_format_timestamp(end_sec)}"
        phases_context = build_outlier_resolution_context(self.outlier_procedure)

        # Use the snapshot passed by the worker (reflects state at worker start time)
        snapshot = history_snapshot if history_snapshot is not None else self.chunk_history
        last_unique = next(
            (c for c in reversed(snapshot) if not c.get("is_repeat")), None
        )
        last_phase_hint = (
            f" Last unique chunk showed Phase {last_unique['detected_phase']}."
            if last_unique and last_unique.get("detected_phase") else ""
        )

        prompt = f"""You are analyzing CHUNK {chunk_index + 1} from a LIVE surgical video using the Outlier Resolution Protocol.

**VIDEO SEGMENT:**
- Time window: {time_range} (elapsed time from surgery start)
- This is chunk {chunk_index + 1} of the ongoing live surgery

**IMPORTANT TIMESTAMP INSTRUCTIONS:**
- All timestamps you report should be relative to the surgery start time
- The video segment you see is from {_format_timestamp(start_sec)} to {_format_timestamp(end_sec)}

{phases_context}
{detected_phases_info}
"""

        if history_text:
            prompt += f"""
**ANALYSIS FROM PREVIOUS CHUNKS:**
The following is the complete analysis from ALL prior chunks. Use this context to:
1. Avoid re-reporting phases already fully detected (see PHASES ALREADY MARKED above)
2. Continue tracking phases/checkpoints that were partially detected
3. Track cumulative error codes across all chunks
4. Build upon previous checkpoint validations

{history_text}
"""
        else:
            prompt += """
**NOTE:** This is the FIRST chunk — no prior analysis history is available.
"""

        prompt += f"""
**YOUR TASK:**
Analyze this video segment ({time_range}) and return a structured JSON response with:
1. Which phases are visible in this chunk
2. Which checkpoints can be validated
3. Which error codes are detected (if any)

**is_repeat rule:** If the surgical state is IDENTICAL to the previous chunk (same phase on screen, same text overlays, no new instrument actions visible), set is_repeat=true and provide a single-sentence analysis_text.{last_phase_hint}

**When is_repeat=false (state has changed or first chunk):**

For each DETECTED phase, include checkpoint_validations array with:
- checkpoint_name: Name of the checkpoint requirement
- status: "MET", "NOT_MET", or "PREVIOUSLY_MET"
- evidence: Visual evidence from the video

**ERROR CODE DETECTION (CRITICAL):**
Carefully examine the video for surgical errors. Report ANY observed errors using these codes:

**Action Errors (A-series):**
- **A1**: Action too long or too short (e.g., premature cannula removal, excessive drilling time)
- **A3**: Wrong direction (e.g., poor cannula bevel orientation, opening ligament laterally instead of medially, wrong endoscope rotation)
- **A4**: Too little or too much (e.g., insufficient drilling, incomplete tissue resection, excessive annulus opening, rough manipulation)
- **A5**: Misalignment (e.g., wrong level/side approach, incorrect target positioning)
- **A8**: Operation omitted (e.g., skipping vessel coagulation, not performing annuloplasty, omitting verification checks)
- **A9**: Wrong object (e.g., missing anatomical landmarks, incorrect structure identification)
- **A10**: Other action errors

**Checking Errors (C-series):**
- **C1**: Check omitted (e.g., no fluoroscopy confirmation, skipping imaging verification - HIGH RISK for wrong-site surgery)
- **C2-C6**: Other checking errors

**Retrieval Errors (R-series):**
- **R2**: Wrong information obtained (e.g., misidentified anatomy, incorrect level counting)
- **R1, R3**: Other retrieval errors

**How to detect errors:**
- Look for VISIBLE deviations from proper technique (instruments in wrong position, skipped steps, rough handling)
- Check if critical safety steps are being performed (coagulation before cutting, fluoroscopy checks, gentle tissue handling)
- Identify if anatomical landmarks are being properly identified before proceeding
- Watch for signs of bleeding, tissue damage, or instrument misplacement

**For each detected error, provide:**
- code: The error code (e.g., "A8", "C1", "A4")
- description: What specific error you observed
- severity: "HIGH" (neural injury risk, wrong-site, dural tear), "MEDIUM" (bleeding, incomplete resection), or "LOW" (minor technique issues)
- phase: Which phase number the error occurred in

**IMPORTANT:** Only report errors you can VISUALLY CONFIRM in the video. Do not assume errors - if the video quality is unclear or the step is not visible, do not report an error for that step.

**CRITICAL: You MUST respond with valid JSON matching the provided schema exactly.**
"""
        return prompt
    
    async def _process_json_analysis_response(
        self,
        analysis_data: Dict[str, Any],
        analysis_text: str,
        chunk_data: Dict[str, Any],
        is_repeat: bool = False
    ):
        """
        Process JSON analysis response and update UI.
        GUARANTEE: analysis_callback is ALWAYS called regardless of parsing errors.
        Each risky step is isolated so one failure cannot kill the callback.
        When is_repeat=True: cumulative state/checkpoints still update, but
        the all_steps rebuild is skipped for efficiency.
        """
        chunk_index = chunk_data.get("chunk_index", -1)
        detected_phases_in_chunk = []
        detected_step_index = None
        checkpoint_status = {"status": "UNKNOWN", "details": []}
        error_codes = []

        # ── STEP 1: Parse phases from JSON ──────────────────────────────────────
        try:
            if self.procedure_source == "outlier":
                phases = analysis_data.get("phases", [])
                error_codes = analysis_data.get("error_codes", [])
                detected_phase_number = None

                for phase in phases:
                    if phase.get("detected"):
                        phase_num = phase.get("phase_number")
                        if phase_num:
                            detected_phases_in_chunk.append(phase_num)
                            if detected_phase_number is None:
                                detected_phase_number = phase_num

                            # Accumulate checkpoint validations PER PHASE
                            for cp in phase.get("checkpoint_validations", []):
                                cp_status = cp.get("status", "NOT_MET")
                                checkpoint_status["details"].append({
                                    "requirement": cp.get("checkpoint_name", ""),
                                    "met": cp_status in ["MET", "PREVIOUSLY_MET"],
                                    "status": cp_status,
                                    "evidence": cp.get("evidence", ""),
                                    "phase": phase_num
                                })

                # Set overall checkpoint status
                if checkpoint_status["details"]:
                    all_met = all(d["met"] for d in checkpoint_status["details"])
                    checkpoint_status["status"] = "PASS" if all_met else "FAIL"

                # Derive primary detected_step_index
                detected_step_index = (
                    self.phase_number_to_index.get(detected_phase_number)
                    if detected_phase_number else None
                )

                logger.info(
                    "outlier_json_analysis_parsed",
                    session_id=self.session_id,
                    chunk_index=chunk_index,
                    detected_phases=detected_phases_in_chunk,
                    checkpoint_status=checkpoint_status["status"],
                    checkpoint_count=len(checkpoint_status["details"]),
                    error_count=len(error_codes),
                    error_codes_detail=[{"code": e.get("code"), "severity": e.get("severity")} for e in error_codes]
                )
            else:
                for step in analysis_data.get("steps", []):
                    if step.get("detected"):
                        step_number = step.get("step_number")
                        detected_step_index = (step_number - 1) if step_number else None
                        break

        except Exception as e:
            logger.error(
                "json_parse_step_failed",
                session_id=self.session_id,
                chunk_index=chunk_index,
                error=str(e),
                traceback=traceback.format_exc()
            )

        # ── STEP 2: Update cumulative detection set (NEVER cleared) ─────────────
        try:
            if self.procedure_source == "outlier":
                for phase_num in detected_phases_in_chunk:
                    phase_idx = self.phase_number_to_index.get(phase_num)
                    if phase_idx is not None:
                        is_new = phase_idx not in self.detected_steps_cumulative
                        self.detected_steps_cumulative.add(phase_idx)
                        self.step_status[phase_idx] = "detected"
                        if is_new:
                            logger.info(
                                "phase_added_to_cumulative",
                                session_id=self.session_id,
                                phase_number=phase_num,
                                phase_index=phase_idx,
                                total_detected=len(self.detected_steps_cumulative)
                            )
            elif detected_step_index is not None:
                is_new = detected_step_index not in self.detected_steps_cumulative
                self.detected_steps_cumulative.add(detected_step_index)
                self.step_status[detected_step_index] = "detected"
                if is_new:
                    logger.info(
                        "step_added_to_cumulative",
                        session_id=self.session_id,
                        step_index=detected_step_index,
                        total_detected=len(self.detected_steps_cumulative)
                    )
        except Exception as e:
            logger.error(
                "cumulative_update_failed",
                session_id=self.session_id,
                chunk_index=chunk_index,
                error=str(e),
                traceback=traceback.format_exc()
            )

        # ── STEP 3: Update checkpoint tracker (isolated - failure won't kill callback) ──
        try:
            if self.procedure_source == "outlier" and self.checkpoint_tracker:
                # Update per-phase so each phase's checkpoints go to the correct phase
                if analysis_data.get("phases"):
                    for phase in analysis_data["phases"]:
                        if phase.get("detected") and phase.get("checkpoint_validations"):
                            phase_num = phase.get("phase_number")
                            phase_details = [
                                {
                                    "requirement": cp.get("checkpoint_name", ""),
                                    "met": cp.get("status") in ["MET", "PREVIOUSLY_MET"],
                                    "evidence": cp.get("evidence", "")
                                }
                                for cp in phase["checkpoint_validations"]
                            ]
                            self.checkpoint_tracker.update_from_ai_checkpoint_details(
                                phase_num, phase_details
                            )
        except Exception as e:
            logger.warning(
                "checkpoint_tracker_update_failed",
                session_id=self.session_id,
                chunk_index=chunk_index,
                error=str(e),
                traceback=traceback.format_exc()
            )

        # ── STEP 4: Create error alerts (isolated) ───────────────────────────────
        try:
            if error_codes:
                error_alerts = [
                    {
                        "alert_type": f"error_{err.get('code', 'unknown').lower()}",
                        "severity": err.get('severity', 'MEDIUM').lower(),
                        "message": f"{err.get('code', 'Unknown')}: {err.get('description', 'Error detected')}",
                        "metadata": {
                            "error_code": err.get('code'),
                            "description": err.get('description'),
                            "chunk_index": chunk_index
                        }
                    }
                    for err in error_codes
                ]
                await self._create_alerts(error_alerts)
        except Exception as e:
            logger.warning(
                "error_alerts_creation_failed",
                session_id=self.session_id,
                error=str(e),
                traceback=traceback.format_exc()
            )

        # ── STEP 5: Build all_steps and ALWAYS fire analysis_callback ────────────
        if not self.analysis_callback:
            return

        # On repeat chunks: skip the expensive all_steps rebuild.
        # Just send a lightweight heartbeat so the frontend knows we're alive.
        if is_repeat:
            if self.analysis_callback:
                try:
                    await self.analysis_callback({
                        "frame_count": chunk_data.get("end_frame", 0),
                        "is_repeat": True,
                        "analysis_text": analysis_text,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                except RuntimeError as e:
                    if "close message has been sent" in str(e):
                        logger.info(
                            "repeat_heartbeat_skipped_websocket_closed",
                            session_id=self.session_id,
                            chunk_index=chunk_data.get("chunk_index", -1)
                        )
                except Exception:
                    pass
            return

        try:
            next_undetected_index = next(
                (i for i in range(len(self.procedure_steps)) if i not in self.detected_steps_cumulative),
                len(self.procedure_steps) - 1
            )
            current_step = self.procedure_steps[next_undetected_index]

            all_steps_data = []
            for i, s in enumerate(self.procedure_steps):
                is_detected = i in self.detected_steps_cumulative
                step_data = {
                    "step_number": s.get('step_number', i + 1),
                    "step_name": s['step_name'],
                    "description": s.get('description'),
                    "is_critical": s.get('is_critical', False),
                    "detected": is_detected
                }

                if self.procedure_source == "outlier":
                    phase_number = s.get('phase_number')
                    # Get checkpoint info - isolated so it can't kill the loop
                    checkpoint_info = {}
                    try:
                        if self.checkpoint_tracker:
                            checkpoint_info = self.checkpoint_tracker.get_phase_checkpoint_status(phase_number) or {}
                    except Exception:
                        pass

                    # CUMULATIVE status - once detected, NEVER goes back to pending
                    if is_detected:
                        if checkpoint_info.get('has_checkpoints') and not checkpoint_info.get('all_complete'):
                            phase_status = "current"
                        else:
                            phase_status = "completed"
                    else:
                        phase_status = "pending"

                    step_data.update({
                        "phase_number": phase_number,
                        "phase_name": s.get('phase_name', s['step_name']),
                        "goal": s.get('goal'),
                        "priority": s.get('priority'),
                        "status": phase_status,
                        "checkpoints": checkpoint_info.get('checkpoints', []),
                        "detected_errors": [
                            e for e in error_codes
                            if self.phase_number_to_index.get(e.get("phase")) == i
                        ]
                    })
                else:
                    step_data["status"] = "completed" if is_detected else "pending"

                all_steps_data.append(step_data)

            # Log cumulative state snapshot
            detected_phases_summary = [
                f"Phase {self.procedure_steps[idx].get('phase_number')}"
                for idx in sorted(self.detected_steps_cumulative)
                if idx < len(self.procedure_steps)
            ]
            logger.info(
                "sending_cumulative_state_to_frontend",
                session_id=self.session_id,
                chunk_index=chunk_index,
                detected_phases=detected_phases_summary,
                total_detected=len(self.detected_steps_cumulative)
            )

            msg = {
                "frame_count": chunk_data.get("end_frame", 0),
                "current_step_index": next_undetected_index,
                "current_step_name": current_step['step_name'],
                "detected_step_index": detected_step_index,
                "procedure_source": self.procedure_source,
                "expected_step": {
                    "step_number": current_step.get('step_number'),
                    "step_name": current_step['step_name'],
                    "description": current_step.get('description'),
                    "is_critical": current_step.get('is_critical', False)
                },
                "all_steps": all_steps_data,
                "analysis_text": analysis_text,
                "checkpoint_status": checkpoint_status,
                "error_codes": error_codes,  # Add error codes at top level for frontend
                "timestamp": datetime.utcnow().isoformat()
            }

            # Log what's being sent to frontend for debugging
            logger.info(
                "frontend_message_prepared",
                session_id=self.session_id,
                chunk_index=chunk_index,
                error_codes_in_message=len(error_codes),
                error_codes_detail=[{"code": e.get("code"), "severity": e.get("severity"), "phase": e.get("phase")} for e in error_codes],
                steps_with_errors=[i for i, s in enumerate(all_steps_data) if s.get("detected_errors")]
            )

            # Defensive callback invocation: check again right before calling
            # (callback could have been nullified by disconnect handler between
            # the check at line 959 and here due to race condition)
            if self.analysis_callback:
                try:
                    await self.analysis_callback(msg)
                except RuntimeError as e:
                    # WebSocket closed between check and send — this is expected
                    # during reconnects. Service stays alive, callback will be
                    # restored on reconnect.
                    if "close message has been sent" in str(e):
                        logger.info(
                            "callback_skipped_websocket_closed",
                            session_id=self.session_id,
                            chunk_index=chunk_index
                        )
                    else:
                        raise

        except Exception as e:
            logger.error(
                "analysis_callback_build_failed",
                session_id=self.session_id,
                chunk_index=chunk_index,
                error=str(e),
                traceback=traceback.format_exc()
            )
    
    def _parse_detected_step_from_comparison(self, analysis: str) -> Optional[int]:
        """
        Parse detected step from comparison-style analysis output.
        Looks for patterns like "Step [number]: [name]" with "Detected: YES"
        """
        try:
            import re
            # Look for "Step X: ... Detected: YES" pattern
            matches = re.finditer(
                r'Step\s+(\d+):[^\n]+\n[^\n]*Detected:\s*YES',
                analysis,
                re.IGNORECASE | re.MULTILINE
            )
            
            for match in matches:
                step_number = int(match.group(1))
                # Convert to 0-based index
                return step_number - 1
            
            return None
        except Exception as e:
            logger.error("failed_to_parse_detected_step_comparison", error=str(e))
            return None
    
    async def _create_alerts(self, alerts: List[Dict[str, Any]]):
        """Create and store alerts in database."""
        try:
            now = datetime.utcnow()
            alert_documents = []
            
            for alert in alerts:
                alert_doc = {
                    "session_id": self.session_doc_id,
                    "alert_type": alert["alert_type"],
                    "severity": alert["severity"],
                    "message": alert["message"],
                    "timestamp": now,
                    "acknowledged": False,
                    "metadata": alert.get("metadata", {})
                }
                alert_documents.append(alert_doc)
            
            if alert_documents:
                await self.db[SESSION_ALERTS].insert_many(alert_documents)
                
                logger.warning(
                    "alerts_generated_comparison",
                    session_id=self.session_id,
                    alert_count=len(alert_documents)
                )
                
                # Send alerts via callback if available
                if self.alert_callback:
                    try:
                        await self.alert_callback(alerts)
                    except Exception as e:
                        logger.error("alert_callback_failed_comparison", error=str(e))
        
        except Exception as e:
            logger.error(
                "failed_to_create_alerts_comparison",
                session_id=self.session_id,
                error=str(e)
            )
    
    async def stop_session(self):
        """Stop the live surgery monitoring session."""
        try:
            # Stop chunk processing immediately
            self.is_processing_chunks = False
            
            # Clear chunk queue
            while not self.chunk_queue.empty():
                try:
                    self.chunk_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            # Clear frame buffer
            self.frame_buffer.clear()
            
            # Cancel chunk processing task
            if self.chunk_task and not self.chunk_task.done():
                self.chunk_task.cancel()
                try:
                    await self.chunk_task
                except asyncio.CancelledError:
                    pass
            
            # Update session in database
            if self.session_doc_id:
                await self.db[LIVE_SESSIONS].update_one(
                    {"_id": self.session_doc_id},
                    {
                        "$set": {
                            "end_time": datetime.utcnow(),
                            "status": "completed"
                        }
                    }
                )
            
            logger.info(
                "live_session_stopped_outlier_comparison",
                session_id=self.session_id,
                frames_processed=self.frame_count,
                chunks_analyzed=len(self.chunk_history)
            )
            
        except Exception as e:
            logger.error(
                "failed_to_stop_session_outlier_comparison",
                session_id=self.session_id,
                error=str(e)
            )
