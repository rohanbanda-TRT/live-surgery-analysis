import { useState, useEffect, useRef } from 'react';
import { Play, Square, AlertCircle, CheckCircle } from 'lucide-react';
import LiveSurgeryWebSocket from '../services/websocket';
import { proceduresAPI } from '../services/api';

function LiveMonitoringPage() {
  const [procedures, setProcedures] = useState([]);
  const [selectedProcedure, setSelectedProcedure] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [surgeonId, setSurgeonId] = useState('surgeon-001');
  const [isConnected, setIsConnected] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [messages, setMessages] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [sessionInfo, setSessionInfo] = useState(null);
  const [currentAnalysis, setCurrentAnalysis] = useState(null);
  const [analysisHistory, setAnalysisHistory] = useState([]);
  const [allSteps, setAllSteps] = useState([]);
  const [availableCameras, setAvailableCameras] = useState([]);
  const [selectedCameraId, setSelectedCameraId] = useState('');
  
  const wsRef = useRef(null);
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const intervalRef = useRef(null);

  useEffect(() => {
    // Load procedures and cameras on mount
    loadProcedures();
    listAvailableCameras();
    
    return () => {
      // Cleanup on unmount
      if (wsRef.current) {
        wsRef.current.close();
      }
      stopVideoStream();
    };
  }, []);

  const loadProcedures = async () => {
    try {
      const data = await proceduresAPI.getAll();
      setProcedures(data);
      addMessage('info', 'Procedures loaded successfully');
    } catch (error) {
      addMessage('error', `Failed to load procedures: ${error.message}`);
    }
  };

  const addMessage = (type, text) => {
    setMessages(prev => [...prev, { type, text, timestamp: new Date().toLocaleTimeString() }]);
  };

  const listAvailableCameras = async (requestPermission = false) => {
    try {
      let devices;
      
      if (requestPermission) {
        // Request camera permission to get actual device labels
        addMessage('info', 'Requesting camera permission...');
        const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        // Stop the stream immediately - we only needed it for permission
        stream.getTracks().forEach(track => track.stop());
        
        // Now enumerate with proper labels
        devices = await navigator.mediaDevices.enumerateDevices();
        addMessage('success', 'Camera access granted');
      } else {
        // Only enumerate devices without requesting camera access
        // This prevents auto-starting the camera on page load
        devices = await navigator.mediaDevices.enumerateDevices();
      }
      
      const videoInputs = devices.filter(device => device.kind === 'videoinput');
      setAvailableCameras(videoInputs);

      if (videoInputs.length > 0 && !selectedCameraId) {
        setSelectedCameraId(videoInputs[0].deviceId);
      }
      
      // If no cameras found or no labels, suggest clicking refresh
      if (videoInputs.length === 0) {
        addMessage('error', 'No cameras detected. Please check your camera connection.');
      } else if (!requestPermission && !videoInputs[0].label) {
        addMessage('info', 'Click "Refresh" to see camera names (requires permission).');
      }
    } catch (error) {
      if (error.name === 'NotAllowedError') {
        addMessage('error', 'Camera permission denied. Please allow camera access and try again.');
      } else {
        addMessage('error', `Unable to list cameras: ${error.message}`);
      }
    }
  };

  const connectWebSocket = async () => {
    if (!selectedProcedure) {
      addMessage('error', 'Please select a procedure first');
      return;
    }

    if (!sessionId) {
      addMessage('error', 'Please enter a session ID');
      return;
    }

    try {
      addMessage('info', 'Connecting to WebSocket...');
      
      wsRef.current = new LiveSurgeryWebSocket();
      
      wsRef.current.onMessage((data) => {
        if (data.type === 'session_started') {
          setSessionInfo(data.data);
          setIsConnected(true);
          // Initialize all steps with pending status
          if (data.data.steps) {
            const initialSteps = data.data.steps.map((step, index) => ({
              ...step,
              status: index === 0 ? 'current' : 'pending'
            }));
            setAllSteps(initialSteps);
          }
          addMessage('success', `Session started: ${data.data.procedure_name}`);
        } else if (data.type === 'alerts') {
          setAlerts(prev => [...prev, ...data.data]);
          addMessage('warning', `Alert received: ${data.data.length} new alerts`);
        } else if (data.type === 'analysis_update') {
          setCurrentAnalysis(data.data);
          setAnalysisHistory(prev => [...prev.slice(-9), data.data]);
          // Update all steps with latest status
          if (data.data.all_steps) {
            setAllSteps(data.data.all_steps);
          }
          addMessage('info', `Analysis: Frame ${data.data.frame_count} - ${data.data.current_step_name}`);
        }
      });

      wsRef.current.onError((error) => {
        addMessage('error', `WebSocket error: ${error.message}`);
      });

      wsRef.current.onClose(() => {
        setIsConnected(false);
        setIsStreaming(false);
        addMessage('info', 'WebSocket disconnected');
      });

      await wsRef.current.connect(sessionId, selectedProcedure, surgeonId);
      
    } catch (error) {
      addMessage('error', `Connection failed: ${error.message}`);
    }
  };

  const startVideoStream = async () => {
    try {
      addMessage('info', 'Starting video stream...');
      
      const videoConstraints = selectedCameraId
        ? { deviceId: { exact: selectedCameraId }, width: 640, height: 480 }
        : { width: 640, height: 480 };

      // Get user media (camera)
      const stream = await navigator.mediaDevices.getUserMedia({
        video: videoConstraints,
        audio: false
      });
      
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
      
      setIsStreaming(true);
      addMessage('success', 'Video stream started');
      
      // Start sending frames
      startFrameCapture();
      
    } catch (error) {
      addMessage('error', `Failed to start video: ${error.message}`);
    }
  };

  const startFrameCapture = () => {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');

    if (intervalRef.current) {
      clearInterval(intervalRef.current);
    }

    intervalRef.current = setInterval(() => {
      if (videoRef.current && wsRef.current && wsRef.current.isConnected()) {
        canvas.width = videoRef.current.videoWidth;
        canvas.height = videoRef.current.videoHeight;
        context.drawImage(videoRef.current, 0, 0);
        
        canvas.toBlob((blob) => {
          if (blob) {
            wsRef.current.sendFrame(blob);
          }
        }, 'image/jpeg', 0.8);
      }
    }, 1000); // Send frame every second
  };

  const stopVideoStream = () => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }

    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    
    // Ensure streaming state is updated
    setIsStreaming(false);
  };

  const stopSession = () => {
    // Stop video stream first
    stopVideoStream();

    // Close WebSocket
    if (wsRef.current) {
      wsRef.current.stop();
      wsRef.current.close();
      wsRef.current = null;
    }
    
    // Update all states
    setIsConnected(false);
    setIsStreaming(false);
    setCurrentAnalysis(null);
    addMessage('info', 'Session stopped');
  };

  return (
    <div className="px-4 py-8">
      <h1 className="text-3xl font-bold text-gray-900 mb-8">Live Surgery Monitoring</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Control Panel */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-lg shadow-md p-6">
            <h2 className="text-xl font-semibold mb-4">Session Control</h2>
            
            {/* Procedure Selection */}
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Select Procedure
              </label>
              <select
                value={selectedProcedure}
                onChange={(e) => setSelectedProcedure(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-primary-500"
                disabled={isConnected}
              >
                <option value="">-- Select a procedure --</option>
                {procedures.map((proc) => (
                  <option key={proc.id} value={proc.id}>
                    {proc.procedure_name}
                  </option>
                ))}
              </select>
            </div>

            {/* Camera Selection */}
            <div className="mb-4">
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-gray-700">
                  Select Camera
                </label>
                <button
                  type="button"
                  onClick={() => listAvailableCameras(true)}
                  className="text-xs text-primary-600 hover:text-primary-700"
                  disabled={isStreaming}
                >
                  Refresh
                </button>
              </div>
              {availableCameras.length === 0 ? (
                <p className="text-sm text-gray-500">
                  No cameras detected. Allow camera access and click refresh.
                </p>
              ) : (
                <select
                  value={selectedCameraId}
                  onChange={(e) => setSelectedCameraId(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-primary-500"
                  disabled={isStreaming}
                >
                  {availableCameras.map((camera, index) => (
                    <option key={camera.deviceId || index} value={camera.deviceId}>
                      {camera.label || `Camera ${index + 1}`}
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* Session ID */}
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Session ID
              </label>
              <input
                type="text"
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
                placeholder="e.g., session-001"
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-primary-500"
                disabled={isConnected}
              />
            </div>

            {/* Surgeon ID */}
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Surgeon ID
              </label>
              <input
                type="text"
                value={surgeonId}
                onChange={(e) => setSurgeonId(e.target.value)}
                placeholder="e.g., surgeon-001"
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-primary-500"
                disabled={isConnected}
              />
            </div>

            {/* Connection Status */}
            <div className="mb-4 p-3 bg-gray-50 rounded-md">
              <div className="flex items-center">
                <div className={`w-3 h-3 rounded-full mr-2 ${isConnected ? 'bg-green-500' : 'bg-gray-400'}`} />
                <span className="text-sm font-medium">
                  {isConnected ? 'Connected' : 'Disconnected'}
                </span>
              </div>
              {sessionInfo && (
                <div className="mt-2 text-sm text-gray-600">
                  <div>Procedure: {sessionInfo.procedure_name}</div>
                  <div>Total Steps: {sessionInfo.total_steps}</div>
                </div>
              )}
            </div>

            {/* Control Buttons */}
            <div className="space-y-2">
              {!isConnected ? (
                <button
                  onClick={connectWebSocket}
                  className="w-full bg-primary-600 text-white px-4 py-2 rounded-md hover:bg-primary-700 flex items-center justify-center"
                >
                  <Play className="h-4 w-4 mr-2" />
                  Connect Session
                </button>
              ) : (
                <>
                  {!isStreaming ? (
                    <button
                      onClick={startVideoStream}
                      className="w-full bg-green-600 text-white px-4 py-2 rounded-md hover:bg-green-700 flex items-center justify-center"
                      disabled={availableCameras.length === 0}
                    >
                      <Play className="h-4 w-4 mr-2" />
                      Start Video Stream
                    </button>
                  ) : (
                    <button
                      onClick={stopSession}
                      className="w-full bg-red-600 text-white px-4 py-2 rounded-md hover:bg-red-700 flex items-center justify-center"
                    >
                      <Square className="h-4 w-4 mr-2" />
                      Stop Session
                    </button>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Alerts Panel */}
          <div className="bg-white rounded-lg shadow-md p-6 mt-6">
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
                        <div className="font-medium text-sm">{alert.alert_type}</div>
                        <div className="text-sm text-gray-600">{alert.message}</div>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Video and Messages Panel */}
        <div className="lg:col-span-2 space-y-6">
          {/* Video Feed */}
          <div className="bg-white rounded-lg shadow-md p-6">
            <h2 className="text-xl font-semibold mb-4">Video Feed</h2>
            <div className="bg-gray-900 rounded-lg overflow-hidden aspect-video">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="w-full h-full object-contain"
              />
            </div>
          </div>

          {/* Procedure Steps Tracker */}
          {allSteps.length > 0 && (
            <div className="bg-white rounded-lg shadow-md p-6">
              <h2 className="text-xl font-semibold mb-4">Procedure Steps Tracker</h2>
              <div className="space-y-3">
                {allSteps.map((step, index) => {
                  const getStatusColor = (status) => {
                    switch(status) {
                      case 'completed': return 'bg-green-50 border-green-500';
                      case 'current': return 'bg-blue-50 border-blue-500';
                      case 'missed': return 'bg-red-50 border-red-500';
                      default: return 'bg-gray-50 border-gray-300';
                    }
                  };
                  
                  const getStatusBadge = (status) => {
                    switch(status) {
                      case 'completed': return 'bg-green-100 text-green-800';
                      case 'current': return 'bg-blue-100 text-blue-800';
                      case 'missed': return 'bg-red-100 text-red-800';
                      default: return 'bg-gray-100 text-gray-600';
                    }
                  };
                  
                  const getStatusIcon = (status) => {
                    switch(status) {
                      case 'completed': return '✓';
                      case 'current': return '▶';
                      case 'missed': return '✗';
                      default: return '○';
                    }
                  };
                  
                  return (
                    <div
                      key={index}
                      className={`p-4 rounded-lg border-l-4 ${getStatusColor(step.status)} transition-all duration-300`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center flex-1">
                          <span className={`inline-flex items-center justify-center w-8 h-8 rounded-full font-semibold mr-3 ${
                            step.status === 'completed' ? 'bg-green-600 text-white' :
                            step.status === 'current' ? 'bg-blue-600 text-white' :
                            step.status === 'missed' ? 'bg-red-600 text-white' :
                            'bg-gray-400 text-white'
                          }`}>
                            {step.step_number}
                          </span>
                          <div className="flex-1">
                            <div className="flex items-center gap-2">
                              <h3 className="font-semibold text-gray-900">{step.step_name}</h3>
                              {step.is_critical && (
                                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-800">
                                  Critical
                                </span>
                              )}
                            </div>
                            {step.description && (
                              <p className="text-sm text-gray-600 mt-1">{step.description}</p>
                            )}
                          </div>
                        </div>
                        <div className="ml-4">
                          <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${getStatusBadge(step.status)}`}>
                            <span className="mr-1">{getStatusIcon(step.status)}</span>
                            {step.status.charAt(0).toUpperCase() + step.status.slice(1)}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Real-time Analysis Display */}
          {currentAnalysis && (
            <div className="bg-white rounded-lg shadow-md p-6">
              <h2 className="text-xl font-semibold mb-4">Real-time Analysis</h2>
              
              {/* Current Step Info */}
              <div className="mb-4 p-4 bg-blue-50 rounded-lg border-l-4 border-blue-500">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center">
                    <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-blue-600 text-white font-semibold mr-3">
                      {currentAnalysis.expected_step.step_number}
                    </span>
                    <div>
                      <h3 className="font-semibold text-gray-900">
                        {currentAnalysis.expected_step.step_name}
                      </h3>
                      <p className="text-sm text-gray-600">
                        Frame {currentAnalysis.frame_count} analyzed
                      </p>
                    </div>
                  </div>
                  {currentAnalysis.expected_step.is_critical && (
                    <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-red-100 text-red-800">
                      Critical Step
                    </span>
                  )}
                </div>
                {currentAnalysis.expected_step.description && (
                  <p className="text-sm text-gray-700 mt-2">
                    {currentAnalysis.expected_step.description}
                  </p>
                )}
              </div>

              {/* AI Analysis Text */}
              <div className="mb-4">
                <h4 className="font-medium text-gray-900 mb-2">AI Analysis:</h4>
                <div className="p-3 bg-gray-50 rounded-md">
                  <p className="text-sm text-gray-700 whitespace-pre-wrap">
                    {currentAnalysis.analysis_text}
                  </p>
                </div>
              </div>

              {/* Progress Indicator */}
              {sessionInfo && (
                <div className="mt-4">
                  <div className="flex justify-between text-sm text-gray-600 mb-1">
                    <span>Progress</span>
                    <span>
                      Step {currentAnalysis.current_step_index + 1} of {sessionInfo.total_steps}
                    </span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                      style={{
                        width: `${((currentAnalysis.current_step_index + 1) / sessionInfo.total_steps) * 100}%`
                      }}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Messages Log */}
          <div className="bg-white rounded-lg shadow-md p-6">
            <h2 className="text-xl font-semibold mb-4">Activity Log</h2>
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {messages.map((msg, index) => (
                <div key={index} className="flex items-start text-sm">
                  <span className="text-gray-500 mr-2">{msg.timestamp}</span>
                  {msg.type === 'success' && <CheckCircle className="h-4 w-4 text-green-500 mr-2 flex-shrink-0" />}
                  {msg.type === 'error' && <AlertCircle className="h-4 w-4 text-red-500 mr-2 flex-shrink-0" />}
                  {msg.type === 'warning' && <AlertCircle className="h-4 w-4 text-yellow-500 mr-2 flex-shrink-0" />}
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
        </div>
      </div>
    </div>
  );
}

export default LiveMonitoringPage;
