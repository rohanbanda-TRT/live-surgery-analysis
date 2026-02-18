"""
Database models for surgical procedures.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from bson import ObjectId


class PyObjectId(ObjectId):
    """Custom ObjectId type for Pydantic."""
    
    @classmethod
    def __get_validators__(cls):
        yield cls.validate
    
    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)
    
    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema):
        field_schema.update(type="string")


class MasterProcedure(BaseModel):
    """Master procedure model for database."""
    
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    procedure_name: str
    procedure_type: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    total_duration_avg: Optional[int] = None  # in seconds
    difficulty_level: Optional[str] = None
    video_source_gs_uri: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class SurgicalStep(BaseModel):
    """Surgical step model for database."""
    
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    procedure_id: PyObjectId
    step_number: int
    step_name: str
    description: Optional[str] = None
    expected_duration_min: Optional[int] = None
    expected_duration_max: Optional[int] = None
    is_critical: bool = False
    instruments_required: List[str] = Field(default_factory=list)
    anatomical_landmarks: List[str] = Field(default_factory=list)
    visual_cues: Optional[str] = None
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class LiveSession(BaseModel):
    """Live surgery session model."""
    
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    procedure_id: PyObjectId
    surgeon_id: PyObjectId
    start_time: datetime = Field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    current_step: int = 0
    status: str = "in_progress"  # in_progress, completed, stopped
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class SessionAlert(BaseModel):
    """Session alert model."""
    
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    session_id: PyObjectId
    alert_type: str
    severity: str  # HIGH, MEDIUM, LOW
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    acknowledged: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
