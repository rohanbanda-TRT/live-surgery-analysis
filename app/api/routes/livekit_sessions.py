"""
API routes for LiveKit-based live surgery sessions (V4 pipeline).

Provides:
  - POST /token — Generate a LiveKit room token for the frontend to join
  - GET  /procedures — Fetch procedures (reuses existing DB)
  - GET  /{session_id}/alerts — Fetch session alerts

The actual AI analysis happens in the LiveKit Agent worker
(app/agents/livekit_surgical_agent.py), NOT in this FastAPI process.
This endpoint only handles token generation and room metadata setup.
"""
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId

from app.db.mongodb import get_db
from app.db.collections import SESSION_ALERTS
from app.schemas.procedure import SessionAlertResponse
from app.core.config import settings
from app.core.logging import logger

router = APIRouter()

# ──────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────

class LiveKitTokenRequest(BaseModel):
    """Request body for generating a LiveKit room token."""
    session_id: str = Field(..., description="Unique session identifier")
    procedure_id: str = Field(..., description="ID of the procedure to monitor")
    surgeon_id: str = Field(default="surgeon-001", description="Surgeon identifier")
    procedure_source: str = Field(default="standard", description="'standard' or 'outlier'")
    participant_name: str = Field(default="Surgeon", description="Display name in the room")


class LiveKitTokenResponse(BaseModel):
    """Response with LiveKit connection details."""
    token: str
    livekit_url: str
    room_name: str
    participant_identity: str
    procedure_name: str
    procedure_source: str
    total_steps: int

# ──────────────────────────────────────────────
# Token generation endpoint
# ──────────────────────────────────────────────

@router.post("/token", response_model=LiveKitTokenResponse)
async def generate_livekit_token(
    request: LiveKitTokenRequest,
    db: AsyncDatabase = Depends(get_db),
):
    """
    Generate a LiveKit access token for a surgery monitoring session.

    This creates a room with procedure metadata embedded so the
    LiveKit Agent worker can read it and configure its analysis context.
    """
    # Validate LiveKit credentials are configured
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET or not settings.LIVEKIT_URL:
        raise HTTPException(
            status_code=503,
            detail="LiveKit is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET in .env"
        )

    # Import livekit-api for token generation
    try:
        from livekit.api import AccessToken, VideoGrants
        from livekit.protocol.room import RoomConfiguration
        from livekit.protocol.agent_dispatch import RoomAgentDispatch
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="livekit-api package not installed. Run: pip install livekit-api"
        )

    # Fetch procedure from DB to embed context in room metadata
    procedure_name = ""
    procedure_steps_text = ""
    total_steps = 0

    if request.procedure_source == "outlier":
        proc = await db["outlier_procedures"].find_one({"_id": ObjectId(request.procedure_id)})
        if not proc:
            raise HTTPException(status_code=404, detail="Outlier procedure not found")

        procedure_name = proc.get("procedure_name", "Unknown")
        phases = proc.get("phases", [])
        total_steps = len(phases)

        lines = []
        for phase in phases:
            pn = phase.get("phase_number", "?")
            pname = phase.get("phase_name", "?")
            goal = phase.get("goal", "")
            priority = phase.get("priority", "")
            lines.append(f"Phase {pn}: {pname} (Priority: {priority})")
            if goal:
                lines.append(f"  Goal: {goal}")
            for cp in phase.get("checkpoints", []):
                lines.append(f"  Checkpoint: {cp.get('name', '?')}")
                for req in cp.get("requirements", []):
                    lines.append(f"    - {req}")
        procedure_steps_text = "\n".join(lines)
    else:
        proc = await db["master_procedures"].find_one({"_id": ObjectId(request.procedure_id)})
        if not proc:
            raise HTTPException(status_code=404, detail="Master procedure not found")

        procedure_name = proc.get("procedure_name", "Unknown")
        steps = proc.get("steps", [])
        total_steps = len(steps)

        lines = []
        for step in steps:
            sn = step.get("step_number", "?")
            sname = step.get("step_name", "?")
            desc = step.get("description", "")
            critical = " [CRITICAL]" if step.get("is_critical") else ""
            lines.append(f"Step {sn}: {sname}{critical}")
            if desc:
                lines.append(f"  Description: {desc}")
            if step.get("instruments_required"):
                lines.append(f"  Instruments: {', '.join(step['instruments_required'])}")
        procedure_steps_text = "\n".join(lines)

    # Build room metadata (the agent reads this to configure analysis)
    room_metadata = json.dumps({
        "procedure_id": request.procedure_id,
        "procedure_name": procedure_name,
        "procedure_source": request.procedure_source,
        "procedure_steps_text": procedure_steps_text,
        "surgeon_id": request.surgeon_id,
        "session_id": request.session_id,
        "analysis_mode": "error-resolution" if request.procedure_source == "outlier" else "standard",
        "total_steps": total_steps,
    })

    # Create room name
    room_name = f"surgery-{request.session_id}-{int(time.time())}"
    participant_identity = f"surgeon-{request.surgeon_id}-{uuid.uuid4().hex[:6]}"

    # Generate LiveKit access token
    token = (
        AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(participant_identity)
        .with_name(request.participant_name)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                room_create=True,
            )
        )
        .with_room_config(
            RoomConfiguration(
                metadata=room_metadata,
                agents=[
                    RoomAgentDispatch(agent_name="surgical-analyst"),
                ],
            )
        )
    )

    jwt_token = token.to_jwt()

    logger.info(
        "livekit_token_generated",
        room=room_name,
        procedure=procedure_name,
        source=request.procedure_source,
        identity=participant_identity,
    )

    return LiveKitTokenResponse(
        token=jwt_token,
        livekit_url=settings.LIVEKIT_URL,
        room_name=room_name,
        participant_identity=participant_identity,
        procedure_name=procedure_name,
        procedure_source=request.procedure_source,
        total_steps=total_steps,
    )

# ──────────────────────────────────────────────
# Alerts endpoint (same pattern as other session routes)
# ──────────────────────────────────────────────

@router.get("/{session_id}/alerts", response_model=List[SessionAlertResponse])
async def get_session_alerts(
    session_id: str,
    db: AsyncDatabase = Depends(get_db),
):
    """Get all alerts for a specific LiveKit session."""
    cursor = db[SESSION_ALERTS].find({"session_id": session_id}).sort("timestamp", -1)
    alerts = await cursor.to_list(length=None)

    for alert in alerts:
        alert["id"] = str(alert.pop("_id"))
        if isinstance(alert.get("session_id"), ObjectId):
            alert["session_id"] = str(alert["session_id"])

    return alerts