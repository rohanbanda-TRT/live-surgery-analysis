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
    Build a complete rolling history string from ALL previous chunk analyses.
    Includes chunk time range, detected phases, and full analysis text.
    NO truncation - user requested ALL past chunk details.
    """
    if not previous_analyses:
        return ""

    parts = []
    detected_phases_summary = []
    
    for prev in previous_analyses:
        time_range = prev.get("time_range", "")
        analysis = prev.get("analysis", "")
        detected_phase = prev.get("detected_phase")
        
        # Track detected phases for summary
        if detected_phase:
            detected_phases_summary.append(f"Phase {detected_phase} (Chunk {prev['chunk_index'] + 1})")
        
        # Include full analysis text (no truncation as requested)
        parts.append(f"=== Chunk {prev['chunk_index'] + 1} ({time_range}) ===\n{analysis}")
    
    # Add detected phases summary at the beginning
    if detected_phases_summary:
        summary = "**DETECTED PHASES ACROSS ALL CHUNKS:**\n" + ", ".join(detected_phases_summary) + "\n\n"
        return summary + "\n\n".join(parts)
    
    return "\n\n".join(parts)


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
        self.chunk_size: int = 5  # 5 seconds at 1 FPS (configurable)
        self.chunk_overlap: int = 1  # 1 second overlap
        self.chunk_queue: asyncio.Queue = asyncio.Queue()
        self.is_processing_chunks: bool = False
        self.chunk_task: Optional[asyncio.Task] = None
        
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
        """Background task to process video chunks from queue."""
        try:
            while self.is_processing_chunks:
                try:
                    # Get next chunk from queue (wait up to 1 second)
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
                    
                    # Analyze the video chunk
                    await self._analyze_video_chunk(chunk_data)
                    
                except asyncio.TimeoutError:
                    # No chunks in queue, continue waiting
                    continue
                except Exception as e:
                    logger.error(
                        "chunk_processing_error_outlier_comparison",
                        session_id=self.session_id,
                        error=str(e)
                    )
                    
        except asyncio.CancelledError:
            logger.info(
                "chunk_processing_cancelled_outlier_comparison",
                session_id=self.session_id
            )
        except Exception as e:
            logger.error(
                "chunk_queue_handler_failed_outlier_comparison",
                session_id=self.session_id,
                error=str(e)
            )
    
    def _create_video_from_frames(self, frames: List[bytes]) -> bytes:
        """Create a video file from frame images."""
        import tempfile
        import subprocess
        
        try:
            # Create temp directory for frames
            with tempfile.TemporaryDirectory() as temp_dir:
                # Save frames as images
                for i, frame_data in enumerate(frames):
                    frame_path = f"{temp_dir}/frame_{i:04d}.jpg"
                    with open(frame_path, 'wb') as f:
                        f.write(frame_data)
                
                # Create video using ffmpeg
                output_path = f"{temp_dir}/chunk.mp4"
                subprocess.run([
                    'ffmpeg', '-y',
                    '-framerate', '1',  # 1 FPS
                    '-i', f'{temp_dir}/frame_%04d.jpg',
                    '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart',
                    output_path
                ], check=True, capture_output=True)
                
                # Read video file
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
        Analyze a video chunk using outlier comparison approach.
        
        Uses same prompts and extraction methods as chunked_video_comparison.py
        """
        try:
            # Early exit if session is stopped
            if not self.is_processing_chunks:
                logger.info(
                    "chunk_skipped_session_stopped_outlier_comparison",
                    session_id=self.session_id,
                    chunk_index=chunk_data.get("chunk_index")
                )
                return
            
            chunk_index = chunk_data["chunk_index"]
            start_sec = chunk_data["start_sec"]
            end_sec = chunk_data["end_sec"]
            time_range = f"{_format_timestamp(start_sec)} – {_format_timestamp(end_sec)}"
            
            # Create video from frames
            video_data = self._create_video_from_frames(chunk_data["frames"])
            
            # Build rolling history from all previous chunks
            history_text = _build_chunk_history_text(self.chunk_history)
            
            # Build detected phases summary for AI context
            detected_phases_info = ""
            if self.procedure_source == "outlier" and self.detected_steps_cumulative:
                detected_list = []
                for idx in sorted(self.detected_steps_cumulative):
                    if idx < len(self.procedure_steps):
                        phase = self.procedure_steps[idx]
                        detected_list.append(f"Phase {phase.get('phase_number')}: {phase.get('phase_name')}")
                if detected_list:
                    detected_phases_info = "\n**PHASES ALREADY MARKED AS DETECTED:**\n" + "\n".join(f"✓ {p}" for p in detected_list) + "\n"
            
            # Build prompt based on procedure source (using comparison approach)
            if self.procedure_source == "outlier":
                prompt = self._build_outlier_chunk_prompt(
                    chunk_index=chunk_index,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    history_text=history_text,
                    detected_phases_info=detected_phases_info
                )
            else:
                prompt = self._build_standard_chunk_prompt(
                    chunk_index=chunk_index,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    history_text=history_text
                )
            
            logger.info(
                "chunk_prompt_built_outlier_comparison",
                session_id=self.session_id,
                chunk_index=chunk_index,
                prompt_length=len(prompt),
                history_chunks_count=len(self.chunk_history)
            )
            
            # Analyze video chunk with Gemini using structured JSON output (no regex parsing needed!)
            response_schema = (
                OutlierComparisonChunkAnalysis if self.procedure_source == "outlier"
                else StandardComparisonChunkAnalysis
            )
            
            analysis_json = await self.gemini_client.analyze_video_chunk(
                video_data=video_data,
                prompt=prompt,
                temperature=0.1,  # Lower temperature = faster, more deterministic responses
                response_schema=response_schema
            )
            
            # Parse JSON response
            analysis_data = json.loads(analysis_json)
            
            # LOG EXACT JSON RESPONSE FROM GEMINI
            logger.info(
                "gemini_json_response_received",
                session_id=self.session_id,
                chunk_index=chunk_index,
                json_response=analysis_data,  # Full JSON for debugging
                phases_count=len(analysis_data.get("phases", [])),
                error_codes_count=len(analysis_data.get("error_codes", []))
            )
            
            # Extract analysis text for display and history
            analysis = analysis_data.get("analysis_text", "")
            
            # Store in chunk history for rolling context with detection metadata
            chunk_record = {
                "chunk_index": chunk_index,
                "time_range": time_range,
                "analysis": analysis,
                "start_frame": chunk_data["start_frame"],
                "end_frame": chunk_data["end_frame"],
                "start_sec": start_sec,
                "end_sec": end_sec
            }
            
            # Add detection metadata from JSON (no regex parsing!)
            if self.procedure_source == "outlier":
                # Extract detected phases from JSON
                phases = analysis_data.get("phases", [])
                for phase in phases:
                    if phase.get("detected"):
                        detected_phase_number = phase.get("phase_number")
                        if detected_phase_number:
                            chunk_record["detected_phase"] = detected_phase_number
                            chunk_record["detected_phase_index"] = self.phase_number_to_index.get(detected_phase_number)
                            break  # Only track first detected phase per chunk
            
            self.chunk_history.append(chunk_record)
            
            # Keep ALL chunks - no limit (user requested full history for better context)
            
            logger.info(
                "chunk_analyzed_outlier_comparison",
                session_id=self.session_id,
                chunk_index=chunk_index,
                time_range=time_range,
                frames=len(chunk_data["frames"]),
                history_size=len(self.chunk_history),
                structured_json=True
            )
            
            # Parse and process JSON response (no regex!)
            await self._process_json_analysis_response(analysis_data, analysis, chunk_data)
            
        except Exception as e:
            logger.error(
                "chunk_analysis_failed_outlier_comparison",
                session_id=self.session_id,
                error=str(e)
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
        detected_phases_info: str = ""
    ) -> str:
        """Build prompt for a chunk in outlier mode (comparison approach)."""
        procedure_name = self.outlier_procedure.get("procedure_name", "Unknown Procedure")
        time_range = f"{_format_timestamp(start_sec)} – {_format_timestamp(end_sec)}"
        phases_context = build_outlier_resolution_context(self.outlier_procedure)
        
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
3. Which error codes (A1-A10, C1-C6, R1-R3) are detected

