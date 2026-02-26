"""
API routes for outlier resolution-based master procedures.
This is separate from existing procedures API to support the new parsing system.
"""
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from typing import Optional, List
from datetime import datetime

from app.db.mongodb import get_db
from app.db.collections import OUTLIER_PROCEDURES
from app.schemas.outlier_procedure import (
    OutlierProcedure,
    OutlierProcedureCreate,
    OutlierProcedureResponse
)
from app.services.outlier_parser import OutlierDocumentParser
from app.core.logging import logger

router = APIRouter()


@router.post("/upload", response_model=OutlierProcedureResponse)
async def upload_outlier_document(
    file: UploadFile = File(..., description="Outlier resolution document (.md or .txt)"),
    created_by: Optional[str] = Form(None, description="User uploading this document"),
    db: AsyncDatabase = Depends(get_db)
):
    """
    Upload and parse an outlier resolution document.
    
    This endpoint:
    1. Accepts a readme/txt file containing outlier resolution protocol
    2. Uses LLM to extract structured surgical procedure data
    3. Stores it in MongoDB as a new outlier procedure
    4. Returns detailed summary of what was added
    
    **Supported file types:** .md, .txt
    **Max file size:** 10MB
    """
    # Validate file type
    allowed_extensions = [".md", ".txt"]
    file_ext = "." + file.filename.split(".")[-1].lower() if "." in file.filename else ""
    
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Validate file size (10MB limit)
    max_size = 10 * 1024 * 1024  # 10MB
    content = await file.read()
    
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: 10MB"
        )
    
    try:
        # Decode content
        document_content = content.decode('utf-8')
        
        logger.info(
            "outlier_document_upload_started",
            filename=file.filename,
            size_bytes=len(content),
            created_by=created_by
        )
        
        # Parse document using LLM
        parser = OutlierDocumentParser()
        parsed_data = await parser.parse_document(
            document_content=document_content,
            filename=file.filename
        )
        
        # Validate parsed data
        await parser.validate_parsed_data(parsed_data)
        
        # Add metadata
        parsed_data["created_by"] = created_by
        parsed_data["created_at"] = datetime.utcnow()
        parsed_data["updated_at"] = datetime.utcnow()
        
        # Insert into MongoDB
        result = await db[OUTLIER_PROCEDURES].insert_one(parsed_data)
        
        logger.info(
            "outlier_procedure_created",
            procedure_id=str(result.inserted_id),
            procedure_name=parsed_data.get("procedure_name"),
            phases_count=len(parsed_data.get("phases", []))
        )
        
        # Build response with summary
        phases_summary = [
            {
                "phase_number": phase.get("phase_number"),
                "phase_name": phase.get("phase_name"),
                "priority": phase.get("priority"),
                "critical_errors_count": len(phase.get("critical_errors", [])),
                "checkpoints_count": len(phase.get("checkpoints", []))
            }
            for phase in parsed_data.get("phases", [])
        ]
        
        return OutlierProcedureResponse(
            id=str(result.inserted_id),
            procedure_name=parsed_data.get("procedure_name"),
            procedure_type=parsed_data.get("procedure_type"),
            version=parsed_data.get("version", "Unknown"),
            total_phases=len(parsed_data.get("phases", [])),
            total_error_codes=len(parsed_data.get("error_codes", [])),
            total_checkpoints=len(parsed_data.get("global_checkpoints", [])),
            phases_summary=phases_summary,
            created_at=parsed_data.get("created_at"),
            message="Outlier procedure created successfully"
        )
        
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="File encoding error. Please ensure file is UTF-8 encoded."
        )
    except ValueError as e:
        logger.error("validation_error", error=str(e), filename=file.filename)
        raise HTTPException(
            status_code=422,
            detail=f"Document validation failed: {str(e)}"
        )
    except Exception as e:
        logger.error(
            "outlier_document_upload_failed",
            filename=file.filename,
            error=str(e)
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process document: {str(e)}"
        )


