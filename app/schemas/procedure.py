"""
API request/response schemas for surgical procedures.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class SurgicalStepBase(BaseModel):
    """Base schema for surgical step."""
    step_number: int
    step_name: str
    description: Optional[str] = None
    expected_duration_min: Optional[int] = None
    expected_duration_max: Optional[int] = None
    is_critical: bool = False
    instruments_required: List[str] = []
    anatomical_landmarks: List[str] = []
    visual_cues: Optional[str] = None


class SurgicalStepCreate(SurgicalStepBase):
    """Schema for creating surgical step."""
    pass


class SurgicalStepResponse(SurgicalStepBase):
    """Schema for surgical step response."""
    id: str
    procedure_id: str
    
    class Config:
        from_attributes = True


class MasterProcedureBase(BaseModel):
    """Base schema for master procedure."""
    procedure_name: str
    procedure_type: str
    total_duration_avg: Optional[float] = None
    video_duration: Optional[float] = None
    difficulty_level: Optional[str] = None
    video_source_gs_uri: Optional[str] = None
    steps: List[SurgicalStepBase] = []
    metadata: Dict[str, Any] = {}


class MasterProcedureCreate(MasterProcedureBase):
    """Schema for creating master procedure."""
    pass


class MasterProcedureResponse(MasterProcedureBase):
    """Schema for master procedure response."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class MasterProcedureWithSteps(MasterProcedureResponse):
    """Schema for master procedure with steps - deprecated, use MasterProcedureResponse."""
    pass


class SessionAlertResponse(BaseModel):
    """Schema for session alert response."""
    id: str
    session_id: str
    alert_type: str
    severity: str
    message: str
    timestamp: datetime
    acknowledged: bool
    metadata: Dict[str, Any] = {}
    
    class Config:
        from_attributes = True


class LiveSessionCreate(BaseModel):
    """Schema for creating live session."""
    procedure_id: str


class LiveSessionResponse(BaseModel):
    """Schema for live session response."""
    id: str
    procedure_id: str
    surgeon_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    current_step: int
    status: str
    
    class Config:
        from_attributes = True


class VideoAnalysisRequest(BaseModel):
    """Schema for video analysis request."""
    video_gs_uri: str
    
    
class VideoAnalysisResponse(BaseModel):
    """Schema for video analysis response."""
    procedure_id: str
    procedure_name: str
    procedure_type: str
    message: str
    steps_count: int
    total_duration_avg: Optional[float] = None
    video_duration: Optional[float] = None
    difficulty_level: Optional[str] = None
    characteristics: Optional[str] = None
    steps: List[SurgicalStepBase] = []
