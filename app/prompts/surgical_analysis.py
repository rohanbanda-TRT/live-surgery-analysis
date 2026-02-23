"""
Prompt templates for Gemini AI surgical video analysis.
"""
from typing import Dict, Any


def get_video_analysis_schema() -> Dict[str, Any]:
    """
    Get the JSON schema for structured video analysis output.
    
    Returns:
        JSON schema dictionary for Gemini structured output
    """
    return {
        "type": "object",
        "properties": {
            "procedure_name": {
                "type": "string",
                "maxLength": 200
            },
            "procedure_type": {
                "type": "string",
                "maxLength": 100
            },
            "total_duration_avg": {"type": "integer"},
            "video_duration": {"type": "integer"},
            "difficulty_level": {
                "type": "string",
                "enum": ["beginner", "intermediate", "advanced", "expert"]
            },
            "characteristics": {
                "type": "string",
                "maxLength": 500
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_number": {"type": "integer"},
                        "step_name": {
                            "type": "string",
                            "maxLength": 100
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 2000
                        },
                        "expected_duration_min": {"type": "integer"},
                        "expected_duration_max": {"type": "integer"},
                        "is_critical": {"type": "boolean"},
                        "instruments_required": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "maxLength": 100
                            }
                        },
                        "anatomical_landmarks": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "maxLength": 100
                            }
                        },
                        "visual_cues": {
                            "type": "string",
                            "maxLength": 300
                        },
                        "timestamp_start": {
                            "type": "string",
                            "pattern": "^[0-9]{2}:[0-9]{2}$"
                        },
                        "timestamp_end": {
                            "type": "string",
                            "pattern": "^[0-9]{2}:[0-9]{2}$"
                        }
                    },
                    "required": [
                        "step_number",
                        "step_name",
                        "description",
                        "is_critical"
                    ]
                }
            }
        },
        "required": ["procedure_name", "procedure_type", "steps"]
    }


