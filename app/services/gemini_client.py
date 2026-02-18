"""
Gemini API client for video analysis using Vertex AI.
"""
from google import genai
from google.genai.types import HttpOptions, Part, GenerateContentConfig
from typing import Optional, Dict, Any, List
import json
import re
import httpx

from app.core.config import settings
from app.core.logging import logger


def parse_json_response(response: str, context: str = "response") -> Dict[str, Any]:
    """
    Parse JSON response from LLM with error handling.
    
    Handles both clean JSON and JSON wrapped in markdown code blocks.
    Extracts JSON from responses that may contain extra text.
    
    Args:
        response: Raw response string from LLM
        context: Context for error messages
        
    Returns:
        Parsed JSON as dictionary
        
    Raises:
        ValueError: If JSON parsing fails
    """
    try:
        # Clean response - remove markdown code blocks if present
        cleaned_response = response.strip()
        
        # Remove markdown code blocks (```json, ```JSON, or just ```)
        if cleaned_response.startswith("```"):
            # Find the first newline after opening ```
            first_newline = cleaned_response.find("\n")
            if first_newline != -1:
                cleaned_response = cleaned_response[first_newline + 1:]
            
            # Remove closing ```
            if cleaned_response.endswith("```"):
                cleaned_response = cleaned_response[:-3]
            
            cleaned_response = cleaned_response.strip()
        
        # Try to extract JSON from response
        # Sometimes LLM adds extra text before/after JSON
        
        # Find JSON object boundaries
        start_idx = cleaned_response.find('{')
        end_idx = cleaned_response.rfind('}')
        
        if start_idx == -1 or end_idx == -1:
            raise ValueError("No JSON object found in response")
        
        json_str = cleaned_response[start_idx:end_idx + 1]
        
        # Try to fix common JSON issues
        # Fix unescaped newlines in strings (basic attempt)
        # This is a simple heuristic and may not work for all cases
        
        parsed = json.loads(json_str)
        
        return parsed
        
    except json.JSONDecodeError as e:
        logger.error(
            "json_parsing_failed",
            context=context,
            error=str(e),
            response_preview=response[:500] if len(response) > 500 else response
        )
        raise ValueError(f"Invalid JSON format in {context}: {str(e)}")
    except Exception as e:
        logger.error(
            "unexpected_parsing_error",
            context=context,
            error=str(e)
        )
        raise ValueError(f"Failed to parse {context}: {str(e)}")


def validate_json_fields(
    data: Dict[str, Any],
    required_fields: List[str],
    optional_fields: Optional[List[str]] = None,
    context: str = "response"
) -> None:
    """
    Validate that required fields exist in parsed JSON.
    
    Args:
        data: Parsed JSON dictionary
        required_fields: List of required field names
        optional_fields: List of optional field names (will be added if missing)
        context: Context for error messages
        
    Raises:
        ValueError: If required fields are missing
    """
    missing_fields = [field for field in required_fields if field not in data]
    
    if missing_fields:
        raise ValueError(f"Missing required fields in {context}: {missing_fields}")
    
    # Add optional fields with None if missing
    if optional_fields:
        for field in optional_fields:
            if field not in data:
                data[field] = None


