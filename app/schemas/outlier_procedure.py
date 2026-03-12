"""
Schemas for Outlier Resolution-based master procedures.
This is separate from the existing master_procedure schema to support
the new outlier resolution document parsing system.
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class ErrorCode(BaseModel):
    """Individual error code definition"""
    code: str = Field(..., description="Error code (e.g., A3, A8, C1)")
    category: str = Field(..., description="Error category (Action/Checking/Retrieval)")
    description: str = Field(..., description="What this error means")
    common: bool = Field(default=False, description="Is this a commonly occurring error")


class CriticalError(BaseModel):
    """Critical error for a phase"""
    error_code: str = Field(..., description="Reference to error code (e.g., A3)")
    description: str = Field(..., description="Specific error description for this phase")
    consequence: str = Field(..., description="What happens if this error occurs")
    priority: str = Field(..., description="HIGH/MEDIUM/LOW")


class PreventionStrategy(BaseModel):
    """Prevention strategy for errors"""
    strategy: str = Field(..., description="Prevention action to take")
    ar_feature: Optional[str] = Field(None, description="AR feature that helps prevent this")


class Checkpoint(BaseModel):
    """Critical safety checkpoint"""
    name: str = Field(..., description="Checkpoint name (e.g., 'Before Incision')")
    requirements: List[str] = Field(..., description="List of requirements that must be met")
    blocking: bool = Field(default=True, description="Does this block progression if not met")


class AlertQuestion(BaseModel):
    """AR alert question asked to surgeon at decision point within a phase"""
    question: str = Field(..., description="The yes/no question to verify before proceeding")
    expected_answer: str = Field(default="YES", description="Expected answer (YES/NO)")
    blocking: bool = Field(default=True, description="Does a wrong answer block progression")


class SubTask(BaseModel):
    """Sub-task within a phase"""
    task_name: str = Field(..., description="Name of the sub-task")
    description: str = Field(..., description="What needs to be done")
    required: bool = Field(default=True, description="Is this sub-task mandatory")
    verification_method: Optional[str] = Field(None, description="How to verify completion")


class SurgicalPhase(BaseModel):
    """Individual surgical phase with error management"""
    phase_number: str = Field(..., description="Phase identifier (e.g., '3.1', '3.2')")
    phase_name: str = Field(..., description="Name of the phase")
    goal: str = Field(..., description="Primary goal of this phase")
    sub_tasks: List[SubTask] = Field(default_factory=list, description="Sub-tasks within this phase")
    critical_errors: List[CriticalError] = Field(default_factory=list, description="Errors that can occur")
    prevention_strategies: List[PreventionStrategy] = Field(default_factory=list, description="How to prevent errors")
    checkpoints: List[Checkpoint] = Field(default_factory=list, description="Safety checkpoints for this phase")
    alert_questions: List[AlertQuestion] = Field(default_factory=list, description="AR alert questions asked to surgeon at decision points")
    dependencies: List[str] = Field(default_factory=list, description="Phase numbers that must be completed first")
    priority: str = Field(..., description="Overall priority level (HIGH/MEDIUM/LOW)")
    anatomical_landmarks: List[str] = Field(default_factory=list, description="Key landmarks to identify")
    instruments_required: List[str] = Field(default_factory=list, description="Instruments needed")


class OutlierProcedure(BaseModel):
    """Complete outlier resolution-based master procedure"""
    procedure_name: str = Field(..., description="Name of the surgical procedure")
    procedure_type: str = Field(..., description="Type/category of surgery")
    version: str = Field(..., description="Protocol version (e.g., '0.9 BETA/25')")
    organization: str = Field(..., description="Organization that created this protocol")
    
    # Core content
    phases: List[SurgicalPhase] = Field(..., description="All surgical phases")
    error_codes: List[ErrorCode] = Field(default_factory=list, description="All error code definitions")
    global_checkpoints: List[Checkpoint] = Field(default_factory=list, description="Global safety checkpoints")
    
    # Metadata
    document_overview: Optional[str] = Field(None, description="Overview/purpose of the document")
    target_users: List[str] = Field(default_factory=list, description="Intended users")
    key_takeaways: Optional[Dict[str, Any]] = Field(None, description="Summary of critical information")
    implementation_recommendations: Optional[Dict[str, Any]] = Field(None, description="How to implement this protocol")
    
    # Tracking
    source_document: Optional[str] = Field(None, description="Original document filename")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = Field(None, description="User who uploaded this")


class OutlierProcedureCreate(BaseModel):
    """Request model for creating outlier procedure from document"""
    document_content: str = Field(..., description="Full text content of the outlier resolution document")
    filename: Optional[str] = Field(None, description="Original filename")
    created_by: Optional[str] = Field(None, description="User uploading this document")


class OutlierProcedureResponse(BaseModel):
    """Response after creating outlier procedure"""
    id: str = Field(..., description="MongoDB ObjectId as string")
    procedure_name: str
    procedure_type: str
    version: str
    total_phases: int
    total_error_codes: int
    total_checkpoints: int
    phases_summary: List[Dict[str, Any]] = Field(..., description="Summary of each phase")
    created_at: datetime
    message: str = Field(default="Outlier procedure created successfully")