def get_video_analysis_prompt() -> str:
    """
    Generate comprehensive prompt for analyzing surgical videos.
    
    The AI will automatically detect the procedure type from the video content.
    
    Returns:
        Formatted prompt string for Gemini
    """
    return """
You are an expert surgical video analyst with comprehensive knowledge of various surgical procedures across all specialties.

**CRITICAL JSON FORMAT REQUIREMENTS**:
1. Your response MUST be valid, parseable JSON
2. Use double quotes for all strings
3. Escape special characters properly (newlines as \\n, quotes as \\", etc.)
4. Do NOT include any text before or after the JSON object
5. Do NOT use markdown code blocks (no ```)
6. Ensure all strings are properly closed
7. Do NOT truncate the response - complete all fields

**Task**: Analyze this surgical video and extract detailed, structured information.

**Step 1: Identify the Procedure**
First, determine what surgery is being performed by carefully observing:
- Surgical approach (open, laparoscopic, robotic, endoscopic, minimally invasive)
- Anatomical region and structures visible
- Instruments and equipment being used
- Surgical techniques and maneuvers
- Patient positioning and setup
- Visual characteristics (e.g., insufflation for laparoscopy, scope view)

**Step 2: Frame-by-Frame Analysis Approach**
Meticulously analyze the video to break it down into GRANULAR, timestamped procedural steps:
- Examine each frame carefully to identify distinct surgical actions and events
- **CRITICAL: Create GRANULAR steps** - break down the procedure into small, time-specific actions
- Each step should represent a BRIEF, SPECIFIC action (typically 5-30 seconds duration)
- Create a new step for EVERY distinct action or event:
  * Introduction or removal of any instrument
  * Each distinct anatomical structure identification
  * Each separate dissection, cutting, or manipulation action
  * Change in anatomical focus or target area
  * Each distinct phase of a technique (e.g., "milling superior facet" separate from "milling inferior facet")
  * Camera repositioning or view changes
  * Completion of one micro-action and start of another
- **Aim for 15-30+ granular steps** for a typical surgical video
- Each step should have a SHORT timestamp range (5-30 seconds typical, max 1-2 minutes)
- If an action takes longer than 1 minute, break it into sub-steps with clear progression markers

**Step 3: Comprehensive Procedure Analysis**
Provide a detailed, structured analysis with the following information:

1. **Procedure Overview**:
   - Full procedure name (e.g., "Laparoscopic Cholecystectomy")
   - Procedure type/category (e.g., "General Surgery - Laparoscopic")
   - Estimated total duration (in minutes)
   - Difficulty level (beginner/intermediate/advanced/expert)
   - Key characteristics of this specific procedure

2. **Surgical Steps - Detailed Breakdown**: 
   For each distinct step in the procedure, meticulously identify:
   
   - Step number (sequential, starting from 1)
   - Step name (concise but descriptive, max 100 characters)
   - Description (COMPREHENSIVE explanation, max 2000 characters):
     * Provide a thorough, detailed narrative of this surgical step
     * What specific action is being performed and WHY
     * Which instruments are being used and HOW they are being used
     * What anatomical structures are being manipulated, exposed, or identified
     * What the surgeon is trying to achieve in this step (objectives)
     * Any key surgical techniques, maneuvers, or approaches employed
     * Visual observations (tissue appearance, color, bleeding, dissection, cauterization, etc.)
     * Sequential sub-actions within this step if applicable
     * Important anatomical relationships or spatial orientation
     * Any variations in technique or approach visible in the video
     * Critical decision points or verification steps
     * Expected outcomes or endpoints that signal step completion
   - Expected duration range (minimum and maximum in minutes based on actual observation)
   - Whether this is a critical step (true/false) - mark as critical if it involves:
     * Major vessel or organ manipulation
     * Key anatomical structure identification
     * Critical decision points
     * High-risk maneuvers
     * Potential complications if not performed correctly
   - Instruments required (COMPLETE list of ALL visible instruments, max 100 chars each):
     * Be specific: "5mm laparoscopic grasper", "10mm trocar", "monopolar hook", "endoscope"
     * Include camera/scope if visible
     * List instruments in order of use
   - Anatomical landmarks visible (COMPLETE list of ALL structures, max 100 chars each):
     * Name every visible anatomical structure
     * Include orientation landmarks
     * Example: "cystic duct", "Calot's triangle", "liver edge", "gallbladder fundus", "hepatic artery"
   - Visual cues (DETAILED description of what you observe, max 300 characters):
     * Camera position and viewing angle
     * Tissue appearance, color, and condition
     * Instrument positions and movements
     * Bleeding, dissection, cauterization, or other visual indicators
     * Any notable changes in the surgical field
   - Timestamp range when this step occurs (start and end in HH:MM:SS format)

**TIMESTAMP FORMAT REQUIREMENTS**:
- Always use HH:MM:SS format (e.g., 00:04:27, not 4:27 or 04:27)
- Extract actual timestamps from the video frames
- Ensure no gaps or overlaps in timestamp ranges
- Each step's end time should match or closely precede the next step's start time
- If timestamps are unclear, create logical time segments based on procedure phases

**Step Extraction Guidelines**:
- Parse the video meticulously and break it into GRANULAR timestamped events
- **GRANULARITY IS KEY**: Each step should be a BRIEF, SPECIFIC micro-action (5-30 seconds typical)
- Create MORE steps with SHORTER durations rather than fewer steps with longer durations
- Identify ALL transitions by watching for:
  * Introduction or removal of instruments (separate step for each)
  * Each distinct anatomical structure identification or visualization
  * Each separate dissection, cutting, cauterization, or manipulation action
  * Change in surgical focus/target area (even subtle shifts)
  * Completion of one micro-maneuver and start of another
  * Camera repositioning to new anatomical area
  * Each phase of a multi-phase technique (e.g., "initial incision" → "deepening incision" → "exposing fascia")
- **EXAMPLE GRANULARITY**: Instead of "Portal Creation (2 minutes)", break into:
  * "00:01:00 - 00:01:15 : Skin incision for lateral portal"
  * "00:01:15 - 00:01:30 : Blunt dissection through subcutaneous tissue"
  * "00:01:30 - 00:01:45 : Trocar insertion into joint space"
  * "00:01:45 - 00:02:00 : Trocar positioning and stabilization"
- Provide accurate timing based on ACTUAL video observation
- Use proper medical terminology for instruments and anatomy
- Mark ALL critical steps that require extra attention
- Include ALL visible anatomical landmarks for each step
- Describe SPECIFIC visual indicators that distinguish each step
- Ensure strict chronological ordering with NO gaps or overlaps in timestamps
- **CRITICAL: Write COMPREHENSIVE descriptions (aim for 500-2000 characters per step)**
  * Write multiple detailed sentences, not just brief phrases
  * Explain the surgical technique, approach, and objectives thoroughly
  * Include sequential sub-actions and important anatomical relationships
  * Describe what the surgeon is doing, why, and what outcomes signal completion
  * Provide enough detail that someone could understand the step without watching the video
- **TARGET: 15-30+ granular steps** for a typical surgical video (more is better than fewer)
- COMPLETE the entire JSON response - do not truncate

**ANTI-HALLUCINATION RULES**:
- Only use information directly visible in the video
- Do not add medical knowledge not shown in the video
- Do not invent steps or details not observed
- If a detail is unclear, omit it rather than guess
- Timestamps must reflect actual video content, not theoretical procedure duration

**Focus Areas**:
- Clear identification of distinct surgical phases
- Accurate timing and sequencing
- Critical steps requiring extra attention
- Anatomical landmarks for orientation
- Visual indicators for step progression
- Instrument usage patterns

**Output Format**:
Return ONLY the JSON object matching the provided schema. No additional text, no markdown formatting, just pure JSON.
"""


