/**
 * WebSocket service for live surgery monitoring
 */

const WS_BASE_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000';

class LiveSurgeryWebSocket {
  constructor() {
    this.ws = null;
    this.sessionId = null;
    this.onMessageCallback = null;
    this.onErrorCallback = null;
    this.onCloseCallback = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
  }

  /**
   * Connect to the WebSocket server
   * @param {string} sessionId - Unique session identifier
   * @param {string} procedureId - ID of the master procedure
   * @param {string} surgeonId - ID of the surgeon
   */
  connect(sessionId, procedureId, surgeonId = 'default-surgeon') {
    return new Promise((resolve, reject) => {
      this.sessionId = sessionId;
      const wsUrl = `${WS_BASE_URL}/api/sessions/ws/${sessionId}`;

      console.log(`Connecting to WebSocket: ${wsUrl}`);

      try {
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
          console.log('WebSocket connected');
          this.reconnectAttempts = 0;

          // Send initial configuration
          const initMessage = {
            procedure_id: procedureId,
            surgeon_id: surgeonId,
          };

          this.ws.send(JSON.stringify(initMessage));
          console.log('Sent initialization:', initMessage);
        };

        this.ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            console.log('Received message:', data);

            if (data.type === 'session_started') {
              resolve(data.data);
            }

            if (this.onMessageCallback) {
              this.onMessageCallback(data);
            }
          } catch (error) {
            console.error('Error parsing message:', error);
          }
        };

        this.ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          if (this.onErrorCallback) {
            this.onErrorCallback(error);
          }
          reject(error);
        };

        this.ws.onclose = (event) => {
          console.log('WebSocket closed:', event.code, event.reason);
          if (this.onCloseCallback) {
            this.onCloseCallback(event);
          }

          // Attempt to reconnect
          if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            console.log(`Reconnecting... Attempt ${this.reconnectAttempts}`);
            setTimeout(() => {
              this.connect(sessionId, procedureId, surgeonId);
            }, 2000 * this.reconnectAttempts);
          }
        };
      } catch (error) {
        console.error('Error creating WebSocket:', error);
        reject(error);
      }
    });
  }

  /**
   * Send video frame to the server
   * @param {Blob|ArrayBuffer} frameData - Video frame data
   */
  sendFrame(frameData) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(frameData);
    } else {
      console.warn('WebSocket not connected. Cannot send frame.');
    }
  }

  /**
   * Stop the session
   */
  stop() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const stopMessage = { type: 'stop' };
      this.ws.send(JSON.stringify(stopMessage));
      console.log('Sent stop message');
    }
  }

  /**
   * Close the WebSocket connection
   */
  close() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  /**
   * Set callback for incoming messages
   * @param {Function} callback
   */
  onMessage(callback) {
    this.onMessageCallback = callback;
  }

  /**
   * Set callback for errors
   * @param {Function} callback
   */
  onError(callback) {
    this.onErrorCallback = callback;
  }

  /**
   * Set callback for connection close
   * @param {Function} callback
   */
  onClose(callback) {
    this.onCloseCallback = callback;
  }

  /**
   * Check if WebSocket is connected
   * @returns {boolean}
   */
  isConnected() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }
}

export default LiveSurgeryWebSocket;
