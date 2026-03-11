import { useState, useEffect, useRef, useCallback } from 'react';
import {
  LiveKitRoom,
  VideoTrack,
  useRoomContext,
  useLocalParticipant,
  useRemoteParticipants,
  useDataChannel,
  useTracks,
  RoomAudioRenderer,
} from '@livekit/components-react';
import '@livekit/components-styles';
import { Track, RoomEvent } from 'livekit-client';
import { Play, Square, AlertCircle, CheckCircle, Radio, Wifi, WifiOff } from 'lucide-react';
import { proceduresAPI, outlierProceduresAPI } from '../services/api';
import { fetchLiveKitToken } from '../services/livekit';

function LiveMonitoringLiveKitPage() {
  const [procedures, setProcedures] = useState([]);
  const [outlierProcedures, setOutlierProcedures] = useState([]);
  const [analysisMode, setAnalysisMode] = useState('standard');
  const [selectedProcedure, setSelectedProcedure] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [surgeonId, setSurgeonId] = useState('surgeon-001');
  const [messages, setMessages] = useState([]);
  const [alerts, setAlerts] = useState([]);

  // LiveKit state
  const [liveKitToken, setLiveKitToken] = useState('');
  const [liveKitUrl, setLiveKitUrl] = useState('');
  const [roomName, setRoomName] = useState('');
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [sessionInfo, setSessionInfo] = useState(null);

  // Analysis state (received from agent via data channels or transcription)
  const [currentAnalysis, setCurrentAnalysis] = useState(null);
  const [analysisHistory, setAnalysisHistory] = useState([]);
  const [agentState, setAgentState] = useState('disconnected');
  const [agentTranscript, setAgentTranscript] = useState([]);

  useEffect(() => {
    loadProcedures();
    loadOutlierProcedures();
  }, []);

  const loadProcedures = async () => {
    try {
      const data = await proceduresAPI.getAll();
      setProcedures(data);
      addMessage('info', 'Standard procedures loaded successfully');
    } catch (error) {
      addMessage('error', `Failed to load procedures: ${error.message}`);
    }
  };

  const loadOutlierProcedures = async () => {
    try {
      const data = await outlierProceduresAPI.getAll();
      setOutlierProcedures(data);
      addMessage('info', 'Error resolution protocols loaded successfully');
    } catch (error) {
      addMessage('error', `Failed to load error resolution protocols: ${error.message}`);
    }
  };

  const addMessage = useCallback((type, text) => {
    setMessages(prev => [...prev, { type, text, timestamp: new Date().toLocaleTimeString() }]);
  }, []);

  const connectToLiveKit = async () => {
    if (!selectedProcedure) {
      addMessage('error', 'Please select a procedure first');
      return;
    }
    if (!sessionId) {
      addMessage('error', 'Please enter a session ID');
      return;
    }

    setIsConnecting(true);
    try {
      addMessage('info', 'Requesting LiveKit token...');
      const procedureSource = analysisMode === 'error-resolution' ? 'outlier' : 'standard';

      const tokenData = await fetchLiveKitToken({
        sessionId,
        procedureId: selectedProcedure,
        surgeonId,
        procedureSource,
        participantName: `Surgeon ${surgeonId}`,
      });

      setLiveKitToken(tokenData.token);
      setLiveKitUrl(tokenData.livekit_url);
      setRoomName(tokenData.room_name);
      setSessionInfo({
        procedure_name: tokenData.procedure_name,
        procedure_source: tokenData.procedure_source,
        total_steps: tokenData.total_steps,
      });

      addMessage('success', `Token received. Connecting to room: ${tokenData.room_name}`);
    } catch (error) {
      addMessage('error', `Failed to connect: ${error.message}`);
      setIsConnecting(false);
    }
  };

  const disconnectFromLiveKit = () => {
    setLiveKitToken('');
    setLiveKitUrl('');
    setRoomName('');
    setIsConnected(false);
    setIsConnecting(false);
    setAgentState('disconnected');
    setCurrentAnalysis(null);
    addMessage('info', 'Disconnected from LiveKit room');
  };

  const handleRoomConnected = useCallback(() => {
    setIsConnected(true);
    setIsConnecting(false);
    addMessage('success', 'Connected to LiveKit room. Camera will be published automatically.');
  }, [addMessage]);

  const handleRoomDisconnected = useCallback(() => {
    setIsConnected(false);
    setIsConnecting(false);
    setAgentState('disconnected');
    addMessage('info', 'Disconnected from LiveKit room');
  }, [addMessage]);

  const handleRoomError = useCallback((error) => {
    addMessage('error', `Room error: ${error?.message || 'Unknown error'}`);
    setIsConnecting(false);
  }, [addMessage]);

  return (
    <div className="px-4 py-8">
      <div className="flex items-center gap-3 mb-8">
        <h1 className="text-3xl font-bold text-gray-900">Live Surgery Monitoring</h1>
        <span className="inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold bg-purple-100 text-purple-800">
          <Radio className="h-4 w-4 mr-1" />
          V4 — LiveKit + Gemini Live
        </span>
      </div>

      {/* Wrap the entire UI in LiveKitRoom when we have a token */}
      {liveKitToken && liveKitUrl ? (
        <LiveKitRoom
          serverUrl={liveKitUrl}
          token={liveKitToken}
          connect={true}
          video={true}
          audio={false}
          onConnected={handleRoomConnected}
          onDisconnected={handleRoomDisconnected}
          onError={handleRoomError}
          data-lk-theme="default"
        >
          <RoomAudioRenderer />
          <RoomContent
            isConnected={isConnected}
            sessionInfo={sessionInfo}
            messages={messages}
            alerts={alerts}
            setAlerts={setAlerts}
            currentAnalysis={currentAnalysis}
            setCurrentAnalysis={setCurrentAnalysis}
            analysisHistory={analysisHistory}
            setAnalysisHistory={setAnalysisHistory}
            agentState={agentState}
            setAgentState={setAgentState}
            agentTranscript={agentTranscript}
            setAgentTranscript={setAgentTranscript}
            addMessage={addMessage}
            onDisconnect={disconnectFromLiveKit}
            analysisMode={analysisMode}
          />
        </LiveKitRoom>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Setup Panel (shown before connection) */}
          <div className="lg:col-span-1">
            <SetupPanel
              analysisMode={analysisMode}
              setAnalysisMode={setAnalysisMode}
              procedures={procedures}
              outlierProcedures={outlierProcedures}
              selectedProcedure={selectedProcedure}
              setSelectedProcedure={setSelectedProcedure}
              sessionId={sessionId}
              setSessionId={setSessionId}
              surgeonId={surgeonId}
              setSurgeonId={setSurgeonId}
              isConnecting={isConnecting}
              onConnect={connectToLiveKit}
            />
          </div>

          {/* Placeholder for video + messages */}
          <div className="lg:col-span-2 space-y-6">
            <div className="bg-white rounded-lg shadow-md p-6">
              <h2 className="text-xl font-semibold mb-4">Video Feed</h2>
              <div className="bg-gray-900 rounded-lg overflow-hidden aspect-video flex items-center justify-center">
                <div className="text-center text-gray-400">
                  <Radio className="h-12 w-12 mx-auto mb-3 opacity-50" />
                  <p className="text-lg">LiveKit + Gemini Live API</p>
                  <p className="text-sm mt-1">Configure session and connect to start real-time analysis</p>
                </div>
              </div>
            </div>

            <MessageLog messages={messages} />
          </div>
        </div>
      )}
    </div>
  );
}