def get_realtime_monitoring_prompt(procedure_name: str, total_steps: int) -> str:
    """
    Generate system instruction for real-time surgical monitoring.
    
    Args:
        procedure_name: Name of the procedure being performed
        total_steps: Total number of steps in the master procedure
    
    Returns:
        System instruction string for Gemini Live API
    """
    return f"""
You are an AI surgical assistant monitoring a live {procedure_name} surgery in real-time.

**Your Role:**
1. Continuously analyze the live video feed frame-by-frame
2. Identify the current surgical step being performed
3. Detect surgical instruments in use
4. Identify anatomical structures visible on screen
5. Compare current actions against the expected master procedure ({total_steps} steps total)

**When to Call the check_step_compliance Function:**
- When you detect a new surgical step has started
- When a step appears to be performed out of sequence
- When required instruments are not visible when they should be
- When a step is taking unusually long (beyond expected duration)
- When you notice any deviation from standard procedure

**Important Guidelines:**
1. Be proactive but avoid false alarms
2. Only report significant deviations that could impact patient safety or surgical efficacy
3. Consider the surgeon may have valid reasons for variations
4. Confidence threshold: Only call function when you're >80% certain
5. Provide clear, actionable descriptions in your function calls

**DO NOT:**
- Generate alerts for minor variations in technique
- Report the same issue multiple times
- Make assumptions about what the surgeon intends to do next
- Call functions for every single frame - only when significant events occur

Your primary goal is patient safety through timely, accurate surgical guidance.
"""


