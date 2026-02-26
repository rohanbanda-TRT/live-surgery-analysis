"""
Chunked video comparison service for long surgical videos.

Strategy:
  - Videos > CHUNK_DURATION_SEC are split into time-based chunks
  - Each chunk uses Gemini's VideoMetadata clipping (no re-upload needed)
  - Overlap of OVERLAP_SEC between adjacent chunks to avoid missing transitions
  - Rolling history: each chunk receives the full analysis of ALL previous chunks
  - Timestamps in prompts are absolute (relative to the original video)
  - Final merge combines all chunk analyses into a single unified result

Gemini limits (Vertex AI, 2025):
  - Max video with audio: ~45 min
  - Max video without audio: ~1 hour
  - Safe chunk size: 20 min (leaves headroom for prompt + history tokens)
"""
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from datetime import datetime
from typing import Dict, Any, List, Optional
import math
import asyncio
import tempfile
import subprocess
import os

from google.cloud import storage
from app.services.gemini_client import GeminiClient
from app.services.recorded_video_comparison import RecordedVideoComparisonService
from app.core.logging import logger
from dotenv import load_dotenv
load_dotenv()
# ── Chunking configuration ──────────────────────────────────
CHUNK_DURATION_SEC = 20 * 60       # 20 minutes per chunk
OVERLAP_SEC = 60                    # 60 seconds overlap between chunks
SHORT_VIDEO_THRESHOLD_SEC = 25 * 60 # Videos <= 25 min go through original path
INTER_CHUNK_DELAY_SEC = 3           # Delay between chunks to avoid rate limits


async def _get_video_duration_from_gcs(video_gs_uri: str) -> Optional[float]:
    """
    Extract video duration from a GCS video file using ffprobe.
    
    Downloads the video temporarily and uses ffprobe to get the duration.
    Returns duration in seconds, or None if extraction fails.
    """
    try:
        # Parse GCS URI: gs://bucket-name/path/to/file.mp4
        if not video_gs_uri.startswith("gs://"):
            logger.warning("invalid_gcs_uri", uri=video_gs_uri)
            return None
        
        uri_parts = video_gs_uri[5:].split("/", 1)
        if len(uri_parts) != 2:
            logger.warning("malformed_gcs_uri", uri=video_gs_uri)
            return None
        
        bucket_name, blob_name = uri_parts
        
        logger.info(
            "fetching_video_duration_from_gcs",
            bucket=bucket_name,
            blob=blob_name,
        )
        
        # Download video to temporary file
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
            tmp_path = tmp_file.name
            blob.download_to_filename(tmp_path)
        
        try:
            # Use ffprobe to get duration
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    tmp_path
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0 and result.stdout.strip():
                duration = float(result.stdout.strip())
                logger.info(
                    "video_duration_extracted",
                    duration_sec=duration,
                    uri=video_gs_uri,
                )
                return duration
            else:
                logger.warning(
                    "ffprobe_failed",
                    returncode=result.returncode,
                    stderr=result.stderr,
                    uri=video_gs_uri,
                )
                return None
        finally:
            # Clean up temporary file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    
    except Exception as e:
        logger.error(
            "video_duration_extraction_failed",
            error=str(e),
            uri=video_gs_uri,
        )
        return None


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to H:MM:SS or MM:SS format for prompts."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def compute_chunk_windows(
    total_duration_sec: float,
    chunk_duration_sec: float = CHUNK_DURATION_SEC,
    overlap_sec: float = OVERLAP_SEC,
) -> List[Dict[str, float]]:
    """
    Compute time windows for chunked video analysis.

    Each window has start_sec, end_sec, and an overlap region with the
    previous chunk. The last chunk may be shorter than chunk_duration_sec.

    Returns:
        List of dicts with keys: index, start_sec, end_sec, overlap_start_sec
    """
    windows = []
    start = 0.0
    index = 0

    while start < total_duration_sec:
        end = min(start + chunk_duration_sec, total_duration_sec)

        # overlap_start_sec: the point where this chunk overlaps with the previous
        overlap_start = max(start - overlap_sec, 0.0) if index > 0 else start

        windows.append({
            "index": index,
            "start_sec": overlap_start if index > 0 else start,
            "end_sec": end,
            "logical_start_sec": start,  # non-overlapping logical boundary
        })

        # Advance by chunk_duration (not chunk_duration + overlap)
        start += chunk_duration_sec
        index += 1

    return windows


