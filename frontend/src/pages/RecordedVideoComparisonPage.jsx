import { useState, useEffect } from 'react';
import { Video, CheckCircle, XCircle, AlertTriangle, Upload, Loader } from 'lucide-react';
import { proceduresAPI, outlierProceduresAPI } from '../services/api';

function RecordedVideoComparisonPage() {
  const [inputMode, setInputMode] = useState('url'); // 'url' or 'upload'
  const [videoUrl, setVideoUrl] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [procedureId, setProcedureId] = useState('');
  const [procedureSource, setProcedureSource] = useState('standard');
  const [procedures, setProcedures] = useState([]);
  const [outlierProcedures, setOutlierProcedures] = useState([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [currentStep, setCurrentStep] = useState(''); // For progress tracking
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadProcedures();
    loadOutlierProcedures();
  }, []);

  const loadProcedures = async () => {
    try {
      const data = await proceduresAPI.getAll();
      setProcedures(data);
    } catch (err) {
      console.error('Failed to load procedures:', err);
    }
  };

  const loadOutlierProcedures = async () => {
    try {
      const data = await outlierProceduresAPI.getAll();
      setOutlierProcedures(data);
    } catch (err) {
      console.error('Failed to load outlier procedures:', err);
    }
  };

  const handleFileSelect = (event) => {
    const file = event.target.files[0];
    if (file) {
      // Validate file type
      const allowedTypes = ['video/mp4', 'video/avi', 'video/mov', 'video/quicktime'];
      if (!allowedTypes.includes(file.type)) {
        setError('Invalid file type. Please upload MP4, AVI, or MOV files.');
        return;
      }
      
      // Validate file size (max 500MB)
      const maxSize = 500 * 1024 * 1024;
      if (file.size > maxSize) {
        setError(`File too large. Maximum size is 500MB, got ${(file.size / 1024 / 1024).toFixed(2)}MB`);
        return;
      }
      
      setSelectedFile(file);
      setError(null);
    }
  };

  const uploadVideo = async () => {
    if (!selectedFile) return null;

    setIsUploading(true);
    setCurrentStep('Uploading video to cloud storage...');
    setUploadProgress(0);

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);

      const response = await fetch('/api/procedures/upload-video', {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Upload failed');
      }

      const data = await response.json();
      setUploadProgress(100);
      setCurrentStep(`Upload complete: ${data.filename} (${data.size_mb}MB)`);
      
      return data.gcs_uri;
    } catch (err) {
      throw new Error(`Upload failed: ${err.message}`);
    } finally {
      setIsUploading(false);
    }
  };

  const handleCompare = async () => {
    // Validation
    if (inputMode === 'url' && !videoUrl) {
      setError('Please provide a video URL');
      return;
    }
    if (inputMode === 'upload' && !selectedFile) {
      setError('Please select a video file');
      return;
    }
    if (!procedureId) {
      setError('Please select a procedure');
      return;
    }

    setError(null);
    setResults(null);
    let finalVideoUrl = videoUrl;

    try {
      // Step 1: Upload video if needed
      if (inputMode === 'upload') {
        finalVideoUrl = await uploadVideo();
        if (!finalVideoUrl) {
          throw new Error('Failed to get video URL after upload');
        }
      }

      // Step 2: Analyze video
      setIsAnalyzing(true);
      setCurrentStep('Analyzing video against procedure...');

      const response = await fetch('/api/procedures/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_gs_uri: finalVideoUrl,
          procedure_id: procedureId,
          procedure_source: procedureSource
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Comparison failed');
      }

      const data = await response.json();
      setResults(data);
      setCurrentStep('Analysis complete!');
    } catch (err) {
      setError(err.message);
      setCurrentStep('');
    } finally {
      setIsAnalyzing(false);
      setIsUploading(false);
    }
  };

  const currentProcedures = procedureSource === 'outlier' ? outlierProcedures : procedures;
  const items = results?.steps || results?.phases || [];

  return (
    <div className="container mx-auto p-6 max-w-7xl">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Recorded Video Comparison</h1>
        <p className="text-gray-600">
          Compare a recorded surgical video against a procedure to analyze step detection, 
          checkpoint validation, and error detection.
        </p>
      </div>

      {/* Input Section */}
      <div className="bg-white rounded-lg shadow-md p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4 flex items-center gap-2">
          <Upload size={24} />
          Video & Procedure Selection
        </h2>
        
        {/* Procedure Source Toggle */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Comparison Type
          </label>
          <div className="flex gap-3">
            <button
              onClick={() => {
                setProcedureSource('standard');
                setProcedureId('');
              }}
              className={`px-6 py-3 rounded-lg font-medium transition-colors ${
                procedureSource === 'standard'
                  ? 'bg-blue-600 text-white shadow-md'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              Standard Procedure
            </button>
            <button
              onClick={() => {
                setProcedureSource('outlier');
                setProcedureId('');
              }}
              className={`px-6 py-3 rounded-lg font-medium transition-colors ${
                procedureSource === 'outlier'
                  ? 'bg-orange-600 text-white shadow-md'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              Outlier/Error Resolution
            </button>
          </div>
          <p className="text-sm text-gray-500 mt-2">
            {procedureSource === 'standard' 
              ? 'Compare against master procedure steps'
              : 'Validate checkpoints and detect error codes'}
          </p>
        </div>

        {/* Input Mode Toggle */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Video Input Method
          </label>
          <div className="flex gap-3">
            <button
              onClick={() => {
                setInputMode('url');
                setSelectedFile(null);
                setError(null);
              }}
              className={`flex-1 px-4 py-3 rounded-lg font-medium transition-colors ${
                inputMode === 'url'
                  ? 'bg-blue-600 text-white shadow-md'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
              disabled={isAnalyzing || isUploading}
            >
              GCS URL
            </button>
            <button
              onClick={() => {
                setInputMode('upload');
                setVideoUrl('');
                setError(null);
              }}
              className={`flex-1 px-4 py-3 rounded-lg font-medium transition-colors ${
                inputMode === 'upload'
                  ? 'bg-blue-600 text-white shadow-md'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
              disabled={isAnalyzing || isUploading}
            >
              Upload File
            </button>
          </div>
        </div>

        {/* Video URL Input (shown when inputMode is 'url') */}
        {inputMode === 'url' && (
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Video GCS URL <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={videoUrl}
              onChange={(e) => setVideoUrl(e.target.value)}
              placeholder="gs://bucket-name/video.mp4"
              className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              disabled={isAnalyzing || isUploading}
            />
            <p className="text-sm text-gray-500 mt-1">
              Enter the Google Cloud Storage URI of your recorded surgical video
            </p>
          </div>
        )}

        {/* File Upload Input (shown when inputMode is 'upload') */}
        {inputMode === 'upload' && (
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Upload Video File <span className="text-red-500">*</span>
            </label>
            <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center hover:border-blue-500 transition-colors">
              <input
                type="file"
                accept="video/mp4,video/avi,video/mov,video/quicktime"
                onChange={handleFileSelect}
                className="hidden"
                id="video-upload"
                disabled={isAnalyzing || isUploading}
              />
              <label
                htmlFor="video-upload"
                className="cursor-pointer flex flex-col items-center"
              >
                <Upload className="h-12 w-12 text-gray-400 mb-2" />
                {selectedFile ? (
                  <div>
                    <p className="text-sm font-medium text-gray-900">{selectedFile.name}</p>
                    <p className="text-sm text-gray-500">
                      {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                    </p>
                  </div>
                ) : (
                  <div>
                    <p className="text-sm font-medium text-gray-900">
                      Click to upload or drag and drop
                    </p>
                    <p className="text-sm text-gray-500">MP4, AVI, or MOV (max 500MB)</p>
                  </div>
                )}
              </label>
            </div>
          </div>
        )}

        {/* Procedure Selection */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Select Procedure <span className="text-red-500">*</span>
          </label>
          <select
            value={procedureId}
            onChange={(e) => setProcedureId(e.target.value)}
            className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            disabled={isAnalyzing}
          >
            <option value="">-- Select a Procedure --</option>
            {currentProcedures.map((proc) => (
              <option key={proc.id} value={proc.id}>
                {proc.procedure_name}
              </option>
            ))}
          </select>
        </div>

        {/* Progress Indicator */}
        {(isUploading || isAnalyzing) && currentStep && (
          <div className="mb-4 bg-blue-50 border border-blue-200 rounded-lg p-4">
            <div className="flex items-center gap-3">
              <Loader className="animate-spin text-blue-600" size={20} />
              <div className="flex-1">
                <p className="text-sm font-medium text-blue-900">{currentStep}</p>
                {isUploading && uploadProgress > 0 && (
                  <div className="mt-2 bg-blue-200 rounded-full h-2 overflow-hidden">
                    <div
                      className="bg-blue-600 h-full transition-all duration-300"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Compare Button */}
        <button
          onClick={handleCompare}
          disabled={isAnalyzing || isUploading || !procedureId || (inputMode === 'url' && !videoUrl) || (inputMode === 'upload' && !selectedFile)}
          className="w-full bg-blue-600 text-white py-3 px-6 rounded-lg font-semibold hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
        >
          {isUploading || isAnalyzing ? (
            <>
              <Loader className="animate-spin" size={20} />
              {isUploading ? 'Uploading...' : 'Analyzing Video...'}
            </>
          ) : (
            <>
              <Video size={20} />
              Compare Video
            </>
          )}
        </button>
      </div>

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-6 flex items-start gap-2">
          <AlertTriangle className="flex-shrink-0 mt-0.5" size={20} />
          <div>
            <p className="font-semibold">Error</p>
            <p>{error}</p>
          </div>
        </div>
      )}

      {/* Results Display */}
      {results && (
        <div className="space-y-6">
          {/* Summary Card */}
          <div className="bg-white rounded-lg shadow-md p-6">
            <h2 className="text-2xl font-bold mb-4">Analysis Results</h2>
            <p className="text-gray-600 mb-4">
              Procedure: <span className="font-semibold">{results.procedure_name}</span>
            </p>
            
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-blue-50 rounded-lg p-4 text-center">
                <div className="text-3xl font-bold text-blue-600">
                  {results.summary.detection_rate_percent}%
                </div>
                <div className="text-sm text-gray-600 mt-1">Detection Rate</div>
              </div>
              
              <div className="bg-green-50 rounded-lg p-4 text-center">
                <div className="text-3xl font-bold text-green-600">
                  {results.summary.detected_steps || results.summary.detected_phases}
                </div>
                <div className="text-sm text-gray-600 mt-1">
                  {procedureSource === 'outlier' ? 'Phases' : 'Steps'} Detected
                </div>
              </div>
              
              <div className="bg-purple-50 rounded-lg p-4 text-center">
                <div className="text-3xl font-bold text-purple-600">
                  {results.summary.completed_steps || results.summary.completed_phases}
                </div>
                <div className="text-sm text-gray-600 mt-1">Completed</div>
              </div>
              
              {procedureSource === 'outlier' && (
                <div className="bg-orange-50 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-orange-600">
                    {results.summary.errors_detected || 0}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Errors Detected</div>
                </div>
              )}
              
              {procedureSource === 'outlier' && (
                <div className="bg-indigo-50 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-indigo-600">
                    {results.summary.checkpoints_met || 0}/{results.summary.total_checkpoints || 0}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Checkpoints Met</div>
                </div>
              )}
            </div>
          </div>

          {/* Steps/Phases List */}
          <div className="bg-white rounded-lg shadow-md p-6">
            <h3 className="text-xl font-semibold mb-4">
              {procedureSource === 'outlier' ? 'Phase Details' : 'Step Details'}
            </h3>
            
            <div className="space-y-3">
              {items.map((item, index) => (
                <div
                  key={index}
                  className={`border rounded-lg p-4 transition-all ${
                    item.detected 
                      ? 'border-green-300 bg-green-50' 
                      : 'border-gray-300 bg-gray-50'
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className="flex-shrink-0 mt-1">
                      {item.detected ? (
                        <CheckCircle className="text-green-600" size={24} />
                      ) : (
                        <XCircle className="text-gray-400" size={24} />
                      )}
                    </div>
                    
                    <div className="flex-1">
                      <div className="flex items-start justify-between mb-2">
                        <h4 className="font-semibold text-lg">
                          {procedureSource === 'outlier' ? 'Phase' : 'Step'}{' '}
                          {item.step_number || item.phase_number}: {item.step_name || item.phase_name}
                        </h4>
                        
                        {item.detected && (
                          <span className={`px-3 py-1 rounded-full text-sm font-medium ${
                            item.completion === 'COMPLETED'
                              ? 'bg-green-200 text-green-800'
                              : item.completion === 'PARTIAL'
                              ? 'bg-yellow-200 text-yellow-800'
                              : 'bg-gray-200 text-gray-800'
                          }`}>
                            {item.completion}
                          </span>
                        )}
                      </div>
                      
                      {item.description && (
                        <p className="text-sm text-gray-600 mb-2">{item.description}</p>
                      )}
                      
                      {item.detected && item.evidence && (
                        <div className="bg-white rounded p-3 mb-2">
                          <p className="text-sm">
                            <span className="font-semibold text-gray-700">Evidence:</span>{' '}
                            <span className="text-gray-600">{item.evidence}</span>
                          </p>
                        </div>
                      )}

                      {/* Checkpoints for Outlier Mode */}
                      {procedureSource === 'outlier' && item.detected && (
                        <div className="mt-3 space-y-2">
                          <div className="flex items-center gap-2 text-sm font-medium">
                            <span className="text-gray-700">
                              Checkpoints: {item.checkpoints_satisfied}/{item.total_checkpoints}
                            </span>
                            {item.checkpoints_satisfied === item.total_checkpoints && (
                              <CheckCircle className="text-green-600" size={16} />
                            )}
                          </div>
                          
                          {item.checkpoints_met && item.checkpoints_met.length > 0 && (
                            <div className="bg-green-50 rounded p-2">
                              <p className="text-sm font-medium text-green-800 mb-1">✓ Met:</p>
                              <ul className="text-sm text-green-700 space-y-1">
                                {item.checkpoints_met.map((cp, idx) => (
                                  <li key={idx}>• {cp.name}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                          
                          {item.checkpoints_not_met && item.checkpoints_not_met.length > 0 && (
                            <div className="bg-red-50 rounded p-2">
                              <p className="text-sm font-medium text-red-800 mb-1">✗ Not Met:</p>
                              <ul className="text-sm text-red-700 space-y-1">
                                {item.checkpoints_not_met.map((cp, idx) => (
                                  <li key={idx}>• {cp.name}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Errors (Outlier Mode Only) */}
          {procedureSource === 'outlier' && results.errors && results.errors.length > 0 && (
            <div className="bg-white rounded-lg shadow-md p-6">
              <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
                <AlertTriangle className="text-red-600" size={24} />
                Error Codes Detected
              </h3>
              <div className="space-y-3">
                {results.errors.map((error, index) => (
                  <div key={index} className="bg-red-50 border border-red-200 rounded-lg p-4">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="text-red-600 flex-shrink-0 mt-0.5" size={20} />
                      <div>
                        <span className="font-semibold text-red-700">{error.code}:</span>{' '}
                        <span className="text-red-600">{error.description}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default RecordedVideoComparisonPage;
