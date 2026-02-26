"""
Optimized live surgery monitoring service (v2).

Key improvements over live_surgery.py:
  - No ffmpeg: sends frames as individual JPEG images to Gemini
  - Structured JSON output: eliminates ALL regex-based parsing (~15 parsers removed)
  - System instruction: static procedure context set once per session
  - Cleaner state management with structured analysis dicts
  - Removed dead code (_analyze_current_state — 280 lines)
  - Reduced duplication between standard/outlier callback data building
  - Compatible with existing frontend WebSocket contract

Note on Gemini Live API:
  Vertex AI Live API only supports audio-native models
  (gemini-live-2.5-flash-native-audio). These models do not support TEXT
  response modality or response_json_schema, making them incompatible with
  surgical JSON analysis. generate_content with 7-frame isolated chunks
  is the correct approach for this use case.
"""
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Set
import asyncio
import json

from app.services.gemini_client import GeminiClient
from app.services.analysis_schemas import (
    get_standard_chunk_schema,
    get_outlier_chunk_schema,
)
from app.prompts.prompts_v2 import (
    build_standard_system_instruction,
    build_outlier_system_instruction,
    build_standard_chunk_prompt,
    build_outlier_chunk_prompt,
    build_chunk_history_summary,
)
from app.services.outlier_analysis import CheckpointTracker
from app.db.collections import (
    MASTER_PROCEDURES,
    LIVE_SESSIONS,
    SESSION_ALERTS,
    OUTLIER_PROCEDURES,
)
from app.core.logging import logger


