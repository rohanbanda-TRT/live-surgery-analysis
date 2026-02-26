"""
Optimized prompts (v2) for structured JSON output.

Key changes from standard_prompts.py / outlier_prompts.py:
  - System instruction (static, set once per session) vs dynamic prompt (per-chunk)
  - Prompts instruct Gemini to fill a JSON schema — no free-text formatting rules
  - Much shorter dynamic prompts → faster inference
"""
from typing import Dict, Any, List, Optional, Set


# ──────────────────────────────────────────────
# System instructions (static — set once per session)
# ──────────────────────────────────────────────

def build_standard_system_instruction(
    procedure_name: str,
    procedure_steps: List[Dict[str, Any]],
) -> str:
    """
    Build a system instruction for standard mode.
    This is set once at session start and remains constant.
    """
    steps_text = ""
    for i, step in enumerate(procedure_steps):
        step_num = step.get("step_number", i + 1)
        steps_text += f"\nStep {step_num}: {step['step_name']}"
        if step.get("description"):
            steps_text += f"\n  Description: {step['description']}"
        if step.get("instruments_required"):
            steps_text += f"\n  Instruments: {', '.join(step['instruments_required'])}"
        if step.get("anatomical_landmarks"):
            steps_text += f"\n  Landmarks: {', '.join(step['anatomical_landmarks'])}"
        if step.get("is_critical"):
            steps_text += "\n  ⚠️ CRITICAL STEP"

    return f"""You are an expert surgical video analyst monitoring a live procedure: {procedure_name}

PROCEDURE STEPS:
{steps_text}

YOUR ROLE:
- Analyze the video frames and identify which surgical step is currently being performed
- Track step progress and provide completion evidence
- Only report what you ACTUALLY SEE in the frames — do not infer or assume

IMPORTANT BEHAVIOURAL RULES:
- Steps do NOT have to occur in strict sequence. The surgeon may jump between steps, repeat steps, or perform them out of order. Report what you SEE regardless of sequence.
- The "expected step" in each prompt is a HINT based on system state — the surgeon may legitimately be performing a different step. Use your own visual judgment.
- A step is IN-PROGRESS as long as the relevant action or instruments are visible and active.
- A step is COMPLETED only when you observe clear visual evidence that the action is fully done (e.g., suture tied off, incision closed, instrument withdrawn after task).
- When uncertain between "in-progress" and "completed", always choose "in-progress".
- "completion_evidence" must contain specific visual proof. Set to null if step is not completed.

OUTPUT: You will respond with structured JSON matching the provided schema.
"""


def build_outlier_system_instruction(
    outlier_procedure: Dict[str, Any],
) -> str:
    """
    Build a system instruction for outlier/error resolution mode.
    """
    procedure_name = outlier_procedure.get("procedure_name", "Unknown")
    procedure_type = outlier_procedure.get("procedure_type", "Unknown")

    phases_text = ""
    for phase in outlier_procedure.get("phases", []):
        phase_num = phase["phase_number"]
        phases_text += f"\nPhase {phase_num}: {phase['phase_name']}"
        phases_text += f"\n  Goal: {phase.get('goal', 'N/A')}"
        phases_text += f"\n  Priority: {phase.get('priority', 'N/A')}"

        for cp in phase.get("checkpoints", []):
            phases_text += f"\n  Checkpoint: {cp['name']}"
            for req in cp.get("requirements", []):
                phases_text += f"\n    - {req}"

    # Collect error codes
    all_errors = outlier_procedure.get("error_codes", [])
    error_text = ""
    if all_errors:
        for err in all_errors:
            error_text += f"\n  {err.get('code', '?')}: {err.get('description', '')}"

    return f"""You are an expert surgical analyst monitoring a live outlier resolution procedure.

PROCEDURE: {procedure_name} ({procedure_type})

PHASES AND CHECKPOINTS:
{phases_text}

ERROR CODES TO WATCH FOR:
{error_text if error_text else "  (refer to phases for error conditions)"}

YOUR ROLE:
- Detect which phase is currently being performed based on what you SEE in the frames
- Validate checkpoint requirements for the DETECTED phase with visual evidence
- Detect surgical error codes (A1-A10, C1-C6, R1-R3) when applicable

IMPORTANT BEHAVIOURAL RULES:
- Phases do NOT have to occur in strict sequence. Report the phase you actually see being performed.
- The "expected phase" in each prompt is a HINT — use your own visual judgment.
- When a phase is ONGOING (in-progress), validate whether its checkpoints are being met DURING the execution. Checkpoints represent safety requirements that must be satisfied during the phase.
- A checkpoint is MET when you can visually confirm the requirement is satisfied. Otherwise mark NOT_MET.
- Only report error codes you can visually confirm. Do not flag errors based on assumptions.
- A8 (Operation Omitted): Only flag if you can clearly see a phase started without its prerequisites completed.

OUTPUT: You will respond with structured JSON matching the provided schema.
"""


# ──────────────────────────────────────────────
# Dynamic prompts (per-chunk — minimal, only changing state)
# ──────────────────────────────────────────────