@router.post("/parse-text", response_model=OutlierProcedureResponse)
async def parse_text_content(
    request: OutlierProcedureCreate,
    db: AsyncDatabase = Depends(get_db)
):
    """
    Parse outlier resolution document from raw text content.
    
    Alternative to file upload - accepts document content directly as JSON.
    Useful for API integrations or when you already have the text content.
    """
    try:
        logger.info(
            "outlier_text_parsing_started",
            content_length=len(request.document_content),
            created_by=request.created_by
        )
        
        # Parse document using LLM
        parser = OutlierDocumentParser()
        parsed_data = await parser.parse_document(
            document_content=request.document_content,
            filename=request.filename
        )
        
        # Validate parsed data
        await parser.validate_parsed_data(parsed_data)
        
        # Add metadata
        parsed_data["created_by"] = request.created_by
        parsed_data["created_at"] = datetime.utcnow()
        parsed_data["updated_at"] = datetime.utcnow()
        
        # Insert into MongoDB
        result = await db[OUTLIER_PROCEDURES].insert_one(parsed_data)
        
        logger.info(
            "outlier_procedure_created_from_text",
            procedure_id=str(result.inserted_id),
            procedure_name=parsed_data.get("procedure_name")
        )
        
        # Build response
        phases_summary = [
            {
                "phase_number": phase.get("phase_number"),
                "phase_name": phase.get("phase_name"),
                "priority": phase.get("priority"),
                "critical_errors_count": len(phase.get("critical_errors", [])),
                "checkpoints_count": len(phase.get("checkpoints", []))
            }
            for phase in parsed_data.get("phases", [])
        ]
        
        return OutlierProcedureResponse(
            id=str(result.inserted_id),
            procedure_name=parsed_data.get("procedure_name"),
            procedure_type=parsed_data.get("procedure_type"),
            version=parsed_data.get("version", "Unknown"),
            total_phases=len(parsed_data.get("phases", [])),
            total_error_codes=len(parsed_data.get("error_codes", [])),
            total_checkpoints=len(parsed_data.get("global_checkpoints", [])),
            phases_summary=phases_summary,
            created_at=parsed_data.get("created_at"),
            message="Outlier procedure created successfully from text"
        )
        
    except ValueError as e:
        logger.error("validation_error", error=str(e))
        raise HTTPException(
            status_code=422,
            detail=f"Document validation failed: {str(e)}"
        )
    except Exception as e:
        logger.error("text_parsing_failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process text content: {str(e)}"
        )


@router.get("/", response_model=List[dict])
async def list_outlier_procedures(
    db: AsyncDatabase = Depends(get_db),
    skip: int = 0,
    limit: int = 100
):
    """
    List all outlier resolution procedures.
    
    Returns summary information for each procedure.
    """
    cursor = db[OUTLIER_PROCEDURES].find().skip(skip).limit(limit).sort("created_at", -1)
    procedures = await cursor.to_list(length=limit)
    
    # Convert ObjectIds and format response
    result = []
    for proc in procedures:
        result.append({
            "id": str(proc["_id"]),
            "procedure_name": proc.get("procedure_name"),
            "procedure_type": proc.get("procedure_type"),
            "version": proc.get("version"),
            "total_phases": len(proc.get("phases", [])),
            "total_error_codes": len(proc.get("error_codes", [])),
            "created_at": proc.get("created_at"),
            "created_by": proc.get("created_by")
        })
    
    return result


@router.get("/{procedure_id}", response_model=dict)
async def get_outlier_procedure(
    procedure_id: str,
    db: AsyncDatabase = Depends(get_db)
):
    """
    Get detailed information for a specific outlier procedure.
    
    Returns complete procedure data including all phases, errors, and checkpoints.
    """
    if not ObjectId.is_valid(procedure_id):
        raise HTTPException(status_code=400, detail="Invalid procedure ID")
    
    procedure = await db[OUTLIER_PROCEDURES].find_one({"_id": ObjectId(procedure_id)})
    
    if not procedure:
        raise HTTPException(status_code=404, detail="Procedure not found")
    
    # Convert ObjectId
    procedure["id"] = str(procedure.pop("_id"))
    
    return procedure


@router.delete("/{procedure_id}")
async def delete_outlier_procedure(
    procedure_id: str,
    db: AsyncDatabase = Depends(get_db)
):
    """
    Delete an outlier procedure.
    """
    if not ObjectId.is_valid(procedure_id):
        raise HTTPException(status_code=400, detail="Invalid procedure ID")
    
    result = await db[OUTLIER_PROCEDURES].delete_one({"_id": ObjectId(procedure_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Procedure not found")
    
    logger.info("outlier_procedure_deleted", procedure_id=procedure_id)
    
    return {"message": "Procedure deleted successfully", "id": procedure_id}
