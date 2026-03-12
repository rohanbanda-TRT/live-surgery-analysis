"""
Live surgery monitoring service using Gemini Live API.
"""
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
import asyncio
import os
import tempfile
import cv2
import numpy as np

from app.services.gemini_client import GeminiClient
from app.db.collections import MASTER_PROCEDURES, LIVE_SESSIONS, SESSION_ALERTS, OUTLIER_PROCEDURES
from app.core.logging import logger
from app.services.outlier_analysis import CheckpointTracker
from app.services.analysis_schemas import get_standard_chunk_schema, get_outlier_chunk_schema
from app.prompts.prompts_v2 import (
    build_standard_system_instruction,
    build_outlier_system_instruction,
    build_standard_chunk_prompt,
    build_outlier_chunk_prompt,
    build_chunk_history_summary,
    CONFIDENCE_THRESHOLD,
)


class LiveSurgeryService:
    """Service for real-time surgical monitoring and compliance checking."""
    
    def __init__(self, db: AsyncDatabase, session_id: str):
        """
        Initialize live surgery service.
        
        Args:
            db: MongoDB database instance
            session_id: Unique session identifier
        """
        self.db = db
        self.session_id = session_id
        self.gemini_client = GeminiClient()
        
        # Session state
        self.master_procedure: Optional[Dict[str, Any]] = None
        self.outlier_procedure: Optional[Dict[str, Any]] = None  # For outlier resolution mode
        self.procedure_source: str = "standard"  # "standard" or "outlier"
        self.procedure_steps: List[Dict[str, Any]] = []
        self.current_step_index: int = 0
        self.session_doc_id: Optional[ObjectId] = None
        self.alert_callback: Optional[Callable] = None
        self.analysis_callback: Optional[Callable] = None
        
        # Previous analysis for context awareness
        self.previous_analysis: Optional[str] = None
        self.chunk_history: List[dict] = []  # Structured dicts from analyze_frames_structured (last 10)

        # System instruction (built once at session start, reused every chunk)
        self._system_instruction: Optional[str] = None
        
        # Cumulative step tracking (like reference implementation)
        self.detected_steps_cumulative: set = set()  # Steps that have been detected (NEVER removed)
        self.step_status: Dict[int, str] = {}  # step_index -> status (pending/detected/missed)
        self.step_detection_history: Dict[int, List[dict]] = {}  # step_index -> list of detection dicts
        self.phase_number_to_index: Dict[str, int] = {}  # For outlier mode: phase_number -> index mapping
        
        # Outlier mode: checkpoint tracking
        self.checkpoint_tracker: Optional[CheckpointTracker] = None
        
        # Video chunk processing
        # 5 frames @ 1 FPS = 5s window; overlap=1 means new chunk every 4s
        self.frame_buffer: List[bytes] = []
        self.frame_count: int = 0
        self.chunk_size: int = 5   # reduced from 7 — smaller window = faster first result
        self.chunk_overlap: int = 1  # reduced from 2 — 4s cadence instead of 5s
        self.chunk_queue: asyncio.Queue = asyncio.Queue()
        self.is_processing_chunks: bool = False
        self.chunk_task: Optional[asyncio.Task] = None
        
        logger.info("live_surgery_service_initialized", session_id=session_id)
    
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
                "starting_live_session",
                session_id=self.session_id,
                procedure_id=procedure_id,
                surgeon_id=surgeon_id,
                procedure_source=procedure_source
            )
            
            # Load procedure based on source type
            if procedure_source == "outlier":
                # Load outlier resolution procedure
                self.outlier_procedure = await self.db[OUTLIER_PROCEDURES].find_one(
                    {"_id": ObjectId(procedure_id)}
                )
                
                if not self.outlier_procedure:
                    raise ValueError(f"Outlier procedure {procedure_id} not found")
                
                # Convert outlier phases to steps format for compatibility
                # IMPORTANT: Use phase_number as the primary identifier, not sequential index
                self.procedure_steps = [
                    {
                        "step_number": phase["phase_number"],  # Use actual phase number (3.1, 3.2, etc.)
                        "step_name": phase["phase_name"],
                        "description": phase["goal"],
                        "is_critical": phase["priority"] == "HIGH",
                        "phase_number": phase["phase_number"],
                        "phase_index": i  # Keep index for array access
                    }
                    for i, phase in enumerate(self.outlier_procedure.get("phases", []))
                ]
                
                # Create phase_number to index mapping for quick lookup
                self.phase_number_to_index = {
                    phase["phase_number"]: i 
                    for i, phase in enumerate(self.outlier_procedure.get("phases", []))
                }
                
                # Initialize checkpoint tracker for outlier mode
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
                # Load standard master procedure with embedded steps
                self.master_procedure = await self.db[MASTER_PROCEDURES].find_one(
                    {"_id": ObjectId(procedure_id)}
                )
                
                if not self.master_procedure:
                    raise ValueError(f"Master procedure {procedure_id} not found")
                
                # Get steps from embedded array
                self.procedure_steps = self.master_procedure.get("steps", [])
                
                logger.info(
                    "master_procedure_loaded",
                    session_id=self.session_id,
                    procedure_name=self.master_procedure.get("procedure_name"),
                    steps_count=len(self.procedure_steps)
                )
        
            # Build system instruction once (reused every chunk — static context)
            self._build_system_instruction()
        
            # Initialize all steps as pending
            for i in range(len(self.procedure_steps)):
                self.step_status[i] = "pending"
            
            # Reset cumulative tracking
            self.detected_steps_cumulative.clear()
            self.step_detection_history.clear()
            
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
                    "total_steps": len(self.procedure_steps)
                }
            }
            
            result = await self.db[LIVE_SESSIONS].insert_one(session_doc)
            self.session_doc_id = result.inserted_id
            # Store callbacks
            self.alert_callback = alert_callback
            self.analysis_callback = analysis_callback
            
            logger.info(
                "chunk_processing_started",
                session_id=self.session_id,
                chunk_size=self.chunk_size,
                overlap=self.chunk_overlap
            )
            
            logger.info(
                "live_session_started",
                session_id=self.session_id,
                session_doc_id=str(self.session_doc_id),
                total_steps=len(self.procedure_steps)
            )
            
        except Exception as e:
            logger.error(
                "failed_to_start_session",
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
                
                # Add to processing queue
                await self.chunk_queue.put({
                    "frames": chunk_frames,
                    "start_frame": self.frame_count - len(chunk_frames) + 1,
                    "end_frame": self.frame_count
                })
                
                logger.debug(
                    "chunk_queued",
                    session_id=self.session_id,
                    chunk_frames=len(chunk_frames),
                    queue_size=self.chunk_queue.qsize()
                )
                
                # Keep overlap frames for next chunk
                self.frame_buffer = self.frame_buffer[self.chunk_size - self.chunk_overlap:]
            
        except Exception as e:
            logger.error(
                "frame_processing_failed",
                session_id=self.session_id,
                frame_count=self.frame_count,
                error=str(e)
            )
    
    def _build_system_instruction(self):
        """Build static system instruction once per session (reused on every chunk call)."""
        if self.procedure_source == "outlier" and self.outlier_procedure:
            self._system_instruction = build_outlier_system_instruction(
                outlier_procedure=self.outlier_procedure
            )
        elif self.master_procedure:
            self._system_instruction = build_standard_system_instruction(
                procedure_name=self.master_procedure.get("procedure_name", "Unknown"),
                procedure_steps=self.procedure_steps,
            )
        logger.info(
            "system_instruction_built",
            session_id=self.session_id,
            procedure_source=self.procedure_source,
            length=len(self._system_instruction) if self._system_instruction else 0,
        )

    async def _process_chunk_queue(self):
        """
        Background task to process video chunks.

        Pull-latest-discard-stale pattern (inspired by LiveRequest Queue):
        If analysis has fallen behind and multiple chunks are queued, skip
        all intermediate ones and jump directly to the most recent state.
        This ensures Gemini always sees the CURRENT surgical field, not a
        backlog from seconds ago.
        """
        try:
            while self.is_processing_chunks:
                try:
                    # Wait for at least one chunk
                    chunk_data = await asyncio.wait_for(
                        self.chunk_queue.get(),
                        timeout=1.0
                    )

                    # Drain any additional queued chunks — keep only the latest
                    skipped = 0
                    while not self.chunk_queue.empty():
                        try:
                            chunk_data = self.chunk_queue.get_nowait()
                            skipped += 1
                        except asyncio.QueueEmpty:
                            break

                    if skipped:
                        logger.info(
                            "stale_chunks_discarded",
                            session_id=self.session_id,
                            skipped=skipped,
                            analyzing_frame=chunk_data["end_frame"],
                        )

                    logger.info(
                        "processing_chunk",
                        session_id=self.session_id,
                        start_frame=chunk_data["start_frame"],
                        end_frame=chunk_data["end_frame"],
                        queue_remaining=self.chunk_queue.qsize(),
                    )

                    await self._analyze_video_chunk(chunk_data)

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(
                        "chunk_processing_error",
                        session_id=self.session_id,
                        error=str(e),
                    )

        except asyncio.CancelledError:
            logger.info(
                "chunk_processing_cancelled",
                session_id=self.session_id,
            )
        except Exception as e:
            logger.error(
                "chunk_queue_handler_failed",
                session_id=self.session_id,
                error=str(e),
            )
    
    def _create_video_from_frames(self, frames: List[bytes]) -> bytes:
        """
        Encode JPEG frames into an MP4 video using OpenCV — no subprocess, no ffmpeg.
        The video is written to a temp file then read back as bytes and deleted.
        """
        tmp_path = None
        try:
            first = cv2.imdecode(np.frombuffer(frames[0], np.uint8), cv2.IMREAD_COLOR)
            if first is None:
                raise ValueError("Failed to decode first frame")
            h, w = first.shape[:2]

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(tmp_path, fourcc, 1.0, (w, h))
            writer.write(first)
            for frame_bytes in frames[1:]:
                frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    writer.write(frame)
            writer.release()

            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def _build_previous_analysis_context(self) -> str:
        """
        Build complete history of ALL previous chunk analyses so Gemini has
        full temporal continuity and cumulative context.
        
        Includes: detected steps/phases, confidence scores, progress status,
        checkpoint validations, completion evidence for every chunk.
        """
        if not self.chunk_history:
            return ""

        lines = [f"COMPLETE ANALYSIS HISTORY ({len(self.chunk_history)} chunks analyzed):"]
        lines.append("")

        for idx, entry in enumerate(self.chunk_history, start=1):
            # Header for this chunk
            step_or_phase = entry.get("detected_step_number") or entry.get("detected_phase_number") or "None"
            confidence = entry.get("confidence_score", 0)
            lines.append(f"CHUNK {idx}:")
            lines.append(f"  Detected: Step/Phase {step_or_phase} (confidence: {confidence}%)")
            
            # Confidence reasoning
            if entry.get("confidence_reason"):
                lines.append(f"  Confidence reason: {entry['confidence_reason']}")
            
            # Progress and action
            progress = entry.get("step_progress", "unknown")
            lines.append(f"  Progress: {progress}")
            
            if entry.get("action_observed"):
                lines.append(f"  Action: {entry['action_observed'][:120]}")
            
            # Completion evidence
            if entry.get("completion_evidence"):
                lines.append(f"  Completion evidence: {entry['completion_evidence'][:100]}")
            
            # Checkpoint validations (outlier mode)
            checkpoints = entry.get("checkpoint_validations", [])
            if checkpoints:
                lines.append(f"  Checkpoints validated:")
                for cp in checkpoints[:5]:  # Limit to 5 per chunk to avoid token overflow
                    cp_name = cp.get("checkpoint_name", "?")
                    status = cp.get("status", "?")
                    req = cp.get("requirement", "")[:60]
                    lines.append(f"    • [{cp_name}] {req} → {status}")
            
            # Error codes (outlier mode)
            errors = entry.get("error_codes", [])
            if errors:
                error_summary = ", ".join([e.get("code", "?") for e in errors[:3]])
                lines.append(f"  Errors detected: {error_summary}")
            
            # Summary
            if entry.get("analysis_summary"):
                lines.append(f"  Summary: {entry['analysis_summary'][:150]}")
            
            lines.append("")  # Blank line between chunks

        # Add cumulative detection summary
        lines.append(f"CUMULATIVE STATUS:")
        lines.append(f"  Total steps/phases detected so far: {len(self.detected_steps_cumulative)}")
        if self.detected_steps_cumulative:
            detected_names = [
                self.procedure_steps[i].get("step_name") or self.procedure_steps[i].get("phase_name", f"#{i}")
                for i in sorted(self.detected_steps_cumulative)
                if i < len(self.procedure_steps)
            ]
            lines.append(f"  Detected: {', '.join(detected_names[:10])}")

        return "\n".join(lines)

    async def _analyze_video_chunk(self, chunk_data: Dict[str, Any]):
        """
        Analyze a video chunk by:
          1. Encoding JPEG frames → MP4 via OpenCV (no subprocess)
          2. Building a structured prompt that includes full previous-analysis context
          3. Sending the video inline to Gemini with structured JSON output
        """
        try:
            if not self.is_processing_chunks:
                logger.info(
                    "chunk_skipped_session_stopped",
                    session_id=self.session_id,
                    start_frame=chunk_data.get("start_frame"),
                    end_frame=chunk_data.get("end_frame"),
                )
                return

            if not self.procedure_steps or self.current_step_index >= len(self.procedure_steps):
                return

            current_step = self.procedure_steps[self.current_step_index]
            frames = chunk_data["frames"]

            # ── Build MP4 video from captured JPEG frames ─────────────────
            # Run blocking OpenCV I/O in a thread so the event loop stays free
            video_bytes = await asyncio.to_thread(self._create_video_from_frames, frames)

            # ── Build per-chunk prompt with previous analysis as context ──
            history_summary = build_chunk_history_summary(self.chunk_history)
            previous_context = self._build_previous_analysis_context()

            if self.procedure_source == "outlier":
                detected_phase_numbers = {
                    self.procedure_steps[i].get("phase_number")
                    for i in self.detected_steps_cumulative
                }
                remaining_phases = [
                    phase
                    for i, phase in enumerate(self.outlier_procedure.get("phases", []))
                    if i not in self.detected_steps_cumulative
                ]
                next_undetected = next(
                    (i for i in range(len(self.procedure_steps)) if i not in self.detected_steps_cumulative),
                    None,
                )
                current_phase = (
                    self.outlier_procedure["phases"][next_undetected]
                    if next_undetected is not None
                    else None
                )
                prompt = build_outlier_chunk_prompt(
                    detected_phases=detected_phase_numbers,
                    remaining_phases=remaining_phases,
                    current_phase=current_phase,
                    chunk_history_summary=history_summary,
                    chunk_frame_count=len(frames),
                )
                schema = get_outlier_chunk_schema()
            else:
                prompt = build_standard_chunk_prompt(
                    current_step=current_step,
                    detected_steps_cumulative=self.detected_steps_cumulative,
                    procedure_steps=self.procedure_steps,
                    chunk_history_summary=history_summary,
                    chunk_frame_count=len(frames),
                )
                schema = get_standard_chunk_schema()

            # Prepend previous analysis context to the prompt
            if previous_context:
                prompt = previous_context + "\n\n" + prompt

            logger.info(
                "chunk_video_built",
                session_id=self.session_id,
                frame_count=len(frames),
                video_size_kb=len(video_bytes) // 1024,
                prompt_length=len(prompt),
            )

            # ── Send MP4 video inline to Gemini ───────────────────────────
            result = await self.gemini_client.analyze_video_chunk_inline(
                video_bytes=video_bytes,
                prompt=prompt,
                response_schema=schema,
                system_instruction=self._system_instruction,
            )

            # Store structured result in chunk history
            self.chunk_history.append(result)
            if len(self.chunk_history) > 10:
                self.chunk_history = self.chunk_history[-10:]

            self.previous_analysis = result.get("analysis_summary", "")

            logger.info(
                "chunk_analyzed",
                session_id=self.session_id,
                current_step=current_step["step_name"],
                frames=len(frames),
                history_size=len(self.chunk_history),
            )

            await self._process_analysis_response(result, current_step, chunk_data)

        except Exception as e:
            logger.error(
                "chunk_analysis_failed",
                session_id=self.session_id,
                error=str(e),
            )
    
    async def _process_analysis_response(
        self,
        analysis: dict,
        current_step: Dict[str, Any],
        chunk_data: Optional[Dict[str, Any]] = None,
    ):
        """Process structured dict from Gemini — no regex, direct field access."""
        try:
            # ── significant_change gate ──────────────────────────────────
            # If Gemini says the scene is the same as the previous chunk,
            # skip all heavy work (tracking, DB writes, alerts, callbacks).
            # Always process if there's no history yet (first chunk).
            significant_change = analysis.get("significant_change", True)
            observation = analysis.get("observation", "")

            if not significant_change and self.chunk_history:
                logger.info(
                    "chunk_no_significant_change_skipped",
                    session_id=self.session_id,
                    observation=observation,
                    frame_count=chunk_data.get("end_frame") if chunk_data else None,
                )
                return

            if self.procedure_source == "outlier" and self.checkpoint_tracker:
                self.checkpoint_tracker.increment_chunk_counter()

            # ── Extract fields directly from structured dict ──────────────
            if self.procedure_source == "outlier":
                detected_phase_number = analysis.get("detected_phase_number")
                checkpoint_validations = analysis.get("checkpoint_validations", [])
                error_codes = analysis.get("error_codes", [])
                step_progress = analysis.get("step_progress")
                completion_evidence = analysis.get("completion_evidence")
                matches_expected = analysis.get("matches_expected", False)

                detected_step_index = (
                    self.phase_number_to_index.get(detected_phase_number)
                    if detected_phase_number
                    else None
                )

                # Update checkpoint tracker from structured validations
                if detected_phase_number and checkpoint_validations and self.checkpoint_tracker:
                    for cv in checkpoint_validations:
                        cp_name = cv.get("checkpoint_name", "")
                        req_text = cv.get("requirement", "")
                        status = cv.get("status", "NOT_MET")
                        evidence = cv.get("evidence", "")
                        completed = status == "MET"
                        phase_cp_status = self.checkpoint_tracker.get_phase_checkpoint_status(
                            detected_phase_number
                        )
                        for cp in phase_cp_status.get("checkpoints", []):
                            if cp["name"].lower() == cp_name.lower():
                                for req in cp.get("requirements", []):
                                    if req_text.lower() in req.get("text", "").lower():
                                        self.checkpoint_tracker.update_checkpoint_requirement(
                                            detected_phase_number,
                                            cp_name,
                                            req.get("text", req_text),
                                            completed,
                                            evidence,
                                        )

                # A8 detection via prerequisite validation
                if detected_phase_number and self.checkpoint_tracker:
                    eligibility = self.checkpoint_tracker.is_phase_eligible(detected_phase_number)
                    if not eligibility["eligible"]:
                        for prereq in eligibility["blocking_prerequisites"]:
                            a8_error = {
                                "code": "A8",
                                "description": (
                                    f"Phase {detected_phase_number} started before "
                                    f"Phase {prereq['phase']} prerequisites satisfied"
                                ),
                                "severity": "HIGH",
                                "blocking_checkpoints": prereq["blocking_checkpoints"],
                            }
                            error_codes.append(a8_error)
                            logger.error(
                                "a8_operation_omitted_detected",
                                session_id=self.session_id,
                                current_phase=detected_phase_number,
                                prerequisite_phase=prereq["phase"],
                            )

                checkpoint_status = {"details": checkpoint_validations} if checkpoint_validations else None

                logger.info(
                    "outlier_analysis_parsed",
                    session_id=self.session_id,
                    detected_phase=detected_phase_number,
                    error_count=len(error_codes),
                    step_progress=step_progress,
                )
            else:
                detected_step_number = analysis.get("detected_step_number")
                detected_step_index = (detected_step_number - 1) if detected_step_number else None
                if detected_step_index is not None and (
                    detected_step_index < 0 or detected_step_index >= len(self.procedure_steps)
                ):
                    detected_step_index = None
                matches_expected = analysis.get("matches_expected", False)
                step_progress = analysis.get("step_progress")
                completion_evidence = analysis.get("completion_evidence")
                checkpoint_status = None
                error_codes = []

            confidence_score = analysis.get("confidence_score", 0)
            confidence_reason = analysis.get("confidence_reason", "")

            logger.info(
                "analysis_parsed",
                session_id=self.session_id,
                detected_step=detected_step_index,
                confidence_score=confidence_score,
                confidence_reason=confidence_reason,
                matches_expected=matches_expected,
                step_progress=step_progress,
                has_completion_evidence=bool(completion_evidence),
                detected_steps_cumulative=list(self.detected_steps_cumulative),
            )

            # ── Cumulative tracking ─────────────────────────────
            # Only track steps/phases Gemini detects with >= CONFIDENCE_THRESHOLD.
            # Low-confidence matches are logged but skipped to avoid false positives.
            if detected_step_index is not None and confidence_score >= CONFIDENCE_THRESHOLD:
                is_new_detection = detected_step_index not in self.detected_steps_cumulative
                self.detected_steps_cumulative.add(detected_step_index)

                if detected_step_index not in self.step_detection_history:
                    self.step_detection_history[detected_step_index] = []
                self.step_detection_history[detected_step_index].append(analysis)
                if len(self.step_detection_history[detected_step_index]) > 3:
                    self.step_detection_history[detected_step_index] = (
                        self.step_detection_history[detected_step_index][-3:]
                    )

                self.step_status[detected_step_index] = "detected"

                # Advance current_step_index to next undetected so the next
                # chunk prompt shows the correct "expected next" hint
                self.current_step_index = next(
                    (i for i in range(len(self.procedure_steps)) if i not in self.detected_steps_cumulative),
                    len(self.procedure_steps) - 1,
                )

                if is_new_detection:
                    logger.info(
                        "step_detected_cumulative",
                        session_id=self.session_id,
                        step_index=detected_step_index,
                        step_name=self.procedure_steps[detected_step_index]["step_name"],
                        total_detected=len(self.detected_steps_cumulative),
                        current_step_index_now=self.current_step_index,
                    )

                # Mark significantly skipped steps as missed
                for i in range(detected_step_index):
                    if i not in self.detected_steps_cumulative:
                        if detected_step_index - i > 2 and self.step_status.get(i) == "pending":
                            self.step_status[i] = "missed"
                            logger.warning(
                                "step_marked_missed",
                                session_id=self.session_id,
                                step_index=i,
                                step_name=self.procedure_steps[i]["step_name"],
                            )
                            await self._generate_missed_step_alert(i)

            # ── Build analysis text from structured result ─────────────────
            analysis_text = analysis.get("analysis_summary", "")
            if analysis.get("action_observed"):
                analysis_text = f"{analysis['action_observed']}\n\n{analysis_text}"

            # ── Send real-time update to frontend ─────────────────────────
            if self.analysis_callback:
                try:
                    frame_info = chunk_data if chunk_data else {
                        "start_frame": self.frame_count,
                        "end_frame": self.frame_count,
                    }
                    next_undetected_index = next(
                        (i for i in range(len(self.procedure_steps)) if i not in self.detected_steps_cumulative),
                        len(self.procedure_steps) - 1,
                    )
                    current_step_for_display = self.procedure_steps[next_undetected_index]

                    all_steps_data = []
                    for i, s in enumerate(self.procedure_steps):
                        step_data = {
                            "step_number": s.get("step_number", i + 1),
                            "step_name": s["step_name"],
                            "description": s.get("description"),
                            "is_critical": s.get("is_critical", False),
                            "detected": i in self.detected_steps_cumulative,
                        }

                        if self.procedure_source == "outlier":
                            phase_number = s.get("phase_number")
                            phase = (
                                self.outlier_procedure["phases"][i]
                                if i < len(self.outlier_procedure.get("phases", []))
                                else {}
                            )
                            checkpoint_info = (
                                self.checkpoint_tracker.get_phase_checkpoint_status(phase_number)
                                if self.checkpoint_tracker
                                else {}
                            )
                            if i in self.detected_steps_cumulative:
                                if checkpoint_info.get("has_checkpoints"):
                                    if checkpoint_info.get("all_complete"):
                                        phase_status = "completed"
                                    else:
                                        blocking = (
                                            self.checkpoint_tracker.get_blocking_checkpoints(phase_number)
                                            if self.checkpoint_tracker
                                            else []
                                        )
                                        phase_status = "blocked" if blocking else "current"
                                else:
                                    phase_status = "completed"
                            else:
                                phase_status = self.step_status.get(i, "pending")

                            step_data.update({
                                "phase_number": phase_number,
                                "phase_name": phase.get("phase_name"),
                                "goal": phase.get("goal"),
                                "priority": phase.get("priority"),
                                "status": phase_status,
                                "checkpoints": checkpoint_info.get("checkpoints", []),
                                "detected_errors": error_codes if i == detected_step_index else [],
                            })
                        else:
                            step_data["status"] = (
                                "completed"
                                if i in self.detected_steps_cumulative
                                else self.step_status.get(i, "pending")
                            )

                        all_steps_data.append(step_data)

                    analysis_data = {
                        "frame_count": frame_info["end_frame"],
                        "current_step_index": next_undetected_index,
                        "current_step_name": current_step_for_display["step_name"],
                        "detected_step_index": detected_step_index,
                        "matches_expected": matches_expected,
                        "procedure_source": self.procedure_source,
                        "expected_step": {
                            "step_number": current_step_for_display.get("step_number"),
                            "step_name": current_step_for_display["step_name"],
                            "description": current_step_for_display.get("description"),
                            "is_critical": current_step_for_display.get("is_critical", False),
                        },
                        "all_steps": all_steps_data,
                        "analysis_text": analysis_text,
                        "timestamp": datetime.utcnow().isoformat(),
                    }

                    if self.procedure_source == "outlier" and checkpoint_status:
                        analysis_data["checkpoint_status"] = checkpoint_status

                    await self.analysis_callback(analysis_data)
                except Exception as callback_error:
                    logger.warning(
                        "analysis_callback_failed",
                        session_id=self.session_id,
                        error=str(callback_error),
                        error_type=type(callback_error).__name__,
                    )

            # ── Compliance check ──────────────────────────────────────────
            try:
                await self._check_compliance(analysis_text, current_step)
            except Exception as compliance_error:
                logger.warning(
                    "compliance_check_failed",
                    session_id=self.session_id,
                    error=str(compliance_error),
                )

        except Exception as e:
            logger.error(
                "analysis_response_processing_failed",
                session_id=self.session_id,
                error=str(e),
            )
    
    async def _check_compliance(
        self,
        analysis: str,
        expected_step: Dict[str, Any]
    ):
        """
        Check for compliance issues and generate alerts.
        
        Args:
            analysis: Gemini analysis result
            expected_step: Expected surgical step
        """
        try:
            # Simple keyword-based alert detection
            # In production, this should use more sophisticated NLP
            alerts = []
            
            analysis_lower = analysis.lower()
            
            # Check for step deviation
            if "no" in analysis_lower and "expected step" in analysis_lower:
                alerts.append({
                    "alert_type": "step_deviation",
                    "severity": "warning",
                    "message": f"Possible deviation from expected step: {expected_step['step_name']}",
                    "metadata": {"analysis": analysis}
                })
            
            # Check for safety concerns
            if any(keyword in analysis_lower for keyword in ["concern", "risk", "danger", "warning"]):
                alerts.append({
                    "alert_type": "safety_concern",
                    "severity": "high" if expected_step.get("is_critical") else "medium",
                    "message": "Safety concern detected during procedure",
                    "metadata": {"analysis": analysis, "step": expected_step['step_name']}
                })
            
            # Check for missing instruments
            if "missing" in analysis_lower or "not visible" in analysis_lower:
                alerts.append({
                    "alert_type": "instrument_check",
                    "severity": "medium",
                    "message": "Expected instruments may not be visible",
                    "metadata": {"analysis": analysis}
                })
            
            # Store and send alerts
            if alerts:
                await self._create_alerts(alerts)
            
        except Exception as e:
            logger.error(
                "compliance_check_failed",
                session_id=self.session_id,
                error=str(e)
            )
    
    async def _generate_missed_step_alert(self, step_index: int):
        """
        Generate alert for a missed/skipped step.
        
        Args:
            step_index: Index of the missed step
        """
        if step_index >= len(self.procedure_steps):
            return
        
        missed_step = self.procedure_steps[step_index]
        alert = {
            "alert_type": "step_skipped",
            "severity": "high" if missed_step.get("is_critical") else "medium",
            "message": f"Step {missed_step.get('step_number', step_index + 1)} '{missed_step['step_name']}' was skipped",
            "metadata": {
                "step_index": step_index,
                "step_name": missed_step['step_name'],
                "is_critical": missed_step.get('is_critical', False)
            }
        }
        await self._create_alerts([alert])
    
    async def _create_alerts(self, alerts: List[Dict[str, Any]]):
        """
        Create and store alerts in database.
        
        Args:
            alerts: List of alert dictionaries
        """
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
                    "alerts_generated",
                    session_id=self.session_id,
                    alert_count=len(alert_documents)
                )
                
                # Send alerts via callback if available
                if self.alert_callback:
                    try:
                        await self.alert_callback(alerts)
                    except Exception as e:
                        logger.error("alert_callback_failed", error=str(e))
        
        except Exception as e:
            logger.error(
                "failed_to_create_alerts",
                session_id=self.session_id,
                error=str(e)
            )
    
    async def advance_step(self):
        """Manually advance to the next surgical step."""
        if self.current_step_index < len(self.procedure_steps) - 1:
            self.current_step_index += 1
            
            # Update session document
            await self.db[LIVE_SESSIONS].update_one(
                {"_id": self.session_doc_id},
                {
                    "$set": {
                        "current_step": self.current_step_index,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            logger.info(
                "step_advanced",
                session_id=self.session_id,
                new_step=self.current_step_index,
                step_name=self.procedure_steps[self.current_step_index]['step_name']
            )
    
    
    async def stop_session(self):
        """Stop the live surgery monitoring session."""
        try:
            # Stop chunk processing immediately
            self.is_processing_chunks = False
            
            # Clear chunk queue to prevent orphaned chunks from processing
            while not self.chunk_queue.empty():
                try:
                    self.chunk_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            # Clear frame buffer
            self.frame_buffer.clear()
            
            logger.info(
                "session_cleanup",
                session_id=self.session_id,
                chunks_cleared=True,
                frames_cleared=True
            )
            
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
                "live_session_stopped",
                session_id=self.session_id,
                frames_processed=self.frame_count
            )
            
        except Exception as e:
            logger.error(
                "failed_to_stop_session",
                session_id=self.session_id,
                error=str(e)
            )