/**
 * Setup panel — procedure selection, session config, connect button.
 */
function SetupPanel({
  analysisMode, setAnalysisMode,
  procedures, outlierProcedures,
  selectedProcedure, setSelectedProcedure,
  sessionId, setSessionId,
  surgeonId, setSurgeonId,
  isConnecting,
  onConnect,
}) {
  return (
    <div className="bg-white rounded-lg shadow-md p-6">
      <h2 className="text-xl font-semibold mb-4">Session Control</h2>

      {/* Pipeline Badge */}
      <div className="mb-4 p-3 bg-purple-50 rounded-md border border-purple-200">
        <div className="flex items-center gap-2 text-sm font-medium text-purple-800">
          <Radio className="h-4 w-4" />
          V4: Gemini Live API via LiveKit WebRTC
        </div>
        <p className="text-xs text-purple-600 mt-1">
          Real-time video streaming with sub-second latency. Agent sees live video feed directly.
        </p>
      </div>

      {/* Analysis Mode */}
      <div className="mb-4">
        <label className="block text-sm font-medium text-gray-700 mb-2">Analysis Mode</label>
        <select
          value={analysisMode}
          onChange={(e) => { setAnalysisMode(e.target.value); setSelectedProcedure(''); }}
          className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
        >
          <option value="standard">Standard Monitoring</option>
          <option value="error-resolution">Error Resolution Protocol</option>
        </select>
      </div>

      {/* Procedure Selection */}
      <div className="mb-4">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          {analysisMode === 'standard' ? 'Select Procedure' : 'Select Protocol'}
        </label>
        <select
          value={selectedProcedure}
          onChange={(e) => setSelectedProcedure(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
        >
          <option value="">
            {analysisMode === 'standard' ? '-- Select a procedure --' : '-- Select a protocol --'}
          </option>
          {analysisMode === 'standard'
            ? procedures.map((proc) => (
                <option key={proc.id} value={proc.id}>{proc.procedure_name}</option>
              ))
            : outlierProcedures.map((proc) => (
                <option key={proc.id} value={proc.id}>{proc.procedure_name} (v{proc.version})</option>
              ))
          }
        </select>
      </div>

      {/* Session ID */}
      <div className="mb-4">
        <label className="block text-sm font-medium text-gray-700 mb-2">Session ID</label>
        <input
          type="text"
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
          placeholder="e.g., session-001"
          className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
        />
      </div>

      {/* Surgeon ID */}
      <div className="mb-4">
        <label className="block text-sm font-medium text-gray-700 mb-2">Surgeon ID</label>
        <input
          type="text"
          value={surgeonId}
          onChange={(e) => setSurgeonId(e.target.value)}
          placeholder="e.g., surgeon-001"
          className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
        />
      </div>

      {/* Connect Button */}
      <button
        onClick={onConnect}
        disabled={isConnecting}
        className="w-full bg-purple-600 text-white px-4 py-2 rounded-md hover:bg-purple-700 flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isConnecting ? (
          <>
            <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent mr-2" />
            Connecting...
          </>
        ) : (
          <>
            <Play className="h-4 w-4 mr-2" />
            Connect via LiveKit
          </>
        )}
      </button>
    </div>
  );
}


