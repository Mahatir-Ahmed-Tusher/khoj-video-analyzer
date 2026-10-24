import logging
import cv2
import numpy as np
from PIL import Image
import pytesseract
from typing import List, Dict, Any

from app.config import settings
from app.utils.text_cleaner import clean_ocr_text, deduplicate_on_screen_frames

logger = logging.getLogger("khoj-video-analyzer")

def preprocess_frame_for_ocr(frame: np.ndarray) -> Image.Image:
    """
    Preprocess an extracted video frame for optimal OCR recognition:
    - Downscale to standard width (e.g. 720px) to conserve RAM and compute.
    - Convert to grayscale.
    - Apply adaptive contrast stretching to make text and subtitles stand out.
    """
    height, width = frame.shape[:2]
    target_width = 720
    if width > target_width:
        scaling_ratio = target_width / float(width)
        target_height = int(height * scaling_ratio)
        frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

    # Convert BGR (OpenCV default) to Grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    # Mild contrast normalization to sharpen text on varied backgrounds
    norm = cv2.normalize(gray, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX)
    
    return Image.fromarray(norm)

def extract_on_screen_text(
    video_path: str,
    interval_seconds: float = None,
    max_frames: int = None
) -> List[Dict[str, Any]]:
    """
    Sample video frames at regular intervals and perform OCR to detect
    burned-in text, lower-thirds, breaking news banners, and editor subtitles.
    Deduplicates consecutive identical text entries.
    """
    sample_interval = interval_seconds or settings.FRAME_SAMPLE_INTERVAL_SECONDS
    max_sample_count = max_frames or settings.MAX_FRAMES_TO_OCR
    
    raw_frame_results: List[Dict[str, Any]] = []
    
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning(f"Could not open video file for OCR: {video_path}")
            return []
            
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_seconds = total_frames / fps if fps > 0 else 0
        
        # Calculate timestamps to sample (e.g., 1.0s, 3.5s, 6.0s...)
        sample_timestamps = []
        curr_t = 1.0 # Start 1s in to avoid initial fade-in black screen
        while curr_t < duration_seconds and len(sample_timestamps) < max_sample_count:
            sample_timestamps.append(curr_t)
            curr_t += sample_interval
            
        logger.info(f"Sampling {len(sample_timestamps)} frames from {video_path} for visual text OCR...")
        
        for t_sec in sample_timestamps:
            frame_no = int(t_sec * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
                
            # Preprocess frame in-memory
            pil_img = preprocess_frame_for_ocr(frame)
            
            # Format timestamp string MM:SS
            m = int(t_sec // 60)
            s = int(t_sec % 60)
            timestamp_str = f"{m:02d}:{s:02d}"
            
            try:
                # Run Tesseract OCR for Bangla and English
                ocr_result = pytesseract.image_to_string(
                    pil_img,
                    lang=settings.OCR_LANGUAGES,
                    config='--psm 6' # Assume a single uniform block of text
                )
                
                cleaned = clean_ocr_text(ocr_result)
                if cleaned:
                    raw_frame_results.append({
                        "timestamp": timestamp_str,
                        "timestamp_seconds": round(t_sec, 2),
                        "text": cleaned
                    })
            except pytesseract.TesseractNotFoundError:
                logger.warning("Tesseract OCR binary not found in system. Skipping visual OCR.")
                break
            except Exception as ocr_err:
                logger.debug(f"OCR frame error at {timestamp_str}: {ocr_err}")
                
        cap.release()
        
    except Exception as e:
        logger.error(f"Error during video OCR extraction: {e}")
        
    # Deduplicate consecutive frames sharing identical banners or headlines
    deduplicated = deduplicate_on_screen_frames(raw_frame_results)
    logger.info(f"Extracted {len(deduplicated)} unique visual text segments from video.")
    return deduplicated
