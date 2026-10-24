import os
import gc
import logging
from typing import Optional

logger = logging.getLogger("khoj-video-analyzer")

def safe_remove_file(file_path: Optional[str]) -> None:
    """
    Safely remove a file from disk without raising exceptions if missing.
    """
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"Failed to delete temp file {file_path}: {e}")

def run_garbage_collection() -> None:
    """
    Force Python garbage collection to release unreferenced buffers and memory.
    """
    try:
        gc.collect()
    except Exception as e:
        logger.warning(f"Garbage collection error: {e}")
