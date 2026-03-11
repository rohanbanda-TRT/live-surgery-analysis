"""
LiveKit Surgical Analysis Agent — V4 pipeline.

Uses the official AgentServer + @rtc_session pattern (matches LiveKit Gemini Vision recipe).
Gemini Live API receives the surgeon's camera via WebRTC and responds with real-time analysis.

Run with:
    source venv/bin/activate
    python -m app.agents.livekit_surgical_agent dev

The FastAPI server (uvicorn) and this worker are two separate processes.
Both must be running for V4 to work.
"""
import os
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root before all else
_project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / ".env")

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    SpeechCreatedEvent,
    UserInputTranscribedEvent,
    AgentStateChangedEvent,
    cli,
    room_io,
)
from livekit.plugins import google, silero

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("surgical-livekit-agent")

# ── AgentServer (official pattern from LiveKit Gemini Vision recipe) ──────────
server = AgentServer()


# ── Prewarm: load VAD once per process for fast connections ───────────────────
def prewarm(proc: JobProcess):
    logger.info("[V4] prewarm: loading Silero VAD model...")
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("[V4] prewarm: VAD model ready")

server.setup_fnc = prewarm


# ── Agent class ───────────────────────────────────────────────────────────────
SYSTEM_INSTRUCTIONS = """You are a surgical video analyst with real-time vision capabilities.

CRITICAL RULES:
1. You MUST describe the SPECIFIC objects you see in the video frame (laptop, keyboard, hands, desk, wall, instruments, tissue, etc.)
2. NEVER say "no video feed available" or "video unavailable" — you ARE receiving video frames
3. DO NOT assume, infer, or narrate expected surgical steps
4. If you see non-surgical objects (desk, laptop, ceiling, etc.), describe them specifically
5. Be objective and literal — describe colors, shapes, objects you actually see

YOUR TASK:
- Look at the current video frame RIGHT NOW
- Name the specific objects visible (e.g., "laptop keyboard", "white wall", "hand holding pen", "surgical drape", "metal instrument")
- If surgical activity is visible, identify the step
- If you see everyday objects, say so explicitly
- Be concise (1-2 sentences)

RESPONSE FORMAT (mandatory):
OBSERVATION: [specific objects/scene you see: "laptop screen and keyboard visible" OR "surgical incision with retractor in place" OR "white ceiling tiles" etc.]
STEP: [surgical step name if surgery visible, or "none" if no surgery]
STATUS: [normal / deviation / alert / no-activity]

EXAMPLES:
- Laptop visible → "OBSERVATION: Laptop computer screen and keyboard in view | STEP: none | STATUS: no-activity"
- Desk scene → "OBSERVATION: Wooden desk surface with papers and pen | STEP: none | STATUS: no-activity"
- Hand in frame → "OBSERVATION: Human hand visible, no surgical field | STEP: none | STATUS: no-activity"
- Surgical field → "OBSERVATION: Scalpel making incision in lumbar region, retractor in place | STEP: skin incision | STATUS: normal"
- Wall/ceiling → "OBSERVATION: White wall or ceiling visible | STEP: none | STATUS: no-activity"

REMEMBER: You ARE receiving video. Describe what you see, even if it's just a wall or desk.
"""


class SurgicalAssistant(Agent):
    def __init__(self, procedure_name: str = "") -> None:
        instructions = SYSTEM_INSTRUCTIONS
        if procedure_name:
            instructions += f"\n\nPROCEDURE BEING MONITORED: {procedure_name}\n"
            instructions += "(Only mention this if you actually see surgical activity related to it.)\n"
        super().__init__(instructions=instructions)


