"""
Service for parsing and processing outlier resolution protocol analysis.
Handles checkpoint-based phase completion tracking.
"""
from typing import Dict, Any, List, Optional, Set
import re
from app.core.logging import logger


class OutlierAnalysisParser:
    """Parser for outlier resolution protocol AI analysis responses."""
    
    @staticmethod
    def parse_detected_phase(analysis: str) -> Optional[str]:
        """
        Extract detected phase number from AI analysis.
        
        Args:
            analysis: AI analysis text
            
        Returns:
            Phase number (e.g., "3.1", "3.5") or None
        """
        logger.debug("parse_detected_phase", analysis_preview=analysis[:200])
        
        # Look for "Detected Phase: 3.1" or similar patterns
        patterns = [
            r"Detected Phase:\s*(\d+\.\d+)",
            r"Current Phase:\s*(\d+\.\d+)",
            r"Phase\s+(\d+\.\d+)\s+detected",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, analysis, re.IGNORECASE)
            if match:
                phase_number = match.group(1)
                logger.info("phase_detected_from_analysis", phase_number=phase_number, pattern=pattern)
                return phase_number
        
        logger.warning("no_phase_detected_in_analysis", analysis_preview=analysis[:300])
        return None
    
    @staticmethod
    def parse_checkpoint_status(analysis: str) -> Dict[str, Any]:
        """
        Extract checkpoint validation status from analysis.
        
        Args:
            analysis: AI analysis text
            
        Returns:
            Dictionary with checkpoint status information
        """
        checkpoint_status = {
            "status": "UNKNOWN",  # PASS, FAIL, UNKNOWN
            "details": [],
            "blocking": False,
            "reason": ""
        }
        
        # Look for checkpoint status
        if re.search(r"Checkpoint Status:\s*PASS", analysis, re.IGNORECASE):
            checkpoint_status["status"] = "PASS"
            logger.info("checkpoint_status_parsed", status="PASS")
        elif re.search(r"Checkpoint Status:\s*FAIL", analysis, re.IGNORECASE):
            checkpoint_status["status"] = "FAIL"
            checkpoint_status["blocking"] = True
            logger.info("checkpoint_status_parsed", status="FAIL")
        
        # Parse checkpoint details (individual requirement validations)
        details_match = re.search(r"Checkpoint Details:\s*(.+?)(?=\n(?:Step Progress|Completion Evidence|Block Progression|Analysis):|$)", analysis, re.IGNORECASE | re.DOTALL)
        if details_match:
            details_text = details_match.group(1).strip()
            # Parse individual checkpoint requirements
            # Format: "Requirement name: MET/NOT MET - Evidence"
            requirement_pattern = r"[•\-]\s*(.+?):\s*(MET|NOT MET)\s*[-–]\s*(.+?)(?=\n[•\-]|$)"
            for match in re.finditer(requirement_pattern, details_text, re.IGNORECASE | re.DOTALL):
                requirement_name = match.group(1).strip()
                status = match.group(2).strip().upper()
                evidence = match.group(3).strip()
                
                checkpoint_status["details"].append({
                    "requirement": requirement_name,
                    "met": status == "MET",
                    "evidence": evidence
                })
            
            logger.info("checkpoint_details_parsed", detail_count=len(checkpoint_status["details"]))
        
        # Look for blocking indicators
        if re.search(r"Block Progression:\s*YES", analysis, re.IGNORECASE):
            checkpoint_status["blocking"] = True
            # Try to extract reason
            reason_match = re.search(r"Block Progression:\s*YES\s*[:-]?\s*(.+?)(?:\n|$)", analysis, re.IGNORECASE)
            if reason_match:
                checkpoint_status["reason"] = reason_match.group(1).strip()
            logger.info("progression_blocked", reason=checkpoint_status["reason"])
        
        return checkpoint_status
    
    @staticmethod
    def parse_error_codes(analysis: str) -> List[Dict[str, str]]:
        """
        Extract detected error codes from analysis.
        
        Args:
            analysis: AI analysis text
            
        Returns:
            List of error code dictionaries
        """
        errors = []
        
        # Look for error codes pattern
        error_pattern = r"Error Codes Detected:\s*(.+?)(?:\n|$)"
        match = re.search(error_pattern, analysis, re.IGNORECASE)
        
        if match:
            error_text = match.group(1).strip()
            if error_text.lower() != "none":
                # Parse individual error codes (A1, A3, C1, etc.)
                code_matches = re.findall(r"([A-Z]\d+)", error_text)
                for code in code_matches:
                    errors.append({
                        "code": code,
                        "detected_in_analysis": True
                    })
        
        return errors
    
    @staticmethod
    def parse_completion_evidence(analysis: str) -> Optional[str]:
        """
        Extract completion evidence from analysis.
        
        Args:
            analysis: AI analysis text
            
        Returns:
            Completion evidence string or None
        """
        pattern = r"Completion Evidence:\s*(.+?)(?:\n|$)"
        match = re.search(pattern, analysis, re.IGNORECASE)
        
        if match:
            evidence = match.group(1).strip()
            if evidence.lower() not in ["n/a", "none", "null"]:
                return evidence
        
        return None
    
    @staticmethod
    def parse_step_progress(analysis: str) -> str:
        """
        Extract step/phase progress status.
        
        Args:
            analysis: AI analysis text
            
        Returns:
            Progress status: "not-started", "in-progress", "completed"
        """
        pattern = r"(?:Step|Phase) Progress:\s*(.+?)(?:\n|$)"
        match = re.search(pattern, analysis, re.IGNORECASE)
        
        if match:
            progress = match.group(1).strip().lower()
            if "completed" in progress:
                return "completed"
            elif "not-started" in progress or "not started" in progress:
                return "not-started"
            elif "in-progress" in progress or "in progress" in progress:
                return "in-progress"
        
        return "in-progress"  # Default


