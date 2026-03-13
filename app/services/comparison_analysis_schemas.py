"""
Pydantic schemas for structured JSON output from outlier comparison analysis.
Used with Gemini's response_json_schema for reliable parsing without regex.

Design goals for speed:
- Minimal required fields (fewer output tokens = faster response)
- No timestamps (AI doesn't need to compute them)
- No ChunkSummary (computed server-side)
- is_repeat flag to skip unchanged chunks from history
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class CheckpointValidation(BaseModel):
    """Individual checkpoint validation result."""
    checkpoint_name: str = Field(description="Name of the checkpoint requirement")
    status: Literal["MET", "NOT_MET", "PREVIOUSLY_MET"] = Field(description="Whether checkpoint is met")
    evidence: str = Field(description="Brief visual evidence supporting this status")


class ErrorCode(BaseModel):
    """Detected error code."""
    code: str = Field(description="Error code (e.g., A1, A8, C1, R1)")
    description: str = Field(description="Brief description of the error")
    severity: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Error severity level")


class PhaseAnalysis(BaseModel):
    """Analysis of a single phase in the chunk."""
    phase_number: str = Field(description="Phase number (e.g., '3.1', '3.3')")
    phase_name: str = Field(description="Name of the phase")
    detected: bool = Field(description="Whether this phase is visible in the video chunk")
    evidence: Optional[str] = Field(default=None, description="Brief visual evidence of phase detection")
    checkpoint_validations: List[CheckpointValidation] = Field(
        default_factory=list,
        description="Checkpoint validation results — only fill for DETECTED phases"
    )


class OutlierComparisonChunkAnalysis(BaseModel):
    """
    Structured output for outlier comparison chunk analysis.
    Lean schema for speed — only fields actually used by the backend.
    """
    is_repeat: bool = Field(
        description=(
            "Set true ONLY when this chunk shows IDENTICAL surgical state as the immediately "
            "preceding chunk: same phase, same on-screen text, no new instruments or actions visible. "
            "When true, provide minimal analysis_text and empty checkpoint_validations."
        )
    )
    phases: List[PhaseAnalysis] = Field(description="Phases visible in this chunk (only detected=true phases need checkpoint_validations)")
    error_codes: List[ErrorCode] = Field(default_factory=list, description="Error codes detected (A1-A10, C1-C6, R1-R3)")
    critical_safety_issues: Optional[str] = Field(default=None, description="Critical safety concerns, if any")
    analysis_text: str = Field(description="1-2 sentence natural language summary for the surgeon display")


class StepAnalysis(BaseModel):
    """Analysis of a single step in standard mode."""
    step_number: int = Field(description="Step number from master procedure")
    step_name: str = Field(description="Name of the step")
    detected: bool = Field(description="Whether this step is visible in the video chunk")
    evidence: Optional[str] = Field(default=None, description="Brief visual evidence of step detection")


class StandardComparisonChunkAnalysis(BaseModel):
    """
    Structured output for standard comparison chunk analysis.
    """
    is_repeat: bool = Field(description="True if surgical state is identical to the previous chunk")
    steps: List[StepAnalysis] = Field(description="Steps visible in this chunk")
    critical_observations: Optional[str] = Field(default=None, description="Critical deviations or safety concerns")
    analysis_text: str = Field(description="1-2 sentence summary for display")
