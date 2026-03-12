"""
Prompts for standard surgical video analysis.
Used for offline video analysis and master procedure creation.
"""
from typing import Dict, Any


def get_video_analysis_schema() -> Dict[str, Any]:
    """
    Get the JSON schema for structured video analysis output.
    
    Returns:
        JSON schema for Gemini structured output
    """
    return {
        "type": "object",
        "properties": {
            "procedure_name": {
                "type": "string",
                "description": "Name of the surgical procedure identified in the video"
            },
            "procedure_type": {
                "type": "string",
                "description": "Type/category of the procedure (e.g., 'Laparoscopic', 'Open', 'Endoscopic')"
            },
            "total_steps": {
                "type": "integer",
                "description": "Total number of distinct surgical steps identified"
            },
            "steps": {
                "type": "array",
                "description": "Detailed breakdown of each surgical step",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_number": {
                            "type": "integer",
                            "description": "Sequential step number"
                        },
                        "step_name": {
                            "type": "string",
                            "description": "Concise name of the step"
                        },
                        "description": {
                            "type": "string",
                            "description": "Detailed description of what happens in this step"
                        },
                        "expected_duration_min": {
                            "type": "integer",
                            "description": "Minimum expected duration in minutes"
                        },
                        "expected_duration_max": {
                            "type": "integer",
                            "description": "Maximum expected duration in minutes"
                        },
                        "instruments_required": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of surgical instruments used in this step"
                        },
                        "anatomical_landmarks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key anatomical structures visible or relevant in this step"
                        },
                        "visual_cues": {
                            "type": "string",
                            "description": "Visual indicators that this step is being performed"
                        },
                        "is_critical": {
                            "type": "boolean",
                            "description": "Whether this is a critical step requiring extra attention"
                        }
                    },
                    "required": [
                        "step_number",
                        "step_name",
                        "description",
                        "instruments_required",
                        "anatomical_landmarks",
                        "is_critical"
                    ]
                }
            }
        },
        "required": ["procedure_name", "procedure_type", "total_steps", "steps"]
    }


def get_standard_chunk_analysis_prompt(
    procedure_name: str,
    current_step: dict,
    detected_context: str,
    remaining_context: str,
    history_context: str,
    cumulative_note: str,
    ui_step_status_context: str,
    chunk_duration: int
) -> str:
    """
    Generate prompt for analyzing video chunks in standard live monitoring mode.
    
    Args:
        procedure_name: Name of the surgical procedure
        current_step: Current expected step details
        detected_context: Context of already detected steps
        remaining_context: Context of remaining steps
        history_context: Recent analysis history (full text of all previous chunks)
        cumulative_note: Note about cumulative tracking
        ui_step_status_context: Complete UI step status including checkpoints
        chunk_duration: Duration of video chunk in seconds
        
    Returns:
        Analysis prompt for standard mode
    """
    current_step_detail = f"""
**Current Expected Step {current_step.get('step_number')}: {current_step['step_name']}**
- Description: {current_step.get('description', 'N/A')}
- Expected Duration: {current_step.get('expected_duration_min', 'N/A')}-{current_step.get('expected_duration_max', 'N/A')} minutes
- Critical Step: {'YES - Extra caution required' if current_step.get('is_critical') else 'No'}
- Required Instruments: {', '.join(current_step.get('instruments_required', [])) or 'Not specified'}
- Anatomical Landmarks: {', '.join(current_step.get('anatomical_landmarks', [])) or 'Not specified'}
- Visual Cues: {current_step.get('visual_cues', 'Not specified')}
"""
    
    return f"""Analyze this {chunk_duration}-second surgical video clip from {procedure_name}.

**MASTER PROCEDURE CONTEXT:**
{current_step_detail}

**DETECTED STEPS (CUMULATIVE - ALREADY IDENTIFIED):** 
{detected_context}
{cumulative_note}
**REMAINING STEPS (FOCUS ON DETECTING THESE):** 
{remaining_context}
{ui_step_status_context}
{history_context}
**CRITICAL RULES - CUMULATIVE TRACKING:**
1. This is CUMULATIVE analysis - once a step is detected, it REMAINS detected forever
2. **FOCUS ONLY on remaining steps** - detected steps are already confirmed
3. Compare video against the MASTER PROCEDURE definition above
4. Steps take MINUTES (50-200+ frames at 1 FPS), not seconds
5. Mark "completed" ONLY when you see clear evidence the step description is fulfilled
6. "in-progress" is default - be conservative
7. Verify actual surgical actions match the step description, not just instrument presence
8. **Review the COMPLETE UI STEP STATUS and COMPLETE ANALYSIS HISTORY** - use all previous context
9. Match visible instruments and anatomical landmarks against requirements
10. **DO NOT re-detect already detected steps** - they remain in the detected list automatically
11. **Use all previous chunk analyses** to understand progression and avoid contradictions

**RESPONSE FORMAT:**
Detected Step: [number] - [name]
Action Being Performed: [what surgeon is doing - compare to step description]
Instruments Visible: [list - compare to required instruments]
Anatomical Landmarks: [list - compare to expected landmarks]
Matches Expected: [yes/no - does video match master procedure definition?]
Step Progress: [just-started/in-progress/nearing-completion/completed]
Completion Evidence: [required if completed - what proves step description is fulfilled? else "N/A"]
Analysis: [brief observation comparing video to master procedure and previous analyses]

Analyze the video clip and respond:"""


def get_video_analysis_prompt() -> str:
    """
    Generate comprehensive prompt for analyzing surgical videos.
    
    Used for offline video analysis to create master procedures.
    
    Returns:
        Detailed analysis prompt
    """
    return """You are an expert surgical analyst. Analyze this surgical video and provide a comprehensive breakdown.

**YOUR TASK:**

1. **IDENTIFY THE PROCEDURE:**
   - What surgical procedure is being performed?
   - What is the approach type (laparoscopic, open, endoscopic, robotic)?
   - What is the anatomical region/organ system?

2. **BREAK DOWN THE STEPS:**
   - Identify each distinct surgical step in chronological order
   - For each step, provide:
     * Step number (sequential)
     * Step name (concise, descriptive)
     * Detailed description of the surgical actions
     * Expected duration range (min-max in minutes)
     * Required instruments
     * Key anatomical landmarks visible
     * Visual cues that indicate this step
     * Whether it's a critical step requiring extra attention

3. **CRITICAL STEPS:**
   - Mark steps as critical if they involve:
     * Major vessel handling
     * Critical anatomical structure manipulation
     * High risk of complications
     * Irreversible actions

4. **ANATOMICAL LANDMARKS:**
   - Identify key anatomical structures that help orient the surgeon
   - Note structures that must be preserved
   - Highlight structures that indicate step progression

5. **INSTRUMENTS:**
   - List all instruments used in each step
   - Be specific (e.g., "5mm grasper" not just "grasper")

**ANALYSIS GUIDELINES:**

- Be precise and medically accurate
- Use standard surgical terminology
- Steps should be distinct and non-overlapping
- Duration estimates should be realistic for experienced surgeons
- Visual cues should be observable in video footage
- Critical steps should genuinely pose significant risk

**OUTPUT:**
Provide a structured JSON response following the schema with all required fields.
"""
