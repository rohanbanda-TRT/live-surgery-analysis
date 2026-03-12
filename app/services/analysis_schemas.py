"""
Pydantic models and JSON schemas for structured Gemini output.

These replace all regex-based extraction by using Gemini's native
response_json_schema / response_mime_type="application/json" support.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum


# ──────────────────────────────────────────────
# Shared enums
# ──────────────────────────────────────────────

class StepProgress(str, Enum):
    JUST_STARTED = "just-started"
    IN_PROGRESS = "in-progress"
    NEARING_COMPLETION = "nearing-completion"
    COMPLETED = "completed"


class ErrorSeverity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CheckpointStatus(str, Enum):
    MET = "MET"
    NOT_MET = "NOT_MET"
    PREVIOUSLY_MET = "PREVIOUSLY_MET"


# ──────────────────────────────────────────────
# Standard mode – chunk analysis
# ──────────────────────────────────────────────

class StandardChunkAnalysis(BaseModel):
    """Schema returned by Gemini for each video chunk in standard mode."""

    detected_step_number: Optional[int] = Field(
        None,
        description="1-based step number detected in this chunk, or null if unclear"
    )
    step_name: Optional[str] = Field(
        None,
        description="Name of the detected step"
    )
    action_observed: str = Field(
        description="Specific surgical action observed in the frames"
    )
    instruments_visible: List[str] = Field(
        default_factory=list,
        description="Instruments actually visible in the frames"
    )
    anatomical_landmarks: List[str] = Field(
        default_factory=list,
        description="Anatomical landmarks visible in the frames"
    )
    matches_expected: bool = Field(
        description="Whether the observed action matches the expected current step"
    )
    step_progress: StepProgress = Field(
        description="Progress of the detected step"
    )
    completion_evidence: Optional[str] = Field(
        None,
        description="Evidence of step completion. Null if step is not completed."
    )
    is_repeated_completed_step: bool = Field(
        default=False,
        description="True if surgeon is repeating a previously completed step"
    )
    confidence_score: int = Field(
        default=0,
        description=(
            "Confidence percentage (0-100) that detected_step_number is correct. "
            "90-100: unmistakable visual evidence. 70-89: strong match. "
            "50-69: ambiguous. 0-49: unclear or no surgical activity. "
            "Set detected_step_number to null if confidence < 70."
        )
    )
    confidence_reason: str = Field(
        default="",
        description="Brief reason explaining the confidence score — what specific visual evidence supports or reduces confidence"
    )
    significant_change: bool = Field(
        default=True,
        description=(
            "True if this chunk shows a meaningfully different surgical state vs the previous chunk "
            "(new step started, visible progress, instrument change, different phase). "
            "False if the scene looks essentially the same as before (surgeon repositioning, "
            "camera still, no new action). Set False to skip redundant processing."
        )
    )
    observation: str = Field(
        description="One-sentence factual observation of the current surgical field state"
    )
    analysis_summary: str = Field(
        description="Brief summary of what is happening in the surgical field"
    )


# ──────────────────────────────────────────────
# Outlier mode – chunk analysis
# ──────────────────────────────────────────────

class DetectedError(BaseModel):
    """A surgical error code detected during analysis."""
    code: str = Field(description="Error code (e.g. A1, A8, C3, R2)")
    description: str = Field(description="Description of the error")
    severity: ErrorSeverity = Field(description="Severity level")


class CheckpointValidation(BaseModel):
    """Validation result for a single checkpoint requirement."""
    checkpoint_name: str = Field(description="Name of the checkpoint")
    requirement: str = Field(description="Specific requirement text")
    status: CheckpointStatus = Field(description="Whether requirement is met")
    evidence: Optional[str] = Field(None, description="Evidence for the status")


class OutlierChunkAnalysis(BaseModel):
    """Schema returned by Gemini for each video chunk in outlier mode."""

    detected_phase_number: Optional[str] = Field(
        None,
        description="Phase number detected (e.g. '3.1', '3.4'), or null if unclear"
    )
    phase_name: Optional[str] = Field(
        None,
        description="Name of the detected phase"
    )
    action_observed: str = Field(
        description="Specific surgical action observed in the frames"
    )
    matches_expected: bool = Field(
        description="Whether the observed action matches the expected current phase"
    )
    step_progress: StepProgress = Field(
        description="Progress of the detected phase"
    )
    completion_evidence: Optional[str] = Field(
        None,
        description="Evidence of phase completion. Null if phase is not completed."
    )
    checkpoint_validations: List[CheckpointValidation] = Field(
        default_factory=list,
        description="Checkpoint requirement validations for the detected phase"
    )
    error_codes: List[DetectedError] = Field(
        default_factory=list,
        description="Surgical error codes detected (A1-A10, C1-C6, R1-R3)"
    )
    confidence_score: int = Field(
        default=0,
        description=(
            "Confidence percentage (0-100) that detected_phase_number is correct. "
            "90-100: unmistakable visual evidence. 70-89: strong match. "
            "50-69: ambiguous. 0-49: unclear or no surgical activity. "
            "Set detected_phase_number to null if confidence < 70."
        )
    )
    confidence_reason: str = Field(
        default="",
        description="Brief reason explaining the confidence score — what specific visual evidence supports or reduces confidence"
    )
    significant_change: bool = Field(
        default=True,
        description=(
            "True if this chunk shows a meaningfully different surgical state vs the previous chunk "
            "(new phase started, checkpoint completed, error detected, instrument change). "
            "False if the scene looks essentially the same as before. Set False to skip redundant processing."
        )
    )
    observation: str = Field(
        description="One-sentence factual observation of the current surgical field state"
    )
    analysis_summary: str = Field(
        description="Brief summary of what is happening in the surgical field"
    )


# ──────────────────────────────────────────────
# Helper: get raw JSON schema dict for google-genai SDK
# ──────────────────────────────────────────────

def get_standard_chunk_schema() -> dict:
    """Return JSON schema dict for StandardChunkAnalysis (for response_json_schema)."""
    return StandardChunkAnalysis.model_json_schema()


def get_outlier_chunk_schema() -> dict:
    """Return JSON schema dict for OutlierChunkAnalysis (for response_json_schema)."""
    return OutlierChunkAnalysis.model_json_schema()