def _build_chunk_history_text(previous_analyses: List[Dict[str, Any]]) -> str:
    """
    Build a condensed rolling history string from previous chunk analyses.

    Each entry includes the chunk time range and the raw analysis text.
    This provides full context to subsequent chunks.
    """
    if not previous_analyses:
        return ""

    parts = []
    for prev in previous_analyses:
        time_range = prev.get("time_range", "")
        analysis = prev.get("analysis", "")
        # Truncate very long individual analyses to keep within token limits
        # ~4000 chars ≈ ~1000 tokens — reasonable per chunk summary
        if len(analysis) > 4000:
            analysis = analysis[:3900] + "\n... [truncated for context window] ..."
        parts.append(f"=== Chunk {prev['chunk_index'] + 1} ({time_range}) ===\n{analysis}")

    return "\n\n".join(parts)


class ChunkedVideoComparisonService:
    """
    Service for comparing long recorded videos against procedures using
    time-based chunking with overlap and rolling history.

    For short videos (<= SHORT_VIDEO_THRESHOLD_SEC), delegates to the
    original RecordedVideoComparisonService for backward compatibility.
    """

    def __init__(self, db: AsyncDatabase):
        self.db = db
        self.gemini_client = GeminiClient()
        # Reuse the original service for short videos and for result processing
        self._original_service = RecordedVideoComparisonService(db)

    async def compare_video(
        self,
        video_gs_uri: str,
        procedure_id: str,
        procedure_source: str = "standard",
        video_duration_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Compare a recorded video against a procedure, using chunking for long videos.

        Args:
            video_gs_uri: GCS URI of the recorded video
            procedure_id: ID of the master or outlier procedure
            procedure_source: "standard" or "outlier"
            video_duration_sec: Duration of the video in seconds (optional - will be fetched if not provided)

        Returns:
            Complete comparison results (same shape as RecordedVideoComparisonService)
        """
        # If duration not provided, try to fetch it from GCS
        if video_duration_sec is None:
            logger.info(
                "video_duration_not_provided_fetching_from_gcs",
                video_uri=video_gs_uri,
            )
            video_duration_sec = await _get_video_duration_from_gcs(video_gs_uri)
            
            if video_duration_sec is None:
                logger.warning(
                    "video_duration_fetch_failed_using_original_service",
                    video_uri=video_gs_uri,
                )
                # Fall back to original service if we can't get duration
                return await self._original_service.compare_video(
                    video_gs_uri=video_gs_uri,
                    procedure_id=procedure_id,
                    procedure_source=procedure_source,
                )
        
        # If video is short, use original service
        if video_duration_sec <= SHORT_VIDEO_THRESHOLD_SEC:
            logger.info(
                "chunked_comparison_delegating_to_original",
                video_uri=video_gs_uri,
                duration_sec=video_duration_sec,
                reason="short_video_or_no_duration",
            )
            return await self._original_service.compare_video(
                video_gs_uri=video_gs_uri,
                procedure_id=procedure_id,
                procedure_source=procedure_source,
            )

        # Long video — use chunked approach
        logger.info(
            "starting_chunked_video_comparison",
            video_uri=video_gs_uri,
            procedure_id=procedure_id,
            procedure_source=procedure_source,
            video_duration_sec=video_duration_sec,
        )

        # Load procedure (reuse from original service)
        procedure, procedure_steps = await self._original_service._load_procedure(
            procedure_id, procedure_source
        )

        # Compute chunk windows
        windows = compute_chunk_windows(video_duration_sec)
        total_chunks = len(windows)

        logger.info(
            "chunked_comparison_plan",
            total_chunks=total_chunks,
            chunk_duration_sec=CHUNK_DURATION_SEC,
            overlap_sec=OVERLAP_SEC,
            windows=[
                {"i": w["index"], "start": w["start_sec"], "end": w["end_sec"]}
                for w in windows
            ],
        )

        # Sequentially analyze each chunk with rolling history
        chunk_analyses: List[Dict[str, Any]] = []

        for window in windows:
            chunk_index = window["index"]
            start_sec = window["start_sec"]
            end_sec = window["end_sec"]
            logical_start = window["logical_start_sec"]
            time_range = f"{_format_timestamp(start_sec)} – {_format_timestamp(end_sec)}"

            # Build the rolling history from all previous chunks
            history_text = _build_chunk_history_text(chunk_analyses)

            # Build prompt for this chunk
            if procedure_source == "outlier":
                prompt = self._build_outlier_chunk_prompt(
                    procedure=procedure,
                    procedure_steps=procedure_steps,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    logical_start_sec=logical_start,
                    history_text=history_text,
                )
            else:
                prompt = self._build_standard_chunk_prompt(
                    procedure=procedure,
                    procedure_steps=procedure_steps,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    logical_start_sec=logical_start,
                    history_text=history_text,
                )

            logger.info(
                "┌─ STARTING CHUNK ANALYSIS ─────────────────────────────────",
                chunk_index=chunk_index + 1,
                total_chunks=total_chunks,
                time_range=time_range,
                prompt_length=len(prompt),
                history_chunks_count=len(chunk_analyses),
                video_uri=video_gs_uri,
            )

            # Call Gemini with clipped video segment (SEQUENTIAL - waits for response)
            try:
                analysis = await self.gemini_client.analyze_video_clipped(
                    video_gs_uri=video_gs_uri,
                    prompt=prompt,
                    start_offset_sec=start_sec,
                    end_offset_sec=end_sec,
                    temperature=0.2,
                )
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                    logger.error(
                        "chunk_rate_limit",
                        chunk_index=chunk_index,
                        error=error_msg,
                    )
                    raise ValueError(
                        f"API rate limit exceeded on chunk {chunk_index + 1}/{total_chunks}. "
                        "Please wait and try again."
                    )
                logger.error(
                    "chunk_analysis_failed",
                    chunk_index=chunk_index,
                    error=error_msg,
                )
                raise ValueError(f"Chunk {chunk_index + 1} analysis failed: {error_msg}")

            chunk_analyses.append({
                "chunk_index": chunk_index,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "logical_start_sec": logical_start,
                "time_range": time_range,
                "analysis": analysis,
            })

            logger.info(
                "└─ CHUNK COMPLETED ✓ ──────────────────────────────────────",
                chunk_index=chunk_index + 1,
                total_chunks=total_chunks,
                time_range=time_range,
                response_length=len(analysis),
                progress=f"{chunk_index + 1}/{total_chunks}",
            )

            # Add delay between chunks to avoid rate limits (except after last chunk)
            if chunk_index < total_chunks - 1:
                logger.info(
                    "⏱  WAITING BEFORE NEXT CHUNK",
                    current_chunk=chunk_index + 1,
                    next_chunk=chunk_index + 2,
                    delay_seconds=INTER_CHUNK_DELAY_SEC,
                    reason="rate_limit_prevention",
                )
                await asyncio.sleep(INTER_CHUNK_DELAY_SEC)

        # Merge all chunk analyses into final result
        merged_analysis = self._merge_chunk_analyses(chunk_analyses)

        # Process results using the original service's parsing logic
        if procedure_source == "outlier":
            results = await self._original_service._process_outlier_results(
                merged_analysis, procedure, procedure_steps
            )
        else:
            results = await self._original_service._process_standard_results(
                merged_analysis, procedure, procedure_steps
            )

        # Add chunking metadata
        results["chunking_metadata"] = {
            "was_chunked": True,
            "total_chunks": total_chunks,
            "chunk_duration_sec": CHUNK_DURATION_SEC,
            "overlap_sec": OVERLAP_SEC,
            "video_duration_sec": video_duration_sec,
            "chunks": [
                {
                    "index": c["chunk_index"],
                    "time_range": c["time_range"],
                    "start_sec": c["start_sec"],
                    "end_sec": c["end_sec"],
                }
                for c in chunk_analyses
            ],
        }

        logger.info(
            "chunked_video_comparison_completed",
            video_uri=video_gs_uri,
            total_chunks=total_chunks,
            video_duration_sec=video_duration_sec,
        )

        return results

    # ──────────────────────────────────────────────
    # Prompt builders for chunked analysis
    # ──────────────────────────────────────────────

    def _build_standard_chunk_prompt(
        self,
        procedure: Dict[str, Any],
        procedure_steps: List[Dict[str, Any]],
        chunk_index: int,
        total_chunks: int,
        start_sec: float,
        end_sec: float,
        logical_start_sec: float,
        history_text: str,
    ) -> str:
        """Build prompt for a single chunk in standard mode."""
        procedure_name = procedure.get("procedure_name", "Unknown Procedure")
        time_range = f"{_format_timestamp(start_sec)} – {_format_timestamp(end_sec)}"
        logical_range = f"{_format_timestamp(logical_start_sec)} – {_format_timestamp(end_sec)}"

        # Build steps list
        steps_list = []
        for i, step in enumerate(procedure_steps, 1):
            step_info = (
                f"Step {step.get('step_number', i)}: {step['step_name']}\n"
                f"- Description: {step.get('description', 'N/A')}\n"
                f"- Expected Duration: {step.get('expected_duration_min', 'N/A')}-"
                f"{step.get('expected_duration_max', 'N/A')} minutes\n"
                f"- Critical: {'YES' if step.get('is_critical') else 'No'}\n"
                f"- Required Instruments: "
                f"{', '.join(step.get('instruments_required', [])) or 'Not specified'}\n"
                f"- Anatomical Landmarks: "
                f"{', '.join(step.get('anatomical_landmarks', [])) or 'Not specified'}"
            )
            steps_list.append(step_info)
        steps_context = "\n\n".join(steps_list)

        prompt = f"""You are analyzing CHUNK {chunk_index + 1} of {total_chunks} from a recorded surgical video of: {procedure_name}

**VIDEO SEGMENT:**
- Time window: {time_range} (absolute timestamps from the original video)
- Logical boundary (non-overlapping): {logical_range}
- This is chunk {chunk_index + 1} of {total_chunks}

**IMPORTANT TIMESTAMP INSTRUCTIONS:**
- All timestamps you report MUST be ABSOLUTE (relative to the start of the full video, not this chunk)
- The video segment you see starts at {_format_timestamp(start_sec)} in the original video
- For example, if you see something 3 minutes into this chunk, the absolute timestamp is {_format_timestamp(start_sec + 180)}

**MASTER PROCEDURE STEPS:**
{steps_context}
"""

        if history_text:
            prompt += f"""
**ANALYSIS FROM PREVIOUS CHUNKS:**
The following is the complete analysis from prior chunks. Use this context to:
1. Avoid re-reporting steps already fully detected in previous chunks
2. Continue tracking steps that were partially detected
3. Maintain continuity of the overall procedure analysis

{history_text}
"""
        else:
            prompt += """
**NOTE:** This is the FIRST chunk — no prior analysis history is available.
"""

        prompt += f"""
**YOUR TASK:**
Analyze this video segment ({time_range}) and determine which steps from the master procedure are visible.

For EACH step you can observe in THIS chunk, provide:

Step [number]: [name]
Detected: [YES/NO]
Evidence: [specific observations from this video segment]
Timestamp: [absolute time range in the original video, e.g., "{_format_timestamp(start_sec + 30)}-{_format_timestamp(start_sec + 300)}"]
Completion: [COMPLETED/PARTIAL/NOT_PERFORMED]
Notes: [any deviations, continuations from previous chunks, or observations]

---

After analyzing all visible steps, provide:

**CHUNK SUMMARY:**
- Chunk: {chunk_index + 1} of {total_chunks}
- Time Window: {time_range}
- Steps Detected in This Chunk: [number]
- Steps Completed in This Chunk: [number]

**CRITICAL OBSERVATIONS:**
[Any critical deviations, skipped steps, or safety concerns observed in this chunk]
"""
        return prompt

    def _build_outlier_chunk_prompt(
        self,
        procedure: Dict[str, Any],
        procedure_steps: List[Dict[str, Any]],
        chunk_index: int,
        total_chunks: int,
        start_sec: float,
        end_sec: float,
        logical_start_sec: float,
        history_text: str,
    ) -> str:
        """Build prompt for a single chunk in outlier mode."""
        from app.prompts.outlier_prompts import build_outlier_resolution_context

        procedure_name = procedure.get("procedure_name", "Unknown Procedure")
        time_range = f"{_format_timestamp(start_sec)} – {_format_timestamp(end_sec)}"
        logical_range = f"{_format_timestamp(logical_start_sec)} – {_format_timestamp(end_sec)}"
        phases_context = build_outlier_resolution_context(procedure)

        prompt = f"""You are analyzing CHUNK {chunk_index + 1} of {total_chunks} from a recorded surgical video using the Outlier Resolution Protocol.

**VIDEO SEGMENT:**
- Time window: {time_range} (absolute timestamps from the original video)
- Logical boundary (non-overlapping): {logical_range}
- This is chunk {chunk_index + 1} of {total_chunks}

**IMPORTANT TIMESTAMP INSTRUCTIONS:**
- All timestamps you report MUST be ABSOLUTE (relative to the start of the full video, not this chunk)
- The video segment you see starts at {_format_timestamp(start_sec)} in the original video

{phases_context}
"""

        if history_text:
            prompt += f"""
**ANALYSIS FROM PREVIOUS CHUNKS:**
The following is the complete analysis from prior chunks. Use this context to:
1. Avoid re-reporting phases already fully detected
2. Continue tracking phases/checkpoints that were partially detected
3. Track cumulative error codes across all chunks

{history_text}
"""
        else:
            prompt += """
**NOTE:** This is the FIRST chunk — no prior analysis history is available.
"""

        prompt += f"""
**YOUR TASK:**
Analyze this video segment ({time_range}) and determine:
1. Which phases are visible in this chunk
2. Which checkpoints can be validated
3. Which error codes (A1-A10, C1-C6, R1-R3) are detected

**FOR EACH PHASE visible in this chunk:**

Phase [number]: [name]
Detected: [YES/NO]
Evidence: [specific visual evidence from this video segment]
Timestamp: [absolute time range in the original video]

**CHECKPOINT VALIDATION:**
For each checkpoint in this phase:
- [Checkpoint name]: [MET/NOT MET] - [Evidence]

**ERROR CODES DETECTED:**
- [List any error codes observed in this chunk]

**PHASE COMPLETION:**
- Status: [COMPLETED/PARTIAL/NOT_PERFORMED]
- Blocking Issues: [Any blocking checkpoints not met]

---

After analyzing all visible phases, provide:

**CHUNK SUMMARY:**
- Chunk: {chunk_index + 1} of {total_chunks}
- Time Window: {time_range}
- Phases Detected: [number]
- Phases Completed: [number]
- Checkpoints Met: [number]
- Error Codes: [list]

**CRITICAL SAFETY ISSUES:**
[List any HIGH priority errors or safety concerns in this chunk]
"""
        return prompt

    # ──────────────────────────────────────────────
    # Merge chunk analyses into final text
    # ──────────────────────────────────────────────

    def _merge_chunk_analyses(
        self,
        chunk_analyses: List[Dict[str, Any]],
    ) -> str:
        """
        Merge all chunk analyses into a single text suitable for the
        original service's regex-based result parsers.

        The merged text concatenates all chunk analyses so that the
        existing _process_standard_results / _process_outlier_results
        parsers can find Step/Phase patterns across the full video.
        """
        parts = []
        for chunk in chunk_analyses:
            parts.append(
                f"\n{'='*60}\n"
                f"CHUNK {chunk['chunk_index'] + 1} "
                f"({chunk['time_range']})\n"
                f"{'='*60}\n"
                f"{chunk['analysis']}"
            )
        return "\n".join(parts)
