"""
Prompt templates for surgical video analysis.

DEPRECATED: This file is kept for backward compatibility only.
New code should import from:
- app.prompts.standard_prompts for offline video analysis
- app.prompts.outlier_prompts for error-focused live analysis
"""

# Re-export from new organized modules for backward compatibility
from app.prompts.standard_prompts import (
    get_video_analysis_schema,
    get_video_analysis_prompt
)
from app.prompts.outlier_prompts import (
    build_outlier_resolution_context,
    get_outlier_chunk_analysis_prompt
)

__all__ = [
    'get_video_analysis_schema',
    'get_video_analysis_prompt',
    'build_outlier_resolution_context',
    'get_outlier_chunk_analysis_prompt'
]
