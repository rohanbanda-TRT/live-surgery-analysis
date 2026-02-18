import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Procedures API
export const proceduresAPI = {
  // Get all procedures
  getAll: async () => {
    const response = await api.get('/api/procedures');
    return response.data;
  },

  // Get single procedure by ID
  getById: async (id) => {
    const response = await api.get(`/api/procedures/${id}`);
    return response.data;
  },

  // Analyze video
  analyzeVideo: async (videoGsUri) => {
    const response = await api.post('/api/procedures/analyze', {
      video_gs_uri: videoGsUri,
    });
    return response.data;
  },
};

// Sessions API
export const sessionsAPI = {
  // Get session alerts
  getAlerts: async (sessionId) => {
    const response = await api.get(`/api/sessions/${sessionId}/alerts`);
    return response.data;
  },
};

export default api;