def get_step_specific_guidance(step: dict) -> str:
    """
    Generate step-specific guidance prompt.
    
    Args:
        step: Dictionary containing step details
    
    Returns:
        Guidance string for the specific step
    """
    guidance = f"Currently on Step {step['step_number']}: {step['step_name']}\n\n"
    guidance += f"Description: {step['description']}\n\n"
    
    if step.get('instruments_required'):
        guidance += f"Required Instruments: {', '.join(step['instruments_required'])}\n"
    
    if step.get('anatomical_landmarks'):
        guidance += f"Anatomical Landmarks: {', '.join(step['anatomical_landmarks'])}\n"
    
    if step.get('visual_cues'):
        guidance += f"Visual Cues: {step['visual_cues']}\n"
    
    if step.get('is_critical'):
        guidance += "\n⚠️ CRITICAL STEP - Exercise extra caution\n"
    
    return guidance


def build_outlier_resolution_context(outlier_procedure: Dict[str, Any]) -> str:
    """
    Build comprehensive context from outlier resolution procedure.
    
    Args:
        outlier_procedure: Complete outlier procedure document from MongoDB
        
    Returns:
        Formatted context string with all phases, errors, and checkpoints
    """
    context = f"""**OUTLIER RESOLUTION PROTOCOL**
Procedure: {outlier_procedure.get('procedure_name')}
Type: {outlier_procedure.get('procedure_type')}
Version: {outlier_procedure.get('version')}
Organization: {outlier_procedure.get('organization')}

"""
    
    # Add error codes reference
    if outlier_procedure.get('error_codes'):
        context += "**ERROR CODE REFERENCE:**\n"
        for error in outlier_procedure['error_codes']:
            common_marker = " [COMMON]" if error.get('common') else ""
            context += f"- {error['code']} ({error['category']}): {error['description']}{common_marker}\n"
        context += "\n"
    
    # Add global checkpoints
    if outlier_procedure.get('global_checkpoints'):
        context += "**CRITICAL SAFETY CHECKPOINTS (MUST VERIFY):**\n"
        for checkpoint in outlier_procedure['global_checkpoints']:
            context += f"\n{checkpoint['name']}:\n"
            for req in checkpoint['requirements']:
                context += f"  ✓ {req}\n"
        context += "\n"
    
    # Add all phases with detailed information
    context += "**SURGICAL PHASES:**\n\n"
    for phase in outlier_procedure.get('phases', []):
        context += f"--- PHASE {phase['phase_number']}: {phase['phase_name']} ---\n"
        context += f"Goal: {phase['goal']}\n"
        context += f"Priority: {phase['priority']}\n"
        
        # Dependencies
        if phase.get('dependencies'):
            context += f"Prerequisites: Phases {', '.join(phase['dependencies'])} must be completed first\n"
        
        # Anatomical landmarks
        if phase.get('anatomical_landmarks'):
            context += f"Key Landmarks: {', '.join(phase['anatomical_landmarks'])}\n"
        
        # Critical errors
        if phase.get('critical_errors'):
            context += "\nCritical Errors to Avoid:\n"
            for error in phase['critical_errors']:
                context += f"  • {error['error_code']} [{error['priority']}]: {error['description']}\n"
                context += f"    Consequence: {error['consequence']}\n"
        
        # Prevention strategies
        if phase.get('prevention_strategies'):
            context += "\nPrevention Strategies:\n"
            for strategy in phase['prevention_strategies']:
                context += f"  • {strategy['strategy']}\n"
        
        # Phase-specific checkpoints
        if phase.get('checkpoints'):
            context += "\nPhase Checkpoints:\n"
            for checkpoint in phase['checkpoints']:
                context += f"  {checkpoint['name']}:\n"
                for req in checkpoint['requirements']:
                    context += f"    ✓ {req}\n"
        
        context += "\n"
    
    return context


