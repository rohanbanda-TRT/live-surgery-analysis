"""
Service for parsing outlier resolution documents using LLM.
Extracts structured surgical procedure data from text documents.
"""
import json
from typing import Dict, Any
from app.core.logging import logger
from app.services.gemini_client import GeminiClient


class OutlierDocumentParser:
    """Parse outlier resolution documents into structured data using LLM."""
    
    def __init__(self):
        self.gemini_client = GeminiClient()
    
    async def parse_document(self, document_content: str, filename: str = None) -> Dict[str, Any]:
        """
        Parse outlier resolution document into structured format.
        
        Args:
            document_content: Full text content of the document
            filename: Optional filename for reference
            
        Returns:
            Structured dictionary matching OutlierProcedure schema
        """
        logger.info(
            "parsing_outlier_document",
            filename=filename,
            content_length=len(document_content)
        )
        
        # Create comprehensive extraction prompt
        prompt = self._build_extraction_prompt(document_content)
        
        try:
            # Use Gemini to extract structured data
            response = await self.gemini_client.generate_content(
                prompt=prompt,
                response_mime_type="application/json"
            )
            
            # Parse JSON response
            parsed_data = json.loads(response)
            
            # Add metadata
            if filename:
                parsed_data["source_document"] = filename
            
            logger.info(
                "document_parsed_successfully",
                filename=filename,
                phases_extracted=len(parsed_data.get("phases", [])),
                error_codes_extracted=len(parsed_data.get("error_codes", []))
            )
            
            return parsed_data
            
        except json.JSONDecodeError as e:
            logger.error(
                "json_parse_error",
                filename=filename,
                error=str(e)
            )
            raise ValueError(f"Failed to parse LLM response as JSON: {str(e)}")
        except Exception as e:
            logger.error(
                "document_parsing_failed",
                filename=filename,
                error=str(e)
            )
            raise
    
    def _build_extraction_prompt(self, document_content: str) -> str:
        """Build comprehensive prompt for extracting structured data."""
        
        return f"""You are a medical data extraction specialist. Extract structured surgical procedure information from the following outlier resolution document.

**DOCUMENT CONTENT:**
{document_content}

**EXTRACTION REQUIREMENTS:**

Extract ALL information into this exact JSON structure:

{{
  "procedure_name": "string - Name of the surgical procedure",
  "procedure_type": "string - Type/category (e.g., 'Endoscopic Spine Surgery')",
  "version": "string - Protocol version (e.g., '0.9 BETA/25')",
  "organization": "string - Organization name (e.g., 'SPINE Outlier Resolutions')",
  "document_overview": "string - Purpose and overview",
  "target_users": ["array of target user types"],
  
  "phases": [
    {{
      "phase_number": "string - e.g., '3.1', '3.2'",
      "phase_name": "string - Name of the phase",
      "goal": "string - Primary goal",
      "sub_tasks": [
        {{
          "task_name": "string",
          "description": "string",
          "required": boolean,
          "verification_method": "string or null"
        }}
      ],
      "critical_errors": [
        {{
          "error_code": "string - e.g., 'A3', 'A8', 'C1'",
          "description": "string - What this error is",
          "consequence": "string - What happens if error occurs",
          "priority": "string - HIGH/MEDIUM/LOW"
        }}
      ],
      "prevention_strategies": [
        {{
          "strategy": "string - Prevention action",
          "ar_feature": "string or null - AR feature if mentioned"
        }}
      ],
      "checkpoints": [
        {{
          "name": "string - Checkpoint name",
          "requirements": ["array of requirements"],
          "blocking": boolean
        }}
      ],
      "dependencies": ["array of phase numbers that must be completed first"],
      "priority": "string - HIGH/MEDIUM/LOW",
      "anatomical_landmarks": ["array of landmarks to identify"],
      "instruments_required": ["array of instruments if mentioned"]
    }}
  ],
  
  "error_codes": [
    {{
      "code": "string - e.g., 'A3', 'A8'",
      "category": "string - Action/Checking/Retrieval",
      "description": "string - What this error means",
      "common": boolean - Is this commonly occurring
    }}
  ],
  
  "global_checkpoints": [
    {{
      "name": "string - e.g., 'Before Incision'",
      "requirements": ["array of requirements"],
      "blocking": true
    }}
  ],
  
  "key_takeaways": {{
    "top_risks": ["array of top risk errors"],
    "top_prevention_strategies": ["array of key prevention strategies"],
    "most_common_errors": ["array of most common preventable errors"]
  }},
  
  "implementation_recommendations": {{
    "for_surgical_teams": ["array of recommendations"],
    "for_surgeons": ["array of recommendations"],
    "for_institutions": ["array of recommendations"]
  }}
}}

**CRITICAL INSTRUCTIONS:**

1. **Extract ALL phases** - Don't skip any phase mentioned in the document
2. **Capture ALL error codes** - Include A1-A10, C1-C6, R1-R3 with descriptions
3. **Identify checkpoints** - Extract "STOP Points" and "Before X" checkpoints
4. **Map dependencies** - Infer which phases must be completed before others
5. **Extract anatomical landmarks** - Any structures mentioned (SAP, IAP, root shoulder, etc.)
6. **Preserve priority levels** - HIGH/MEDIUM/LOW as stated in document
7. **Include AR features** - Any mention of AR assistance or alerts
8. **Extract sub-tasks** - Break down each phase into specific tasks
9. **Capture consequences** - What happens when errors occur
10. **Maintain accuracy** - Don't invent information, extract only what's in the document

**IMPORTANT:**
- Use exact error codes from document (A3, A8, C1, etc.)
- Preserve all medical terminology exactly
- Include ALL prevention strategies mentioned
- Extract verification questions as checkpoints
- Map phase dependencies based on "Before X" requirements

Return ONLY the JSON object, no additional text.
"""
    
    async def validate_parsed_data(self, parsed_data: Dict[str, Any]) -> bool:
        """
        Validate that parsed data has required fields and structure.
        
        Args:
            parsed_data: Parsed dictionary from LLM
            
        Returns:
            True if valid, raises ValueError if invalid
        """
        required_fields = ["procedure_name", "procedure_type", "phases"]
        
        for field in required_fields:
            if field not in parsed_data:
                raise ValueError(f"Missing required field: {field}")
        
        if not isinstance(parsed_data["phases"], list) or len(parsed_data["phases"]) == 0:
            raise ValueError("Document must contain at least one surgical phase")
        
        # Validate each phase has required fields
        for i, phase in enumerate(parsed_data["phases"]):
            phase_required = ["phase_number", "phase_name", "goal", "priority"]
            for field in phase_required:
                if field not in phase:
                    raise ValueError(f"Phase {i} missing required field: {field}")
        
        logger.info(
            "parsed_data_validated",
            phases_count=len(parsed_data["phases"]),
            error_codes_count=len(parsed_data.get("error_codes", []))
        )
        
        return True
