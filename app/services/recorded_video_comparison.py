"""
Recorded video comparison service - analyzes full recorded videos against procedures.

Simple approach: analyzes the entire video at once and returns results similar to live monitoring,
including step detection, checkpoint validation, and error detection.
"""
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from datetime import datetime
from typing import Dict, Any, List, Optional

from app.services.gemini_client import GeminiClient
from app.db.collections import MASTER_PROCEDURES, OUTLIER_PROCEDURES
from app.prompts.outlier_prompts import build_outlier_resolution_context
from app.prompts.standard_prompts import get_video_analysis_prompt
from app.services.outlier_analysis import OutlierAnalysisParser, CheckpointTracker
from app.services.procedure_cache import ProcedureCache
from app.core.config import settings
from app.core.logging import logger


class RecordedVideoComparisonService:
    """
    Service for comparing recorded videos against procedures.
    
    Analyzes the full video and returns:
    - Which steps/phases were detected
    - Which checkpoints were met (for outlier mode)
    - Which error codes were detected
    - Overall comparison results
    """
    
    def __init__(self, db: AsyncDatabase, procedure_cache: Optional[ProcedureCache] = None, gemini_model: Optional[str] = None):
        """
        Initialize recorded video comparison service.
        
        Args:
            db: MongoDB database instance
            procedure_cache: Optional procedure cache to avoid repeated DB queries
            gemini_model: Optional Gemini model override (e.g., 'gemini-2.5-flash', 'gemini-2.5-pro')
        """
        self.db = db
        self.gemini_client = GeminiClient()
        if gemini_model:
            self.gemini_client.model = gemini_model
        self.procedure_cache = procedure_cache or ProcedureCache()
    
    async def compare_video(
        self,
        video_gs_uri: str,
        procedure_id: str,
        procedure_source: str = "standard",
        cached_procedure: Optional[tuple] = None
    ) -> Dict[str, Any]:
        """
        Compare a recorded video against a procedure.
        
        Args:
            video_gs_uri: Google Cloud Storage URI of the recorded video
            procedure_id: ID of the master or outlier procedure to compare against
            procedure_source: "standard" for master procedures, "outlier" for outlier procedures
            cached_procedure: Optional pre-loaded (procedure, procedure_steps) tuple to avoid DB access
            
        Returns:
            Complete comparison results with detected steps, checkpoints, and errors
        """
        try:
            logger.info(
                "starting_recorded_video_comparison",
                video_uri=video_gs_uri,
                procedure_id=procedure_id,
                procedure_source=procedure_source
            )
            
            # Load procedure (from cache or parameter if provided)
            if cached_procedure:
                procedure, procedure_steps = cached_procedure
                logger.info(
                    "using_cached_procedure_data",
                    procedure_id=procedure_id,
                    procedure_source=procedure_source
                )
            else:
                procedure, procedure_steps = await self.procedure_cache.load_procedure(
                    self.db, procedure_id, procedure_source
                )
            
            # Build prompt based on procedure source
            if procedure_source == "outlier":
                prompt = self._build_outlier_comparison_prompt(procedure, procedure_steps)
            else:
                prompt = self._build_standard_comparison_prompt(procedure, procedure_steps)
            
            # Analyze full video
            logger.info("analyzing_full_video", video_uri=video_gs_uri)
            
            try:
                analysis = await self.gemini_client.analyze_video(
                    video_gs_uri=video_gs_uri,
                    prompt=prompt,
                    temperature=0.2
                )
            except Exception as api_error:
                error_msg = str(api_error)
                
                # Handle rate limit errors
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                    logger.error(
                        "gemini_rate_limit_exceeded",
                        video_uri=video_gs_uri,
                        error=error_msg
                    )
                    raise ValueError(
                        "API rate limit exceeded. Please wait a few minutes and try again. "
                        "If this persists, consider upgrading your Gemini API quota."
                    )
                
                # Handle other API errors
                logger.error(
                    "gemini_api_error",
                    video_uri=video_gs_uri,
                    error=error_msg
                )
                raise ValueError(f"Video analysis failed: {error_msg}")
            
            # Process results based on procedure source
            if procedure_source == "outlier":
                results = await self._process_outlier_results(
                    analysis, procedure, procedure_steps
                )
            else:
                results = await self._process_standard_results(
                    analysis, procedure, procedure_steps
                )
            
            # Log completion with appropriate field names based on mode
            if procedure_source == "outlier":
                logger.info(
                    "recorded_video_comparison_completed",
                    video_uri=video_gs_uri,
                    phases_detected=results["summary"]["detected_phases"],
                    total_phases=results["summary"]["total_phases"]
                )
            else:
                logger.info(
                    "recorded_video_comparison_completed",
                    video_uri=video_gs_uri,
                    steps_detected=results["summary"]["detected_steps"],
                    total_steps=results["summary"]["total_steps"]
                )
            
            return results
            
        except Exception as e:
            logger.error(
                "recorded_video_comparison_failed",
                video_uri=video_gs_uri,
                error=str(e)
            )
            raise
    
    async def _load_procedure(self, procedure_id: str, procedure_source: str):
        """Load procedure from cache or database."""
        return await self.procedure_cache.load_procedure(
            self.db, procedure_id, procedure_source
        )
    
    def _build_standard_comparison_prompt(
        self, 
        procedure: Dict[str, Any], 
        procedure_steps: List[Dict[str, Any]]
    ) -> str:
        """Build prompt for standard procedure comparison."""
        procedure_name = procedure.get("procedure_name", "Unknown Procedure")
        
        # Build steps list
        steps_list = []
        for i, step in enumerate(procedure_steps, 1):
            step_info = f"""
Step {step.get('step_number', i)}: {step['step_name']}
- Description: {step.get('description', 'N/A')}
- Expected Duration: {step.get('expected_duration_min', 'N/A')}-{step.get('expected_duration_max', 'N/A')} minutes
- Critical: {'YES' if step.get('is_critical') else 'No'}
- Required Instruments: {', '.join(step.get('instruments_required', [])) or 'Not specified'}
- Anatomical Landmarks: {', '.join(step.get('anatomical_landmarks', [])) or 'Not specified'}
"""
            steps_list.append(step_info)
        
        steps_context = "\n".join(steps_list)
        
        prompt = f"""You are analyzing a COMPLETE recorded surgical video of: {procedure_name}

**MASTER PROCEDURE STEPS:**
{steps_context}

**YOUR TASK:**
Analyze this FULL video and determine which steps from the master procedure were performed.

For EACH step in the master procedure, determine:
1. **Was this step detected in the video?** (YES/NO)
2. **Evidence**: What specific visual evidence confirms this step was performed (or not)?
3. **Timestamp**: Approximate time range when this step occurred (if detected)
4. **Completion**: Was the step fully completed? (COMPLETED/PARTIAL/NOT_PERFORMED)

**OUTPUT FORMAT:**
For each step, provide:

Step [number]: [name]
Detected: [YES/NO]
Evidence: [specific observations from video]
Timestamp: [approximate time range, e.g., "2:30-5:45" or "Not detected"]
Completion: [COMPLETED/PARTIAL/NOT_PERFORMED]
Notes: [any deviations or observations]

---

After analyzing all steps, provide:

**SUMMARY:**
- Total Steps in Procedure: [number]
- Steps Detected: [number]
- Steps Completed: [number]
- Steps Partial: [number]
- Steps Not Performed: [number]
- Overall Match: [percentage]

**CRITICAL OBSERVATIONS:**
[Any critical deviations, skipped steps, or safety concerns]
"""
        
        return prompt
    
    def _build_outlier_comparison_prompt(
        self, 
        procedure: Dict[str, Any], 
        procedure_steps: List[Dict[str, Any]]
    ) -> str:
        """Build prompt for outlier procedure comparison."""
        procedure_name = procedure.get("procedure_name", "Unknown Procedure")
        
        # Build detailed phase context with checkpoints
        phases_context = build_outlier_resolution_context(procedure)
        
        prompt = f"""You are analyzing a COMPLETE recorded surgical video using the Outlier Resolution Protocol.

{phases_context}

**YOUR TASK:**
Analyze this FULL video and determine:
1. Which phases were performed
2. Which checkpoints were satisfied
3. Which error codes (A1-A10, C1-C6, R1-R3) were detected

**FOR EACH PHASE, provide:**

Phase [number]: [name]
Detected: [YES/NO]
Evidence: [specific visual evidence from video]
Timestamp: [approximate time range when this phase occurred]

**CHECKPOINT VALIDATION:**
IMPORTANT: Only validate the specific checkpoint requirements listed for this phase. Do NOT include prevention strategies or general observations here.
For each checkpoint requirement listed in the phase definition:
- [Checkpoint requirement]: [MET/NOT MET] - [Evidence]

Example format:
- Correct imaging available and verified: MET - X-ray and MRI shown
- Level/laterality confirmed with fluoroscopy: NOT MET - No fluoroscopy shown

**ALERT QUESTIONS ASSESSMENT:**
IMPORTANT: Only answer the alert questions listed for this phase. Each must be answered YES or NO based on video evidence.
For each alert question listed in the phase definition:
- [Question text]: [YES/NO] - [Evidence from video]

Example format:
- Is the cannula positioning compatible with the defined target?: YES - Cannula clearly positioned at L4-L5 level
- Are there signs of bleeding?: NO - Field is clear with no active bleeding visible

**ERROR CODES DETECTED:**
IMPORTANT: Error codes can occur multiple times at different points during surgery. List each occurrence separately with timestamp and specific details.
Format for each error occurrence:
- [Code] ([Category]) at [timestamp]: [What exactly happened] - [Specific visual evidence]

Example:
- A8 (Action Errors) at 05:23: Coagulation omitted before tissue manipulation - Surgeon proceeded to manipulate tissue without performing bipolar coagulation on visible vessels
- C1 (Checking Errors) at 12:45: Fluoroscopy confirmation skipped - No fluoroscopy verification shown before advancing to next phase

**PHASE COMPLETION:**
- Status: [COMPLETED/PARTIAL/NOT_PERFORMED]
- Blocking Issues: [Any blocking checkpoints not met]

---

After analyzing all phases, provide:

**SUMMARY:**
- Total Phases: [number]
- Phases Detected: [number]
- Phases Completed: [number]
- Total Checkpoints: [number]
- Checkpoints Met: [number]
- Error Codes Detected: [list all codes]

**CRITICAL SAFETY ISSUES:**
[List any HIGH priority errors or safety concerns]

**OVERALL ASSESSMENT:**
[Brief assessment of procedure execution quality]
"""
        
        return prompt
    
    async def _process_standard_results(
        self,
        analysis: str,
        procedure: Dict[str, Any],
        procedure_steps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Process results for standard procedure comparison."""
        import re
        
        # Parse step detection from analysis
        detected_steps = []
        
        for i, step in enumerate(procedure_steps):
            step_number = step.get("step_number", i + 1)
            step_name = step["step_name"]
            
            # Look for this step in the analysis
            # Pattern: "Step X: ... Detected: YES/NO"
            pattern = rf"Step {step_number}:.*?Detected:\s*(YES|NO)"
            match = re.search(pattern, analysis, re.IGNORECASE | re.DOTALL)
            
            detected = False
            evidence = ""
            completion = "NOT_PERFORMED"
            
            if match:
                detected = match.group(1).upper() == "YES"
                
                # Extract evidence
                evidence_pattern = rf"Step {step_number}:.*?Evidence:\s*(.+?)(?=Timestamp:|$)"
                evidence_match = re.search(evidence_pattern, analysis, re.IGNORECASE | re.DOTALL)
                if evidence_match:
                    evidence = evidence_match.group(1).strip()[:200]
                
                # Extract completion status
                completion_pattern = rf"Step {step_number}:.*?Completion:\s*(COMPLETED|PARTIAL|NOT_PERFORMED)"
                completion_match = re.search(completion_pattern, analysis, re.IGNORECASE | re.DOTALL)
                if completion_match:
                    completion = completion_match.group(1).upper()
            
            detected_steps.append({
                "step_number": step_number,
                "step_name": step_name,
                "description": step.get("description"),
                "detected": detected,
                "completion": completion,
                "evidence": evidence,
                "is_critical": step.get("is_critical", False)
            })
        
        # Calculate summary
        total_steps = len(procedure_steps)
        detected_count = sum(1 for s in detected_steps if s["detected"])
        completed_count = sum(1 for s in detected_steps if s["completion"] == "COMPLETED")
        
        return {
            "procedure_source": "standard",
            "procedure_name": procedure.get("procedure_name"),
            "procedure_id": str(procedure["_id"]),
            "model_used": self.gemini_client.model,
            "summary": {
                "total_steps": total_steps,
                "detected_steps": detected_count,
                "completed_steps": completed_count,
                "detection_rate_percent": round((detected_count / total_steps * 100) if total_steps > 0 else 0, 2),
                "completion_rate_percent": round((completed_count / total_steps * 100) if total_steps > 0 else 0, 2)
            },
            "steps": detected_steps,
            "full_analysis": analysis,
            "completed_at": datetime.utcnow().isoformat()
        }
    
    async def _process_outlier_results(
        self,
        analysis: str,
        procedure: Dict[str, Any],
        procedure_steps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Process results for outlier procedure comparison."""
        import re
        
        # Initialize checkpoint tracker
        checkpoint_tracker = CheckpointTracker()
        for phase in procedure.get("phases", []):
            checkpoint_tracker.initialize_phase_checkpoints(phase)
        
        # Parse phase detection and checkpoints
        detected_phases = []
        all_errors = []
        
        for i, step in enumerate(procedure_steps):
            phase_number = step.get("phase_number")
            phase_name = step["step_name"]
            
            # Look for this phase in the analysis
            # Handle markdown bold: **Phase 3.1:** and bullet points: *   **Detected:** YES
            pattern = rf"\*{{0,2}}Phase {re.escape(phase_number)}:\*{{0,2}}.*?\*{{0,2}}Detected:\*{{0,2}}\s*(YES|NO)"
            match = re.search(pattern, analysis, re.IGNORECASE | re.DOTALL)
            
            detected = False
            evidence = ""
            completion = "NOT_PERFORMED"
            checkpoint_groups = []  # Changed to grouped structure
            
            # Get checkpoint and alert question definitions from procedure
            phase_checkpoints = step.get("checkpoints", [])
            phase_alert_questions = step.get("alert_questions", [])
            
            # alert_questions result list (populated if phase detected)
            alert_question_results = []
            
            if match:
                detected = match.group(1).upper() == "YES"
                
                # Extract evidence
                evidence_pattern = rf"\*{{0,2}}Phase {re.escape(phase_number)}:\*{{0,2}}.*?\*{{0,2}}Evidence:\*{{0,2}}\s*(.+?)(?=\*{{0,2}}Timestamp:|\*{{0,2}}CHECKPOINT|$)"
                evidence_match = re.search(evidence_pattern, analysis, re.IGNORECASE | re.DOTALL)
                if evidence_match:
                    evidence = evidence_match.group(1).strip()[:200]
                
                # Extract the entire phase section to avoid cross-phase contamination
                phase_section_pattern = rf"(\*{{0,2}}Phase {re.escape(phase_number)}:\*{{0,2}}.*?)(?=\n---\s*\n|\n\*{{0,2}}Phase \d+\.\d+:\*{{0,2}}|\Z)"
                phase_section_match = re.search(phase_section_pattern, analysis, re.IGNORECASE | re.DOTALL)
                
                # Build checkpoint validation results map
                validation_results = {}
                # Build alert question answers map
                alert_answers = {}
                
                if phase_section_match:
                    phase_section = phase_section_match.group(1)
                    
                    # Extract checkpoint validation within this phase section
                    checkpoint_section_pattern = r"\*{0,2}CHECKPOINT VALIDATION:\*{0,2}(.*?)(?=\*{0,2}ALERT QUESTIONS|\*{0,2}ERROR CODES|\*{0,2}PHASE COMPLETION|\Z)"
                    checkpoint_section_match = re.search(checkpoint_section_pattern, phase_section, re.IGNORECASE | re.DOTALL)
                    
                    if checkpoint_section_match:
                        checkpoint_text = checkpoint_section_match.group(1)
                        checkpoint_pattern = r"[*-]\s*\*{0,2}(.+?)\*{0,2}:\s*(MET|NOT\s+MET)(?:\s*-\s*(.+?))?(?=\n[*-]|\n\*\*|\Z)"
                        for cp_match in re.finditer(checkpoint_pattern, checkpoint_text, re.DOTALL | re.IGNORECASE):
                            cp_name = cp_match.group(1).strip()
                            cp_status = cp_match.group(2).strip().upper().replace(" ", "_")
                            cp_evidence = cp_match.group(3).strip() if cp_match.group(3) else ""
                            validation_results[cp_name] = {"status": cp_status, "evidence": cp_evidence}
                    
                    # Extract alert question answers within this phase section
                    alert_section_pattern = r"\*{0,2}ALERT QUESTIONS ASSESSMENT:\*{0,2}(.*?)(?=\*{0,2}ERROR CODES|\*{0,2}PHASE COMPLETION|\Z)"
                    alert_section_match = re.search(alert_section_pattern, phase_section, re.IGNORECASE | re.DOTALL)
                    
                    if alert_section_match:
                        alert_text = alert_section_match.group(1)
                        # Pattern: "- Question text?: YES/NO - evidence"
                        alert_pattern = r"[*-]\s*\*{0,2}(.+?\?)\*{0,2}:\s*(YES|NO)(?:\s*-\s*(.+?))?(?=\n[*-]|\n\*\*|\Z)"
                        for aq_match in re.finditer(alert_pattern, alert_text, re.DOTALL | re.IGNORECASE):
                            aq_text = aq_match.group(1).strip()
                            aq_answer = aq_match.group(2).strip().upper()
                            aq_evidence = aq_match.group(3).strip() if aq_match.group(3) else ""
                            alert_answers[aq_text] = {"answer": aq_answer, "evidence": aq_evidence}
                
                # Build checkpoint groups from phase definition
                for checkpoint in phase_checkpoints:
                    checkpoint_name = checkpoint.get("name", "")
                    requirements = checkpoint.get("requirements", [])
                    blocking = checkpoint.get("blocking", False)
                    
                    requirement_items = []
                    for req in requirements:
                        validation = validation_results.get(req, {})
                        status = validation.get("status", "NOT_MET" if detected else "UNKNOWN")
                        evidence = validation.get("evidence", "")
                        requirement_items.append({"name": req, "status": status, "evidence": evidence})
                    
                    checkpoint_groups.append({
                        "name": checkpoint_name,
                        "blocking": blocking,
                        "requirements": requirement_items
                    })
                
                # Build alert question results from phase definition
                for aq_def in phase_alert_questions:
                    aq_text = aq_def.get("question", "")
                    expected = aq_def.get("expected_answer", "YES")
                    blocking = aq_def.get("blocking", True)
                    # Try exact match first, then fuzzy key search
                    aq_result = alert_answers.get(aq_text)
                    if not aq_result:
                        for key, val in alert_answers.items():
                            if aq_text.lower().strip("?") in key.lower() or key.lower().strip("?") in aq_text.lower():
                                aq_result = val
                                break
                    answer = aq_result["answer"] if aq_result else ("UNKNOWN" if detected else "NOT_ASSESSED")
                    aq_evidence = aq_result["evidence"] if aq_result else ""
                    alert_question_results.append({
                        "question": aq_text,
                        "answer": answer,
                        "expected_answer": expected,
                        "passed": answer == expected,
                        "blocking": blocking,
                        "evidence": aq_evidence
                    })
                
                # Extract completion status from LLM response
                completion_pattern = rf"\*{{0,2}}Phase {re.escape(phase_number)}:\*{{0,2}}.*?\*{{0,2}}Status:\*{{0,2}}\s*(COMPLETED|PARTIAL|NOT_PERFORMED)"
                completion_match = re.search(completion_pattern, analysis, re.IGNORECASE | re.DOTALL)
                if completion_match:
                    completion = completion_match.group(1).upper()
                else:
                    # Infer completion status if not explicitly stated
                    if detected:
                        total_reqs = sum(len(cg["requirements"]) for cg in checkpoint_groups)
                        met_reqs = sum(1 for cg in checkpoint_groups for req in cg["requirements"] if req["status"] == "MET")
                        
                        if total_reqs == 0:
                            completion = "COMPLETED"
                        elif met_reqs == total_reqs:
                            completion = "COMPLETED"
                        elif met_reqs > 0:
                            completion = "PARTIAL"
                        else:
                            completion = "PARTIAL"
                    # else: remains "NOT_PERFORMED" (default)
            else:
                # Phase not detected - build checkpoint groups and alert questions with UNKNOWN status
                for checkpoint in phase_checkpoints:
                    checkpoint_name = checkpoint.get("name", "")
                    requirements = checkpoint.get("requirements", [])
                    blocking = checkpoint.get("blocking", False)
                    
                    requirement_items = []
                    for req in requirements:
                        requirement_items.append({"name": req, "status": "UNKNOWN", "evidence": ""})
                    
                    checkpoint_groups.append({
                        "name": checkpoint_name,
                        "blocking": blocking,
                        "requirements": requirement_items
                    })
                
                for aq_def in phase_alert_questions:
                    alert_question_results.append({
                        "question": aq_def.get("question", ""),
                        "answer": "NOT_ASSESSED",
                        "expected_answer": aq_def.get("expected_answer", "YES"),
                        "passed": False,
                        "blocking": aq_def.get("blocking", True),
                        "evidence": ""
                    })
            
            # Count total checkpoint requirements and satisfied count
            total_checkpoint_requirements = sum(len(cg["requirements"]) for cg in checkpoint_groups)
            checkpoints_satisfied = sum(1 for cg in checkpoint_groups for req in cg["requirements"] if req["status"] == "MET")
            alert_questions_passed = sum(1 for aq in alert_question_results if aq["passed"])
            
            detected_phases.append({
                "phase_number": phase_number,
                "phase_name": phase_name,
                "description": step.get("description"), 
                "priority": step.get("priority"),
                "detected": detected,
                "completion": completion,
                "evidence": evidence,
                "checkpoint_groups": checkpoint_groups,
                "total_checkpoints": total_checkpoint_requirements,
                "checkpoints_satisfied": checkpoints_satisfied,
                "alert_questions": alert_question_results,
                "alert_questions_passed": alert_questions_passed,
                "total_alert_questions": len(alert_question_results)
            })
        
        # Extract error codes with timestamp and detailed description
        # Formats to match:
        # - **C1 (Checking Errors) at 00:03:** Description text
        # - **A8 (Action Errors) at 05:23:** What happened - Specific evidence
        # - C1 (Checking Errors): description (legacy format)
        # Pattern handles optional markdown bold (**), optional category in parens, optional timestamp
        error_pattern = r"-\s*\*{0,2}\s*(A\d+|C\d+|R\d+)\s*(?:\([^)]+\))?\s*(?:at\s+([\d:]+))?\s*\*{0,2}\s*:\s*\*{0,2}\s*(.+?)(?=\n-|\n\*\*|\Z)"
        for error_match in re.finditer(error_pattern, analysis, re.MULTILINE | re.DOTALL):
            error_code = error_match.group(1)
            timestamp = error_match.group(2) if error_match.group(2) else None
            error_description = error_match.group(3).strip()
            # Clean up the description (remove markdown bold markers, extra whitespace, newlines, and trailing periods)
            error_description = error_description.replace('**', '').strip()
            error_description = ' '.join(error_description.split()).rstrip('.')
            
            error_entry = {
                "code": error_code,
                "description": error_description
            }
            
            # Add timestamp if present
            if timestamp:
                error_entry["timestamp"] = timestamp
            
            all_errors.append(error_entry)
        
        # Calculate summary
        total_phases = len(procedure_steps)
        detected_count = sum(1 for p in detected_phases if p["detected"])
        completed_count = sum(1 for p in detected_phases if p["completion"] == "COMPLETED")
        total_checkpoints = sum(p["total_checkpoints"] for p in detected_phases)
        checkpoints_met = sum(p["checkpoints_satisfied"] for p in detected_phases)
        
        return {
            "procedure_source": "outlier",
            "procedure_name": procedure.get("procedure_name"),
            "procedure_id": str(procedure["_id"]),
            "model_used": self.gemini_client.model,
            "summary": {
                "total_phases": total_phases,
                "detected_phases": detected_count,
                "completed_phases": completed_count,
                "total_checkpoints": total_checkpoints,
                "checkpoints_met": checkpoints_met,
                "errors_detected": len(all_errors),
                "detection_rate_percent": round((detected_count / total_phases * 100) if total_phases > 0 else 0, 2),
                "checkpoint_completion_percent": round((checkpoints_met / total_checkpoints * 100) if total_checkpoints > 0 else 0, 2)
            },
            "phases": detected_phases,
            "errors": all_errors,
            "full_analysis": analysis,
            "completed_at": datetime.utcnow().isoformat()
        }
