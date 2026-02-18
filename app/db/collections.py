"""
MongoDB collection names and indexes.
"""

# Collection names
MASTER_PROCEDURES = "master_procedures"
SURGICAL_STEPS = "surgical_steps"
LIVE_SESSIONS = "live_sessions"
SESSION_ALERTS = "session_alerts"
USERS = "users"
VIDEO_METADATA = "video_metadata"


async def create_indexes(db):
    """Create database indexes for optimal query performance."""
    
    # Master Procedures indexes
    await db[MASTER_PROCEDURES].create_index("procedure_type")
    await db[MASTER_PROCEDURES].create_index("created_at")
    
    # Note: SURGICAL_STEPS collection is deprecated - steps are now embedded in master_procedures
    
    # Live Sessions indexes
    await db[LIVE_SESSIONS].create_index("surgeon_id")
    await db[LIVE_SESSIONS].create_index("procedure_id")
    await db[LIVE_SESSIONS].create_index("start_time")
    await db[LIVE_SESSIONS].create_index("status")
    
    # Session Alerts indexes
    await db[SESSION_ALERTS].create_index("session_id")
    await db[SESSION_ALERTS].create_index([("session_id", 1), ("timestamp", -1)])
    await db[SESSION_ALERTS].create_index("severity")
    
    # Users indexes
    await db[USERS].create_index("email", unique=True)
    await db[USERS].create_index("role")
    
    # Video Metadata indexes
    await db[VIDEO_METADATA].create_index("uploaded_by")
    await db[VIDEO_METADATA].create_index("upload_timestamp")
