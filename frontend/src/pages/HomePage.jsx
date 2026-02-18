import { Link } from 'react-router-dom';
import { Video, Activity, FileText } from 'lucide-react';

function HomePage() {
  return (
    <div className="px-4 py-8">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold text-gray-900 mb-4">
          Surgical Analysis Platform
        </h1>
        <p className="text-xl text-gray-600 max-w-3xl mx-auto">
          AI-powered surgical video analysis and real-time monitoring using Google Gemini 2.5 Flash
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-12">
        {/* Video Analysis Card */}
        <Link
          to="/analyze"
          className="bg-white rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow"
        >
          <div className="flex items-center justify-center w-12 h-12 bg-primary-100 rounded-lg mb-4">
            <Video className="h-6 w-6 text-primary-600" />
          </div>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">
            Analyze Video
          </h3>
          <p className="text-gray-600">
            Upload surgical videos to automatically extract procedural steps, instruments, and landmarks
          </p>
        </Link>

        {/* Live Monitoring Card */}
        <Link
          to="/live"
          className="bg-white rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow"
        >
          <div className="flex items-center justify-center w-12 h-12 bg-green-100 rounded-lg mb-4">
            <Activity className="h-6 w-6 text-green-600" />
          </div>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">
            Live Monitoring
          </h3>
          <p className="text-gray-600">
            Monitor live surgeries in real-time with AI-powered step detection and compliance checking
          </p>
        </Link>

        {/* Procedures Card */}
        <Link
          to="/procedures"
          className="bg-white rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow"
        >
          <div className="flex items-center justify-center w-12 h-12 bg-purple-100 rounded-lg mb-4">
            <FileText className="h-6 w-6 text-purple-600" />
          </div>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">
            View Procedures
          </h3>
          <p className="text-gray-600">
            Browse analyzed procedures and their detailed surgical steps
          </p>
        </Link>
      </div>

      {/* Features Section */}
      <div className="mt-16 bg-white rounded-lg shadow-md p-8">
        <h2 className="text-2xl font-bold text-gray-900 mb-6">Key Features</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <h3 className="font-semibold text-gray-900 mb-2">✅ Automatic Step Detection</h3>
            <p className="text-gray-600">AI identifies surgical steps, instruments, and anatomical landmarks</p>
          </div>
          <div>
            <h3 className="font-semibold text-gray-900 mb-2">✅ Real-Time Monitoring</h3>
            <p className="text-gray-600">WebSocket-based live surgery monitoring with instant alerts</p>
          </div>
          <div>
            <h3 className="font-semibold text-gray-900 mb-2">✅ Compliance Checking</h3>
            <p className="text-gray-600">Detect deviations from standard procedures and safety concerns</p>
          </div>
          <div>
            <h3 className="font-semibold text-gray-900 mb-2">✅ Intelligent Alerts</h3>
            <p className="text-gray-600">Get notified of missed steps, sequence issues, or safety checkpoints</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default HomePage;