class LiveSurgeryServiceV2:
    """Optimized service for real-time surgical monitoring and compliance checking."""

    def __init__(self, db: AsyncDatabase, session_id: str):
        self.db = db
        self.session_id = session_id
        self.gemini_client = GeminiClient()

        # Session state
        self.master_procedure: Optional[Dict[str, Any]] = None
        self.outlier_procedure: Optional[Dict[str, Any]] = None
        self.procedure_source: str = "standard"
        self.procedure_steps: List[Dict[str, Any]] = []
        self.current_step_index: int = 0
        self.session_doc_id: Optional[ObjectId] = None
        self.alert_callback: Optional[Callable] = None
        self.analysis_callback: Optional[Callable] = None

        # System instruction (set once at session start)
        self._system_instruction: Optional[str] = None

        # Cumulative step tracking
        self.detected_steps_cumulative: Set[int] = set()
        self.step_status: Dict[int, str] = {}
        self.step_detection_history: Dict[int, List[dict]] = {}
        self.phase_number_to_index: Dict[str, int] = {}

        # Chunk history (structured dicts, not raw text)
        self.chunk_history: List[dict] = []

        # Outlier mode
        self.checkpoint_tracker: Optional[CheckpointTracker] = None

        # Video chunk processing
        self.frame_buffer: List[bytes] = []
        self.frame_count: int = 0
        self.chunk_size: int = 7
        self.chunk_overlap: int = 2
        self.chunk_queue: asyncio.Queue = asyncio.Queue()
        self.is_processing_chunks: bool = False
        self.chunk_task: Optional[asyncio.Task] = None

        logger.info("live_surgery_service_v2_initialized", session_id=session_id)

    # ──────────────────────────────────────────────
    # Session lifecycle
    # ──────────────────────────────────────────────

    async def start_session(
        self,
        procedure_id: str,
        surgeon_id: str,
        procedure_source: str = "standard",
        alert_callback: Optional[Callable] = None,
        analysis_callback: Optional[Callable] = None,
    ):
        """Start a new live surgery monitoring session."""
        try:
            self.is_processing_chunks = True
            self.chunk_task = asyncio.create_task(self._process_chunk_queue())
            self.procedure_source = procedure_source

            logger.info(
                "starting_live_session_v2",
                session_id=self.session_id,
                procedure_id=procedure_id,
                procedure_source=procedure_source,
            )

            if procedure_source == "outlier":
                await self._load_outlier_procedure(procedure_id)
            else:
                await self._load_standard_procedure(procedure_id)

            # Initialize step statuses
            for i in range(len(self.procedure_steps)):
                self.step_status[i] = "pending"
            self.detected_steps_cumulative.clear()
            self.step_detection_history.clear()

            # Create session document
            procedure_name = (
                self.outlier_procedure.get("procedure_name")
                if self.procedure_source == "outlier"
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
                },
            }
            result = await self.db[LIVE_SESSIONS].insert_one(session_doc)
            self.session_doc_id = result.inserted_id

            self.alert_callback = alert_callback
            self.analysis_callback = analysis_callback

            logger.info(
                "live_session_v2_started",
                session_id=self.session_id,
                session_doc_id=str(self.session_doc_id),
                total_steps=len(self.procedure_steps),
            )

        except Exception as e:
            logger.error("failed_to_start_session_v2", session_id=self.session_id, error=str(e))
            raise

    async def _load_standard_procedure(self, procedure_id: str):
        """Load standard master procedure and build system instruction."""
        self.master_procedure = await self.db[MASTER_PROCEDURES].find_one(
            {"_id": ObjectId(procedure_id)}
        )
        if not self.master_procedure:
            raise ValueError(f"Master procedure {procedure_id} not found")

        self.procedure_steps = self.master_procedure.get("steps", [])
        self._system_instruction = build_standard_system_instruction(
            procedure_name=self.master_procedure.get("procedure_name", "Unknown"),
            procedure_steps=self.procedure_steps,
        )

        logger.info(
            "standard_procedure_loaded_v2",
            session_id=self.session_id,
            procedure_name=self.master_procedure.get("procedure_name"),
            steps_count=len(self.procedure_steps),
            system_instruction_length=len(self._system_instruction),
        )

    async def _load_outlier_procedure(self, procedure_id: str):
        """Load outlier procedure and build system instruction + checkpoint tracker."""
        self.outlier_procedure = await self.db[OUTLIER_PROCEDURES].find_one(
            {"_id": ObjectId(procedure_id)}
        )
        if not self.outlier_procedure:
            raise ValueError(f"Outlier procedure {procedure_id} not found")

        self.procedure_steps = [
            {
                "step_number": phase["phase_number"],
                "step_name": phase["phase_name"],
                "description": phase.get("goal"),
                "is_critical": phase.get("priority") == "HIGH",
                "phase_number": phase["phase_number"],
                "phase_index": i,
            }
            for i, phase in enumerate(self.outlier_procedure.get("phases", []))
        ]

        self.phase_number_to_index = {
            phase["phase_number"]: i
            for i, phase in enumerate(self.outlier_procedure.get("phases", []))
        }

        self.checkpoint_tracker = CheckpointTracker()
        for phase in self.outlier_procedure.get("phases", []):
            self.checkpoint_tracker.initialize_phase_checkpoints(phase)

        self._system_instruction = build_outlier_system_instruction(
            outlier_procedure=self.outlier_procedure
        )

        logger.info(
            "outlier_procedure_loaded_v2",
            session_id=self.session_id,
            procedure_name=self.outlier_procedure.get("procedure_name"),
            phases_count=len(self.procedure_steps),
            system_instruction_length=len(self._system_instruction),
        )

    # ──────────────────────────────────────────────
    # Frame buffering & chunk queuing
    # ──────────────────────────────────────────────

    async def process_frame(self, frame_data: bytes):
        """Process incoming video frame — accumulate into chunks for analysis."""
        try:
            self.frame_count += 1
            self.frame_buffer.append(frame_data)

            if len(self.frame_buffer) >= self.chunk_size:
                chunk_frames = self.frame_buffer[: self.chunk_size]
                await self.chunk_queue.put(
                    {
                        "frames": chunk_frames,
                        "start_frame": self.frame_count - len(chunk_frames) + 1,
                        "end_frame": self.frame_count,
                    }
                )
                logger.debug(
                    "chunk_queued_v2",
                    session_id=self.session_id,
                    chunk_frames=len(chunk_frames),
                    queue_size=self.chunk_queue.qsize(),
                )
                self.frame_buffer = self.frame_buffer[self.chunk_size - self.chunk_overlap :]

        except Exception as e:
            logger.error(
                "frame_processing_failed_v2",
                session_id=self.session_id,
                frame_count=self.frame_count,
                error=str(e),
            )

    async def _process_chunk_queue(self):
        """Background task to process video chunks from queue."""
        try:
            while self.is_processing_chunks:
                try:
                    chunk_data = await asyncio.wait_for(
                        self.chunk_queue.get(), timeout=1.0
                    )
                    logger.info(
                        "processing_chunk_v2",
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
                        "chunk_processing_error_v2",
                        session_id=self.session_id,
                        error=str(e),
                    )

        except asyncio.CancelledError:
            logger.info("chunk_processing_cancelled_v2", session_id=self.session_id)
        except Exception as e:
            logger.error(
                "chunk_queue_handler_failed_v2",
                session_id=self.session_id,
                error=str(e),
            )

    # ──────────────────────────────────────────────
    # Core: Chunk analysis (NO ffmpeg, structured output)
    # ──────────────────────────────────────────────

    async def _analyze_video_chunk(self, chunk_data: Dict[str, Any]):
        """Analyze a video chunk using multi-image + structured JSON output."""
        try:
            if not self.is_processing_chunks:
                return
            if not self.procedure_steps or self.current_step_index >= len(self.procedure_steps):
                return

            current_step = self.procedure_steps[self.current_step_index]

            # Build per-chunk dynamic prompt
            history_summary = build_chunk_history_summary(self.chunk_history)

            if self.procedure_source == "outlier":
                analysis_result = await self._analyze_outlier_chunk(
                    chunk_data, history_summary
                )
            else:
                analysis_result = await self._analyze_standard_chunk(
                    chunk_data, current_step, history_summary
                )

            # Store in chunk history (structured dict, not raw text)
            self.chunk_history.append(analysis_result)
            if len(self.chunk_history) > 10:
                self.chunk_history = self.chunk_history[-10:]

            logger.info(
                "chunk_analyzed_v2",
                session_id=self.session_id,
                current_step=current_step["step_name"],
                frames=len(chunk_data["frames"]),
                history_size=len(self.chunk_history),
            )

            # Process the structured result
            await self._process_structured_response(analysis_result, current_step, chunk_data)

        except Exception as e:
            logger.error(
                "chunk_analysis_failed_v2",
                session_id=self.session_id,
                error=str(e),
            )

    async def _analyze_standard_chunk(
        self,
        chunk_data: Dict[str, Any],
        current_step: Dict[str, Any],
        history_summary: Optional[str],
    ) -> dict:
        """Run Gemini structured analysis for standard mode."""
        prompt = build_standard_chunk_prompt(
            current_step=current_step,
            detected_steps_cumulative=self.detected_steps_cumulative,
            procedure_steps=self.procedure_steps,
            chunk_history_summary=history_summary,
            chunk_frame_count=len(chunk_data["frames"]),
        )
        return await self.gemini_client.analyze_frames_structured(
            frames=chunk_data["frames"],
            prompt=prompt,
            response_schema=get_standard_chunk_schema(),
            system_instruction=self._system_instruction,
        )

    async def _analyze_outlier_chunk(
        self,
        chunk_data: Dict[str, Any],
        history_summary: Optional[str],
    ) -> dict:
        """Run Gemini structured analysis for outlier mode."""
        detected_phase_numbers = {
            self.procedure_steps[i].get("phase_number")
            for i in self.detected_steps_cumulative
        }
        remaining_phases = [
            phase
            for i, phase in enumerate(self.outlier_procedure.get("phases", []))
            if i not in self.detected_steps_cumulative
        ]
        # Determine current expected phase
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
            chunk_frame_count=len(chunk_data["frames"]),
        )
        return await self.gemini_client.analyze_frames_structured(
            frames=chunk_data["frames"],
            prompt=prompt,
            response_schema=get_outlier_chunk_schema(),
            system_instruction=self._system_instruction,
        )

    # ──────────────────────────────────────────────
    # Response processing (NO regex — direct dict access)
    # ──────────────────────────────────────────────

    async def _process_structured_response(
        self,
        result: dict,
        current_step: Dict[str, Any],
        chunk_data: Dict[str, Any],
    ):
        """Process the structured analysis result. Replaces _process_analysis_response."""
        try:
            if self.procedure_source == "outlier":
                await self._process_outlier_result(result, current_step, chunk_data)
            else:
                await self._process_standard_result(result, current_step, chunk_data)
        except Exception as e:
            logger.error(
                "structured_response_processing_failed",
                session_id=self.session_id,
                error=str(e),
            )

    async def _process_standard_result(
        self,
        result: dict,
        current_step: Dict[str, Any],
        chunk_data: Dict[str, Any],
    ):
        """Process standard mode structured analysis result."""
        # Direct dict access — no regex needed
        detected_step_number = result.get("detected_step_number")
        matches_expected = result.get("matches_expected", False)
        step_progress = result.get("step_progress")
        completion_evidence = result.get("completion_evidence")

        # Convert 1-based step number to 0-based index
        detected_step_index = (detected_step_number - 1) if detected_step_number else None

        # Validate index is in range
        if detected_step_index is not None and (
            detected_step_index < 0 or detected_step_index >= len(self.procedure_steps)
        ):
            detected_step_index = None

        logger.info(
            "standard_result_parsed_v2",
            session_id=self.session_id,
            detected_step=detected_step_index,
            matches_expected=matches_expected,
            step_progress=step_progress,
            has_evidence=bool(completion_evidence),
        )

        # Cumulative tracking
        error_codes = []
        self._update_cumulative_tracking(
            detected_step_index, matches_expected, step_progress, completion_evidence
        )

        # Send frontend update
        await self._send_analysis_update(
            result=result,
            detected_step_index=detected_step_index,
            matches_expected=matches_expected,
            error_codes=error_codes,
            chunk_data=chunk_data,
        )

        # Compliance check
        await self._check_compliance_structured(result, current_step)

    async def _process_outlier_result(
        self,
        result: dict,
        current_step: Dict[str, Any],
        chunk_data: Dict[str, Any],
    ):
        """Process outlier mode structured analysis result."""
        if self.checkpoint_tracker:
            self.checkpoint_tracker.increment_chunk_counter()

        detected_phase_number = result.get("detected_phase_number")
        matches_expected = result.get("matches_expected", False)
        step_progress = result.get("step_progress")
        completion_evidence = result.get("completion_evidence")
        checkpoint_validations = result.get("checkpoint_validations", [])
        error_codes = result.get("error_codes", [])

        # Convert phase number to index
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

                # Find matching checkpoint in tracker
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
                    }
                    error_codes.append(a8_error)
                    logger.error(
                        "a8_operation_omitted_detected_v2",
                        session_id=self.session_id,
                        current_phase=detected_phase_number,
                        prerequisite_phase=prereq["phase"],
                    )

        logger.info(
            "outlier_result_parsed_v2",
            session_id=self.session_id,
            detected_phase=detected_phase_number,
            error_count=len(error_codes),
            checkpoint_validations=len(checkpoint_validations),
        )

        # Cumulative tracking
        self._update_cumulative_tracking(
            detected_step_index, matches_expected, step_progress, completion_evidence
        )

        # Send frontend update
        await self._send_analysis_update(
            result=result,
            detected_step_index=detected_step_index,
            matches_expected=matches_expected,
            error_codes=error_codes,
            chunk_data=chunk_data,
        )

        # Compliance check
        await self._check_compliance_structured(result, current_step)

    # ──────────────────────────────────────────────
    # Cumulative step tracking (shared logic)
    # ──────────────────────────────────────────────

    def _update_cumulative_tracking(
        self,
        detected_step_index: Optional[int],
        matches_expected: bool,
        step_progress: Optional[str],
        completion_evidence: Optional[str],
    ):
        """Update cumulative step detection tracking."""
        if detected_step_index is None or not matches_expected:
            return

        is_new = detected_step_index not in self.detected_steps_cumulative
        self.detected_steps_cumulative.add(detected_step_index)

        # Store detection history (structured)
        if detected_step_index not in self.step_detection_history:
            self.step_detection_history[detected_step_index] = []
        self.step_detection_history[detected_step_index].append(
            {"progress": step_progress, "evidence": completion_evidence}
        )
        if len(self.step_detection_history[detected_step_index]) > 3:
            self.step_detection_history[detected_step_index] = (
                self.step_detection_history[detected_step_index][-3:]
            )

        self.step_status[detected_step_index] = "detected"

        if is_new:
            logger.info(
                "step_detected_cumulative_v2",
                session_id=self.session_id,
                step_index=detected_step_index,
                step_name=self.procedure_steps[detected_step_index]["step_name"],
                total_detected=len(self.detected_steps_cumulative),
            )

        # Check for skipped steps
        for i in range(detected_step_index):
            if i not in self.detected_steps_cumulative:
                if detected_step_index - i > 2 and self.step_status.get(i) == "pending":
                    self.step_status[i] = "missed"
                    logger.warning(
                        "step_marked_missed_v2",
                        session_id=self.session_id,
                        step_index=i,
                        step_name=self.procedure_steps[i]["step_name"],
                    )
                    asyncio.create_task(self._create_missed_step_alert(i))

    # ──────────────────────────────────────────────
    # Frontend callback (unified for both modes)
    # ──────────────────────────────────────────────

    async def _send_analysis_update(
        self,
        result: dict,
        detected_step_index: Optional[int],
        matches_expected: bool,
        error_codes: List[dict],
        chunk_data: Dict[str, Any],
    ):
        """Build and send analysis update to frontend via callback."""
        if not self.analysis_callback:
            return

        try:
            # Calculate next undetected step for progress display
            next_undetected_index = next(
                (
                    i
                    for i in range(len(self.procedure_steps))
                    if i not in self.detected_steps_cumulative
                ),
                len(self.procedure_steps) - 1,
            )
            current_step_for_display = self.procedure_steps[next_undetected_index]

            # Build all_steps data for frontend
            all_steps_data = self._build_all_steps_data(detected_step_index, error_codes)

            # Build analysis text from structured result
            analysis_text = result.get("analysis_summary", "")
            if result.get("action_observed"):
                analysis_text = f"{result['action_observed']}\n\n{analysis_text}"

            analysis_data = {
                "frame_count": chunk_data["end_frame"],
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

            await self.analysis_callback(analysis_data)

        except Exception as callback_error:
            logger.warning(
                "analysis_callback_failed_v2",
                session_id=self.session_id,
                error=str(callback_error),
            )

    def _build_all_steps_data(
        self,
        detected_step_index: Optional[int],
        error_codes: List[dict],
    ) -> List[dict]:
        """Build the all_steps array for the frontend — unified for both modes."""
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

                # Get checkpoint status
                checkpoint_info = (
                    self.checkpoint_tracker.get_phase_checkpoint_status(phase_number)
                    if self.checkpoint_tracker
                    else {}
                )

                # Determine phase status
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

                step_data.update(
                    {
                        "phase_number": phase_number,
                        "phase_name": phase.get("phase_name"),
                        "goal": phase.get("goal"),
                        "priority": phase.get("priority"),
                        "status": phase_status,
                        "checkpoints": checkpoint_info.get("checkpoints", []),
                        "detected_errors": error_codes if i == detected_step_index else [],
                    }
                )
            else:
                step_data["status"] = (
                    "completed"
                    if i in self.detected_steps_cumulative
                    else self.step_status.get(i, "pending")
                )

            all_steps_data.append(step_data)

        return all_steps_data

    # ──────────────────────────────────────────────
    # Compliance / alerts
    # ──────────────────────────────────────────────

    async def _check_compliance_structured(
        self, result: dict, expected_step: Dict[str, Any]
    ):
        """Check compliance using structured analysis result (no text parsing)."""
        try:
            alerts = []
            summary = (result.get("analysis_summary") or "").lower()

            if not result.get("matches_expected", True):
                alerts.append(
                    {
                        "alert_type": "step_deviation",
                        "severity": "warning",
                        "message": f"Possible deviation from expected step: {expected_step['step_name']}",
                        "metadata": {"analysis_summary": result.get("analysis_summary")},
                    }
                )

            if any(kw in summary for kw in ("concern", "risk", "danger", "warning")):
                alerts.append(
                    {
                        "alert_type": "safety_concern",
                        "severity": "high" if expected_step.get("is_critical") else "medium",
                        "message": "Safety concern detected during procedure",
                        "metadata": {"step": expected_step["step_name"]},
                    }
                )

            # Check for error codes in outlier mode
            for err in result.get("error_codes", []):
                alerts.append(
                    {
                        "alert_type": f"error_code_{err.get('code', 'unknown')}",
                        "severity": "high" if err.get("severity") == "HIGH" else "medium",
                        "message": f"Error {err.get('code')}: {err.get('description')}",
                        "metadata": err,
                    }
                )

            if alerts:
                await self._create_alerts(alerts)

        except Exception as e:
            logger.error(
                "compliance_check_failed_v2",
                session_id=self.session_id,
                error=str(e),
            )

    async def _create_missed_step_alert(self, step_index: int):
        """Generate alert for a missed/skipped step."""
        if step_index >= len(self.procedure_steps):
            return
        missed_step = self.procedure_steps[step_index]
        alert = {
            "alert_type": "step_skipped",
            "severity": "high" if missed_step.get("is_critical") else "medium",
            "message": (
                f"Step {missed_step.get('step_number', step_index + 1)} "
                f"'{missed_step['step_name']}' was skipped"
            ),
            "metadata": {
                "step_index": step_index,
                "step_name": missed_step["step_name"],
                "is_critical": missed_step.get("is_critical", False),
            },
        }
        await self._create_alerts([alert])

    async def _create_alerts(self, alerts: List[Dict[str, Any]]):
        """Create and store alerts in database."""
        try:
            now = datetime.utcnow()
            alert_documents = [
                {
                    "session_id": self.session_doc_id,
                    "alert_type": alert["alert_type"],
                    "severity": alert["severity"],
                    "message": alert["message"],
                    "timestamp": now,
                    "acknowledged": False,
                    "metadata": alert.get("metadata", {}),
                }
                for alert in alerts
            ]

            if alert_documents:
                await self.db[SESSION_ALERTS].insert_many(alert_documents)
                logger.warning(
                    "alerts_generated_v2",
                    session_id=self.session_id,
                    alert_count=len(alert_documents),
                )
                if self.alert_callback:
                    try:
                        await self.alert_callback(alerts)
                    except Exception as e:
                        logger.error("alert_callback_failed_v2", error=str(e))

        except Exception as e:
            logger.error(
                "failed_to_create_alerts_v2",
                session_id=self.session_id,
                error=str(e),
            )

    # ──────────────────────────────────────────────
    # Session stop / cleanup
    # ──────────────────────────────────────────────

    async def stop_session(self):
        """Stop the live surgery monitoring session."""
        try:
            self.is_processing_chunks = False

            # Clear queue
            while not self.chunk_queue.empty():
                try:
                    self.chunk_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            self.frame_buffer.clear()

            if self.chunk_task and not self.chunk_task.done():
                self.chunk_task.cancel()
                try:
                    await self.chunk_task
                except asyncio.CancelledError:
                    pass

            if self.session_doc_id:
                await self.db[LIVE_SESSIONS].update_one(
                    {"_id": self.session_doc_id},
                    {"$set": {"end_time": datetime.utcnow(), "status": "completed"}},
                )

            logger.info(
                "live_session_stopped_v2",
                session_id=self.session_id,
                frames_processed=self.frame_count,
            )

        except Exception as e:
            logger.error(
                "failed_to_stop_session_v2",
                session_id=self.session_id,
                error=str(e),
            )