class GeminiClient:
    """Client for interacting with Gemini API via Vertex AI."""
    
    def __init__(self):
        """Initialize Gemini client with Vertex AI configuration."""
        self.client = genai.Client(
            http_options=HttpOptions(api_version="v1"),
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.VERTEX_AI_LOCATION
        )
        self.model = settings.GEMINI_MODEL
        self.temperature = settings.GEMINI_TEMPERATURE
        # self.max_output_tokens = settings.GEMINI_MAX_OUTPUT_TOKENS
        # Video analysis timeout - 10 minutes for long videos
        self.video_analysis_timeout = 600.0
        logger.info(
            "gemini_client_initialized",
            model=self.model,
            temperature=self.temperature,
            # max_output_tokens=self.max_output_tokens,
            video_timeout=self.video_analysis_timeout,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.VERTEX_AI_LOCATION
        )
    
    async def analyze_video(
        self,
        video_gs_uri: str,
        prompt: str,
        temperature: Optional[float] = None,
        response_schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Analyze a video from Google Cloud Storage using Gemini.
        
        Args:
            video_gs_uri: GCS URI of the video (e.g., gs://bucket/video.mp4)
            prompt: Text prompt for analysis
            temperature: Model temperature (default from settings)
            response_schema: Optional JSON schema for structured output
            
        Returns:
            Analysis result as text or JSON string
        """
        try:
            logger.info(
                "analyzing_video",
                video_uri=video_gs_uri,
                model=self.model
            )
            
            # Prepare content parts
            contents = [
                Part.from_uri(
                    file_uri=video_gs_uri,
                    mime_type="video/mp4"
                ),
                prompt
            ]
            
            # Configure generation - no max_output_tokens to avoid truncation
            config = GenerateContentConfig(
                temperature=temperature or settings.GEMINI_TEMPERATURE,
                response_mime_type="application/json" if response_schema else "text/plain",
                response_schema=response_schema
            )
            
            # Generate content
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config
            )
            
            result = response.text
            logger.info(
                "video_analysis_result",
                result=result
            )
            
            logger.info(
                "video_analysis_completed",
                video_uri=video_gs_uri,
                response_length=len(result)
            )
            
            return result
            
        except Exception as e:
            logger.error(
                "video_analysis_failed",
                video_uri=video_gs_uri,
                error=str(e)
            )
            raise
    
    async def analyze_video_with_structured_output(
        self,
        video_gs_uri: str,
        prompt: str,
        response_schema: Dict[str, Any],
        temperature: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Analyze video and return structured JSON output.
        
        Args:
            video_gs_uri: GCS URI of the video
            prompt: Text prompt for analysis
            response_schema: JSON schema for structured output
            temperature: Model temperature
            
        Returns:
            Parsed and validated JSON response as dictionary
        """
        result = await self.analyze_video(
            video_gs_uri=video_gs_uri,
            prompt=prompt,
            temperature=temperature,
            response_schema=response_schema
        )
        
        # Use robust JSON parser
        parsed_result = parse_json_response(result, context="video_analysis")
        
        # Validate required fields from schema
        if "required" in response_schema:
            validate_json_fields(
                data=parsed_result,
                required_fields=response_schema["required"],
                context="video_analysis"
            )
        
        logger.info(
            "structured_output_parsed",
            video_uri=video_gs_uri,
            fields_count=len(parsed_result.keys())
        )
        
        return parsed_result
    
    async def analyze_frame(
        self,
        frame_data: bytes,
        prompt: str,
        temperature: Optional[float] = None
    ) -> str:
        """
        Analyze a single video frame.
        
        Args:
            frame_data: Raw frame data as bytes
            prompt: Text prompt for analysis
            temperature: Model temperature
            
        Returns:
            Analysis result as string
        """
        try:
            # Create content parts
            contents = [
                Part.from_bytes(data=frame_data, mime_type="image/jpeg"),
                prompt
            ]
            
            # Configure generation
            config = GenerateContentConfig(
                temperature=temperature if temperature is not None else self.temperature,
                # max_output_tokens=self.max_output_tokens
            )
            
            # Generate content
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config
            )
            
            return response.text
            
        except Exception as e:
            logger.error(
                "frame_analysis_failed",
                error=str(e)
            )
            raise
    
    async def analyze_video_chunk(
        self,
        video_data: bytes,
        prompt: str,
        temperature: Optional[float] = None
    ) -> str:
        """
        Analyze a video chunk (short video clip).
        
        Args:
            video_data: Raw video data as bytes (MP4 format)
            prompt: Text prompt for analysis
            temperature: Model temperature
            
        Returns:
            Analysis result as string
        """
        try:
            # Create content parts with video
            contents = [
                Part.from_bytes(data=video_data, mime_type="video/mp4"),
                prompt
            ]
            
            # Configure generation
            config = GenerateContentConfig(
                temperature=temperature if temperature is not None else self.temperature,
                # max_output_tokens=self.max_output_tokens
            )
            
            logger.info(
                "analyzing_video_chunk",
                video_size_kb=len(video_data) / 1024
            )
            
            # Generate content
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config
            )
            
            logger.info(
                "video_chunk_analyzed",
                response_length=len(response.text)
            )
            
            return response.text
            
        except Exception as e:
            logger.error(
                "video_chunk_analysis_failed",
                error=str(e)
            )
            raise