class CheckpointTracker:
    """Tracks checkpoint completion for outlier resolution phases."""
    
    def __init__(self):
        """Initialize checkpoint tracker."""
        self.checkpoint_states: Dict[str, Dict[str, Any]] = {}
        # phase_number -> {checkpoint_name -> {requirements: {req: bool}, completed: bool}}
    
    def initialize_phase_checkpoints(self, phase: Dict[str, Any]):
        """
        Initialize checkpoint tracking for a phase.
        
        Args:
            phase: Phase dictionary from outlier procedure
        """
        phase_number = phase["phase_number"]
        
        if phase_number not in self.checkpoint_states:
            self.checkpoint_states[phase_number] = {}
        
        # Initialize each checkpoint in the phase
        for checkpoint in phase.get("checkpoints", []):
            checkpoint_name = checkpoint["name"]
            self.checkpoint_states[phase_number][checkpoint_name] = {
                "requirements": {req: False for req in checkpoint["requirements"]},
                "completed": False,
                "blocking": checkpoint.get("blocking", False)
            }
    
    def update_checkpoint_requirement(
        self, 
        phase_number: str, 
        checkpoint_name: str, 
        requirement: str, 
        completed: bool
    ):
        """
        Update a specific checkpoint requirement status.
        
        Args:
            phase_number: Phase number (e.g., "3.1")
            checkpoint_name: Name of checkpoint
            requirement: Requirement text
            completed: Whether requirement is completed
        """
        if phase_number in self.checkpoint_states:
            if checkpoint_name in self.checkpoint_states[phase_number]:
                checkpoint = self.checkpoint_states[phase_number][checkpoint_name]
                if requirement in checkpoint["requirements"]:
                    checkpoint["requirements"][requirement] = completed
                    # Update checkpoint completion status
                    checkpoint["completed"] = all(checkpoint["requirements"].values())
    
    def is_phase_checkpoint_complete(self, phase_number: str) -> bool:
        """
        Check if all checkpoints for a phase are complete.
        
        Args:
            phase_number: Phase number
            
        Returns:
            True if all checkpoints complete, False otherwise
        """
        if phase_number not in self.checkpoint_states:
            return True  # No checkpoints means phase can complete
        
        # All checkpoints must be completed
        for checkpoint in self.checkpoint_states[phase_number].values():
            if not checkpoint["completed"]:
                return False
        
        return True
    
    def get_phase_checkpoint_status(self, phase_number: str) -> Dict[str, Any]:
        """
        Get detailed checkpoint status for a phase.
        
        Args:
            phase_number: Phase number
            
        Returns:
            Dictionary with checkpoint status details
        """
        if phase_number not in self.checkpoint_states:
            return {
                "has_checkpoints": False,
                "all_complete": True,
                "checkpoints": []
            }
        
        checkpoints = []
        all_complete = True
        
        for name, checkpoint in self.checkpoint_states[phase_number].items():
            completed_reqs = sum(1 for completed in checkpoint["requirements"].values() if completed)
            total_reqs = len(checkpoint["requirements"])
            
            checkpoints.append({
                "name": name,
                "completed": checkpoint["completed"],
                "blocking": checkpoint["blocking"],
                "requirements": [
                    {"text": req, "completed": completed}
                    for req, completed in checkpoint["requirements"].items()
                ],
                "progress": f"{completed_reqs}/{total_reqs}"
            })
            
            if not checkpoint["completed"]:
                all_complete = False
        
        return {
            "has_checkpoints": True,
            "all_complete": all_complete,
            "checkpoints": checkpoints
        }
    
    def get_blocking_checkpoints(self, phase_number: str) -> List[str]:
        """
        Get list of incomplete blocking checkpoints for a phase.
        
        Args:
            phase_number: Phase number
            
        Returns:
            List of blocking checkpoint names that are incomplete
        """
        blocking = []
        
        if phase_number in self.checkpoint_states:
            for name, checkpoint in self.checkpoint_states[phase_number].items():
                if checkpoint["blocking"] and not checkpoint["completed"]:
                    blocking.append(name)
        
        return blocking
    
    def update_from_ai_checkpoint_details(
        self,
        phase_number: str,
        checkpoint_details: List[Dict[str, Any]]
    ):
        """
        Update checkpoint states based on AI-parsed checkpoint details.
        
        Args:
            phase_number: Phase number
            checkpoint_details: List of parsed checkpoint requirement validations
                               Each item has: requirement, met, evidence
        """
        if phase_number not in self.checkpoint_states:
            logger.warning(
                "checkpoint_update_failed_no_phase",
                phase_number=phase_number
            )
            return
        
        # Try to match AI-provided requirement text to our checkpoint requirements
        for detail in checkpoint_details:
            requirement_text = detail["requirement"]
            is_met = detail["met"]
            
            # Search through all checkpoints in this phase to find matching requirement
            for checkpoint_name, checkpoint in self.checkpoint_states[phase_number].items():
                for req in checkpoint["requirements"]:
                    # Fuzzy match - check if requirement text is similar
                    if (requirement_text.lower() in req.lower() or 
                        req.lower() in requirement_text.lower() or
                        self._similarity_score(requirement_text, req) > 0.6):
                        
                        # Update the requirement status
                        checkpoint["requirements"][req] = is_met
                        logger.info(
                            "checkpoint_requirement_updated",
                            phase_number=phase_number,
                            checkpoint=checkpoint_name,
                            requirement=req,
                            met=is_met,
                            evidence=detail.get("evidence", "")
                        )
                        break
                
                # Update checkpoint completion status
                checkpoint["completed"] = all(checkpoint["requirements"].values())
    
    @staticmethod
    def _similarity_score(text1: str, text2: str) -> float:
        """
        Calculate simple similarity score between two strings.
        
        Args:
            text1: First text
            text2: Second text
            
        Returns:
            Similarity score between 0 and 1
        """
        # Simple word-based similarity
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
