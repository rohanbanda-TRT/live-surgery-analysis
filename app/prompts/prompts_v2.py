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

# Confidence threshold — steps below this score are not tracked
CONFIDENCE_THRESHOLD = 70


def build_standard_chunk_prompt(
    current_step: Dict[str, Any],
    detected_steps_cumulative: Set[int],
    procedure_steps: List[Dict[str, Any]],
    chunk_history_summary: Optional[str] = None,
    chunk_frame_count: int = 5,
) -> str:
    """
    Build the per-chunk dynamic prompt for standard mode.
    Lists ALL steps for confidence-based matching (no sequence assumption).
    """
    # All steps table (detected marked)
    all_steps_text = ""
    for i, s in enumerate(procedure_steps):
        num = s.get('step_number', i + 1)
        name = s['step_name']
        desc = s.get('description', '') or s.get('goal', '')
        status = " [ALREADY DETECTED]" if i in detected_steps_cumulative else ""
        desc_part = f" — {desc[:80]}" if desc else ""
        all_steps_text += f"\n  Step {num}: {name}{desc_part}{status}"

    prompt = f"""Analyze this video chunk from the live surgery.

ALL PROCEDURE STEPS ({len(procedure_steps)} total):{all_steps_text}

ALREADY DETECTED: {len(detected_steps_cumulative)}/{len(procedure_steps)} steps
"""
    if chunk_history_summary:
        prompt += f"\nRECENT CHUNK HISTORY:\n{chunk_history_summary}\n"

    prompt += """
YOUR TASK:
1. Compare the video against ALL steps above and find the ONE step most clearly being performed.
2. Assign a confidence_score (0-100) for how certain the match is:
   - 90-100: unmistakable visual evidence (specific instruments, anatomy, exact action)
   - 70-89: strong visual match (clear activity consistent with the step)
   - 50-69: ambiguous — some similarity but not conclusive
   - 0-49: no clear surgical activity, camera away, or cannot determine
3. If confidence_score < 70, set detected_step_number to null — do not guess.
4. confidence_reason: briefly explain what specific visual evidence led to your confidence score.

Fill the JSON schema:
- observation: one factual sentence describing the current surgical field.
- significant_change: true if scene is meaningfully different from previous chunk context. false if same.
- detected_step_number: 1-based step number with >= 70 confidence. null if uncertain.
- matches_expected: ignore sequence — set true only if step is a clear unambiguous match regardless of order.
- confidence_score: 0-100 integer.
- confidence_reason: brief visual evidence explanation.
- step_progress: "in-progress" unless you see definitive completion evidence.
- completion_evidence: specific visual proof if completed. null otherwise.
- action_observed: exactly what is visible — instruments, anatomy, surgeon action.
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
    Lists ALL phases with checkpoints for confidence-based matching.
    """
    # Build full phase list with checkpoints
    all_phases_text = ""
    for phase in remaining_phases:
        pn = phase.get('phase_number', '?')
        pname = phase.get('phase_name', '?')
        desc = phase.get('description', '') or phase.get('goal', '')
        desc_part = f" — {desc[:80]}" if desc else ""
        all_phases_text += f"\n  Phase {pn}: {pname}{desc_part}"
        checkpoints = phase.get('checkpoints', [])
        for cp in checkpoints:
            cp_name = cp.get('name', '')
            reqs = cp.get('requirements', [])
            for req in reqs:
                req_text = req.get('text', '') if isinstance(req, dict) else str(req)
                all_phases_text += f"\n    • [{cp_name}] {req_text}"

    detected_text_full = ", ".join(sorted(detected_phases)) if detected_phases else "None yet"

    prompt = f"""Analyze this video chunk from the live surgery (outlier resolution mode).

ALL PHASES TO DETECT:{all_phases_text}

ALREADY DETECTED PHASES: {detected_text_full}
"""
    if chunk_history_summary:
        prompt += f"\nRECENT CHUNK HISTORY:\n{chunk_history_summary}\n"

    prompt += """
YOUR TASK:
1. Compare the video against ALL phases above and find the ONE phase most clearly being performed.
2. Assign a confidence_score (0-100):
   - 90-100: unmistakable visual evidence matching the phase and its checkpoints
   - 70-89: strong visual match — activity clearly consistent with this phase
   - 50-69: ambiguous — some similarity but not conclusive
   - 0-49: no clear surgical activity, wrong angle, or cannot determine
3. If confidence_score < 70, set detected_phase_number to null — do not guess.
4. confidence_reason: briefly state what visual evidence supports or reduces your confidence.
5. For the DETECTED phase, validate each checkpoint requirement you can observe:
   - MET: visually confirmed the requirement is satisfied in this video
   - NOT_MET: can see it is NOT satisfied
   - Only include checkpoints you can actually assess from the video
6. error_codes: only flag codes you can visually confirm. Do not assume.

Fill the JSON schema:
- observation: one factual sentence describing the current surgical field.
- significant_change: true if scene meaningfully differs from previous chunk. false if same.
- detected_phase_number: phase number with >= 70 confidence. null if uncertain.
- confidence_score: 0-100 integer.
- confidence_reason: brief visual evidence explanation.
- matches_expected: set true if the detected phase matches any UNDETECTED phase (order doesn't matter).
- step_progress: "in-progress" unless you see definitive completion evidence.
- completion_evidence: specific visual proof if completed. null otherwise.
- action_observed: exactly what is visible — instruments, anatomy, surgeon action.
- checkpoint_validations: for detected phase only, one entry per assessable checkpoint requirement.
- error_codes: only visually confirmed codes.
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
