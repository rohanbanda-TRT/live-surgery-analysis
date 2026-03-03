"""
Procedure cache service - stores loaded procedure data in memory.

This eliminates the need for MongoDB background connections during long-running
comparison analyses by loading the procedure data once and caching it.
"""
from typing import Dict, Any, List, Optional, Tuple
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.collections import MASTER_PROCEDURES, OUTLIER_PROCEDURES
from app.core.logging import logger


class ProcedureCache:
    """
    In-memory cache for procedure data.
    
    Stores procedure documents and their steps to avoid repeated MongoDB queries
    during video comparison analysis.
    """
    
    def __init__(self):
        """Initialize empty cache."""
        self._cache: Dict[str, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
    
    def _make_key(self, procedure_id: str, procedure_source: str) -> str:
        """Generate cache key from procedure ID and source."""
        return f"{procedure_source}:{procedure_id}"
    
    async def load_procedure(
        self,
        db: AsyncDatabase,
        procedure_id: str,
        procedure_source: str
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Load procedure from cache or database.
        
        Args:
            db: MongoDB database instance
            procedure_id: ID of the procedure
            procedure_source: "standard" or "outlier"
            
        Returns:
            Tuple of (procedure document, procedure steps list)
        """
        cache_key = self._make_key(procedure_id, procedure_source)
        
        # Check cache first
        if cache_key in self._cache:
            logger.info(
                "procedure_loaded_from_cache",
                procedure_id=procedure_id,
                procedure_source=procedure_source
            )
            return self._cache[cache_key]
        
        # Load from database
        logger.info(
            "loading_procedure_from_database",
            procedure_id=procedure_id,
            procedure_source=procedure_source
        )
        
        if procedure_source == "outlier":
            procedure = await db[OUTLIER_PROCEDURES].find_one(
                {"_id": ObjectId(procedure_id)}
            )
            
            if not procedure:
                raise ValueError(f"Outlier procedure {procedure_id} not found")
            
            # Build procedure steps from phases
            procedure_steps = []
            for phase in procedure.get("phases", []):
                procedure_steps.append({
                    "step_number": phase.get("phase_number"),
                    "step_name": phase.get("phase_name"),
                    "description": phase.get("goal"),
                    "phase_number": phase.get("phase_number"),
                    "priority": phase.get("priority"),
                    "checkpoints": phase.get("checkpoints", []),
                    "critical_errors": phase.get("critical_errors", [])
                })
            
            logger.info(
                "outlier_procedure_loaded",
                procedure_name=procedure.get("procedure_name"),
                phases_count=len(procedure_steps)
            )
            
        else:
            procedure = await db[MASTER_PROCEDURES].find_one(
                {"_id": ObjectId(procedure_id)}
            )
            
            if not procedure:
                raise ValueError(f"Master procedure {procedure_id} not found")
            
            procedure_steps = procedure.get("steps", [])
            
            logger.info(
                "master_procedure_loaded",
                procedure_name=procedure.get("procedure_name"),
                steps_count=len(procedure_steps)
            )
        
        # Store in cache
        self._cache[cache_key] = (procedure, procedure_steps)
        
        return procedure, procedure_steps
    
    def get_cached(
        self,
        procedure_id: str,
        procedure_source: str
    ) -> Optional[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
        """
        Get procedure from cache without database access.
        
        Args:
            procedure_id: ID of the procedure
            procedure_source: "standard" or "outlier"
            
        Returns:
            Tuple of (procedure document, procedure steps list) or None if not cached
        """
        cache_key = self._make_key(procedure_id, procedure_source)
        return self._cache.get(cache_key)
    
    def clear(self):
        """Clear all cached procedures."""
        self._cache.clear()
        logger.info("procedure_cache_cleared")
    
    def clear_procedure(self, procedure_id: str, procedure_source: str):
        """
        Clear a specific procedure from cache.
        
        Args:
            procedure_id: ID of the procedure
            procedure_source: "standard" or "outlier"
        """
        cache_key = self._make_key(procedure_id, procedure_source)
        if cache_key in self._cache:
            del self._cache[cache_key]
            logger.info(
                "procedure_removed_from_cache",
                procedure_id=procedure_id,
                procedure_source=procedure_source
            )
