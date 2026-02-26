"""
OpenAI GPT-4o/4.1-mini vision client for surgical video analysis (V3 pipeline).

Architecture:
  - Sends 7 JPEG frames per API call as base64 image_url content parts
  - detail="low" by default → 85 tokens/image → ~$0.0005/chunk at gpt-4.1-mini rates
  - detail="high" available for precision tasks → ~1700 tokens/image
  - Structured JSON output via response_format={type:"json_schema"} — guaranteed valid
  - System message carries static procedure context (set once, reused every chunk call)
  - Previous chunk results passed as text summary → model always has rolling context

Context continuity strategy:
  Every API call is stateless, but we inject the last N chunk results as a text summary
  in the user message. This gives the model memory of what happened in previous chunks
  without accumulating frames (which would explode token cost).

Cost vs Gemini 2.5 Flash (7 frames, detail:low, ~200 token JSON output):
  gpt-4.1-mini  : ~$0.0005/chunk  → ~$0.25/hour at 1 FPS
  gpt-4o        : ~$0.003/chunk   → ~$1.55/hour at 1 FPS
  gemini-2.5-fl : ~$0.0007/chunk  → ~$0.36/hour at 1 FPS
"""
from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI
from typing import Optional, List, Any
import base64
import json

from app.core.config import settings
from app.core.logging import logger


class OpenAIClientV2:
    """GPT-4o/4.1-mini vision client for multi-frame structured surgical analysis."""

    # Models that support response_format json_schema (structured output)
    SUPPORTED_MODELS = {
        "gpt-4o",
        "gpt-4o-2024-08-06",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    }

    def __init__(self, model: Optional[str] = None):
        if not settings.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is not set. Add it to your .env file as OPENAI_API_KEY=sk-..."
            )
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = model or settings.OPENAI_MODEL
        self.temperature = settings.OPENAI_TEMPERATURE

        logger.info(
            "openai_client_v2_initialized",
            model=self.model,
            temperature=self.temperature,
        )

    # ──────────────────────────────────────────────
    # Core: Multi-frame structured analysis
    # ──────────────────────────────────────────────

    async def analyze_frames_structured(
        self,
        frames: List[bytes],
        prompt: str,
        response_schema: dict,
        system_instruction: Optional[str] = None,
        detail: str = "low",
        temperature: Optional[float] = None,
    ) -> dict:
        """
        Analyze multiple JPEG frames with guaranteed structured JSON output.

        Args:
            frames: List of JPEG frame bytes (typically 7 frames)
            prompt: Dynamic per-chunk prompt including rolling history summary
            response_schema: JSON Schema dict (from Pydantic .model_json_schema())
            system_instruction: Static procedure context (set once per session)
            detail: "low" (85 tok/img, fast, cheap) or "high" (1700 tok/img, precise)
            temperature: Override temperature

        Returns:
            Parsed dict guaranteed to match response_schema
        """
        try:
            # Build content array: images first, then the text prompt
            content: List[Any] = []
            for frame_data in frames:
                b64 = base64.b64encode(frame_data).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                        "detail": detail,
                    },
                })
            content.append({"type": "text", "text": prompt})

            # Build messages
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": content})

            # Strip unsupported keys from schema (OpenAI is strict)
            clean_schema = _strip_unsupported_schema_keys(response_schema)

            logger.info(
                "openai_analyzing_frames",
                frame_count=len(frames),
                model=self.model,
                detail=detail,
                prompt_length=len(prompt),
                has_system_instruction=bool(system_instruction),
            )

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature if temperature is not None else self.temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "surgical_analysis",
                        "strict": True,
                        "schema": clean_schema,
                    },
                },
            )

            raw = response.choices[0].message.content
            result = json.loads(raw)

            logger.info(
                "openai_analysis_completed",
                frame_count=len(frames),
                model=self.model,
                usage_input=response.usage.prompt_tokens if response.usage else 0,
                usage_output=response.usage.completion_tokens if response.usage else 0,
                response_keys=list(result.keys()),
            )
            return result

        except json.JSONDecodeError as e:
            logger.error(
                "openai_json_parse_failed",
                error=str(e),
                response_preview=raw[:500] if "raw" in dir() else "N/A",
            )
            raise
        except Exception as e:
            logger.error(
                "openai_analysis_failed",
                frame_count=len(frames),
                model=self.model,
                error=str(e),
            )
            raise


# ──────────────────────────────────────────────
# Schema cleanup helper
# ──────────────────────────────────────────────

def _strip_unsupported_schema_keys(schema: dict) -> dict:
    """
    Prepare a Pydantic-generated JSON schema for OpenAI strict json_schema mode.

    OpenAI strict mode rules:
      - Strips: 'title', 'default', 'examples'  (NOT 'description' — enums need it)
      - All $ref references must be inlined (no $defs allowed at top level)
      - Every object must have additionalProperties: false
      - Every property key in an object must appear in 'required'
    """
    DISALLOWED = {"title", "default", "examples"}

    # Extract $defs for reference resolution
    defs = schema.get("$defs", {})

    def resolve_refs(node: Any) -> Any:
        """Recursively resolve $ref references using $defs."""
        if isinstance(node, dict):
            if "$ref" in node:
                ref_path = node["$ref"]
                # Format: "#/$defs/ModelName"
                if ref_path.startswith("#/$defs/"):
                    def_name = ref_path[len("#/$defs/"):]
                    if def_name in defs:
                        return resolve_refs(defs[def_name])
                return node
            return {k: resolve_refs(v) for k, v in node.items()}
        elif isinstance(node, list):
            return [resolve_refs(i) for i in node]
        return node

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            # Resolve any $ref first
            node = resolve_refs(node)
            cleaned = {}
            for k, v in node.items():
                if k in DISALLOWED or k == "$defs":
                    continue
                cleaned[k] = clean(v)
            # OpenAI strict mode: objects must have additionalProperties: false
            if cleaned.get("type") == "object":
                cleaned["additionalProperties"] = False
                # All properties must be in 'required' for strict mode
                props = cleaned.get("properties", {})
                if props:
                    cleaned["required"] = list(props.keys())
            return cleaned
        elif isinstance(node, list):
            return [clean(i) for i in node]
        return node

    return clean(schema)
