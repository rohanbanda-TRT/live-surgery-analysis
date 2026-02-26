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
    
    def __init__(self, db: AsyncDatabase):
        """
        Initialize recorded video comparison service.
        
        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.gemini_client = GeminiClient()
    
    async def compare_video(
        self,
        video_gs_uri: str,
        procedure_id: str,
        procedure_source: str = "standard"
    ) -> Dict[str, Any]:
        """
        Compare a recorded video against a procedure.
        
        Args:
            video_gs_uri: Google Cloud Storage URI of the recorded video
            procedure_id: ID of the master or outlier procedure to compare against
            procedure_source: "standard" for master procedures, "outlier" for outlier procedures
            
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
            
            # Load procedure
            procedure, procedure_steps = await self._load_procedure(procedure_id, procedure_source)
            
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
        """Load procedure from database."""
        if procedure_source == "outlier":
            procedure = await self.db[OUTLIER_PROCEDURES].find_one(
                {"_id": ObjectId(procedure_id)}
            )
            
            if not procedure:
                raise ValueError(f"Outlier procedure {procedure_id} not found")
            
            # Build procedure steps from phases
            procedure_steps = []
            for phase in procedure.get("phases", []):
                procedure_steps.append({
                    "step_number": phase.get("phase_number"),
                    "step_name": phase.get("phase_name"),
                    "description": phase.get("goal"),
                    "phase_number": phase.get("phase_number"),
                    "priority": phase.get("priority"),
                    "checkpoints": phase.get("checkpoints", []),
                    "critical_errors": phase.get("critical_errors", [])
                })
            
            logger.info(
                "outlier_procedure_loaded",
                procedure_name=procedure.get("procedure_name"),
                phases_count=len(procedure_steps)
            )
            
        else:
            procedure = await self.db[MASTER_PROCEDURES].find_one(
                {"_id": ObjectId(procedure_id)}
            )
            
            if not procedure:
                raise ValueError(f"Master procedure {procedure_id} not found")
            
            procedure_steps = procedure.get("steps", [])
            
            logger.info(
                "master_procedure_loaded",
                procedure_name=procedure.get("procedure_name"),
                steps_count=len(procedure_steps)
            )
        
        return procedure, procedure_steps
    
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
For each checkpoint in this phase:
- [Checkpoint name]: [MET/NOT MET] - [Evidence]

**ERROR CODES DETECTED:**
- [List any error codes observed, e.g., "A8 - Coagulation omitted before tissue manipulation"]

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
            pattern = rf"Phase {re.escape(phase_number)}:.*?Detected:\s*(YES|NO)"
            match = re.search(pattern, analysis, re.IGNORECASE | re.DOTALL)
            
            detected = False
            evidence = ""
            completion = "NOT_PERFORMED"
            checkpoints_met = []
            checkpoints_not_met = []
            
            if match:
                detected = match.group(1).upper() == "YES"
                
                # Extract evidence
                evidence_pattern = rf"Phase {re.escape(phase_number)}:.*?Evidence:\s*(.+?)(?=Timestamp:|CHECKPOINT|$)"
                evidence_match = re.search(evidence_pattern, analysis, re.IGNORECASE | re.DOTALL)
                if evidence_match:
                    evidence = evidence_match.group(1).strip()[:200]
                
                # Extract checkpoint validation
                checkpoint_section_pattern = rf"Phase {re.escape(phase_number)}:.*?CHECKPOINT VALIDATION:(.*?)(?=\*\*ERROR CODES|\*\*PHASE COMPLETION|Phase \d|$)"
                checkpoint_section_match = re.search(checkpoint_section_pattern, analysis, re.IGNORECASE | re.DOTALL)
                
                if checkpoint_section_match:
                    checkpoint_text = checkpoint_section_match.group(1)
                    
                    # Parse individual checkpoints - more flexible pattern
                    # Matches: "- [name]: MET - [evidence]" or "- [name]: NOT MET - [evidence]"
                    # Evidence is optional
                    checkpoint_pattern = r"-\s*(.+?):\s*(MET|NOT\s+MET)(?:\s*-\s*(.+?))?(?=\n-|\n\*\*|\Z)"
                    for cp_match in re.finditer(checkpoint_pattern, checkpoint_text, re.DOTALL | re.IGNORECASE):
                        cp_name = cp_match.group(1).strip()
                        cp_status = cp_match.group(2).strip().upper().replace(" ", "_")
                        cp_evidence = cp_match.group(3).strip() if cp_match.group(3) else ""
                        
                        if cp_status == "MET":
                            checkpoints_met.append({
                                "name": cp_name,
                                "evidence": cp_evidence
                            })
                        else:
                            checkpoints_not_met.append({
                                "name": cp_name,
                                "evidence": cp_evidence
                            })
                
                # Extract completion status
                completion_pattern = rf"Phase {re.escape(phase_number)}:.*?Status:\s*(COMPLETED|PARTIAL|NOT_PERFORMED)"
                completion_match = re.search(completion_pattern, analysis, re.IGNORECASE | re.DOTALL)
                if completion_match:
                    completion = completion_match.group(1).upper()
            
            detected_phases.append({
                "phase_number": phase_number,
                "phase_name": phase_name,
                "description": step.get("description"),
                "priority": step.get("priority"),
                "detected": detected,
                "completion": completion,
                "evidence": evidence,
                "checkpoints_met": checkpoints_met,
                "checkpoints_not_met": checkpoints_not_met,
                "total_checkpoints": len(step.get("checkpoints", [])),
                "checkpoints_satisfied": len(checkpoints_met)
            })
        
        # Extract error codes
        error_pattern = r"(A\d+|C\d+|R\d+)\s*-\s*(.+?)(?=\n|$)"
        for error_match in re.finditer(error_pattern, analysis):
            error_code = error_match.group(1)
            error_description = error_match.group(2).strip()
            all_errors.append({
                "code": error_code,
                "description": error_description
            })
        
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