# ── RTC session entrypoint (called when a room job is dispatched) ─────────────
@server.rtc_session(agent_name="surgical-analyst")
async def entrypoint(ctx: JobContext):
    import asyncio
    ctx.log_context_fields = {"room": ctx.room.name}

    logger.info("[V4] ── job_received ── room=%s", ctx.room.name)

    # ── Step 1: Connect agent to room first to read metadata ─────────────────
    logger.info("[V4] step=1 connecting to room...")
    await ctx.connect()
    logger.info("[V4] step=1 DONE — connected | participants=%d", len(ctx.room.remote_participants))

    # ── Step 2: Read procedure metadata embedded by token endpoint ────────────
    logger.info("[V4] step=2 reading room metadata...")
    try:
        metadata = json.loads(ctx.room.metadata or "{}")
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    procedure_name  = metadata.get("procedure_name", "")
    procedure_steps = metadata.get("procedure_steps_text", "")
    analysis_mode   = metadata.get("analysis_mode", "standard")

    logger.info(
        "[V4] step=2 DONE — procedure=%r | mode=%s",
        procedure_name, analysis_mode,
    )

    # ── Step 3: We intentionally DO NOT pass procedure_steps to the agent ─────
    # Reason: Passing expected steps causes Gemini to hallucinate/narrate them
    # instead of objectively describing what it sees in the video.
    # We only pass the procedure name so it knows the context but must still
    # ground its observations in actual visual input.

    # ── Step 4: Create Gemini Realtime model ──────────────────────────────────
    # IMPORTANT: If GOOGLE_APPLICATION_CREDENTIALS is set in the environment,
    # the google-genai SDK will ignore api_key and use Vertex AI instead.
    # We must unset it temporarily when using Google AI Studio (GOOGLE_API_KEY).
    logger.info("[V4] step=4 configuring Gemini Realtime model...")

    google_api_key = os.environ.get("GOOGLE_API_KEY", "")
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    use_vertex = bool(gac) and not google_api_key

    if google_api_key:
        # Force Google AI Studio — remove service account credentials from env
        # so the google-genai SDK cannot auto-detect Vertex AI
        if gac:
            logger.warning(
                "[V4] GOOGLE_APPLICATION_CREDENTIALS is set (%s) but GOOGLE_API_KEY "
                "takes priority — temporarily unsetting service account for this session.",
                gac,
            )
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

        logger.info("[V4] step=4 backend=Google-AI-Studio | key=...%s", google_api_key[-6:])
        realtime_model = google.realtime.RealtimeModel(
            api_key=google_api_key,
            vertexai=False,
            voice="Puck",
            proactivity=True,
            enable_affective_dialog=False,
        )
    elif use_vertex:
        project  = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        location = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
        logger.info("[V4] step=4 backend=Vertex-AI | project=%s | location=%s", project, location)
        realtime_model = google.realtime.RealtimeModel(
            vertexai=True,
            project=project,
            location=location,
            voice="Puck",
            proactivity=True,
            enable_affective_dialog=False,
        )
    else:
        raise RuntimeError(
            "No credentials found. Set GOOGLE_API_KEY in .env for Google AI Studio."
        )

    logger.info("[V4] step=4 DONE — model created")

    # ── Step 5: Create agent session ──────────────────────────────────────────
    logger.info("[V4] step=5 creating AgentSession (VAD + Gemini)...")
    session = AgentSession(
        llm=realtime_model,
        vad=ctx.proc.userdata["vad"],
    )
    logger.info("[V4] step=5 DONE — AgentSession ready")

    # ── Step 6: Attach structured logging hooks ───────────────────────────────
    @session.on("agent_state_changed")
    def _on_state(ev: AgentStateChangedEvent):
        logger.info("[V4-STATE] → %s", ev.new_state)

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev: UserInputTranscribedEvent):
        if ev.is_final:
            logger.info("[V4-VIDEO-TRANSCRIPT] surgeon: %s", ev.transcript)

    @session.on("speech_created")
    def _on_speech(ev: SpeechCreatedEvent):
        logger.info("[V4-GEMINI-REPLY] source=%s — Gemini is responding", ev.source)

    # ── Step 7: Start session — video-only (no audio from surgeon) ────────────
    # video_input=True  → agent receives camera frames from the room
    # audio_input=False → no audio from surgeon sent to Gemini (video-only as requested)
    # audio_output=True → Gemini speaks back (transcription is published to frontend)
    logger.info("[V4] step=7 starting session | video_input=True audio_input=False")
    await session.start(
        room=ctx.room,
        agent=SurgicalAssistant(procedure_name=procedure_name),
        room_options=room_io.RoomOptions(
            video_input=True,
            audio_input=False,
            audio_output=True,
        ),
    )
    logger.info("[V4] step=7 DONE — session started and wired to room")

    # ── Step 8: First Gemini response ────────────────────────────────────────
    # Video is sampled every 3s when no audio is present (per LiveKit docs).
    # We wait 6s to guarantee at least one full frame cycle before triggering.
    logger.info("[V4] step=8 waiting 6s for first video frame to be sampled...")
    await asyncio.sleep(6)

    logger.info("[V4] step=8 calling generate_reply() — first analysis...")
    try:
        await session.generate_reply(
            instructions=(
                "Look at the current video frame RIGHT NOW. "
                "Describe ONLY what you actually see - do not assume or infer. "
                "If you see a desk, laptop, or non-surgical scene, say 'OBSERVATION: [what you see] | STEP: none | STATUS: no-activity'. "
                "If you see surgical activity, describe it objectively."
            )
        )
        logger.info("[V4] step=8 DONE — first generate_reply() succeeded")
    except Exception as e:
        logger.warning("[V4] step=8 first generate_reply() failed: %s", e)

    logger.info(
        "[V4] ── agent_fully_active ── room=%s | procedure=%s | mode=%s",
        ctx.room.name, procedure_name, analysis_mode,
    )

    # ── Step 9: Periodic video analysis loop ──────────────────────────────────
    # Per LiveKit docs: for video-only non-conversational agents, use a timer
    # to periodically trigger LLM analysis since there is no audio VAD.
    # Video frames are sampled every 3s (no audio) — we analyze every 10s.
    analysis_interval = 10
    analysis_count = 0
    logger.info("[V4] step=9 starting periodic analysis loop (every %ds)...", analysis_interval)

    while True:
        await asyncio.sleep(analysis_interval)
        analysis_count += 1
        logger.info("[V4-LOOP] periodic_analysis #%d | room=%s", analysis_count, ctx.room.name)
        try:
            await asyncio.wait_for(
                session.generate_reply(
                    instructions=(
                        "Look at the current video frame. "
                        "Describe ONLY what you actually see right now. "
                        "Use the mandatory format: OBSERVATION: [...] | STEP: [...] | STATUS: [...]"
                    )
                ),
                timeout=30.0  # 30s timeout per analysis
            )
            logger.info("[V4-LOOP] analysis #%d complete", analysis_count)
        except asyncio.TimeoutError:
            logger.warning("[V4-LOOP] analysis #%d timed out after 30s", analysis_count)
        except Exception as e:
            logger.warning("[V4-LOOP] analysis #%d failed: %s", analysis_count, e)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"ERROR: missing env vars: {missing}")
    cli.run_app(server)