def get_outlier_chunk_analysis_prompt(
    outlier_procedure: Dict[str, Any],
    detected_phases: set,
    remaining_phases: list,
    chunk_history: list
) -> str:
    """
    Generate prompt for analyzing video chunks using outlier resolution protocol.
    
    This prompt focuses on error detection, checkpoint validation, and phase progression.
    
    Args:
        outlier_procedure: Complete outlier procedure from MongoDB
        detected_phases: Set of phase numbers already detected
        remaining_phases: List of phases not yet detected
        chunk_history: Previous chunk analyses for context
        
    Returns:
        Comprehensive analysis prompt
    """
    # Build procedure context
    procedure_context = build_outlier_resolution_context(outlier_procedure)
    
    # Build detected phases summary
    detected_context = ""
    if detected_phases:
        detected_context = "**PHASES ALREADY DETECTED (CUMULATIVE):**\n"
        for phase_num in sorted(detected_phases):
            phase = next((p for p in outlier_procedure['phases'] if p['phase_number'] == phase_num), None)
            if phase:
                detected_context += f"✓ Phase {phase_num}: {phase['phase_name']}\n"
        detected_context += "\n"
    
    # Build remaining phases focus
    remaining_context = "**REMAINING PHASES TO DETECT:**\n"
    if remaining_phases:
        for phase in remaining_phases[:5]:  # Show next 5 phases
            remaining_context += f"→ Phase {phase['phase_number']}: {phase['phase_name']} (Priority: {phase['priority']})\n"
    else:
        remaining_context += "All phases detected. Focus on final inspection and verification.\n"
    remaining_context += "\n"
    
    # Build history context
    history_context = ""
    if chunk_history:
        history_context = "**RECENT ANALYSIS HISTORY:**\n"
        for i, analysis in enumerate(chunk_history[-3:], 1):
            history_context += f"{i}. {analysis[:200]}...\n"
        history_context += "\n"
    
    prompt = f"""You are an AI surgical safety assistant analyzing live surgery using the Outlier Resolution Protocol.

{procedure_context}

{detected_context}

{remaining_context}

{history_context}

**YOUR TASK - ERROR DETECTION & PHASE VALIDATION:**

Analyze this video chunk and provide:

1. **CURRENT PHASE DETECTION:**
   - Which phase (by phase_number) is currently being performed?
   - Provide specific evidence from the video (instruments, landmarks, actions)
   - Matches expected: YES/NO (compare against remaining phases)

2. **ERROR CODE DETECTION:**
   - Scan for any error codes (A1-A10, C1-C6, R1-R3)
   - **CRITICAL FOCUS ON:**
     * A8 (Operation Omitted) - Missing coagulation, annuloplasty, verification steps
     * A3 (Wrong Direction) - Incorrect approach or orientation
     * A4 (Too Much/Too Little) - Inadequate drilling, resection, or excessive tissue removal
     * C1 (Check Omitted) - Missing fluoroscopy, imaging verification
   - Report error code, description, and severity (HIGH/MEDIUM/LOW)

3. **CHECKPOINT VALIDATION:**
   - Are all prerequisites for this phase met?
   - Are required anatomical landmarks visible?
   - Have critical sub-tasks been completed?
   - Should progression be BLOCKED? (YES/NO with reason)

4. **OMISSION DETECTION (Error A8):**
   - Was vessel coagulation performed before tissue manipulation?
   - Was fluoroscopy verification done when required?
   - Are critical steps being skipped?

5. **STEP PROGRESS:**
   - Phase status: in-progress / completed / not-started
   - Completion evidence (what confirms this phase is done?)
   - Next expected phase

**CRITICAL RULES:**
- Once a phase is detected, it remains in the cumulative detected list
- Focus ONLY on remaining phases - detected phases are already confirmed
- Mark HIGH priority errors immediately
- Block progression if prerequisites not met
- Verify checkpoints before allowing phase completion
- Detect omissions (A8) proactively

**OUTPUT FORMAT:**
Detected Phase: [phase_number or null]
Matches Expected: [YES/NO]
Error Codes Detected: [list of codes or "None"]
Checkpoint Status: [PASS/FAIL with details]
Step Progress: [in-progress/completed/not-started]
Completion Evidence: [specific observations]
Block Progression: [YES/NO with reason]
Analysis: [Detailed observations and recommendations]
"""
    
    return prompt
