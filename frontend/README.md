# Surgical Analysis Platform - Frontend

React-based frontend for testing the live surgery monitoring system with WebSocket support.

## 🚀 Quick Start

### Prerequisites

- Node.js 20.x (use `nvm use 20`)
- Backend API running on `http://localhost:8000`

### Installation

```bash
# Switch to Node 20
nvm use 20

# Install dependencies
npm install

# Start development server
npm run dev
```

The app will be available at `http://localhost:3000`

## 📁 Project Structure

```
frontend/
├── src/
│   ├── components/          # Reusable React components
│   ├── pages/              # Page components
│   │   ├── HomePage.jsx           # Landing page
│   │   ├── VideoAnalysisPage.jsx  # Video analysis interface
│   │   ├── LiveMonitoringPage.jsx # Live surgery monitoring
│   │   └── ProceduresPage.jsx     # Browse procedures
│   ├── services/           # API and WebSocket services
│   │   ├── api.js                 # REST API client
│   │   └── websocket.js           # WebSocket client
│   ├── hooks/              # Custom React hooks
│   ├── utils/              # Utility functions
│   ├── App.jsx             # Main app component
│   ├── main.jsx            # Entry point
│   └── index.css           # Global styles
├── public/                 # Static assets
├── index.html              # HTML template
├── vite.config.js          # Vite configuration
├── tailwind.config.js      # Tailwind CSS config
└── package.json            # Dependencies
```

## 🧪 Testing Live Surgery Monitoring

### Step 1: Analyze a Video First

Before you can test live monitoring, you need to create a procedure by analyzing a video:

1. Go to **Analyze Video** page
2. Enter a GCS URI: `gs://your-bucket/video.mp4`
3. Click **Analyze Video**
4. Note the `procedure_id` from the response

### Step 2: Start Live Monitoring

1. Go to **Live Monitoring** page
2. Select the procedure you just created
3. Enter a unique session ID (e.g., `session-001`)
4. Enter surgeon ID (e.g., `surgeon-001`)
5. Click **Connect Session**
6. Once connected, click **Start Video Stream**
7. Allow camera access when prompted
8. The system will start sending frames to the backend
9. Watch for alerts in the Alerts panel

### Step 3: Monitor Activity

- **Video Feed**: Shows your camera feed
- **Activity Log**: Shows connection status and events
- **Alerts Panel**: Displays AI-generated alerts for:
  - Step deviations
  - Safety concerns
  - Missing instruments
  - Compliance issues

## 🎯 Features

### Video Analysis
- Upload surgical videos from Google Cloud Storage
- Automatic procedure type detection
- Extract surgical steps, instruments, and landmarks
- View analysis results

### Live Monitoring
- Real-time WebSocket connection
- Camera feed streaming
- Frame-by-frame analysis
- Intelligent alert system
- Session management

### Procedures Browser
- View all analyzed procedures
- Browse surgical steps
- See procedure details
- Identify critical steps

## 🔧 Configuration

### Environment Variables

Create a `.env` file in the frontend directory:

```env
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
```

### API Proxy

The Vite dev server is configured to proxy API requests:

- `/api/*` → `http://localhost:8000/api/*`
- `/ws/*` → `ws://localhost:8000/ws/*`

## 📡 WebSocket Protocol

### Connection Flow

1. **Connect**: `ws://localhost:8000/api/sessions/ws/{session_id}`
2. **Initialize**: Send JSON with `procedure_id` and `surgeon_id`
3. **Receive Confirmation**: Get `session_started` message
4. **Stream Frames**: Send JPEG frames as binary data
5. **Receive Alerts**: Get `alerts` messages
6. **Stop**: Send `{"type": "stop"}` message

### Message Types

**Client → Server:**
```javascript
// Initialize session
{
  "procedure_id": "507f1f77bcf86cd799439011",
  "surgeon_id": "surgeon-001"
}

// Stop session
{
  "type": "stop"
}

// Video frame (binary JPEG data)
<Blob>
```

**Server → Client:**
```javascript
// Session started
{
  "type": "session_started",
  "data": {
    "procedure_name": "Laparoscopic Cholecystectomy",
    "total_steps": 8
  }
}

// Alerts
{
  "type": "alerts",
  "data": [
    {
      "alert_type": "step_deviation",
      "severity": "warning",
      "message": "Possible deviation from expected step",
      "metadata": {...}
    }
  ]
}
```

## 🛠️ Development

### Available Scripts

```bash
# Start dev server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Lint code
npm run lint
```

### Tech Stack

- **React 18** - UI library
- **Vite** - Build tool
- **Tailwind CSS** - Styling
- **React Router** - Routing
- **Axios** - HTTP client
- **Lucide React** - Icons
- **WebSocket API** - Real-time communication

## 🐛 Troubleshooting

### WebSocket Connection Issues

**Problem**: Cannot connect to WebSocket

**Solutions**:
1. Ensure backend is running on `http://localhost:8000`
2. Check that MongoDB is connected
3. Verify procedure_id exists in database
4. Check browser console for errors

### Camera Access Issues

**Problem**: Cannot access camera

**Solutions**:
1. Grant camera permissions in browser
2. Use HTTPS in production (required for camera access)
3. Check if another app is using the camera
4. Try a different browser

### No Procedures Available

**Problem**: Procedure dropdown is empty

**Solutions**:
1. Analyze a video first using the Video Analysis page
2. Check backend logs for analysis errors
3. Verify MongoDB connection
4. Ensure Gemini API credentials are configured

### CORS Errors

**Problem**: CORS policy blocking requests

**Solutions**:
1. Ensure backend CORS is configured for `http://localhost:3000`
2. Check `.env` file in backend has correct `ALLOWED_ORIGINS`
3. Restart backend after changing CORS settings

## 📝 Testing Checklist

- [ ] Backend API is running
- [ ] MongoDB is connected
- [ ] At least one procedure is analyzed
- [ ] Frontend dev server is running
- [ ] Camera permissions granted
- [ ] WebSocket connects successfully
- [ ] Video frames are streaming
- [ ] Alerts are displayed
- [ ] Session can be stopped cleanly

## 🚀 Production Deployment

### Build

```bash
npm run build
```

Output will be in `dist/` directory.

### Deploy

Deploy the `dist/` folder to:
- Netlify
- Vercel
- AWS S3 + CloudFront
- Google Cloud Storage + Load Balancer

### Environment Variables

Set these in your hosting platform:
- `VITE_API_URL`: Your backend API URL
- `VITE_WS_URL`: Your WebSocket URL (wss:// for production)

## 📚 Additional Resources

- [Vite Documentation](https://vitejs.dev/)
- [React Documentation](https://react.dev/)
- [Tailwind CSS](https://tailwindcss.com/)
- [WebSocket API](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)

## 🤝 Contributing

1. Follow React best practices
2. Use functional components with hooks
3. Keep components small and focused
4. Add PropTypes or TypeScript for type safety
5. Test WebSocket connections thoroughly

---

**Built with React + Vite + Tailwind CSS**
