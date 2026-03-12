"""
Pydantic schemas for structured JSON output from outlier comparison analysis.
Used with Gemini's response_json_schema for reliable parsing without regex.
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class CheckpointValidation(BaseModel):
    """Individual checkpoint validation result."""
    checkpoint_name: str = Field(description="Name of the checkpoint requirement")
    status: Literal["MET", "NOT_MET", "PREVIOUSLY_MET"] = Field(description="Whether checkpoint is met")
    evidence: str = Field(description="Visual evidence from video supporting this status")


class ErrorCode(BaseModel):
    """Detected error code."""
    code: str = Field(description="Error code (e.g., A1, A8, C1, R1)")
    description: str = Field(description="Description of the error")
    severity: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Error severity level")
    blocking_checkpoints: Optional[List[str]] = Field(default=None, description="Checkpoints that are blocking due to this error")


class PhaseAnalysis(BaseModel):
    """Analysis of a single phase in the chunk."""
    phase_number: str = Field(description="Phase number (e.g., '3.1', '3.3')")
    phase_name: str = Field(description="Name of the phase")
    detected: bool = Field(description="Whether this phase is visible in the video chunk")
    evidence: Optional[str] = Field(default=None, description="Visual evidence of phase detection")
    timestamp_start: str = Field(description="Start timestamp in format MM:SS or H:MM:SS")
    timestamp_end: str = Field(description="End timestamp in format MM:SS or H:MM:SS")
    checkpoint_validations: List[CheckpointValidation] = Field(default_factory=list, description="Checkpoint validation results for this phase")
    completion_status: Literal["COMPLETED", "PARTIAL", "NOT_PERFORMED"] = Field(description="Phase completion status")
    blocking_issues: Optional[str] = Field(default=None, description="Any blocking issues preventing phase completion")


class ChunkSummary(BaseModel):
    """Summary of the chunk analysis."""
    chunk_number: int = Field(description="Chunk number being analyzed")
    time_window: str = Field(description="Time window of this chunk (e.g., '00:08 – 00:13')")
    phases_detected_count: int = Field(description="Number of phases detected in this chunk")
    phases_completed_count: int = Field(description="Number of phases completed in this chunk")
    checkpoints_met_count: int = Field(description="Number of checkpoints met in this chunk")
    error_codes_detected: List[str] = Field(default_factory=list, description="List of error codes detected (e.g., ['A8'])")


class OutlierComparisonChunkAnalysis(BaseModel):
    """
    Structured output for outlier comparison chunk analysis.
    This replaces regex parsing with reliable JSON schema.
    """
    phases: List[PhaseAnalysis] = Field(description="Analysis of each phase visible in this chunk")
    error_codes: List[ErrorCode] = Field(default_factory=list, description="All error codes detected in this chunk")
    chunk_summary: ChunkSummary = Field(description="Summary of this chunk's analysis")
    critical_safety_issues: Optional[str] = Field(default=None, description="Any critical safety concerns observed")
    analysis_text: str = Field(description="Natural language analysis summary for display")


class StepAnalysis(BaseModel):
    """Analysis of a single step in standard mode."""
    step_number: int = Field(description="Step number from master procedure")
    step_name: str = Field(description="Name of the step")
    detected: bool = Field(description="Whether this step is visible in the video chunk")
    evidence: Optional[str] = Field(default=None, description="Visual evidence of step detection")
    timestamp_start: str = Field(description="Start timestamp in format MM:SS or H:MM:SS")
    timestamp_end: str = Field(description="End timestamp in format MM:SS or H:MM:SS")
    completion_status: Literal["COMPLETED", "PARTIAL", "NOT_PERFORMED"] = Field(description="Step completion status")
    deviations: Optional[str] = Field(default=None, description="Any deviations from expected procedure")


class StandardComparisonChunkAnalysis(BaseModel):
    """
    Structured output for standard comparison chunk analysis.
    """
    steps: List[StepAnalysis] = Field(description="Analysis of each step visible in this chunk")
    chunk_summary: ChunkSummary = Field(description="Summary of this chunk's analysis")
    critical_observations: Optional[str] = Field(default=None, description="Any critical deviations or safety concerns")
    analysis_text: str = Field(description="Natural language analysis summary for display")
