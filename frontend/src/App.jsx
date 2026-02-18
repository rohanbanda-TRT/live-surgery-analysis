import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import { Activity } from 'lucide-react';
import HomePage from './pages/HomePage';
import VideoAnalysisPage from './pages/VideoAnalysisPage';
import LiveMonitoringPage from './pages/LiveMonitoringPage';
import ProceduresPage from './pages/ProceduresPage';

function App() {
  return (
    <Router>
      <div className="min-h-screen bg-gray-50">
        {/* Navigation */}
        <nav className="bg-white shadow-sm border-b">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex justify-between h-16">
              <div className="flex">
                <Link to="/" className="flex items-center">
                  <Activity className="h-8 w-8 text-primary-600" />
                  <span className="ml-2 text-xl font-bold text-gray-900">
                    Surgical Analysis Platform
                  </span>
                </Link>
              </div>
              <div className="flex space-x-8">
                <Link
                  to="/"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-900 hover:text-primary-600"
                >
                  Home
                </Link>
                <Link
                  to="/procedures"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-primary-600"
                >
                  Procedures
                </Link>
                <Link
                  to="/analyze"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-primary-600"
                >
                  Analyze Video
                </Link>
                <Link
                  to="/live"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-primary-600"
                >
                  Live Monitoring
                </Link>
              </div>
            </div>
          </div>
        </nav>

        {/* Main Content */}
        <main className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/procedures" element={<ProceduresPage />} />
            <Route path="/analyze" element={<VideoAnalysisPage />} />
            <Route path="/live" element={<LiveMonitoringPage />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;