**CRITICAL: You MUST respond with valid JSON matching the provided schema.**

The JSON response should include:
- "phases": Array of phase objects with detected status, evidence, timestamps, and checkpoint_validations
- "error_codes": Array of detected error codes with severity
- "chunk_summary": Summary statistics for this chunk
- "analysis_text": Natural language summary for display to the surgeon

For each phase detected, include checkpoint_validations array with:
- checkpoint_name: Name of the checkpoint requirement
- status: "MET", "NOT_MET", or "PREVIOUSLY_MET"
- evidence: Visual evidence from the video

**EXAMPLE STRUCTURE (you must follow this):**

Phase [number]: [name]
Detected: [YES/NO]
Evidence: [specific visual evidence from this video segment]
Timestamp: [time range in the surgery]

**CHECKPOINT VALIDATION:**
For each checkpoint in this phase:
- [Checkpoint name]: [MET/NOT MET/PREVIOUSLY_MET] - [Evidence]

**ERROR CODES DETECTED:**
- [List any error codes observed in this chunk]

**PHASE COMPLETION:**
- Status: [COMPLETED/PARTIAL/NOT_PERFORMED]
- Blocking Issues: [Any blocking checkpoints not met]

---

After analyzing all visible phases, provide:

**CHUNK SUMMARY:**
- Chunk: {chunk_index + 1}
- Time Window: {time_range}
- Phases Detected: [number]
- Phases Completed: [number]
- Checkpoints Met: [number]
- Error Codes: [list]

**CRITICAL SAFETY ISSUES:**
[List any HIGH priority errors or safety concerns in this chunk]
"""
        return prompt
    
    async def _process_json_analysis_response(
        self,
        analysis_data: Dict[str, Any],
        analysis_text: str,
        chunk_data: Dict[str, Any]
    ):
        """
        Process JSON analysis response and update UI.
        GUARANTEE: analysis_callback is ALWAYS called regardless of parsing errors.
        Each risky step is isolated so one failure cannot kill the callback.
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
                    error_count=len(error_codes)
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
                "timestamp": datetime.utcnow().isoformat()
            }

            await self.analysis_callback(msg)

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
