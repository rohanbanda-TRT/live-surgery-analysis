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
from app.services.live_surgery_outlier_comparison import LiveSurgeryOutlierComparisonService
from app.core.logging import logger

router = APIRouter()

# Global registry: session_id -> active service instance
# Services survive WebSocket reconnects; only removed on explicit stop
_active_services: dict = {}


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    db: AsyncDatabase = Depends(get_db)
):
    """
    WebSocket endpoint for real-time surgical monitoring.
    
    Supports multiple analysis modes:
    - V1 (default): Standard live surgery analysis
    - outlier_comparison: Uses chunked video comparison analysis approach
    """
    await websocket.accept()
    logger.info("websocket_connected", session_id=session_id)
    
    service = None
    is_reconnect = False
    
    try:
        # Receive initial configuration
        init_data = await websocket.receive_json()
        procedure_id = init_data.get("procedure_id")
        surgeon_id = init_data.get("surgeon_id", "default-surgeon")
        procedure_source = init_data.get("procedure_source", "standard")  # "standard" or "outlier"
        analysis_mode = init_data.get("analysis_mode", "v1")  # "v1" or "outlier_comparison"
        
        if not procedure_id:
            await websocket.send_json({"error": "procedure_id required"})
            await websocket.close()
            return
        
        # Check if an active service already exists for this session_id (reconnect case)
        existing = _active_services.get(session_id)
        if existing is not None and getattr(existing, 'is_processing_chunks', False):
            service = existing
            is_reconnect = True
            logger.info(
                "reconnecting_to_existing_service",
                session_id=session_id,
                service_type=type(service).__name__,
                detected_so_far=len(getattr(service, 'detected_steps_cumulative', set()))
            )
        else:
            # Create live surgery service based on analysis mode
            if analysis_mode == "outlier_comparison":
                logger.info(
                    "creating_outlier_comparison_service",
                    session_id=session_id,
                    procedure_source=procedure_source
                )
                service = LiveSurgeryOutlierComparisonService(db, session_id)
            else:
                logger.info(
                    "creating_standard_v1_service",
                    session_id=session_id,
                    procedure_source=procedure_source
                )
                service = LiveSurgeryService(db, session_id)
            _active_services[session_id] = service
        
        # Define callbacks bound to this WebSocket connection
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
        
        if is_reconnect:
            # Swap callbacks to new WebSocket — service keeps running uninterrupted
            service.analysis_callback = send_analysis_update
            service.alert_callback = send_alerts
            logger.info("callbacks_updated_on_reconnect", session_id=session_id)
        else:
            # Start new session
            await service.start_session(
                procedure_id=procedure_id,
                surgeon_id=surgeon_id,
                procedure_source=procedure_source,
                alert_callback=send_alerts,
                analysis_callback=send_analysis_update
            )
        
        # Build session started / reconnect response
        if is_reconnect:
            # Re-send current cumulative state to restore frontend UI
            procedure_source = getattr(service, 'procedure_source', procedure_source)
        
        if procedure_source == "outlier":
            if not service.outlier_procedure:
                await websocket.send_json({"error": "Outlier procedure not found"})
                await websocket.close()
                return
            
            # Build complete phase data with checkpoints for outlier mode
            phases_data = []
            for i, phase in enumerate(service.outlier_procedure.get("phases", [])):
                phase_number = phase["phase_number"]
                
                # Get checkpoint status
                checkpoint_info = service.checkpoint_tracker.get_phase_checkpoint_status(phase_number) if service.checkpoint_tracker else {}
                
                # On reconnect, restore cumulative detected state
                is_detected = i in getattr(service, 'detected_steps_cumulative', set())
                if is_detected:
                    phase_status = "completed" if not checkpoint_info.get('has_checkpoints') or checkpoint_info.get('all_complete') else "current"
                else:
                    phase_status = "pending"
                
                phase_data = {
                    "step_number": phase_number,
                    "step_name": phase["phase_name"],
                    "phase_number": phase_number,
                    "phase_name": phase["phase_name"],
                    "description": phase.get("goal"),
                    "goal": phase.get("goal"),
                    "priority": phase.get("priority"),
                    "is_critical": phase.get("priority") == "HIGH",
                    "status": phase_status,
                    "detected": is_detected,
                    "checkpoints": checkpoint_info.get('checkpoints', []),
                    "detected_errors": []
                }
                phases_data.append(phase_data)
            
            logger.info(
                "session_started_outlier_mode",
                session_id=session_id,
                phases_count=len(phases_data),
                sample_phase=phases_data[0] if phases_data else None
            )
            
            session_data = {
                "procedure_name": service.outlier_procedure.get("procedure_name"),
                "procedure_type": service.outlier_procedure.get("procedure_type"),
                "procedure_source": "outlier",
                "version": service.outlier_procedure.get("version"),
                "total_steps": len(phases_data),
                "steps": phases_data
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
                "steps": service.master_procedure.get("steps", [])
            }
        
        await websocket.send_json({
            "type": "session_started",
            "data": session_data
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
                        # Explicit stop: fully stop service and remove from registry
                        _active_services.pop(session_id, None)
                        if service:
                            await service.stop_session()
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
        # Null out callback so the service stops trying to send on dead socket
        # Service itself stays alive in registry for potential reconnect
        if service:
            service.analysis_callback = None
            service.alert_callback = None
    except Exception as e:
        logger.error("websocket_error", session_id=session_id, error=str(e))
        if service:
            service.analysis_callback = None
            service.alert_callback = None
    finally:
        # Only fully stop service when user sends explicit stop or on clean close
        # (not on raw WebSocket disconnect, which may be a transient reconnect)
        pass


async def stop_session_for(session_id: str):
    """Explicitly stop and remove a session from the registry."""
    service = _active_services.pop(session_id, None)
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
