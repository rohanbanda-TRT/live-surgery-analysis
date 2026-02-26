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
            # Format: "Requirement name: MET/NOT MET/PREVIOUSLY_MET - Evidence"
            requirement_pattern = r"[•\-]\s*(.+?):\s*(MET|NOT MET|PREVIOUSLY_MET|PREVIOUSLY MET)\s*[-–]\s*(.+?)(?=\n[•\-]|$)"
            for match in re.finditer(requirement_pattern, details_text, re.IGNORECASE | re.DOTALL):
                requirement_name = match.group(1).strip()
                status = match.group(2).strip().upper().replace(" ", "_")
                evidence = match.group(3).strip()
                
                # Treat PREVIOUSLY_MET as MET for state tracking (temporal stability)
                is_met = status in ["MET", "PREVIOUSLY_MET"]
                
                checkpoint_status["details"].append({
                    "requirement": requirement_name,
                    "met": is_met,
                    "status": status,  # Preserve original status for logging
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
        
        # State locking mechanism (inspired by LangGraph checkpointing)
        self.checkpoint_locks: Dict[str, Dict[str, bool]] = {}
        # phase_number -> {checkpoint_name -> locked}
        
        # Checkpoint history tracking (LangGraph-style state snapshots)
        self.checkpoint_history: Dict[str, List[Dict[str, Any]]] = {}
        # phase_number -> [{timestamp, checkpoint_name, event, evidence, chunk_id}]
        
        # Prerequisite dependency graph
        self.phase_dependencies: Dict[str, List[str]] = {}
        # phase_number -> [prerequisite_phase_numbers]
        
        # Chunk counter for history tracking
        self.chunk_counter: int = 0
    
    def initialize_phase_checkpoints(self, phase: Dict[str, Any]):
        """
        Initialize checkpoint tracking for a phase.
        
        Args:
            phase: Phase dictionary from outlier procedure
        """
        phase_number = phase["phase_number"]
        
        if phase_number not in self.checkpoint_states:
            self.checkpoint_states[phase_number] = {}
            self.checkpoint_locks[phase_number] = {}
            self.checkpoint_history[phase_number] = []
        
        # Initialize each checkpoint in the phase
        for checkpoint in phase.get("checkpoints", []):
            checkpoint_name = checkpoint["name"]
            self.checkpoint_states[phase_number][checkpoint_name] = {
                "requirements": {req: False for req in checkpoint["requirements"]},
                "completed": False,
                "blocking": checkpoint.get("blocking", False)
            }
            self.checkpoint_locks[phase_number][checkpoint_name] = False
        
        # Initialize dependencies from phase data
        if phase.get("dependencies"):
            self.phase_dependencies[phase_number] = phase["dependencies"]
    
    def update_checkpoint_requirement(
        self, 
        phase_number: str, 
        checkpoint_name: str, 
        requirement: str, 
        completed: bool,
        evidence: str = ""
    ):
        """
        Update a specific checkpoint requirement status with temporal stability.
        
        Args:
            phase_number: Phase number (e.g., "3.1")
            checkpoint_name: Name of checkpoint
            requirement: Requirement text
            completed: Whether requirement is completed
            evidence: Evidence for the update
        """
        if phase_number in self.checkpoint_states:
            if checkpoint_name in self.checkpoint_states[phase_number]:
                checkpoint = self.checkpoint_states[phase_number][checkpoint_name]
                is_locked = self.checkpoint_locks[phase_number].get(checkpoint_name, False)
                
                if requirement in checkpoint["requirements"]:
                    old_value = checkpoint["requirements"][requirement]
                    
                    # Temporal stability: prevent regression on locked checkpoints
                    if is_locked and old_value and not completed:
                        logger.warning(
                            "checkpoint_regression_blocked",
                            phase_number=phase_number,
                            checkpoint=checkpoint_name,
                            requirement=requirement,
                            evidence=evidence,
                            reason="Checkpoint is locked - ignoring NOT MET status"
                        )
                        # Log as potential regression but don't flip state
                        self._log_checkpoint_event(
                            phase_number, checkpoint_name, "REGRESSION_BLOCKED", evidence
                        )
                        return
                    
                    # Update requirement
                    checkpoint["requirements"][requirement] = completed
                    
                    # Log state change
                    if old_value != completed:
                        event = "MET" if completed else "NOT_MET"
                        self._log_checkpoint_event(
                            phase_number, checkpoint_name, event, evidence
                        )
                    
                    # Update checkpoint completion status
                    all_met = all(checkpoint["requirements"].values())
                    if all_met and not checkpoint["completed"]:
                        checkpoint["completed"] = True
                        self._lock_checkpoint(phase_number, checkpoint_name, evidence)
                    elif not all_met and checkpoint["completed"]:
                        # Only allow uncomplete if not locked
                        if not is_locked:
                            checkpoint["completed"] = False
    
    def _lock_checkpoint(self, phase_number: str, checkpoint_name: str, evidence: str = ""):
        """Lock a checkpoint once satisfied - prevents temporal instability."""
        if phase_number not in self.checkpoint_locks:
            self.checkpoint_locks[phase_number] = {}
        
        self.checkpoint_locks[phase_number][checkpoint_name] = True
        self._log_checkpoint_event(phase_number, checkpoint_name, "LOCKED", evidence)
        
        logger.info(
            "checkpoint_locked",
            phase_number=phase_number,
            checkpoint=checkpoint_name,
            evidence=evidence
        )
    
    def _log_checkpoint_event(
        self, 
        phase_number: str, 
        checkpoint_name: str, 
        event: str, 
        evidence: str
    ):
        """Log checkpoint state change to history (LangGraph-style snapshot)."""
        from datetime import datetime
        
        if phase_number not in self.checkpoint_history:
            self.checkpoint_history[phase_number] = []
        
        snapshot = {
            "timestamp": datetime.utcnow().isoformat(),
            "chunk_id": self.chunk_counter,
            "checkpoint_name": checkpoint_name,
            "event": event,
            "evidence": evidence
        }
        
        self.checkpoint_history[phase_number].append(snapshot)
        
        # Keep last 50 events per phase to avoid memory bloat
        if len(self.checkpoint_history[phase_number]) > 50:
            self.checkpoint_history[phase_number] = self.checkpoint_history[phase_number][-50:]
    
    def increment_chunk_counter(self):
        """Increment chunk counter for history tracking."""
        self.chunk_counter += 1
    
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
    
    def is_phase_eligible(self, phase_number: str) -> Dict[str, Any]:
        """
        Check if a phase is eligible to execute based on prerequisite dependencies.
        
        This is critical for A8 (Operation Omitted) detection - if a phase is detected
        but its prerequisites are not satisfied, it indicates a surgical error.
        
        Args:
            phase_number: Phase number to check
            
        Returns:
            Dictionary with eligibility status and blocking prerequisites
        """
        prerequisites = self.phase_dependencies.get(phase_number, [])
        
        blocking_prerequisites = []
        for prereq_phase in prerequisites:
            if not self.is_phase_checkpoint_complete(prereq_phase):
                blocking_checkpoints = self.get_blocking_checkpoints(prereq_phase)
                blocking_prerequisites.append({
                    "phase": prereq_phase,
                    "blocking_checkpoints": blocking_checkpoints,
                    "all_checkpoints_incomplete": not self.is_phase_checkpoint_complete(prereq_phase)
                })
        
        is_eligible = len(blocking_prerequisites) == 0
        
        if not is_eligible:
            logger.warning(
                "phase_not_eligible",
                phase_number=phase_number,
                prerequisites=prerequisites,
                blocking_count=len(blocking_prerequisites)
            )
        
        return {
            "eligible": is_eligible,
            "blocking_prerequisites": blocking_prerequisites,
            "prerequisite_phases": prerequisites
        }
    
    def get_checkpoint_history(self, phase_number: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent checkpoint history for a phase.
        
        Args:
            phase_number: Phase number
            limit: Maximum number of history entries to return
            
        Returns:
            List of recent checkpoint events
        """
        if phase_number not in self.checkpoint_history:
            return []
        
        return self.checkpoint_history[phase_number][-limit:]
    
    def update_from_ai_checkpoint_details(
        self,
        phase_number: str,
        checkpoint_details: List[Dict[str, Any]]
    ):
        """
        Update checkpoint states based on AI-parsed checkpoint details.
        Enhanced with better matching and evidence tracking.
        
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
        
        unmatched_requirements = []
        
        # Try to match AI-provided requirement text to our checkpoint requirements
        for detail in checkpoint_details:
            requirement_text = detail["requirement"]
            is_met = detail["met"]
            evidence = detail.get("evidence", "")
            matched = False
            
            # Strategy 1: Exact substring match
            for checkpoint_name, checkpoint in self.checkpoint_states[phase_number].items():
                for req in checkpoint["requirements"]:
                    if (requirement_text.lower() in req.lower() or 
                        req.lower() in requirement_text.lower()):
                        
                        # Use the enhanced update method with evidence
                        self.update_checkpoint_requirement(
                            phase_number, checkpoint_name, req, is_met, evidence
                        )
                        matched = True
                        break
                if matched:
                    break
            
            # Strategy 2: Keyword matching if no exact match
            if not matched:
                keywords = self._extract_keywords(requirement_text)
                for checkpoint_name, checkpoint in self.checkpoint_states[phase_number].items():
                    for req in checkpoint["requirements"]:
                        req_keywords = self._extract_keywords(req)
                        if len(keywords.intersection(req_keywords)) >= 2:
                            self.update_checkpoint_requirement(
                                phase_number, checkpoint_name, req, is_met, evidence
                            )
                            matched = True
                            logger.info(
                                "checkpoint_matched_via_keywords",
                                requirement=requirement_text,
                                matched_to=req
                            )
                            break
                    if matched:
                        break
            
            if not matched:
                unmatched_requirements.append(requirement_text)
        
        if unmatched_requirements:
            logger.warning(
                "unmatched_checkpoint_requirements",
                phase=phase_number,
                unmatched=unmatched_requirements
            )
    
    @staticmethod
    def _extract_keywords(text: str) -> set:
        """
        Extract meaningful keywords from text for matching.
        
        Args:
            text: Input text
            
        Returns:
            Set of keywords
        """
        # Remove common stop words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been", 
                      "being", "have", "has", "had", "do", "does", "did", "will",
                      "would", "should", "could", "may", "might", "must", "can",
                      "of", "to", "in", "for", "on", "with", "at", "by", "from"}
        
        words = set(text.lower().split())
        return words - stop_words
    
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
