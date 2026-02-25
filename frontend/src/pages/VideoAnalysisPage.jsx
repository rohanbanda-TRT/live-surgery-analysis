import { useState } from 'react';
import { Upload, Loader, CheckCircle, AlertCircle } from 'lucide-react';
import { proceduresAPI } from '../services/api';
import '../styles/components/VideoAnalysisPage.css';
import '../styles/common/buttons.css';
import '../styles/common/cards.css';
import '../styles/common/forms.css';

function VideoAnalysisPage() {
  const [inputMode, setInputMode] = useState('url'); // 'url' or 'upload'
  const [videoUri, setVideoUri] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [currentStep, setCurrentStep] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const formatMinutes = (value) => {
    if (value === null || value === undefined) return null;
    const numberValue = typeof value === 'number' ? value : parseFloat(value);
    if (Number.isNaN(numberValue)) return null;
    return `${numberValue} minutes`;
  };

  const handleFileSelect = (event) => {
    const file = event.target.files[0];
    if (file) {
      const allowedTypes = ['video/mp4', 'video/avi', 'video/mov', 'video/quicktime'];
      if (!allowedTypes.includes(file.type)) {
        setError('Invalid file type. Please upload MP4, AVI, or MOV files.');
        return;
      }
      
      const maxSize = 500 * 1024 * 1024;
      if (file.size > maxSize) {
        setError(`File too large. Maximum size is 500MB, got ${(file.size / 1024 / 1024).toFixed(2)}MB`);
        return;
      }
      
      setSelectedFile(file);
      setError('');
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

  const handleAnalyze = async () => {
    if (inputMode === 'url' && !videoUri) {
      setError('Please enter a video GCS URI');
      return;
    }
    if (inputMode === 'upload' && !selectedFile) {
      setError('Please select a video file');
      return;
    }

    setError('');
    setResult(null);
    let finalVideoUri = videoUri;

    try {
      // Step 1: Upload video if needed
      if (inputMode === 'upload') {
        finalVideoUri = await uploadVideo();
        if (!finalVideoUri) {
          throw new Error('Failed to get video URL after upload');
        }
      }

      // Step 2: Analyze video
      setLoading(true);
      setCurrentStep('Analyzing video and extracting procedure steps...');

      const data = await proceduresAPI.analyzeVideo(finalVideoUri);
      setResult(data);
      setCurrentStep('Analysis complete!');
    } catch (err) {
      setError(err.message);
      setCurrentStep('');
    } finally {
      setLoading(false);
      setIsUploading(false);
    }
  };

  return (
    <div className="video-analysis-container">
      <h1 className="video-analysis-title">Analyze Surgical Video</h1>

      <div className="video-input-card">
        {/* Input Mode Toggle */}
        <div style={{ marginBottom: '1.5rem' }}>
          <label className="form-label">Video Input Method</label>
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.5rem' }}>
            <button
              onClick={() => {
                setInputMode('url');
                setSelectedFile(null);
                setError('');
              }}
              className={inputMode === 'url' ? 'btn btn-primary' : 'btn btn-secondary'}
              disabled={loading || isUploading}
              style={{ flex: 1 }}
            >
              GCS URL
            </button>
            <button
              onClick={() => {
                setInputMode('upload');
                setVideoUri('');
                setError('');
              }}
              className={inputMode === 'upload' ? 'btn btn-primary' : 'btn btn-secondary'}
              disabled={loading || isUploading}
              style={{ flex: 1 }}
            >
              Upload File
            </button>
          </div>
        </div>

        {/* URL Input */}
        {inputMode === 'url' && (
          <div style={{ marginBottom: '1rem' }}>
            <label className="form-label">Video GCS URI</label>
            <input
              type="text"
              value={videoUri}
              onChange={(e) => setVideoUri(e.target.value)}
              placeholder="gs://bucket-name/video.mp4"
              className="form-input"
              disabled={loading || isUploading}
            />
          </div>
        )}

        {/* File Upload */}
        {inputMode === 'upload' && (
          <div style={{ marginBottom: '1rem' }}>
            <label className="form-label">Upload Video File</label>
            <div style={{
              border: '2px dashed #d1d5db',
              borderRadius: '0.5rem',
              padding: '1.5rem',
              textAlign: 'center',
              cursor: 'pointer',
              transition: 'border-color 0.2s'
            }}>
              <input
                type="file"
                accept="video/mp4,video/avi,video/mov,video/quicktime"
                onChange={handleFileSelect}
                style={{ display: 'none' }}
                id="video-file-upload"
                disabled={loading || isUploading}
              />
              <label htmlFor="video-file-upload" style={{ cursor: 'pointer', display: 'block' }}>
                <Upload style={{ margin: '0 auto 0.5rem', color: '#9ca3af' }} size={48} />
                {selectedFile ? (
                  <div>
                    <p style={{ fontWeight: 500, marginBottom: '0.25rem' }}>{selectedFile.name}</p>
                    <p style={{ fontSize: '0.875rem', color: '#6b7280' }}>
                      {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                    </p>
                  </div>
                ) : (
                  <div>
                    <p style={{ fontWeight: 500, marginBottom: '0.25rem' }}>Click to upload or drag and drop</p>
                    <p style={{ fontSize: '0.875rem', color: '#6b7280' }}>MP4, AVI, or MOV (max 500MB)</p>
                  </div>
                )}
              </label>
            </div>
          </div>
        )}

        {/* Progress Indicator */}
        {(isUploading || loading) && currentStep && (
          <div style={{
            backgroundColor: '#eff6ff',
            border: '1px solid #bfdbfe',
            borderRadius: '0.5rem',
            padding: '1rem',
            marginBottom: '1rem'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <Loader className="animate-spin" style={{ color: '#2563eb' }} size={20} />
              <div style={{ flex: 1 }}>
                <p style={{ fontSize: '0.875rem', fontWeight: 500, color: '#1e3a8a' }}>{currentStep}</p>
                {isUploading && uploadProgress > 0 && (
                  <div style={{
                    marginTop: '0.5rem',
                    backgroundColor: '#bfdbfe',
                    borderRadius: '9999px',
                    height: '0.5rem',
                    overflow: 'hidden'
                  }}>
                    <div style={{
                      backgroundColor: '#2563eb',
                      height: '100%',
                      width: `${uploadProgress}%`,
                      transition: 'width 0.3s'
                    }} />
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        <button
          onClick={handleAnalyze}
          disabled={loading || isUploading || (inputMode === 'url' && !videoUri) || (inputMode === 'upload' && !selectedFile)}
          className="btn btn-primary"
        >
          {isUploading || loading ? (
            <>
              <Loader className="btn-icon animate-spin" />
              {isUploading ? 'Uploading...' : 'Analyzing...'}
            </>
          ) : (
            <>
              <Upload className="btn-icon" />
              Analyze Video
            </>
          )}
        </button>
      </div>

      {error && (
        <div className="alert alert-error">
          <div className="flex-row">
            <AlertCircle className="alert-icon" />
            <p className="alert-error-text">{error}</p>
          </div>
        </div>
      )}

      {result && (
        <div className="results-container">
          {/* Success Message */}
          <div className="alert alert-success">
            <div className="flex-row">
              <CheckCircle className="alert-icon" />
              <p className="alert-success-text">{result.message}</p>
            </div>
          </div>

          {/* Procedure Overview */}
          <div className="procedure-overview">
            <h2 className="procedure-overview-title">Procedure Overview</h2>
            <div className="procedure-grid">
              <div className="procedure-info-item">
                <p className="procedure-info-label">Procedure ID</p>
                <p className="procedure-info-value">{result.procedure_id}</p>
              </div>
              <div className="procedure-info-item">
                <p className="procedure-info-label">Procedure Name</p>
                <p className="procedure-info-value">{result.procedure_name}</p>
              </div>
              <div className="procedure-info-item">
                <p className="procedure-info-label">Procedure Type</p>
                <p className="procedure-info-value">{result.procedure_type}</p>
              </div>
              <div className="procedure-info-item">
                <p className="procedure-info-label">Steps Extracted</p>
                <p className="procedure-info-value">{result.steps_count}</p>
              </div>
              {result.total_duration_avg && (
                <div className="procedure-info-item">
                  <p className="procedure-info-label">Average Duration</p>
                  <p className="procedure-info-value">{formatMinutes(result.total_duration_avg)}</p>
                </div>
              )}
              {result.video_duration && (
                <div className="procedure-info-item">
                  <p className="procedure-info-label">Video Duration</p>
                  <p className="procedure-info-value">{formatMinutes(result.video_duration)}</p>
                </div>
              )}
              {result.difficulty_level && (
                <div className="procedure-info-item">
                  <p className="procedure-info-label">Difficulty Level</p>
                  <p className="procedure-info-value capitalize">{result.difficulty_level}</p>
                </div>
              )}
            </div>
            
            {result.characteristics && (
              <div className="procedure-characteristics">
                <p className="procedure-characteristics-label">Characteristics</p>
                <p className="procedure-characteristics-text">{result.characteristics}</p>
              </div>
            )}
          </div>

          {/* Surgical Steps */}
          {result.steps && result.steps.length > 0 && (
            <div className="surgical-steps-section">
              <h2 className="surgical-steps-title">Surgical Steps</h2>
              <div className="steps-list">
                {result.steps.map((step, index) => (
                  <div key={index} className="step-card">
                    <div className="step-header">
                      <div className="step-title-row">
                        <span className="step-number">
                          {step.step_number}
                        </span>
                        <h3 className="step-name">{step.step_name}</h3>
                      </div>
                      {step.is_critical && (
                        <span className="step-critical-badge">
                          Critical Step
                        </span>
                      )}
                    </div>
                    
                    {step.description && (
                      <p className="step-description">{step.description}</p>
                    )}
                    
                    <div className="step-metadata">
                      {step.expected_duration_min && step.expected_duration_max && (
                        <div className="step-metadata-item">
                          <span className="step-metadata-label">Duration:</span>
                          <span className="step-metadata-value">
                            {step.expected_duration_min}-{step.expected_duration_max} min
                          </span>
                        </div>
                      )}
                      
                      {step.timestamp_start && step.timestamp_end && (
                        <div className="step-metadata-item">
                          <span className="step-metadata-label">Timestamp:</span>
                          <span className="step-metadata-value">
                            {step.timestamp_start} - {step.timestamp_end}
                          </span>
                        </div>
                      )}
                    </div>
                    
                    {step.instruments_required && step.instruments_required.length > 0 && (
                      <div className="step-section">
                        <span className="step-section-label">Instruments:</span>
                        <div className="step-badges-container">
                          {step.instruments_required.map((instrument, idx) => (
                            <span key={idx} className="step-badge-instrument">
                              {instrument}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    
                    {step.anatomical_landmarks && step.anatomical_landmarks.length > 0 && (
                      <div className="step-section">
                        <span className="step-section-label">Anatomical Landmarks:</span>
                        <div className="step-badges-container">
                          {step.anatomical_landmarks.map((landmark, idx) => (
                            <span key={idx} className="step-badge-landmark">
                              {landmark}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    
                    {step.visual_cues && (
                      <div className="step-section">
                        <span className="step-section-label">Visual Cues:</span>
                        <p className="step-visual-cues">{step.visual_cues}</p>
                      </div>
                    )}
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

export default VideoAnalysisPage;
