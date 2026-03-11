/**
 * LiveKit service for V4 pipeline — Gemini Live API via LiveKit.
 * Handles token generation and room connection management.
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * Fetch a LiveKit access token from the backend.
 * @param {Object} params
 * @param {string} params.sessionId - Unique session identifier
 * @param {string} params.procedureId - ID of the procedure
 * @param {string} params.surgeonId - Surgeon identifier
 * @param {string} params.procedureSource - 'standard' or 'outlier'
 * @param {string} params.participantName - Display name in the room
 * @returns {Promise<Object>} Token response with token, livekit_url, room_name, etc.
 */
export async function fetchLiveKitToken({
  sessionId,
  procedureId,
  surgeonId = 'surgeon-001',
  procedureSource = 'standard',
  participantName = 'Surgeon',
}) {
  const response = await fetch(`${API_BASE_URL}/api/livekit/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      procedure_id: procedureId,
      surgeon_id: surgeonId,
      procedure_source: procedureSource,
      participant_name: participantName,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get LiveKit token' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export default { fetchLiveKitToken };
