"""
OpenAI-powered live surgery monitoring service (V3 pipeline).

Inherits from LiveSurgeryServiceV2 and overrides only the AI client calls.
All state management, chunk buffering, compliance checking, and frontend callbacks
are identical to V2 — only the underlying model changes from Gemini to GPT-4.1-mini.

Architecture differences from V2:
  - Uses OpenAIClientV2 instead of GeminiClientV2
  - Schema is pre-cleaned for OpenAI strict mode (no $defs, all required listed)
  - system_instruction passed as OpenAI "system" role message (same concept, different SDK)
  - detail="low" → 85 tokens/image → cost-efficient for 1 FPS surgical streams
"""
from typing import Optional, Dict, Any

from app.services.live_surgery_v2 import LiveSurgeryServiceV2
from app.services.openai_client_v2 import OpenAIClientV2
from app.services.analysis_schemas import (
    get_standard_chunk_schema,
    get_outlier_chunk_schema,
)
from app.prompts.prompts_v2 import (
    build_standard_chunk_prompt,
    build_outlier_chunk_prompt,
)
from app.core.logging import logger


class LiveSurgeryServiceV3(LiveSurgeryServiceV2):
    """
    OpenAI GPT-4.1-mini powered surgery monitoring service.

    Inherits all state management, frame buffering, chunk queue, compliance
    checking, and frontend callbacks from LiveSurgeryServiceV2.
    Only _analyze_standard_chunk and _analyze_outlier_chunk are overridden
    to use OpenAIClientV2 instead of GeminiClientV2.
    """

    def __init__(self, db, session_id: str):
        super().__init__(db, session_id)
        # Replace Gemini client with OpenAI client
        self.openai_client = OpenAIClientV2()
        # Pre-clean schemas once (avoids repeated cleanup per chunk)
        self._standard_schema_clean: Optional[dict] = None
        self._outlier_schema_clean: Optional[dict] = None
        logger.info("live_surgery_service_v3_initialized", session_id=session_id)

    # ──────────────────────────────────────────────
    # Override: Standard chunk analysis via OpenAI
    # ──────────────────────────────────────────────

    async def _analyze_standard_chunk(
        self,
        chunk_data: Dict[str, Any],
        current_step: Dict[str, Any],
        history_summary: Optional[str],
    ) -> dict:
        """Run OpenAI structured analysis for standard mode."""
        prompt = build_standard_chunk_prompt(
            current_step=current_step,
            detected_steps_cumulative=self.detected_steps_cumulative,
            procedure_steps=self.procedure_steps,
            chunk_history_summary=history_summary,
            chunk_frame_count=len(chunk_data["frames"]),
        )

        if self._standard_schema_clean is None:
            self._standard_schema_clean = get_standard_chunk_schema()

        return await self.openai_client.analyze_frames_structured(
            frames=chunk_data["frames"],
            prompt=prompt,
            response_schema=self._standard_schema_clean,
            system_instruction=self._system_instruction,
            detail="low",
        )

    # ──────────────────────────────────────────────
    # Override: Outlier chunk analysis via OpenAI
    # ──────────────────────────────────────────────

    async def _analyze_outlier_chunk(
        self,
        chunk_data: Dict[str, Any],
        history_summary: Optional[str],
    ) -> dict:
        """Run OpenAI structured analysis for outlier mode."""
        detected_phase_numbers = {
            self.procedure_steps[i].get("phase_number")
            for i in self.detected_steps_cumulative
        }
        remaining_phases = [
            phase
            for i, phase in enumerate(self.outlier_procedure.get("phases", []))
            if i not in self.detected_steps_cumulative
        ]
        next_undetected = next(
            (i for i in range(len(self.procedure_steps)) if i not in self.detected_steps_cumulative),
            None,
        )
        current_phase = (
            self.outlier_procedure["phases"][next_undetected]
            if next_undetected is not None
            else None
        )

        prompt = build_outlier_chunk_prompt(
            detected_phases=detected_phase_numbers,
            remaining_phases=remaining_phases,
            current_phase=current_phase,
            chunk_history_summary=history_summary,
            chunk_frame_count=len(chunk_data["frames"]),
        )

        if self._outlier_schema_clean is None:
            self._outlier_schema_clean = get_outlier_chunk_schema()

        return await self.openai_client.analyze_frames_structured(
            frames=chunk_data["frames"],
            prompt=prompt,
            response_schema=self._outlier_schema_clean,
            system_instruction=self._system_instruction,
            detail="low",
        )
