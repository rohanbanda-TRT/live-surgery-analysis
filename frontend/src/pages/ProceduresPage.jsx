import { useState, useEffect } from 'react';
import { FileText, Loader } from 'lucide-react';
import { proceduresAPI } from '../services/api';

function ProceduresPage() {
  const [procedures, setProcedures] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedProcedure, setSelectedProcedure] = useState(null);

  useEffect(() => {
    loadProcedures();
  }, []);

  const loadProcedures = async () => {
    try {
      const data = await proceduresAPI.getAll();
      setProcedures(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const loadProcedureDetails = async (id) => {
    try {
      const data = await proceduresAPI.getById(id);
      setSelectedProcedure(data);
    } catch (err) {
      setError(err.message);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center items-center h-64">
        <Loader className="animate-spin h-8 w-8 text-primary-600" />
      </div>
    );
  }

  return (
    <div className="px-4 py-8">
      <h1 className="text-3xl font-bold text-gray-900 mb-8">Surgical Procedures</h1>

      {error && (
        <div className="bg-red-50 border-l-4 border-red-500 p-4 mb-6">
          <p className="text-red-700">{error}</p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Procedures List */}
        <div className="bg-white rounded-lg shadow-md p-6">
          <h2 className="text-xl font-semibold mb-4">Available Procedures</h2>
          {procedures.length === 0 ? (
            <p className="text-gray-500">No procedures found. Analyze a video first.</p>
          ) : (
            <div className="space-y-2">
              {procedures.map((proc) => (
                <button
                  key={proc.id}
                  onClick={() => loadProcedureDetails(proc.id)}
                  className="w-full text-left p-4 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <div className="flex items-start">
                    <FileText className="h-5 w-5 text-primary-600 mr-3 flex-shrink-0 mt-1" />
                    <div>
                      <h3 className="font-semibold text-gray-900">{proc.procedure_name}</h3>
                      <p className="text-sm text-gray-600">{proc.procedure_type}</p>
                      <p className="text-xs text-gray-500 mt-1">
                        {proc.total_steps || 0} steps • {proc.difficulty_level}
                      </p>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Procedure Details */}
        <div className="bg-white rounded-lg shadow-md p-6">
          <h2 className="text-xl font-semibold mb-4">Procedure Details</h2>
          {!selectedProcedure ? (
            <p className="text-gray-500">Select a procedure to view details</p>
          ) : (
            <div className="space-y-4">
              <div>
                <h3 className="font-semibold text-gray-900">{selectedProcedure.procedure_name}</h3>
                <p className="text-sm text-gray-600">{selectedProcedure.procedure_type}</p>
              </div>
              
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <span className="text-gray-600">Duration:</span>
                  <span className="ml-2 font-medium">{selectedProcedure.total_duration_avg} min</span>
                </div>
                <div>
                  <span className="text-gray-600">Difficulty:</span>
                  <span className="ml-2 font-medium">{selectedProcedure.difficulty_level}</span>
                </div>
              </div>

              <div>
                <h4 className="font-semibold text-gray-900 mb-2">Surgical Steps</h4>
                <div className="space-y-3 max-h-96 overflow-y-auto">
                  {selectedProcedure.steps?.map((step, index) => (
                    <div key={index} className="border-l-4 border-primary-500 pl-4 py-2">
                      <div className="flex items-start justify-between">
                        <h5 className="font-medium text-gray-900">
                          Step {step.step_number}: {step.step_name}
                        </h5>
                        {step.is_critical && (
                          <span className="text-xs bg-red-100 text-red-800 px-2 py-1 rounded">
                            Critical
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-gray-600 mt-1">{step.description}</p>
                      {step.instruments_required?.length > 0 && (
                        <p className="text-xs text-gray-500 mt-2">
                          <strong>Instruments:</strong> {step.instruments_required.join(', ')}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default ProceduresPage;
