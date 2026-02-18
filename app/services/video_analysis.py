"""
Video analysis service for surgical procedures using Gemini.
"""
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from datetime import datetime
from typing import Dict, Any, List

from app.services.gemini_client import GeminiClient
from app.db.collections import MASTER_PROCEDURES, SURGICAL_STEPS
from app.prompts.surgical_analysis import get_video_analysis_prompt, get_video_analysis_schema
from app.core.logging import logger


class VideoAnalysisService:
    """Service for analyzing surgical videos and extracting procedure steps."""
    
    def __init__(self, db: AsyncDatabase):
        """
        Initialize video analysis service.
        
        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.gemini_client = GeminiClient()
    
    async def analyze_and_store(
        self,
        video_gs_uri: str
    ) -> Dict[str, Any]:
        """
        Analyze a surgical video and store the extracted procedure as a master procedure.
        
        The AI will automatically identify the procedure type from the video content.
        
        Args:
            video_gs_uri: Google Cloud Storage URI of the video
            
        Returns:
            Dictionary with procedure_id, procedure_name, procedure_type, message, and steps_count
        """
        try:
            logger.info(
                "starting_video_analysis",
                video_uri=video_gs_uri
            )
            
            # Analyze video to extract surgical steps (AI detects procedure type)
            analysis_result = await self._analyze_surgical_video(
                video_gs_uri=video_gs_uri
            )

            # Normalize duration fields (convert seconds to minutes before storing/displaying)
            analysis_result = self._normalize_duration_fields(analysis_result)
            
            # Store master procedure and steps in database
            procedure_id = await self._store_procedure(
                analysis_result=analysis_result,
                video_gs_uri=video_gs_uri
            )
            
            steps_count = len(analysis_result.get("steps", []))
            procedure_name = analysis_result.get("procedure_name", "Unknown")
            procedure_type = analysis_result.get("procedure_type", "Unknown")
            
            logger.info(
                "video_analysis_completed",
                procedure_id=str(procedure_id),
                procedure_name=procedure_name,
                procedure_type=procedure_type,
                steps_count=steps_count
            )
            
            return {
                "procedure_id": str(procedure_id),
                "procedure_name": procedure_name,
                "procedure_type": procedure_type,
                "message": "Video analysis completed successfully",
                "steps_count": steps_count,
                "total_duration_avg": analysis_result.get("total_duration_avg"),
                "video_duration": analysis_result.get("video_duration"),
                "difficulty_level": analysis_result.get("difficulty_level"),
                "characteristics": analysis_result.get("characteristics"),
                "steps": analysis_result.get("steps", [])
            }
            
        except Exception as e:
            logger.error(
                "video_analysis_failed",
                video_uri=video_gs_uri,
                error=str(e)
            )
            raise
    
    async def _analyze_surgical_video(
        self,
        video_gs_uri: str
    ) -> Dict[str, Any]:
        """
        Use Gemini to analyze surgical video and extract structured information.
        
        The AI will automatically identify the procedure type from the video.
        
        Args:
            video_gs_uri: GCS URI of the video
            
        Returns:
            Structured analysis result with procedure details and steps
        """
        # Get prompt and schema from prompts module
        prompt = get_video_analysis_prompt()
        response_schema = get_video_analysis_schema()
        
        # Analyze video with structured output
        result = await self.gemini_client.analyze_video_with_structured_output(
            video_gs_uri=video_gs_uri,
            prompt=prompt,
            response_schema=response_schema,
            temperature=0.1,  # Low temperature for consistent analysis
            # max_output_tokens=8192
        )
        
        return result

    def _convert_seconds_to_minutes(self, value: Any) -> Any:
        """Convert seconds to minutes (rounded to 2 decimals)."""
        if value is None:
            return None
        try:
            minutes = float(value) / 60.0
            return round(minutes, 2)
        except (TypeError, ValueError):
            return value

    def _normalize_duration_fields(self, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure duration fields are expressed in minutes before storage/display."""
        if not analysis_result:
            return analysis_result
        normalized = dict(analysis_result)
        normalized["total_duration_avg"] = self._convert_seconds_to_minutes(
            analysis_result.get("total_duration_avg")
        )
        normalized["video_duration"] = self._convert_seconds_to_minutes(
            analysis_result.get("video_duration")
        )
        return normalized

    async def _store_procedure(
        self,
        analysis_result: Dict[str, Any],
        video_gs_uri: str
    ) -> ObjectId:
        """
        Store the analyzed procedure with steps as an embedded array.
        
        Args:
            analysis_result: Structured analysis from Gemini (includes procedure_type)
            video_gs_uri: GCS URI of the source video
            
        Returns:
            ObjectId of the created master procedure
        """
        now = datetime.utcnow()
        
        # Prepare steps array
        steps = analysis_result.get("steps", [])
        steps_array = []
        for step in steps:
            step_doc = {
                "step_number": step.get("step_number"),
                "step_name": step.get("step_name"),
                "description": step.get("description"),
                "expected_duration_min": step.get("expected_duration_min"),
                "expected_duration_max": step.get("expected_duration_max"),
                "is_critical": step.get("is_critical", False),
                "instruments_required": step.get("instruments_required", []),
                "anatomical_landmarks": step.get("anatomical_landmarks", []),
                "visual_cues": step.get("visual_cues"),
                "timestamp_start": step.get("timestamp_start"),
                "timestamp_end": step.get("timestamp_end")
            }
            steps_array.append(step_doc)
        
        # Prepare master procedure document with embedded steps
        master_procedure = {
            "procedure_name": analysis_result.get("procedure_name"),
            "procedure_type": analysis_result.get("procedure_type"),
            "total_duration_avg": analysis_result.get("total_duration_avg"),
            "video_duration": analysis_result.get("video_duration"),
            "difficulty_level": analysis_result.get("difficulty_level"),
            "video_source_gs_uri": video_gs_uri,
            "steps": steps_array,
            "metadata": {
                "characteristics": analysis_result.get("characteristics", ""),
                "analysis_timestamp": now.isoformat()
            },
            "created_at": now,
            "updated_at": now
        }
        
        # Insert master procedure with embedded steps
        result = await self.db[MASTER_PROCEDURES].insert_one(master_procedure)
        procedure_id = result.inserted_id
        
        logger.info(
            "master_procedure_created",
            procedure_id=str(procedure_id),
            procedure_name=master_procedure["procedure_name"],
            steps_count=len(steps_array)
        )
        
        return procedure_id