def build_standard_chunk_prompt(
    current_step: Dict[str, Any],
    detected_steps_cumulative: Set[int],
    procedure_steps: List[Dict[str, Any]],
    chunk_history_summary: Optional[str] = None,
    chunk_frame_count: int = 5,
) -> str:
    """
    Build the per-chunk dynamic prompt for standard mode.
    Much shorter than v1 — procedure context is in system_instruction.
    """
    # Detected steps summary
    detected_list = []
    for i in sorted(detected_steps_cumulative):
        s = procedure_steps[i]
        detected_list.append(f"✓ Step {s.get('step_number', i+1)}: {s['step_name']}")
    detected_text = "\n".join(detected_list) if detected_list else "None yet"

    # Remaining steps
    remaining_list = []
    for i, s in enumerate(procedure_steps):
        if i not in detected_steps_cumulative:
            marker = " ← EXPECTED NEXT" if s == current_step else ""
            remaining_list.append(
                f"Step {s.get('step_number', i+1)}: {s['step_name']}{marker}"
            )
    remaining_text = "\n".join(remaining_list) if remaining_list else "All steps detected!"

    # Current step info
    step_num = current_step.get("step_number", "?")
    step_name = current_step.get("step_name", "Unknown")

    prompt = f"""Analyze these {chunk_frame_count} sequential frames from the live surgery.

CURRENT STATE:
- Suggested next step (hint only): Step {step_num} — {step_name}
- Already detected: {len(detected_steps_cumulative)}/{len(procedure_steps)} steps

DETECTED STEPS (cumulative):
{detected_text}

REMAINING STEPS:
{remaining_text}
"""
    if chunk_history_summary:
        prompt += f"\nRECENT HISTORY:\n{chunk_history_summary}\n"

    prompt += """
Fill the JSON schema based on what you observe in the frames:
- detected_step_number: 1-based step number you can visually identify. null if frames are unclear, camera is away, or nothing surgical is happening.
- matches_expected: true only if the observed step matches the suggested next step above.
- step_progress: use "in-progress" unless you see clear completion evidence. "completed" requires definitive visual proof.
- completion_evidence: specific visual proof of completion. null if step is not completed.
- action_observed: describe what is ACTUALLY visible — including "camera pointed away", "nothing visible", "surgeon repositioning" etc.
"""
    return prompt


def build_outlier_chunk_prompt(
    detected_phases: Set[str],
    remaining_phases: List[Dict[str, Any]],
    current_phase: Optional[Dict[str, Any]] = None,
    chunk_history_summary: Optional[str] = None,
    chunk_frame_count: int = 5,
) -> str:
    """
    Build the per-chunk dynamic prompt for outlier mode.
    """
    detected_text = ", ".join(sorted(detected_phases)) if detected_phases else "None yet"

    remaining_text = ""
    for phase in remaining_phases:
        pn = phase.get("phase_number", "?")
        remaining_text += f"\n  Phase {pn}: {phase.get('phase_name', '?')}"

    current_text = ""
    if current_phase:
        current_text = f"Expected phase: {current_phase.get('phase_number')} — {current_phase.get('phase_name', '?')}"

    prompt = f"""Analyze these {chunk_frame_count} sequential frames from the live surgery (outlier resolution mode).

CURRENT STATE:
- Suggested phase (hint only): {current_text if current_text else "No specific phase expected"}
- Detected phases so far: {detected_text}

REMAINING PHASES:{remaining_text}
"""
    if chunk_history_summary:
        prompt += f"\nRECENT HISTORY:\n{chunk_history_summary}\n"

    prompt += """
Fill the JSON schema based on what you observe in the frames:
- detected_phase_number: phase number (e.g. "3.1") you can visually identify. null if camera is away, frames are unclear, or nothing surgical is happening.
- action_observed: describe what is ACTUALLY visible — including "camera pointed away", "nothing visible", "surgeon repositioning" etc.
- matches_expected: true only if observed phase matches the suggested phase above.
- step_progress: "in-progress" unless you have definitive visual proof of completion. When a phase is in-progress, validate its checkpoints.
- checkpoint_validations: for the DETECTED phase (if any), validate each checkpoint requirement you can see. A checkpoint is MET when you visually confirm it. NOT_MET when you can see it is not satisfied. Only include checkpoints you can assess from the frames.
- error_codes: only report codes you can visually confirm from the frames. Do not flag based on assumptions.
- completion_evidence: specific visual proof if step_progress is "completed". null otherwise.
"""
    return prompt


def build_chunk_history_summary(
    chunk_history: List[dict],
    max_entries: int = 5,
) -> Optional[str]:
    """
    Build a brief history summary from previous chunk analysis results.
    Since results are now structured dicts, this is much cleaner than parsing text.
    """
    if not chunk_history:
        return None

    recent = chunk_history[-max_entries:]
    lines = []
    for idx, entry in enumerate(recent, start=1):
        step = entry.get("detected_step_number") or entry.get("detected_phase_number") or "?"
        progress = entry.get("step_progress", "?")
        summary = entry.get("analysis_summary", "")[:80]
        lines.append(f"  Chunk {idx}: Step/Phase {step} — {progress} — {summary}")

    return "\n".join(lines)
