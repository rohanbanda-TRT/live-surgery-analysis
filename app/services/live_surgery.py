"""
Live surgery monitoring service using Gemini Live API.
"""
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
import asyncio
import cv2
import numpy as np

from app.services.gemini_client import GeminiClient
from app.db.collections import MASTER_PROCEDURES, SURGICAL_STEPS, LIVE_SESSIONS, SESSION_ALERTS
from app.core.logging import logger


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
        self.procedure_steps: List[Dict[str, Any]] = []
        self.current_step_index: int = 0
        self.session_doc_id: Optional[ObjectId] = None
        self.alert_callback: Optional[Callable] = None
        self.analysis_callback: Optional[Callable] = None
        
        # Previous analysis for context awareness
        self.previous_analysis: Optional[str] = None
        self.chunk_history: List[str] = []  # Full history of all chunk analyses (last 10)
        
        # Cumulative step tracking (like reference implementation)
        self.detected_steps_cumulative: set = set()  # Steps that have been detected (NEVER removed)
        self.step_status: Dict[int, str] = {}  # step_index -> status (pending/detected/missed)
        self.step_detection_history: Dict[int, List[str]] = {}  # step_index -> list of detection analyses
        
        # Video chunk processing
        self.frame_buffer: List[bytes] = []
        self.frame_count: int = 0
        self.chunk_size: int = 7  # 7 seconds at 1 FPS
        self.chunk_overlap: int = 2  # 2 second overlap
        self.chunk_queue: asyncio.Queue = asyncio.Queue()
        self.is_processing_chunks: bool = False
        self.chunk_task: Optional[asyncio.Task] = None
        
        logger.info("live_surgery_service_initialized", session_id=session_id)
    
    async def start_session(
        self,
        procedure_id: str,
        surgeon_id: str,
        alert_callback: Optional[Callable] = None,
        analysis_callback: Optional[Callable] = None
    ):
        """
        Start a new live surgery monitoring session.
        
        Args:
            procedure_id: ID of the master procedure
            surgeon_id: ID of the surgeon
            alert_callback: Callback for sending alerts
            analysis_callback: Callback for sending real-time analysis updates
        """
        try:
            # Start chunk processing task
            self.is_processing_chunks = True
            self.chunk_task = asyncio.create_task(self._process_chunk_queue())
            
            logger.info(
                "starting_live_session",
                session_id=self.session_id,
                procedure_id=procedure_id,
                surgeon_id=surgeon_id
            )
            
            # Load master procedure with embedded steps
            self.master_procedure = await self.db[MASTER_PROCEDURES].find_one(
                {"_id": ObjectId(procedure_id)}
            )
            
            if not self.master_procedure:
                raise ValueError(f"Master procedure {procedure_id} not found")
            
            # Get steps from embedded array
            self.procedure_steps = self.master_procedure.get("steps", [])
            
            # Initialize all steps as pending
            for i in range(len(self.procedure_steps)):
                self.step_status[i] = "pending"
            
            # Reset cumulative tracking
            self.detected_steps_cumulative.clear()
            self.step_detection_history.clear()
            
            # Create session document
            session_doc = {
                "session_id": self.session_id,
                "procedure_id": ObjectId(procedure_id),
                "surgeon_id": surgeon_id,
                "start_time": datetime.utcnow(),
                "end_time": None,
                "current_step": 0,
                "status": "active",
                "metadata": {
                    "procedure_name": self.master_procedure.get("procedure_name"),
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
                        "processing_chunk",
                        session_id=self.session_id,
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
                        "chunk_processing_error",
                        session_id=self.session_id,
                        error=str(e)
                    )
                    
        except asyncio.CancelledError:
            logger.info(
                "chunk_processing_cancelled",
                session_id=self.session_id
            )
        except Exception as e:
            logger.error(
                "chunk_queue_handler_failed",
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
                "video_creation_failed",
                session_id=self.session_id,
                error=str(e)
            )
            raise
    
    async def _analyze_video_chunk(self, chunk_data: Dict[str, Any]):
        """Analyze a video chunk."""
        try:
            # Early exit if session is stopped
            if not self.is_processing_chunks:
                logger.info(
                    "chunk_skipped_session_stopped",
                    session_id=self.session_id,
                    start_frame=chunk_data.get("start_frame"),
                    end_frame=chunk_data.get("end_frame")
                )
                return
            
            if not self.procedure_steps or self.current_step_index >= len(self.procedure_steps):
                return
            
            current_step = self.procedure_steps[self.current_step_index]
            
            # Create video from frames
            video_data = self._create_video_from_frames(chunk_data["frames"])
            
            # Build cumulative detected steps context (like reference implementation)
            detected_steps = []
            for i in sorted(self.detected_steps_cumulative):
                s = self.procedure_steps[i]
                step_info = f"✓ Step {s.get('step_number', i+1)}: {s['step_name']}"
                # Add detection history if available
                if i in self.step_detection_history and self.step_detection_history[i]:
                    last_detection = self.step_detection_history[i][-1]
                    step_info += f" (Last seen: {last_detection[:100]}...)"
                detected_steps.append(step_info)
            detected_context = "\n".join(detected_steps) if detected_steps else "None yet"
            
            # Build remaining steps (not yet detected)
            remaining_steps = []
            for i, s in enumerate(self.procedure_steps):
                if i not in self.detected_steps_cumulative:
                    step_detail = f"Step {s.get('step_number', i+1)}: {s['step_name']}"
                    # Mark expected next step
                    if i == min([idx for idx in range(len(self.procedure_steps)) if idx not in self.detected_steps_cumulative], default=len(self.procedure_steps)):
                        step_detail += " ← EXPECTED NEXT"
                    remaining_steps.append(step_detail)
            remaining_context = "\n".join(remaining_steps) if remaining_steps else "All steps detected!"
            
            # Build list of detected step numbers
            detected_step_numbers = [
                self.procedure_steps[i].get('step_number', i+1) 
                for i in sorted(self.detected_steps_cumulative)
            ]
            cumulative_note = f"\n**IMPORTANT:** Steps {', '.join(map(str, detected_step_numbers))} have been detected and should REMAIN detected. Focus on detecting remaining steps.\n" if detected_step_numbers else ""
            
            # Build full chunk history context with complete analysis
            history_context = ""
            if self.chunk_history:
                # Show last 5 chunks with full analysis for better context
                recent_history = self.chunk_history
                history_lines = []
                for idx, hist in enumerate(recent_history, start=len(self.chunk_history)-len(recent_history)+1):
                    # Extract key information from analysis
                    lines = hist.split('\n')
                    detected_step = next((line for line in lines if 'Detected Step:' in line), 'Unknown')
                    progress = next((line for line in lines if 'Step Progress:' in line), 'Unknown')
                    matches = next((line for line in lines if 'Matches Expected:' in line), 'Unknown')
                    
                    history_lines.append(f"Chunk {idx}:")
                    history_lines.append(f"  {detected_step}")
                    history_lines.append(f"  {progress}")
                    history_lines.append(f"  {matches}")
                history_context = f"\n**Analysis History (Last {len(recent_history)} chunks):**\n" + "\n".join(history_lines) + "\n"
            
            # Build detailed current step context from master procedure
            current_step_detail = f"""
**Current Expected Step {current_step.get('step_number', self.current_step_index + 1)}: {current_step['step_name']}**
- Description: {current_step.get('description', 'N/A')}
- Expected Duration: {current_step.get('expected_duration_min', 'N/A')}-{current_step.get('expected_duration_max', 'N/A')} minutes
- Critical Step: {'YES - Extra caution required' if current_step.get('is_critical') else 'No'}
- Required Instruments: {', '.join(current_step.get('instruments_required', [])) or 'Not specified'}
- Anatomical Landmarks: {', '.join(current_step.get('anatomical_landmarks', [])) or 'Not specified'}
- Visual Cues: {current_step.get('visual_cues', 'Not specified')}
"""
            
            # Enhanced prompt with master procedure alignment
            prompt = f"""Analyze this {len(chunk_data['frames'])}-second surgical video clip from {self.master_procedure.get('procedure_name')}.

**MASTER PROCEDURE CONTEXT:**
{current_step_detail}

**DETECTED STEPS (CUMULATIVE - ALREADY IDENTIFIED):** 
{detected_context}
{cumulative_note}
**REMAINING STEPS (FOCUS ON DETECTING THESE):** 
{remaining_context}
{history_context}
**CRITICAL RULES - CUMULATIVE TRACKING:**
1. This is CUMULATIVE analysis - once a step is detected, it REMAINS detected forever
2. **FOCUS ONLY on remaining steps** - detected steps are already confirmed
3. Compare video against the MASTER PROCEDURE definition above
4. Steps take MINUTES (50-200+ frames at 1 FPS), not seconds
5. Mark "completed" ONLY when you see clear evidence the step description is fulfilled
6. "in-progress" is default - be conservative
7. Verify actual surgical actions match the step description, not just instrument presence
8. Review analysis history and master procedure definition before making status updates
9. Match visible instruments and anatomical landmarks against requirements
10. **DO NOT re-detect already detected steps** - they remain in the detected list automatically

**RESPONSE FORMAT:**
Detected Step: [number] - [name]
Action Being Performed: [what surgeon is doing - compare to step description]
Instruments Visible: [list - compare to required instruments]
Anatomical Landmarks: [list - compare to expected landmarks]
Matches Expected: [yes/no - does video match master procedure definition?]
Step Progress: [just-started/in-progress/nearing-completion/completed]
Completion Evidence: [required if completed - what proves step description is fulfilled? else "N/A"]
Analysis: [brief observation comparing video to master procedure]

Analyze the video clip and respond:"""
            
            logger.info(
                "chunk_prompt_built",
                session_id=self.session_id,
                prompt=prompt
            )

            # Analyze video chunk
            analysis = await self.gemini_client.analyze_video_chunk(
                video_data=video_data,
                prompt=prompt
            )
            
            # Store in chunk history for full context
            self.chunk_history.append(analysis)
            # Keep only last 10 chunks to avoid memory bloat
            if len(self.chunk_history) > 10:
                self.chunk_history = self.chunk_history[-10:]
            
            # Store for backward compatibility
            self.previous_analysis = analysis
            
            logger.info(
                "chunk_analyzed",
                session_id=self.session_id,
                current_step=current_step['step_name'],
                frames=len(chunk_data["frames"]),
                history_size=len(self.chunk_history)
            )
            
            # Parse and process response
            await self._process_analysis_response(analysis, current_step, chunk_data)
            
        except Exception as e:
            logger.error(
                "chunk_analysis_failed",
                session_id=self.session_id,
                error=str(e)
            )
    
    async def _process_analysis_response(
        self, 
        analysis: str, 
        current_step: Dict[str, Any],
        chunk_data: Optional[Dict[str, Any]] = None
    ):
        """Process analysis response using cumulative step tracking (like reference implementation)."""
        try:
            # Parse AI response
            detected_step_index = self._parse_detected_step(analysis)
            matches_expected = "yes" in analysis.lower() and "matches expected: yes" in analysis.lower()
            step_progress = self._parse_step_progress(analysis)
            completion_evidence = self._parse_completion_evidence(analysis)
            
            # Log current state before processing
            logger.info(
                "analysis_parsed",
                session_id=self.session_id,
                detected_step=detected_step_index,
                matches_expected=matches_expected,
                step_progress=step_progress,
                has_completion_evidence=bool(completion_evidence),
                detected_steps_cumulative=list(self.detected_steps_cumulative)
            )
            
            # CUMULATIVE TRACKING: Add detected step to cumulative set (NEVER removed)
            if detected_step_index is not None and matches_expected:
                # Check if this is a new detection
                is_new_detection = detected_step_index not in self.detected_steps_cumulative
                
                # Add to cumulative set (once added, never removed)
                self.detected_steps_cumulative.add(detected_step_index)
                
                # Store detection in history
                if detected_step_index not in self.step_detection_history:
                    self.step_detection_history[detected_step_index] = []
                self.step_detection_history[detected_step_index].append(analysis)
                # Keep only last 3 detections per step
                if len(self.step_detection_history[detected_step_index]) > 3:
                    self.step_detection_history[detected_step_index] = self.step_detection_history[detected_step_index][-3:]
                
                # Update status to detected
                self.step_status[detected_step_index] = "detected"
                
                if is_new_detection:
                    logger.info(
                        "step_detected_cumulative",
                        session_id=self.session_id,
                        step_index=detected_step_index,
                        step_name=self.procedure_steps[detected_step_index]['step_name'],
                        total_detected=len(self.detected_steps_cumulative)
                    )
                else:
                    logger.debug(
                        "step_seen_again",
                        session_id=self.session_id,
                        step_index=detected_step_index,
                        step_name=self.procedure_steps[detected_step_index]['step_name']
                    )
                
                # Check for skipped steps (steps that should have been detected but weren't)
                undetected_before = [i for i in range(detected_step_index) if i not in self.detected_steps_cumulative]
                
                # Mark significantly skipped steps as missed (more than 2 steps behind)
                for i in undetected_before:
                    if detected_step_index - i > 2 and self.step_status.get(i) == "pending":
                        self.step_status[i] = "missed"
                        logger.warning(
                            "step_marked_missed",
                            session_id=self.session_id,
                            step_index=i,
                            step_name=self.procedure_steps[i]['step_name'],
                            reason=f"Step {detected_step_index} detected, but step {i} not seen"
                        )
                        await self._create_missed_step_alert(i)
            
            # Send real-time update to frontend
            if self.analysis_callback:
                try:
                    frame_info = chunk_data if chunk_data else {"start_frame": self.frame_count, "end_frame": self.frame_count}
                    
                    # Calculate current step as the next undetected step (for frontend progress display)
                    next_undetected_index = next(
                        (i for i in range(len(self.procedure_steps)) if i not in self.detected_steps_cumulative),
                        len(self.procedure_steps) - 1  # Default to last step if all detected
                    )
                    current_step_for_display = self.procedure_steps[next_undetected_index]
                    
                    analysis_data = {
                        "frame_count": frame_info["end_frame"],
                        "current_step_index": next_undetected_index,
                        "current_step_name": current_step_for_display['step_name'],
                        "detected_step_index": detected_step_index,
                        "matches_expected": matches_expected,
                        "expected_step": {
                            "step_number": current_step_for_display.get('step_number'),
                            "step_name": current_step_for_display['step_name'],
                            "description": current_step_for_display.get('description'),
                            "is_critical": current_step_for_display.get('is_critical', False)
                        },
                        "all_steps": [
                            {
                                "step_number": s.get('step_number', i+1),
                                "step_name": s['step_name'],
                                "description": s.get('description'),
                                "is_critical": s.get('is_critical', False),
                                # Map internal status to frontend-compatible values
                                "status": (
                                    "completed" if i in self.detected_steps_cumulative
                                    else self.step_status.get(i, "pending")
                                ),
                                "detected": i in self.detected_steps_cumulative
                            }
                            for i, s in enumerate(self.procedure_steps)
                        ],
                        "analysis_text": analysis,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    await self.analysis_callback(analysis_data)
                except Exception as callback_error:
                    # WebSocket may be closed - log but don't fail processing
                    logger.warning(
                        "analysis_callback_failed",
                        session_id=self.session_id,
                        error=str(callback_error),
                        error_type=type(callback_error).__name__
                    )
            
            # Check compliance (don't fail if this errors either)
            try:
                await self._check_compliance(analysis, current_step)
            except Exception as compliance_error:
                logger.warning(
                    "compliance_check_failed",
                    session_id=self.session_id,
                    error=str(compliance_error)
                )
            
        except Exception as e:
            logger.error(
                "analysis_response_processing_failed",
                session_id=self.session_id,
                error=str(e)
            )
    
    async def _analyze_current_state(self):
        """
        Analyze current surgical state using the latest frame.
        """
        if not self.frame_buffer or not self.procedure_steps:
            return
        
        try:
            # Get current expected step
            if self.current_step_index >= len(self.procedure_steps):
                logger.info(
                    "procedure_completed",
                    session_id=self.session_id,
                    total_steps=len(self.procedure_steps)
                )
                return
            
            current_step = self.procedure_steps[self.current_step_index]
            
            # Build completed steps context with details
            completed_steps = [
                f"Step {s.get('step_number', i+1)}: {s['step_name']} - COMPLETED ✓\n  Description: {s.get('description', 'N/A')[:100]}..."
                for i, s in enumerate(self.procedure_steps)
                if self.step_status.get(i) == "completed"
            ]
            completed_context = "\n".join(completed_steps) if completed_steps else "None yet"
            
            # Build remaining steps context with full details (current and pending only)
            remaining_steps = []
            for i, s in enumerate(self.procedure_steps):
                if self.step_status.get(i) in ["current", "pending"]:
                    step_detail = f"Step {s.get('step_number', i+1)}: {s['step_name']}"
                    if i == self.current_step_index:
                        step_detail += " (EXPECTED NOW)"
                    step_detail += f"\n  Description: {s.get('description', 'N/A')}"
                    step_detail += f"\n  Instruments: {', '.join(s.get('instruments_required', []))}"
                    step_detail += f"\n  Landmarks: {', '.join(s.get('anatomical_landmarks', []))}"
                    remaining_steps.append(step_detail)
            remaining_context = "\n\n".join(remaining_steps)
            
            # Build previous frame context for temporal awareness
            previous_frame_context = ""
            if self.previous_analysis:
                previous_frame_context = f"""

**PREVIOUS FRAME ANALYSIS (for context awareness):**
{self.previous_analysis}

**IMPORTANT:** Use the previous frame analysis to understand:
- What was happening in the last frame
- Continuity of surgical actions
- Whether the same action is continuing or a new action has started
- Temporal progression of the procedure
"""
            
            # Prepare detailed analysis prompt with completed steps awareness
            # IMPORTANT: This matches the strict Live API format for consistency
            prompt = f"""
You are monitoring a live surgical procedure: {self.master_procedure.get('procedure_name')}

**COMPLETED STEPS (DO NOT MATCH AGAINST THESE):**
{completed_context}

**REMAINING STEPS TO PERFORM:**
{remaining_context}

**EXPECTED CURRENT STEP (Step {current_step.get('step_number', self.current_step_index + 1)}):**
- Name: {current_step['step_name']}
- Description: {current_step.get('description', 'N/A')}
- Critical: {current_step.get('is_critical', False)}
- Expected Instruments: {', '.join(current_step.get('instruments_required', []))}
- Anatomical Landmarks: {', '.join(current_step.get('anatomical_landmarks', []))}
{previous_frame_context}
**CRITICAL: UNDERSTANDING STEP COMPLETION**

⚠️ SURGICAL STEPS TAKE TIME - DO NOT RUSH TO COMPLETION ⚠️

A surgical step is NOT complete just because you see instruments or landmarks that match the step description.

**WHAT DOES NOT MEAN A STEP IS COMPLETE:**
❌ Seeing instruments mentioned in the step
❌ Seeing anatomical landmarks mentioned in the step  
❌ Frame "looks similar" to what the step describes
❌ Surgeon is "working on" the area mentioned in the step
❌ Some action from the step is visible

**WHAT MEANS A STEP IS COMPLETE:**
✅ You observe the ENTIRE action sequence being performed
✅ You see clear COMPLETION markers (e.g., suture tied, organ removed, port secured)
✅ Surgeon moves to NEXT anatomical area or changes instruments for next step
✅ The surgical field shows EVIDENCE of completion (e.g., hemostasis achieved, dissection finished)

**YOUR TASK:**
1. **OBSERVE, DON'T ASSUME**: Watch what is actually happening, not what might be happening
2. **VERIFY ACTIONS**: Confirm the surgeon is performing the specific action described in the step
3. **WAIT FOR COMPLETION**: Do not mark complete until you see clear completion evidence
4. **BE CONSERVATIVE**: When in doubt, keep the step as "in-progress", do NOT mark complete

**RESPONSE FORMAT (REQUIRED):**

Detected Step: [number] - [name]
Action Being Performed: [specific action you observe - be detailed]
Instruments Visible: [list what you actually see]
Anatomical Landmarks: [list what you actually see]
Matches Expected: [yes/no - does current frame match expected step?]
Step Progress: [just-started / in-progress / nearing-completion / completed]
Completion Evidence: [REQUIRED if marking completed - what proves it's done? If not complete, write "N/A"]
Sequence Status: [in-sequence/out-of-sequence/skipped-step]
Repeated Completed Step: [yes/no]
Analysis: [detailed observation - what is the surgeon doing RIGHT NOW?]

**STRICT COMPLETION RULES:**

1. **DO NOT mark "Matches Expected: yes" unless:**
   - Current frame shows the expected step being actively performed
   - You can describe the specific action happening
   - The action matches the step description

2. **DO NOT mark Step Progress as "completed" unless:**
   - You observe clear completion evidence (describe it explicitly in Completion Evidence field)
   - The surgical field changes indicating progression to next step

3. **DO NOT match similarity - VERIFY ACTIONS:**
   - Bad: "I see a grasper, so this must be Step 3"
   - Good: "I see the surgeon using a grasper to dissect Calot's triangle, actively separating the cystic duct from surrounding tissue - Step 3 is being performed"

4. **BE EXTREMELY CONSERVATIVE:**
   - If unsure whether step is complete → mark "in-progress", NOT "completed"
   - If you see partial progress → mark "in-progress"
   - If instruments are present but no clear action → mark "just-started" or "in-progress"
   - Only mark "completed" when you have clear evidence in the Completion Evidence field

**ANTI-HALLUCINATION RULES:**
1. Only report what you ACTUALLY SEE in the current frame
2. Do not infer completion from instrument presence alone
3. Do not assume steps are done quickly - surgery is slow and methodical
4. If the view is unclear or obstructed, say so in Analysis - do not guess
5. ONLY compare against REMAINING STEPS, not completed ones
"""
            
            # Analyze latest frame
            latest_frame = self.frame_buffer[-1]
            analysis = await self.gemini_client.analyze_frame(
                frame_data=latest_frame,
                prompt=prompt
            )
            
            # Store this analysis for next frame's context awareness
            self.previous_analysis = analysis
            
            logger.info(
                "frame_analyzed",
                session_id=self.session_id,
                current_step=current_step['step_name'],
                frame_count=self.frame_count
            )
            
            # Parse AI response with new strict format
            detected_step_index = self._parse_detected_step(analysis)
            matches_expected = "yes" in analysis.lower() and "matches expected: yes" in analysis.lower()
            is_repeated_step = "repeated completed step: yes" in analysis.lower()
            
            # Parse step progress and completion evidence (new fields)
            step_progress = self._parse_step_progress(analysis)
            completion_evidence = self._parse_completion_evidence(analysis)
            
            logger.info(
                "per_frame_analysis_parsed",
                session_id=self.session_id,
                detected_step=detected_step_index,
                step_progress=step_progress,
                has_completion_evidence=bool(completion_evidence),
                matches_expected=matches_expected
            )
            
            # CRITICAL: Prevent completed steps from reverting to current
            # Only allow going back to completed step if AI is VERY confident it's being repeated
            if detected_step_index is not None:
                detected_step_status = self.step_status.get(detected_step_index)
                
                # If detected step is already completed, only accept if AI confirms repetition
                if detected_step_status == "completed" and not is_repeated_step:
                    logger.info(
                        "ignoring_completed_step_detection",
                        session_id=self.session_id,
                        detected_step=detected_step_index,
                        current_step=self.current_step_index,
                        reason="Step already completed and no strong evidence of repetition"
                    )
                    # Treat as if still on current step
                    detected_step_index = self.current_step_index
            
            # Update step status based on detection with stricter completion logic
            if detected_step_index is not None and detected_step_index != self.current_step_index:
                # Check if this is a valid transition
                detected_status = self.step_status.get(detected_step_index)
                
                if detected_status == "completed" and is_repeated_step:
                    # Very rare case: surgeon is repeating a completed step
                    logger.warning(
                        "step_repetition_detected",
                        session_id=self.session_id,
                        step_index=detected_step_index,
                        step_name=self.procedure_steps[detected_step_index]['step_name']
                    )
                    # Mark current step as pending and go back to repeated step
                    self.step_status[self.current_step_index] = "pending"
                    self.current_step_index = detected_step_index
                    self.step_status[detected_step_index] = "current"
                    
                elif detected_step_index > self.current_step_index:
                    # Surgeon jumped ahead - mark skipped steps as missed
                    for i in range(self.current_step_index, detected_step_index):
                        if self.step_status.get(i) != "completed":
                            self.step_status[i] = "missed"
                            await self._generate_missed_step_alert(i)
                    
                    # Update to detected step
                    self.current_step_index = detected_step_index
                    self.step_status[detected_step_index] = "current"
                    
            elif matches_expected and detected_step_index == self.current_step_index:
                # STRICTER COMPLETION LOGIC: Only mark complete if step_progress is "completed" AND completion_evidence exists
                if step_progress == "completed" and completion_evidence:
                    logger.info(
                        "step_completed_with_evidence",
                        session_id=self.session_id,
                        step_index=self.current_step_index,
                        evidence=completion_evidence
                    )
                    self.step_status[self.current_step_index] = "completed"
                    self.current_step_index += 1
                    if self.current_step_index < len(self.procedure_steps):
                        self.step_status[self.current_step_index] = "current"
                else:
                    # Step is being performed but not complete yet
                    logger.debug(
                        "step_in_progress_per_frame",
                        session_id=self.session_id,
                        step_index=self.current_step_index,
                        progress=step_progress,
                        has_evidence=bool(completion_evidence)
                    )
            
            # Send real-time analysis update to frontend with all step statuses
            if self.analysis_callback:
                analysis_data = {
                    "frame_count": self.frame_count,
                    "current_step_index": self.current_step_index,
                    "current_step_name": current_step['step_name'],
                    "detected_step_index": detected_step_index,
                    "matches_expected": matches_expected,
                    "expected_step": {
                        "step_number": current_step.get('step_number'),
                        "step_name": current_step['step_name'],
                        "description": current_step.get('description'),
                        "is_critical": current_step.get('is_critical', False)
                    },
                    "all_steps": [
                        {
                            "step_number": s.get('step_number', i+1),
                            "step_name": s['step_name'],
                            "description": s.get('description'),
                            "is_critical": s.get('is_critical', False),
                            "status": self.step_status.get(i, "pending")
                        }
                        for i, s in enumerate(self.procedure_steps)
                    ],
                    "analysis_text": analysis,
                    "timestamp": datetime.utcnow().isoformat()
                }
                await self.analysis_callback(analysis_data)
            
            # Check for deviations and generate alerts
            await self._check_compliance(analysis, current_step)
            
        except Exception as e:
            logger.error(
                "state_analysis_failed",
                session_id=self.session_id,
                error=str(e)
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
    
    def _parse_detected_step(self, analysis: str) -> Optional[int]:
        """
        Parse the detected step number from AI analysis response.
        
        Args:
            analysis: AI analysis text
            
        Returns:
            Step index (0-based) or None if not detected
        """
        try:
            # Look for "Detected Step: [number]" pattern
            import re
            match = re.search(r'Detected Step:\s*(\d+)', analysis, re.IGNORECASE)
            if match:
                step_number = int(match.group(1))
                # Convert to 0-based index
                return step_number - 1
            return None
        except Exception as e:
            logger.error("failed_to_parse_detected_step", error=str(e))
            return None
    
    def _parse_step_progress(self, analysis: str) -> Optional[str]:
        """
        Parse the step progress from AI analysis response.
        
        Args:
            analysis: AI analysis text
            
        Returns:
            Step progress status or None
        """
        try:
            import re
            match = re.search(r'Step Progress:\s*(just-started|in-progress|nearing-completion|completed)', analysis, re.IGNORECASE)
            if match:
                return match.group(1).lower()
            return None
        except Exception as e:
            logger.error("failed_to_parse_step_progress", error=str(e))
            return None
    
    def _parse_completion_evidence(self, analysis: str) -> Optional[str]:
        """
        Parse the completion evidence from AI analysis response.
        
        Args:
            analysis: AI analysis text
            
        Returns:
            Completion evidence text or None
        """
        try:
            import re
            match = re.search(r'Completion Evidence:\s*(.+?)(?:\n|$)', analysis, re.IGNORECASE)
            if match:
                evidence = match.group(1).strip()
                # Only return if it's not empty or placeholder text
                if evidence and evidence.lower() not in ['none', 'n/a', '-', 'null']:
                    return evidence
            return None
        except Exception as e:
            logger.error("failed_to_parse_completion_evidence", error=str(e))
            return None
    
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
