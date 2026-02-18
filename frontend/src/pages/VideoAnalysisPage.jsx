import { useState } from 'react';
import { Upload, Loader, CheckCircle, AlertCircle } from 'lucide-react';
import { proceduresAPI } from '../services/api';
import '../styles/components/VideoAnalysisPage.css';
import '../styles/common/buttons.css';
import '../styles/common/cards.css';
import '../styles/common/forms.css';

function VideoAnalysisPage() {
  const [videoUri, setVideoUri] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const formatMinutes = (value) => {
    if (value === null || value === undefined) return null;
    const numberValue = typeof value === 'number' ? value : parseFloat(value);
    if (Number.isNaN(numberValue)) return null;
    return `${numberValue} minutes`;
  };

  const handleAnalyze = async () => {
    if (!videoUri) {
      setError('Please enter a video GCS URI');
      return;
    }

    setLoading(true);
    setError('');
    setResult(null);

    try {
      const data = await proceduresAPI.analyzeVideo(videoUri);
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="video-analysis-container">
      <h1 className="video-analysis-title">Analyze Surgical Video</h1>

      <div className="video-input-card">
        <label className="form-label">
          Video GCS URI
        </label>
        <input
          type="text"
          value={videoUri}
          onChange={(e) => setVideoUri(e.target.value)}
          placeholder="gs://bucket-name/video.mp4"
          className="form-input"
          style={{ marginBottom: '1rem' }}
        />
        <button
          onClick={handleAnalyze}
          disabled={loading}
          className="btn btn-primary"
        >
          {loading ? (
            <>
              <Loader className="btn-icon animate-spin" />
              Analyzing...
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