/**
 * Main room content — shown after LiveKit connection is established.
 */
function RoomContent({
  isConnected,
  sessionInfo,
  messages,
  alerts,
  setAlerts,
  currentAnalysis,
  setCurrentAnalysis,
  analysisHistory,
  setAnalysisHistory,
  agentState,
  setAgentState,
  agentTranscript,
  setAgentTranscript,
  addMessage,
  onDisconnect,
  analysisMode,
}) {
  const room = useRoomContext();
  const localParticipant = useLocalParticipant();
  const remoteParticipants = useRemoteParticipants();
  const transcriptRef = useRef(null);
  const agentLoggedRef = useRef(false);
  const streamingSegmentRef = useRef('');

  // Get local camera tracks
  const localVideoTracks = useTracks(
    [{ source: Track.Source.Camera, withPlaceholder: true }],
    { onlySubscribed: false }
  );

  // Listen for agent participant state changes
  useEffect(() => {
    if (!room) return;

    const handleParticipantConnected = (participant) => {
      if (participant.isAgent) {
        setAgentState('connected');
        addMessage('success', `AI Agent joined the room: ${participant.identity}`);
      }
    };

    const handleParticipantDisconnected = (participant) => {
      if (participant.isAgent) {
        setAgentState('disconnected');
        addMessage('info', 'AI Agent left the room');
      }
    };

    const handleDataReceived = (payload, participant) => {
      try {
        const text = new TextDecoder().decode(payload);
        const data = JSON.parse(text);

        if (data.type === 'analysis_update') {
          setCurrentAnalysis(data.data);
          setAnalysisHistory(prev => [...prev.slice(-9), data.data]);
          addMessage('info', `Analysis update: ${data.data?.action_observed || 'Frame analyzed'}`);
        } else if (data.type === 'alert') {
          setAlerts(prev => [...prev, data.data]);
          addMessage('warning', `Alert: ${data.data?.message || 'Safety concern detected'}`);
        }
      } catch {
        // Not JSON — treat as plain text transcript
        const text = new TextDecoder().decode(payload);
        if (text.trim()) {
          setAgentTranscript(prev => [...prev.slice(-19), {
            text,
            timestamp: new Date().toLocaleTimeString(),
            speaker: participant?.isAgent ? 'agent' : 'user',
          }]);
        }
      }
    };

    // Listen for transcription events — handles both streaming (non-final) and final segments
    const handleTranscriptionReceived = (segments, participant) => {
      if (!segments || segments.length === 0) return;
      const isAgentSpeaker = participant?.isAgent || participant?.identity?.startsWith('agent-');

      for (const segment of segments) {
        if (!segment.text?.trim()) continue;
        if (segment.final) {
          streamingSegmentRef.current = '';
          setAgentTranscript(prev => {
            // Replace last streaming entry if it exists, otherwise append
            const filtered = prev.filter(e => !e.streaming);
            return [...filtered.slice(-19), {
              text: segment.text,
              timestamp: new Date().toLocaleTimeString(),
              speaker: isAgentSpeaker ? 'agent' : 'user',
              streaming: false,
            }];
          });
        } else {
          // Non-final: show streaming text in-place
          streamingSegmentRef.current = segment.text;
          setAgentTranscript(prev => {
            const filtered = prev.filter(e => !e.streaming);
            return [...filtered.slice(-19), {
              text: segment.text + ' ▍',
              timestamp: new Date().toLocaleTimeString(),
              speaker: isAgentSpeaker ? 'agent' : 'user',
              streaming: true,
            }];
          });
        }
      }
    };

    room.on(RoomEvent.ParticipantConnected, handleParticipantConnected);
    room.on(RoomEvent.ParticipantDisconnected, handleParticipantDisconnected);
    room.on(RoomEvent.DataReceived, handleDataReceived);
    room.on(RoomEvent.TranscriptionReceived, handleTranscriptionReceived);

    return () => {
      room.off(RoomEvent.ParticipantConnected, handleParticipantConnected);
      room.off(RoomEvent.ParticipantDisconnected, handleParticipantDisconnected);
      room.off(RoomEvent.DataReceived, handleDataReceived);
      room.off(RoomEvent.TranscriptionReceived, handleTranscriptionReceived);
    };
  }, [room, addMessage, setCurrentAnalysis, setAnalysisHistory, setAlerts, setAgentState, setAgentTranscript]);

  // Check once if agent is already in room (avoids spam on re-renders)
  useEffect(() => {
    if (agentLoggedRef.current) return;
    for (const p of remoteParticipants) {
      if (p.isAgent || p.identity?.startsWith('agent-')) {
        agentLoggedRef.current = true;
        setAgentState('connected');
        addMessage('success', `AI Agent active in room: ${p.identity}`);
        break;
      }
    }
  }, [remoteParticipants, addMessage, setAgentState]);

  // Auto-scroll transcript
  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [agentTranscript]);

  const localVideoTrack = localVideoTracks.find(
    (t) => t.source === Track.Source.Camera && t.publication?.track
  );

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Left Panel — Status and Controls */}
      <div className="lg:col-span-1 space-y-6">
        {/* Connection Status */}
        <div className="bg-white rounded-lg shadow-md p-6">
          <h2 className="text-xl font-semibold mb-4">Session Status</h2>

          <div className="space-y-3">
            {/* Room connection */}
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded-md">
              <div className="flex items-center">
                {isConnected ? (
                  <Wifi className="h-5 w-5 text-green-500 mr-2" />
                ) : (
                  <WifiOff className="h-5 w-5 text-gray-400 mr-2" />
                )}
                <span className="text-sm font-medium">LiveKit Room</span>
              </div>
              <span className={`text-xs px-2 py-1 rounded-full ${isConnected ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                {isConnected ? 'Connected' : 'Disconnected'}
              </span>
            </div>

            {/* Agent status */}
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded-md">
              <div className="flex items-center">
                <Radio className={`h-5 w-5 mr-2 ${agentState === 'connected' ? 'text-purple-500' : 'text-gray-400'}`} />
                <span className="text-sm font-medium">AI Agent</span>
              </div>
              <span className={`text-xs px-2 py-1 rounded-full ${
                agentState === 'connected' ? 'bg-purple-100 text-purple-800' :
                'bg-gray-100 text-gray-600'
              }`}>
                {agentState === 'connected' ? 'Active' : 'Waiting...'}
              </span>
            </div>

            {/* Camera status */}
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded-md">
              <div className="flex items-center">
                <Play className={`h-5 w-5 mr-2 ${localVideoTrack ? 'text-green-500' : 'text-gray-400'}`} />
                <span className="text-sm font-medium">Camera</span>
              </div>
              <span className={`text-xs px-2 py-1 rounded-full ${localVideoTrack ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'}`}>
                {localVideoTrack ? 'Publishing' : 'Starting...'}
              </span>
            </div>
          </div>

          {/* Session Info */}
          {sessionInfo && (
            <div className="mt-4 p-3 bg-purple-50 rounded-md">
              <div className="text-sm font-medium text-purple-800">
                {sessionInfo.procedure_name}
              </div>
              <div className="text-xs text-purple-600 mt-1">
                {sessionInfo.total_steps} steps · {sessionInfo.procedure_source} mode
              </div>
            </div>
          )}

          {/* Disconnect Button */}
          <button
            onClick={onDisconnect}
            className="w-full mt-4 bg-red-600 text-white px-4 py-2 rounded-md hover:bg-red-700 flex items-center justify-center"
          >
            <Square className="h-4 w-4 mr-2" />
            Stop Session
          </button>
        </div>

        {/* Alerts Panel */}
        <div className="bg-white rounded-lg shadow-md p-6">
          <h2 className="text-xl font-semibold mb-4">Alerts</h2>
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {alerts.length === 0 ? (
              <p className="text-gray-500 text-sm">No alerts yet</p>
            ) : (
              alerts.map((alert, index) => (
                <div
                  key={index}
                  className={`p-3 rounded-md ${
                    alert.severity === 'high'
                      ? 'bg-red-50 border-l-4 border-red-500'
                      : alert.severity === 'medium'
                      ? 'bg-yellow-50 border-l-4 border-yellow-500'
                      : 'bg-blue-50 border-l-4 border-blue-500'
                  }`}
                >
                  <div className="flex items-start">
                    <AlertCircle className="h-5 w-5 mr-2 flex-shrink-0" />
                    <div>
                      <div className="font-medium text-sm">{alert.alert_type || 'Alert'}</div>
                      <div className="text-sm text-gray-600">{alert.message}</div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Right Panel — Video, Transcript, Messages */}
      <div className="lg:col-span-2 space-y-6">
        {/* Video Feed */}
        <div className="bg-white rounded-lg shadow-md p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold">Live Video Feed</h2>
            <div className="flex items-center gap-2">
              {localVideoTrack && (
                <span className="flex items-center text-xs text-green-600">
                  <span className="w-2 h-2 bg-green-500 rounded-full mr-1 animate-pulse" />
                  LIVE via WebRTC
                </span>
              )}
            </div>
          </div>
          <div className="bg-gray-900 rounded-lg overflow-hidden aspect-video">
            {localVideoTrack ? (
              <VideoTrack
                trackRef={localVideoTrack}
                className="w-full h-full object-contain"
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center text-gray-400">
                <div className="text-center">
                  <div className="animate-spin rounded-full h-8 w-8 border-2 border-gray-400 border-t-transparent mx-auto mb-2" />
                  <p>Initializing camera...</p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Agent Transcript — Real-time analysis from Gemini Live */}
        <div className="bg-white rounded-lg shadow-md p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold">AI Analysis Transcript</h2>
            <span className={`text-xs px-2 py-1 rounded-full ${
              agentState === 'connected'
                ? 'bg-purple-100 text-purple-800'
                : 'bg-gray-100 text-gray-600'
            }`}>
              {agentState === 'connected' ? 'Agent Active' : 'Waiting for Agent'}
            </span>
          </div>

          <div
            ref={transcriptRef}
            className="space-y-3 max-h-96 overflow-y-auto p-3 bg-gray-50 rounded-lg"
          >
            {agentTranscript.length === 0 ? (
              <p className="text-gray-500 text-sm text-center py-8">
                {agentState === 'connected'
                  ? 'Agent is analyzing the video feed...'
                  : 'Waiting for AI agent to connect and begin analysis...'}
              </p>
            ) : (
              agentTranscript.map((entry, index) => (
                <div
                  key={index}
                  className={`p-3 rounded-lg ${
                    entry.speaker === 'agent'
                      ? 'bg-purple-50 border-l-4 border-purple-400'
                      : 'bg-blue-50 border-l-4 border-blue-400'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-xs font-semibold ${
                      entry.speaker === 'agent' ? 'text-purple-700' : 'text-blue-700'
                    }`}>
                      {entry.speaker === 'agent' ? 'AI Analyst' : 'Surgeon'}
                    </span>
                    <span className="text-xs text-gray-400">{entry.timestamp}</span>
                  </div>
                  <p className={`text-sm whitespace-pre-wrap ${entry.streaming ? 'text-gray-500 italic' : 'text-gray-700'}`}>
                    {entry.text}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Activity Log */}
        <MessageLog messages={messages} />
      </div>
    </div>
  );
}


/**
 * Reusable message log component.
 */
function MessageLog({ messages }) {
  return (
    <div className="bg-white rounded-lg shadow-md p-6">
      <h2 className="text-xl font-semibold mb-4">Activity Log</h2>
      <div className="space-y-2 max-h-96 overflow-y-auto">
        {messages.map((msg, index) => (
          <div key={index} className="flex items-start text-sm">
            <span className="text-gray-500 mr-2 flex-shrink-0">{msg.timestamp}</span>
            {msg.type === 'success' && <CheckCircle className="h-4 w-4 text-green-500 mr-2 flex-shrink-0 mt-0.5" />}
            {msg.type === 'error' && <AlertCircle className="h-4 w-4 text-red-500 mr-2 flex-shrink-0 mt-0.5" />}
            {msg.type === 'warning' && <AlertCircle className="h-4 w-4 text-yellow-500 mr-2 flex-shrink-0 mt-0.5" />}
            <span className={
              msg.type === 'error' ? 'text-red-600' :
              msg.type === 'success' ? 'text-green-600' :
              msg.type === 'warning' ? 'text-yellow-600' :
              'text-gray-600'
            }>{msg.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}


export default LiveMonitoringLiveKitPage;
