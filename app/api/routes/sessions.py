"""
API routes for live surgery sessions.
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from pymongo.asynchronous.database import AsyncDatabase
from bson import ObjectId
from typing import List

from app.db.mongodb import get_db
from app.db.collections import LIVE_SESSIONS, SESSION_ALERTS
from app.schemas.procedure import LiveSessionCreate, LiveSessionResponse, SessionAlertResponse
from app.services.live_surgery import LiveSurgeryService
from app.core.logging import logger

router = APIRouter()


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    db: AsyncDatabase = Depends(get_db)
):
    """
    WebSocket endpoint for real-time surgical monitoring.
    """
    await websocket.accept()
    logger.info("websocket_connected", session_id=session_id)
    
    service = None
    
    try:
        # Receive initial configuration
        init_data = await websocket.receive_json()
        procedure_id = init_data.get("procedure_id")
        surgeon_id = init_data.get("surgeon_id", "default-surgeon")
        
        if not procedure_id:
            await websocket.send_json({"error": "procedure_id required"})
            await websocket.close()
            return
        
        # Create live surgery service
        service = LiveSurgeryService(db, session_id)
        
        # Define callbacks for real-time updates
        async def send_alerts(alerts):
            await websocket.send_json({
                "type": "alerts",
                "data": alerts
            })
        
        async def send_analysis_update(analysis_data):
            await websocket.send_json({
                "type": "analysis_update",
                "data": analysis_data
            })
        
        # Start session
        await service.start_session(
            procedure_id=procedure_id,
            surgeon_id=surgeon_id,
            alert_callback=send_alerts,
            analysis_callback=send_analysis_update
        )
        
        await websocket.send_json({
            "type": "session_started",
            "data": {
                "procedure_name": service.master_procedure.get("procedure_name"),
                "procedure_type": service.master_procedure.get("procedure_type"),
                "total_steps": len(service.master_procedure.get("steps", [])),
                "steps": service.master_procedure.get("steps", [])
            }
        })
        
        # Process incoming video frames
        while True:
            try:
                message = await websocket.receive()
                
                # Check if connection was closed
                if message.get("type") == "websocket.disconnect":
                    logger.info("websocket_disconnect_received", session_id=session_id)
                    break
                
                if "bytes" in message:
                    # Video frame
                    await service.process_frame(message["bytes"])
                elif "text" in message:
                    # Control message
                    import json
                    data = json.loads(message["text"])
                    
                    if data.get("type") == "stop":
                        break
            except WebSocketDisconnect:
                logger.info("websocket_disconnected_in_loop", session_id=session_id)
                break
            except RuntimeError as e:
                # Handle "Cannot call 'receive' once a disconnect message has been received"
                if "disconnect" in str(e).lower():
                    logger.info("websocket_already_disconnected", session_id=session_id)
                    break
                raise
    
    except WebSocketDisconnect:
        logger.info("websocket_disconnected", session_id=session_id)
    except Exception as e:
        logger.error("websocket_error", session_id=session_id, error=str(e))
    finally:
        if service:
            await service.stop_session()


@router.get("/{session_id}/alerts", response_model=List[SessionAlertResponse])
async def get_session_alerts(
    session_id: str,
    db: AsyncDatabase = Depends(get_db)
):
    """Get all alerts for a specific session."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    
    cursor = db[SESSION_ALERTS].find({"session_id": ObjectId(session_id)}).sort("timestamp", -1)
    alerts = await cursor.to_list(length=None)
    
    # Convert ObjectIds
    for alert in alerts:
        alert["id"] = str(alert.pop("_id"))
        alert["session_id"] = str(alert["session_id"])
    
    return alerts
