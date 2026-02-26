"""
API routes for live surgery sessions (v2 — optimized).

Uses LiveSurgeryServiceV2 which eliminates:
  - ffmpeg video creation (sends frames as images)
  - Regex-based response parsing (uses structured JSON output)
  - Dead code paths
  - Duplicated callback building logic

Frontend contract is fully backward-compatible with sessions.py.
Mount this at /api/sessions-v2 for parallel testing, or replace sessions.py.
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from typing import List

from app.db.mongodb import get_db
from app.db.collections import LIVE_SESSIONS, SESSION_ALERTS
from app.schemas.procedure import SessionAlertResponse
from app.services.live_surgery_v2 import LiveSurgeryServiceV2
from app.core.logging import logger

router = APIRouter()


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    db: AsyncDatabase = Depends(get_db),
):
    """
    WebSocket endpoint for real-time surgical monitoring (v2).
    Same frontend contract as sessions.py — drop-in replacement.
    """
    await websocket.accept()
    logger.info("websocket_connected_v2", session_id=session_id)

    service = None

    try:
        # Receive initial configuration
        init_data = await websocket.receive_json()
        procedure_id = init_data.get("procedure_id")
        surgeon_id = init_data.get("surgeon_id", "default-surgeon")
        procedure_source = init_data.get("procedure_source", "standard")

        if not procedure_id:
            await websocket.send_json({"error": "procedure_id required"})
            await websocket.close()
            return

        # Create v2 service
        service = LiveSurgeryServiceV2(db, session_id)

        # Define callbacks (same contract as v1)
        async def send_alerts(alerts):
            await websocket.send_json({"type": "alerts", "data": alerts})

        async def send_analysis_update(analysis_data):
            await websocket.send_json({"type": "analysis_update", "data": analysis_data})

        # Start session
        await service.start_session(
            procedure_id=procedure_id,
            surgeon_id=surgeon_id,
            procedure_source=procedure_source,
            alert_callback=send_alerts,
            analysis_callback=send_analysis_update,
        )

        # Build session_started response (same format as v1)
        if procedure_source == "outlier":
            if not service.outlier_procedure:
                await websocket.send_json({"error": "Outlier procedure not found"})
                await websocket.close()
                return

            phases_data = []
            for i, phase in enumerate(service.outlier_procedure.get("phases", [])):
                phase_number = phase["phase_number"]
                checkpoint_info = (
                    service.checkpoint_tracker.get_phase_checkpoint_status(phase_number)
                    if service.checkpoint_tracker
                    else {}
                )
                phase_data = {
                    "step_number": phase_number,
                    "step_name": phase["phase_name"],
                    "phase_number": phase_number,
                    "phase_name": phase["phase_name"],
                    "description": phase.get("goal"),
                    "goal": phase.get("goal"),
                    "priority": phase.get("priority"),
                    "is_critical": phase.get("priority") == "HIGH",
                    "status": "pending",
                    "detected": False,
                    "checkpoints": checkpoint_info.get("checkpoints", []),
                    "detected_errors": [],
                }
                phases_data.append(phase_data)

            session_data = {
                "procedure_name": service.outlier_procedure.get("procedure_name"),
                "procedure_type": service.outlier_procedure.get("procedure_type"),
                "procedure_source": "outlier",
                "version": service.outlier_procedure.get("version"),
                "total_steps": len(phases_data),
                "steps": phases_data,
            }
        else:
            if not service.master_procedure:
                await websocket.send_json({"error": "Master procedure not found"})
                await websocket.close()
                return

            session_data = {
                "procedure_name": service.master_procedure.get("procedure_name"),
                "procedure_type": service.master_procedure.get("procedure_type"),
                "procedure_source": "standard",
                "total_steps": len(service.master_procedure.get("steps", [])),
                "steps": service.master_procedure.get("steps", []),
            }

        await websocket.send_json({"type": "session_started", "data": session_data})

        # Process incoming video frames
        while True:
            try:
                message = await websocket.receive()

                if message.get("type") == "websocket.disconnect":
                    logger.info("websocket_disconnect_received_v2", session_id=session_id)
                    break

                if "bytes" in message:
                    await service.process_frame(message["bytes"])
                elif "text" in message:
                    import json
                    data = json.loads(message["text"])
                    if data.get("type") == "stop":
                        break
            except WebSocketDisconnect:
                logger.info("websocket_disconnected_in_loop_v2", session_id=session_id)
                break
            except RuntimeError as e:
                if "disconnect" in str(e).lower():
                    logger.info("websocket_already_disconnected_v2", session_id=session_id)
                    break
                raise

    except WebSocketDisconnect:
        logger.info("websocket_disconnected_v2", session_id=session_id)
    except Exception as e:
        logger.error("websocket_error_v2", session_id=session_id, error=str(e))
    finally:
        if service:
            await service.stop_session()


@router.get("/{session_id}/alerts", response_model=List[SessionAlertResponse])
async def get_session_alerts(
    session_id: str,
    db: AsyncDatabase = Depends(get_db),
):
    """Get all alerts for a specific session."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")

    cursor = db[SESSION_ALERTS].find({"session_id": ObjectId(session_id)}).sort(
        "timestamp", -1
    )
    alerts = await cursor.to_list(length=None)

    for alert in alerts:
        alert["id"] = str(alert.pop("_id"))
        alert["session_id"] = str(alert["session_id"])

    return alerts
