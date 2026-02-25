"""
Prompts for outlier resolution protocol (error-focused surgical analysis).
Handles checkpoint-based phase validation and error detection.
"""
from typing import Dict, Any


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
    
    # Build remaining phases focus with checkpoint details
    remaining_context = "**REMAINING PHASES TO DETECT:**\n"
    if remaining_phases:
        for phase in remaining_phases[:3]:  # Show next 3 phases with full details
            remaining_context += f"\n→ Phase {phase['phase_number']}: {phase['phase_name']} (Priority: {phase['priority']})\n"
            remaining_context += f"  Goal: {phase['goal']}\n"
            
            # Add checkpoint requirements for this phase
            if phase.get('checkpoints'):
                remaining_context += f"  **Checkpoints to validate:**\n"
                for checkpoint in phase['checkpoints']:
                    blocking_marker = " [BLOCKING]" if checkpoint.get('blocking') else ""
                    remaining_context += f"    • {checkpoint['name']}{blocking_marker}\n"
                    for req in checkpoint['requirements']:
                        remaining_context += f"      - {req}\n"
            
            # Add critical errors to watch for
            if phase.get('critical_errors'):
                remaining_context += f"  **Critical errors to avoid:**\n"
                for error in phase['critical_errors'][:3]:  # Top 3 errors
                    remaining_context += f"    • {error['error_code']}: {error['description']}\n"
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

**YOUR TASK - ERROR DETECTION & CHECKPOINT-BASED PHASE VALIDATION:**

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

3. **CHECKPOINT VALIDATION (CRITICAL - REQUIRED FOR PHASE COMPLETION):**
   For the detected phase, validate EACH checkpoint requirement:
   - Review the checkpoint requirements listed above for the current phase
   - For EACH requirement, verify if it is satisfied in the video
   - **BLOCKING checkpoints MUST be satisfied before phase can progress**
   
   **TEMPORAL VALIDATION RULES (CRITICAL FOR STABILITY):**
   - If a checkpoint was MET in previous chunks and is NOT visible in current chunk, mark as "PREVIOUSLY_MET"
   - Only mark as NOT MET if you observe active violation or reversal of the requirement
   - Use evidence from RECENT ANALYSIS HISTORY to maintain temporal stability
   - Camera angle changes do NOT invalidate previously satisfied checkpoints
   - Example: "Coagulation Completed: PREVIOUSLY_MET - Not visible in current frame but was confirmed in chunk history"
   
   - Report which requirements are MET, NOT MET, or PREVIOUSLY_MET
   - Provide specific visual evidence for each checkpoint validation
   
   Example checkpoint validation:
   - "Anatomical Exposure Verified: MET - Clear view of mitral annulus visible"
   - "Leaflet Mobility Assessed: NOT MET - Leaflets not yet visible in frame"
   - "Coagulation Completed: PREVIOUSLY_MET - Confirmed in chunk 3, not visible now due to camera angle"
   - "Hemostasis Achieved: MET - No active bleeding visible"

4. **PHASE COMPLETION LOGIC:**
   A phase can ONLY be marked as "completed" if:
   - The phase has been detected in the video
   - ALL checkpoint requirements are satisfied (especially BLOCKING ones)
   - No critical errors are present
   
   If checkpoints are incomplete:
   - Mark phase as "in-progress" 
   - List which checkpoints are blocking completion
   - Specify what needs to be observed to satisfy them

5. **OMISSION DETECTION (Error A8 - CRITICAL):**
   **Before marking a phase as detected, verify prerequisites are satisfied:**
   - Review DETECTED PHASES section to check if prerequisite phases have completed checkpoints
   - If current phase requires prior checkpoint completion, verify it was done
   
   **Common A8 violations to detect:**
   - Phase 3.3 (tissue manipulation) detected but Phase 3.2 (coagulation) checkpoints NOT MET
   - Phase 3.7 (closure) detected but Phase 3.6 (verification) checkpoints NOT MET
   - Fluoroscopy/imaging verification omitted before proceeding to next phase
   - Vessel coagulation not performed before tissue manipulation
   
   **If prerequisites are missing:**
   - Report A8 error with specific omitted phase and checkpoint
   - Format: "A8 - Phase 3.2 Coagulation checkpoint 'Vessel sealed' not satisfied before Phase 3.3"
   - Mark as HIGH severity

**CRITICAL RULES:**
- Once a phase is detected, it remains in the cumulative detected list
- Focus ONLY on remaining phases - detected phases are already confirmed
- **A phase is NOT completed until ALL its checkpoints are validated**
- Mark HIGH priority errors immediately
- Block progression if BLOCKING checkpoints not met
- Detect omissions (A8) proactively
- Be specific about which checkpoint requirements are met/not met

**OUTPUT FORMAT:**
Detected Phase: [phase_number or null]
Matches Expected: [YES/NO]
Error Codes Detected: [list of codes or "None"]
Checkpoint Status: [PASS/FAIL]
Checkpoint Details: [For each checkpoint requirement: "Requirement name: MET/NOT MET/PREVIOUSLY_MET - Evidence"]
Step Progress: [in-progress/completed/not-started]
Completion Evidence: [specific observations - only if ALL checkpoints PASS]
Block Progression: [YES/NO with reason - YES if BLOCKING checkpoints not met]
Analysis: [Detailed observations and recommendations]
"""
    
    return prompt
