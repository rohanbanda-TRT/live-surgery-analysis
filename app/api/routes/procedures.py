"""
API routes for surgical procedures.
"""
from fastapi import APIRouter, Depends, HTTPException
from pymongo.asynchronous.database import AsyncDatabase
from typing import List
from bson import ObjectId

from app.db.mongodb import get_db
from app.db.collections import MASTER_PROCEDURES, SURGICAL_STEPS
from app.schemas.procedure import (
    MasterProcedureCreate,
    MasterProcedureResponse,
    MasterProcedureWithSteps,
    VideoAnalysisRequest,
    VideoAnalysisResponse
)
from app.services.video_analysis import VideoAnalysisService

router = APIRouter()


@router.get("", response_model=List[MasterProcedureResponse])
async def list_procedures(
    db: AsyncDatabase = Depends(get_db),
    skip: int = 0,
    limit: int = 100
):
    """List all master procedures with embedded steps."""
    cursor = db[MASTER_PROCEDURES].find().skip(skip).limit(limit)
    procedures = await cursor.to_list(length=limit)
    
    # Convert ObjectId to string
    for proc in procedures:
        proc["id"] = str(proc.pop("_id"))
    
    return procedures


@router.get("/{procedure_id}", response_model=MasterProcedureResponse)
async def get_procedure(
    procedure_id: str,
    db: AsyncDatabase = Depends(get_db)
):
    """Get a specific procedure with embedded steps."""
    if not ObjectId.is_valid(procedure_id):
        raise HTTPException(status_code=400, detail="Invalid procedure ID")
    
    # Get procedure with embedded steps
    procedure = await db[MASTER_PROCEDURES].find_one({"_id": ObjectId(procedure_id)})
    if not procedure:
        raise HTTPException(status_code=404, detail="Procedure not found")
    
    # Convert ObjectId to string
    procedure["id"] = str(procedure.pop("_id"))
    
    return procedure


@router.post("/analyze", response_model=VideoAnalysisResponse)
async def analyze_video(
    request: VideoAnalysisRequest,
    db: AsyncDatabase = Depends(get_db)
):
    """
    Analyze a surgical video and create a master procedure.
    
    The AI will automatically identify the procedure type from the video content.
    You only need to provide the video GCS URI.
    """
    service = VideoAnalysisService(db)
    result = await service.analyze_and_store(
        video_gs_uri=request.video_gs_uri
    )
    
    return result
