import pandas as pd
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import logging
import traceback
import io
import sys
from contextlib import redirect_stdout, redirect_stderr
import numpy as np

logger = logging.getLogger(__name__)


def get_descriptive_dtype(series: pd.Series) -> str:
    """Get a more descriptive data type for a pandas Series."""
    dtype = series.dtype
    
    # Handle datetime types
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    
    # Handle numeric types
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    
    # Handle boolean
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    
    # Handle object dtype - need to inspect actual values
    if dtype == 'object':
        # Sample a few non-null values to determine type
        sample = series.dropna().head(100)
        if len(sample) == 0:
            return "unknown"
        
        # Check if all sampled values are strings
        if all(isinstance(x, str) for x in sample):
            return "string"
        
        # Check if it's a mix or other types
        types_found = set(type(x).__name__ for x in sample)
        if len(types_found) == 1:
            return list(types_found)[0].lower()
        else:
            return f"mixed({', '.join(sorted(types_found))})"
    
    # Handle categorical
    if pd.api.types.is_categorical_dtype(dtype):
        return "categorical"
    
    # Fallback to string representation
    return str(dtype)


def get_descriptive_dtypes(df: pd.DataFrame) -> Dict[str, str]:
    """Get descriptive data types for all columns in a DataFrame."""
    return {col: get_descriptive_dtype(df[col]) for col in df.columns}


class CollectionMetadata:
    def __init__(self, collection_id: str, name: str, shape: Tuple[int, int], columns: list, dtypes: Dict[str, str]):
        self.id = collection_id
        self.name = name
        self.shape = shape
        self.columns = columns
        self.dtypes = dtypes
        self.created_at = datetime.now()
        self.last_modified = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "shape": {"rows": self.shape[0], "columns": self.shape[1]},
            "columns": self.columns,
            "dtypes": self.dtypes,
            "created_at": self.created_at.isoformat(),
            "last_modified": self.last_modified.isoformat()
        }


