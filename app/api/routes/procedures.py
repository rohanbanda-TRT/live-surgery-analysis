"""
API routes for surgical procedures.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pymongo.asynchronous.database import AsyncDatabase
from typing import List
from bson import ObjectId

from app.db.mongodb import get_db
from app.db.collections import MASTER_PROCEDURES, SURGICAL_STEPS
from app.schemas.procedure import (
    MasterProcedureResponse,
    VideoAnalysisRequest,
    VideoAnalysisResponse
)
from app.services.video_analysis import VideoAnalysisService
from app.services.recorded_video_comparison import RecordedVideoComparisonService
from app.services.chunked_video_comparison import ChunkedVideoComparisonService
from app.services.video_upload import VideoUploadService
from app.services.procedure_cache import ProcedureCache
from app.services.openai_client_v2 import OpenAIClientV2

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


@router.post("/upload-video")
async def upload_video(
    file: UploadFile = File(...)
):
    """
    Upload a video file to Google Cloud Storage.
    
    Returns the GCS URI that can be used for analysis or comparison.
    
    Args:
        file: Video file to upload (MP4, AVI, MOV, etc.)
        
    Returns:
        Dictionary with gcs_uri and filename
    """
    # Validate file type
    allowed_types = ["video/mp4", "video/avi", "video/mov", "video/quicktime", "video/x-msvideo"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed types: {', '.join(allowed_types)}"
        )
    
    # Validate file size (max 500MB)
    max_size = 500 * 1024 * 1024  # 500MB
    file.file.seek(0, 2)  # Seek to end
    file_size = file.file.tell()
    file.file.seek(0)  # Reset to beginning
    
    if file_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is 500MB, got {file_size / 1024 / 1024:.2f}MB"
        )
    
    try:
        upload_service = VideoUploadService()
        gcs_uri = await upload_service.upload_video(
            file=file.file,
            filename=file.filename,
            content_type=file.content_type
        )
        
        return {
            "gcs_uri": gcs_uri,
            "filename": file.filename,
            "size_mb": round(file_size / 1024 / 1024, 2),
            "message": "Video uploaded successfully"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {str(e)}"
        )


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


@router.post("/compare")
async def compare_recorded_video(
    request: dict,
    db: AsyncDatabase = Depends(get_db)
):
    """
    Compare a recorded video against a procedure.
    
    Analyzes the FULL video at once (not in chunks) and returns:
    - Which steps/phases were detected
    - Which checkpoints were met (for outlier mode)
    - Which error codes were detected
    - Overall comparison results
    
    Similar to live monitoring but for recorded videos.
    
    Request body:
        {
            "video_gs_uri": "gs://bucket/video.mp4",
            "procedure_id": "507f1f77bcf86cd799439011",
            "procedure_source": "standard"  // or "outlier"
        }
    
    Returns:
        Complete comparison results with detected steps, checkpoints, and errors
    """
    video_gs_uri = request.get("video_gs_uri")
    procedure_id = request.get("procedure_id")
    procedure_source = request.get("procedure_source", "standard")
    model_provider = request.get("model_provider", "gemini").lower()
    gemini_model = request.get("gemini_model")  # e.g. "gemini-2.5-flash" or "gemini-2.5-pro"
    
    if not video_gs_uri:
        raise HTTPException(status_code=400, detail="video_gs_uri is required")
    
    if not procedure_id:
        raise HTTPException(status_code=400, detail="procedure_id is required")
    
    if not ObjectId.is_valid(procedure_id):
        raise HTTPException(status_code=400, detail="Invalid procedure ID")
    
    if procedure_source not in ["standard", "outlier"]:
        raise HTTPException(
            status_code=400, 
            detail="procedure_source must be 'standard' or 'outlier'"
        )
    
    if model_provider not in ["gemini"]:
        raise HTTPException(
            status_code=400,
            detail=f"model_provider '{model_provider}' is not supported on this endpoint. "
                   "For OpenAI, use POST /api/procedures/compare-with-upload and upload the video file directly."
        )
    
    try:
        procedure_cache = ProcedureCache()
        cached_data = await procedure_cache.load_procedure(db, procedure_id, procedure_source)
        
        service = RecordedVideoComparisonService(db, procedure_cache, gemini_model=gemini_model)
        result = await service.compare_video(
            video_gs_uri=video_gs_uri,
            procedure_id=procedure_id,
            procedure_source=procedure_source,
            cached_procedure=cached_data
        )
        
        return result
    except ValueError as e:
        raise HTTPException(status_code=429 if "rate limit" in str(e).lower() else 400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")


@router.post("/compare-chunked")
async def compare_recorded_video_chunked(
    request: dict,
    db: AsyncDatabase = Depends(get_db)
):
    """
    Compare a long recorded video against a procedure using chunked analysis.

    For videos longer than ~25 minutes, the video is automatically split into
    20-minute chunks with 60-second overlap. Each chunk receives the full
    analysis of all previous chunks as rolling history context.

    For short videos (<= 25 min), this transparently delegates to the
    original /compare endpoint logic.

    Request body:
        {
            "video_gs_uri": "gs://bucket/video.mp4",
            "procedure_id": "507f1f77bcf86cd799439011",
            "procedure_source": "standard",  // or "outlier"
            "video_duration_sec": 4200        // required — total video duration in seconds
        }

    Returns:
        Complete comparison results with detected steps, checkpoints, errors,
        plus chunking_metadata showing how the video was split.
    """
    video_gs_uri = request.get("video_gs_uri")
    procedure_id = request.get("procedure_id")
    procedure_source = request.get("procedure_source", "standard")
    video_duration_sec = request.get("video_duration_sec")
    model_provider = request.get("model_provider", "gemini").lower()
    gemini_model = request.get("gemini_model")  # e.g. "gemini-2.5-flash" or "gemini-2.5-pro"

    if not video_gs_uri:
        raise HTTPException(status_code=400, detail="video_gs_uri is required")

    if not procedure_id:
        raise HTTPException(status_code=400, detail="procedure_id is required")

    if not ObjectId.is_valid(procedure_id):
        raise HTTPException(status_code=400, detail="Invalid procedure ID")

    if procedure_source not in ["standard", "outlier"]:
        raise HTTPException(
            status_code=400,
            detail="procedure_source must be 'standard' or 'outlier'"
        )

    if model_provider not in ["gemini"]:
        raise HTTPException(
            status_code=400,
            detail=f"model_provider '{model_provider}' is not supported for video comparison. "
                   "Video analysis requires Gemini (GCS URI support). Use model_provider='gemini' "
                   "and optionally set gemini_model to 'gemini-2.5-flash' or 'gemini-2.5-pro'."
        )

    if video_duration_sec is not None:
        try:
            video_duration_sec = float(video_duration_sec)
            if video_duration_sec <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="video_duration_sec must be a positive number (seconds)"
            )

    try:
        # Create procedure cache and load procedure data once
        procedure_cache = ProcedureCache()
        
        # Load procedure data into cache (single DB query)
        cached_data = await procedure_cache.load_procedure(
            db, procedure_id, procedure_source
        )
        
        # Run comparison with cached data (no DB access needed)
        service = ChunkedVideoComparisonService(db, procedure_cache, gemini_model=gemini_model)
        result = await service.compare_video(
            video_gs_uri=video_gs_uri,
            procedure_id=procedure_id,
            procedure_source=procedure_source,
            video_duration_sec=video_duration_sec,
        )

        return result
    except ValueError as e:
        raise HTTPException(status_code=429 if "rate limit" in str(e).lower() else 400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chunked comparison failed: {str(e)}")


@router.post("/compare-with-upload")
async def compare_with_upload(
    file: UploadFile = File(..., description="Video file to analyze (MP4, AVI, MOV)"),
    procedure_id: str = File(..., description="ID of the procedure to compare against"),
    procedure_source: str = File(default="standard", description="'standard' or 'outlier'"),
    openai_model: str = File(default="", description="OpenAI model override (e.g. gpt-4.1-mini, gpt-4o)"),
    num_frames: int = File(default=16, description="Number of frames to sample from video"),
    db: AsyncDatabase = Depends(get_db)
):
    """
    Compare an uploaded video against a procedure using OpenAI vision.

    Unlike /compare which requires a GCS URI (Gemini only), this endpoint
    accepts a direct video file upload. Frames are extracted and sent to
    OpenAI GPT-4o/4.1-mini as base64 images.

    Form fields:
        file            — video file (MP4, AVI, MOV)
        procedure_id    — MongoDB ObjectId of the procedure
        procedure_source — "standard" or "outlier" (default: "standard")
        openai_model    — optional model override (default: from config)
        num_frames      — frames to sample across video (default: 16)
    """
    if not ObjectId.is_valid(procedure_id):
        raise HTTPException(status_code=400, detail="Invalid procedure ID")

    if procedure_source not in ["standard", "outlier"]:
        raise HTTPException(status_code=400, detail="procedure_source must be 'standard' or 'outlier'")

    allowed_types = ["video/mp4", "video/avi", "video/mov", "video/quicktime", "video/x-msvideo"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}"
        )

    try:
        video_bytes = await file.read()

        # Load procedure once; downstream logic uses cached data
        procedure_cache = ProcedureCache()
        procedure, procedure_steps = await procedure_cache.load_procedure(db, procedure_id, procedure_source)

        # Build the same prompt as the Gemini path
        from app.services.recorded_video_comparison import RecordedVideoComparisonService
        svc = RecordedVideoComparisonService(db, procedure_cache)
        if procedure_source == "outlier":
            prompt = svc._build_outlier_comparison_prompt(procedure, procedure_steps)
        else:
            prompt = svc._build_standard_comparison_prompt(procedure, procedure_steps)

        # Analyze with OpenAI
        client = OpenAIClientV2(model=openai_model if openai_model else None)
        analysis = await client.analyze_video_from_file(
            video_bytes=video_bytes,
            prompt=prompt,
            num_frames=num_frames,
        )

        # Process results using same parsing logic
        if procedure_source == "outlier":
            result = await svc._process_outlier_results(analysis, procedure, procedure_steps)
        else:
            result = await svc._process_standard_results(analysis, procedure, procedure_steps)

        result["model_used"] = client.model
        result["frames_analyzed"] = num_frames
        return result

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI comparison failed: {str(e)}")
