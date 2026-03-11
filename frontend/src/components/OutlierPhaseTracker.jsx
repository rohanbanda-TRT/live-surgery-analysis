import { useState } from 'react';
import { ChevronDown, ChevronRight, AlertCircle, Bell } from 'lucide-react';

/**
 * Component for displaying outlier resolution protocol phases with checkpoints.
 * Compact view with expandable details matching the design.
 */
function OutlierPhaseTracker({ phases = [], currentPhaseIndex = 0 }) {
  const [expandedPhases, setExpandedPhases] = useState(new Set([0])); // Expand first phase by default

  // Debug logging
  console.log('[OutlierPhaseTracker] Received props:', {
    phases_count: phases?.length,
    currentPhaseIndex,
    phases_sample: phases?.[0],
    all_phases: phases
  });

  if (!phases || phases.length === 0) {
    console.log('[OutlierPhaseTracker] No phases to display');
    return null;
  }

  const togglePhase = (index) => {
    const newExpanded = new Set(expandedPhases);
    if (newExpanded.has(index)) {
      newExpanded.delete(index);
    } else {
      newExpanded.add(index);
    }
    setExpandedPhases(newExpanded);
  };

  const getPhaseStatusBadge = (status) => {
    switch(status) {
      case 'completed': return { bg: 'bg-green-100', text: 'text-green-800', label: 'Completed' };
      case 'current': return { bg: 'bg-blue-100', text: 'text-blue-800', label: 'Current' };
      case 'blocked': return { bg: 'bg-red-100', text: 'text-red-800', label: 'Blocked' };
      default: return { bg: 'bg-gray-100', text: 'text-gray-600', label: 'Pending' };
    }
  };

  const getPhaseCircleColor = (status) => {
    switch(status) {
      case 'completed': return 'bg-green-600 text-white';
      case 'current': return 'bg-blue-600 text-white';
      case 'blocked': return 'bg-red-600 text-white';
      default: return 'bg-gray-400 text-white';
    }
  };

  const getBorderColor = (status) => {
    switch(status) {
      case 'completed': return 'border-green-500';
      case 'current': return 'border-blue-500';
      case 'blocked': return 'border-red-500';
      default: return 'border-gray-300';
    }
  };

  return (
    <div className="space-y-2">
      {phases.map((phase, index) => {
        const isExpanded = expandedPhases.has(index);
        const statusBadge = getPhaseStatusBadge(phase.status);
        const hasCheckpoints = phase.checkpoints && phase.checkpoints.length > 0;
        const hasErrors = phase.detected_errors && phase.detected_errors.length > 0;

        return (
          <div
            key={phase.phase_number || index}
            className={`border-l-4 ${getBorderColor(phase.status)} bg-gray-50 rounded-r-lg transition-all duration-200`}
          >
            {/* Compact Phase Row */}
            <div 
              className="flex items-center justify-between p-3 cursor-pointer hover:bg-gray-100"
              onClick={() => togglePhase(index)}
            >
              <div className="flex items-center gap-3 flex-1">
                {/* Phase Number Circle */}
                <span className={`inline-flex items-center justify-center w-10 h-10 rounded-full font-semibold text-sm ${getPhaseCircleColor(phase.status)}`}>
                  {phase.phase_number}
                </span>
                
                {/* Phase Name (only shown when collapsed) */}
                {!isExpanded && (
                  <span className="text-sm font-medium text-gray-700">{phase.phase_name}</span>
                )}
              </div>

              {/* Status Badge */}
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusBadge.bg} ${statusBadge.text}`}>
                  ▶ {statusBadge.label}
                </span>
                
                {/* Expand/Collapse Icon */}
                {(hasCheckpoints || hasErrors) && (
                  isExpanded ? 
                    <ChevronDown className="h-4 w-4 text-gray-500" /> : 
                    <ChevronRight className="h-4 w-4 text-gray-500" />
                )}
              </div>
            </div>

            {/* Expanded Details */}
            {isExpanded && (
              <div className="px-3 pb-3 space-y-3">
                {/* Phase Info */}
                <div className="bg-white rounded-md p-3 border border-gray-200">
                  <h3 className="font-semibold text-gray-900 mb-1">{phase.phase_name}</h3>
                  <p className="text-sm text-gray-600 mb-2">{phase.goal}</p>
                  {phase.priority && (
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                      phase.priority === 'HIGH' ? 'bg-red-100 text-red-800' :
                      phase.priority === 'MEDIUM' ? 'bg-yellow-100 text-yellow-800' :
                      'bg-blue-100 text-blue-800'
                    }`}>
                      Priority: {phase.priority}
                    </span>
                  )}
                </div>

                {/* Checkpoints */}
                {hasCheckpoints && (
                  <div className="space-y-2">
                    {phase.checkpoints.map((checkpoint, cpIndex) => (
                      <div
                        key={cpIndex}
                        className={`p-3 rounded-md border ${
                          checkpoint.completed 
                            ? 'bg-white border-green-300' 
                            : checkpoint.blocking 
                              ? 'bg-red-50 border-red-300' 
                              : 'bg-white border-gray-200'
                        }`}
                      >
                        <div className="flex items-start justify-between mb-2">
                          <div className="flex items-center gap-2">
                            <div className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold ${
                              checkpoint.completed 
                                ? 'bg-green-500 text-white' 
                                : 'bg-gray-300 text-gray-600'
                            }`}>
                              {checkpoint.completed ? '✓' : '○'}
                            </div>
                            <span className="font-medium text-sm text-gray-900">
                              {checkpoint.name}
                            </span>
                            {checkpoint.blocking && !checkpoint.completed && (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-800">
                                BLOCKING
                              </span>
                            )}
                          </div>
                          {checkpoint.progress && (
                            <span className="text-xs text-gray-500">{checkpoint.progress}</span>
                          )}
                        </div>

                        {/* Checkpoint Requirements */}
                        {checkpoint.requirements && checkpoint.requirements.length > 0 && (
                          <div className="ml-7 space-y-1">
                            {checkpoint.requirements.map((req, reqIndex) => (
                              <div key={reqIndex} className="flex items-start gap-2">
                                <input
                                  type="checkbox"
                                  checked={req.completed || false}
                                  readOnly
                                  className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                />
                                <span className={`text-xs ${
                                  req.completed ? 'text-gray-700 line-through' : 'text-gray-600'
                                }`}>
                                  {req.text}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Alert Questions */}
                {phase.alert_questions && phase.alert_questions.length > 0 && (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 mb-2">
                      <Bell className="h-4 w-4 text-blue-600" />
                      <span className="text-sm font-medium text-gray-700">Alert Questions</span>
                      <span className="text-xs text-gray-500">
                        ({phase.alert_questions_passed || 0}/{phase.total_alert_questions || phase.alert_questions.length} passed)
                      </span>
                    </div>
                    {phase.alert_questions.map((aq, aqIndex) => (
                      <div
                        key={aqIndex}
                        className={`p-3 rounded-md border ${
                          aq.passed 
                            ? 'bg-white border-blue-300' 
                            : aq.blocking 
                              ? 'bg-orange-50 border-orange-300' 
                              : 'bg-white border-gray-200'
                        }`}
                      >
                        <div className="flex items-start justify-between mb-2">
                          <div className="flex items-start gap-2 flex-1">
                            <div className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5 ${
                              aq.passed 
                                ? 'bg-blue-500 text-white' 
                                : 'bg-orange-500 text-white'
                            }`}>
                              {aq.passed ? '✓' : '?'}
                            </div>
                            <div className="flex-1">
                              <span className="font-medium text-sm text-gray-900 block mb-1">
                                {aq.question}
                              </span>
                              <div className="flex items-center gap-2 text-xs">
                                <span className={`font-semibold ${
                                  aq.answer === 'YES' ? 'text-green-700' : 
                                  aq.answer === 'NO' ? 'text-red-700' : 
                                  'text-gray-500'
                                }`}>
                                  Answer: {aq.answer}
                                </span>
                                {aq.expected_answer && (
                                  <span className="text-gray-500">
                                    (Expected: {aq.expected_answer})
                                  </span>
                                )}
                              </div>
                              {aq.evidence && (
                                <p className="text-xs text-gray-600 mt-1 italic">
                                  {aq.evidence}
                                </p>
                              )}
                            </div>
                          </div>
                          {aq.blocking && !aq.passed && (
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-orange-100 text-orange-800 flex-shrink-0">
                              BLOCKING
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Error Codes */}
                {hasErrors && (
                  <div className="bg-red-50 border border-red-200 rounded-md p-3">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="h-5 w-5 text-red-600 flex-shrink-0 mt-0.5" />
                      <div className="flex-1">
                        <h4 className="text-sm font-medium text-red-900 mb-1">Errors Detected</h4>
                        <div className="space-y-1">
                          {phase.detected_errors.map((error, errIndex) => (
                            <div key={errIndex} className="text-xs text-red-800">
                              <span className="font-semibold">{error.code}:</span> {error.description}
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default OutlierPhaseTracker;