class DataStore:
    def __init__(self):
        self._collections: Dict[str, pd.DataFrame] = {}
        self._metadata: Dict[str, CollectionMetadata] = {}
        self._execution_history: Dict[str, list] = {}
    
    def create(self, data: pd.DataFrame, name: Optional[str] = None) -> Dict[str, Any]:
        collection_id = str(uuid.uuid4())
        
        if name is None:
            name = f"collection_{collection_id[:8]}"
        
        self._collections[collection_id] = data.copy()
        self._metadata[collection_id] = CollectionMetadata(
            collection_id=collection_id,
            name=name,
            shape=data.shape,
            columns=list(data.columns),
            dtypes=get_descriptive_dtypes(data)
        )
        self._execution_history[collection_id] = []
        
        logger.info(f"Created collection {collection_id} with shape {data.shape}")
        
        return self._metadata[collection_id].to_dict()
    
    def update(self, collection_id: str, operation: str) -> Dict[str, Any]:
        if collection_id not in self._collections:
            raise ValueError(f"Collection {collection_id} not found")
        
        df = self._collections[collection_id]
        metadata = self._metadata[collection_id]
        
        # Create execution context
        context = {
            'df': df.copy(),
            'pd': pd,
            'result': None
        }
        
        # Capture output
        output_buffer = io.StringIO()
        error_buffer = io.StringIO()
        
        try:
            with redirect_stdout(output_buffer), redirect_stderr(error_buffer):
                # Execute the operation
                exec(f"result = {operation}", context)
                
                # Get the result
                result = context.get('result', context['df'])
                
                if not isinstance(result, pd.DataFrame):
                    raise ValueError(f"Operation must return a DataFrame, got {type(result)}")
                
                # Update the collection
                self._collections[collection_id] = result
                
                # Update metadata
                metadata.shape = result.shape
                metadata.columns = list(result.columns)
                metadata.dtypes = get_descriptive_dtypes(result)
                metadata.last_modified = datetime.now()
                
                # Record operation
                self._execution_history[collection_id].append({
                    "operation": operation,
                    "timestamp": datetime.now().isoformat(),
                    "success": True,
                    "output": output_buffer.getvalue(),
                    "shape_before": df.shape,
                    "shape_after": result.shape
                })
                
                logger.info(f"Updated collection {collection_id}: {df.shape} -> {result.shape}")
                
                return {
                    **metadata.to_dict(),
                    "operation_output": output_buffer.getvalue()
                }
                
        except Exception as e:
            # Record failed operation
            self._execution_history[collection_id].append({
                "operation": operation,
                "timestamp": datetime.now().isoformat(),
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            })
            
            logger.error(f"Failed to update collection {collection_id}: {e}")
            raise ValueError(f"Operation failed: {str(e)}")
    
    def fetch(self, collection_id: str, limit: Optional[int] = None, format: str = "records") -> Dict[str, Any]:
        if collection_id not in self._collections:
            raise ValueError(f"Collection {collection_id} not found")
        
        df = self._collections[collection_id]
        metadata = self._metadata[collection_id]
        
        # Apply limit if specified
        display_df = df.head(limit) if limit else df
        
        # Convert to requested format
        if format == "records":
            data = display_df.to_dict(orient="records")
        elif format == "dict":
            data = display_df.to_dict()
        elif format == "csv":
            data = display_df.to_csv(index=False)
        elif format == "json":
            data = display_df.to_json(orient="records")
        else:
            raise ValueError(f"Unsupported format: {format}")
        
        return {
            "metadata": metadata.to_dict(),
            "data": data,
            "truncated": limit is not None and len(df) > limit,
            "total_rows": len(df)
        }
    
    def delete(self, collection_id: str) -> bool:
        if collection_id not in self._collections:
            raise ValueError(f"Collection {collection_id} not found")
        
        del self._collections[collection_id]
        del self._metadata[collection_id]
        del self._execution_history[collection_id]
        
        logger.info(f"Deleted collection {collection_id}")
        return True
    
    def list_collections(self) -> list:
        return [metadata.to_dict() for metadata in self._metadata.values()]
    
    def get_history(self, collection_id: str) -> list:
        if collection_id not in self._collections:
            raise ValueError(f"Collection {collection_id} not found")
        
        return self._execution_history[collection_id]
    
    def preview(self, collection_id: str, rows: int = 5) -> Dict[str, Any]:
        if collection_id not in self._collections:
            raise ValueError(f"Collection {collection_id} not found")
        
        df = self._collections[collection_id]
        metadata = self._metadata[collection_id]
        
        return {
            "metadata": metadata.to_dict(),
            "preview": df.head(rows).to_dict(orient="records"),
            "dtypes": get_descriptive_dtypes(df)
        }
    
    def combine(self, target_collection_id: str, source_collection_id: str) -> Dict[str, Any]:
        """Combine two collections by appending the source collection to the target collection.
        
        The collections must have the same shape (number of columns and column names).
        The source collection is appended to the target collection, and the source collection
        remains unchanged.
        
        Args:
            target_collection_id: ID of the collection to append to
            source_collection_id: ID of the collection to append from
            
        Returns:
            Updated metadata of the target collection
            
        Raises:
            ValueError: If either collection doesn't exist or if they have incompatible shapes
        """
        if target_collection_id not in self._collections:
            raise ValueError(f"Target collection {target_collection_id} not found")
        
        if source_collection_id not in self._collections:
            raise ValueError(f"Source collection {source_collection_id} not found")
        
        target_df = self._collections[target_collection_id]
        source_df = self._collections[source_collection_id]
        target_metadata = self._metadata[target_collection_id]
        source_metadata = self._metadata[source_collection_id]
        
        # Check if collections have the same shape (columns)
        if target_df.shape[1] != source_df.shape[1]:
            raise ValueError(
                f"Collections have different number of columns: "
                f"target has {target_df.shape[1]}, source has {source_df.shape[1]}"
            )
        
        # Check if column names match
        if list(target_df.columns) != list(source_df.columns):
            raise ValueError(
                f"Collections have different column names: "
                f"target has {list(target_df.columns)}, source has {list(source_df.columns)}"
            )
        
        # Combine the dataframes
        try:
            combined_df = pd.concat([target_df, source_df], ignore_index=True)
            
            # Update the target collection
            self._collections[target_collection_id] = combined_df
            
            # Update metadata
            target_metadata.shape = combined_df.shape
            target_metadata.dtypes = get_descriptive_dtypes(combined_df)
            target_metadata.last_modified = datetime.now()
            
            # Record the combine operation
            self._execution_history[target_collection_id].append({
                "operation": f"combine with collection {source_collection_id}",
                "timestamp": datetime.now().isoformat(),
                "success": True,
                "output": f"Combined {source_df.shape[0]} rows from source collection",
                "shape_before": target_df.shape,
                "shape_after": combined_df.shape
            })
            
            logger.info(f"Combined collections: {target_collection_id} ({target_df.shape}) + {source_collection_id} ({source_df.shape}) = {combined_df.shape}")
            
            return target_metadata.to_dict()
            
        except Exception as e:
            # Record failed operation
            self._execution_history[target_collection_id].append({
                "operation": f"combine with collection {source_collection_id}",
                "timestamp": datetime.now().isoformat(),
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            })
            
            logger.error(f"Failed to combine collections {target_collection_id} and {source_collection_id}: {e}")
            raise ValueError(f"Failed to combine collections: {str(e)}")