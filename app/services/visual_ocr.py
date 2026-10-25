import logging
import cv2
import httpx
import concurrent.futures
from typing import List, Dict, Any, Optional, Tuple

from app.config import settings
from app.utils.text_cleaner import clean_ocr_text, deduplicate_on_screen_frames

logger = logging.getLogger("khoj-video-analyzer")

def crop_and_prepare_frame(frame) -> Optional[bytes]:
    """
    Extract region of interest (ROI) where subtitles, tickers, and lower-third banners reside:
    - For horizontal video (16:9 / standard): Lower-third 35% (height * 0.65 to height).
    - For vertical video (9:16 Shorts/Reels/TikTok): Center-to-lower 45% (height * 0.45 to height * 0.92).
    - Downscale to max 720px width to minimize network transmission overhead.
    - Encode to in-memory JPEG bytes.
    """
    if frame is None or frame.size == 0:
        return None

    height, width = frame.shape[:2]

    # Detect horizontal vs vertical orientation
    if width <= height * 0.9:
        # Vertical video: focus on center-to-lower area (subtitles/captions overlay)
        start_y = int(height * 0.45)
        end_y = int(height * 0.92)
    else:
        # Standard horizontal video: focus on lower-third banner & news ticker area
        start_y = int(height * 0.65)
        end_y = height

    cropped = frame[start_y:end_y, :]

    # Resize if width exceeds standard 720px to conserve bandwidth and speed up OCR
    crop_h, crop_w = cropped.shape[:2]
    target_w = 720
    if crop_w > target_w:
        scaling_ratio = target_w / float(crop_w)
        target_h = int(crop_h * scaling_ratio)
        cropped = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_AREA)

    # Encode in-memory to compressed JPEG
    success, encoded_buf = cv2.imencode(".jpg", cropped, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not success:
        return None

    return encoded_buf.tobytes()

def _ocr_single_frame(
    img_bytes: bytes,
    timestamp_str: str,
    t_sec: float
) -> Optional[Dict[str, Any]]:
    """
    Send a single frame's JPEG buffer to the dedicated Bangla OCR Cloudflare Worker.
    Extracts high-accuracy Bengali & English neural OCR text in ~0.8s.
    """
    try:
        with httpx.Client(timeout=15.0) as client:
            files = {"file": ("frame.jpg", img_bytes, "image/jpeg")}
            headers = {"User-Agent": "KhojApp/1.0 (Android)"}
            
            resp = client.post(settings.BANGLA_OCR_API_URL, files=files, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                raw_text = data.get("full_text") or data.get("text", "")
                cleaned = clean_ocr_text(raw_text)
                if cleaned:
                    return {
                        "timestamp": timestamp_str,
                        "timestamp_seconds": round(t_sec, 2),
                        "text": cleaned
                    }
            else:
                logger.debug(f"OCR Worker returned HTTP {resp.status_code} for frame at {timestamp_str}")
    except Exception as e:
        logger.debug(f"OCR frame error at {timestamp_str}: {e}")
    return None

def extract_on_screen_text(
    video_path: str,
    interval_seconds: float = None,
    max_frames: int = None
) -> List[Dict[str, Any]]:
    """
    Sample video keyframes at regular intervals and perform high-speed neural OCR
    via the dedicated Bangla OCR Gateway to detect burned-in text, lower-thirds,
    breaking news banners, and editor subtitles.
    Deduplicates consecutive identical text entries.
    """
    sample_interval = interval_seconds or settings.FRAME_SAMPLE_INTERVAL_SECONDS
    max_sample_count = max_frames or settings.MAX_FRAMES_TO_OCR

    frames_to_process: List[Tuple[bytes, str, float]] = []

    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning(f"Could not open video file for OCR: {video_path}")
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_seconds = total_frames / fps if fps > 0 else 0

        # Calculate timestamps to sample (e.g., 1.0s, 4.0s, 7.0s...)
        sample_timestamps = []
        curr_t = 1.0  # Start 1s in to avoid initial fade-in black screen
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

            jpeg_bytes = crop_and_prepare_frame(frame)
            if not jpeg_bytes:
                continue

            m = int(t_sec // 60)
            s = int(t_sec % 60)
            timestamp_str = f"{m:02d}:{s:02d}"

            frames_to_process.append((jpeg_bytes, timestamp_str, t_sec))

        cap.release()

    except Exception as e:
        logger.error(f"Error during video frame sampling: {e}")

    if not frames_to_process:
        return []

    # Process sampled frames concurrently via the dedicated OCR Gateway (Cloudflare Worker)
    raw_frame_results: List[Dict[str, Any]] = []
    worker_count = min(5, len(frames_to_process))
    logger.info(f"Dispatching {len(frames_to_process)} frames across {worker_count} concurrent OCR workers...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_ocr_single_frame, img_bytes, ts_str, t_sec): (ts_str, t_sec)
            for img_bytes, ts_str, t_sec in frames_to_process
        }
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                raw_frame_results.append(res)

    # Sort results chronologically by timestamp
    raw_frame_results.sort(key=lambda x: x["timestamp_seconds"])

    # Deduplicate consecutive frames sharing identical banners or headlines
    deduplicated = deduplicate_on_screen_frames(raw_frame_results)
    logger.info(f"Extracted {len(deduplicated)} unique visual text segments from video via Neural OCR.")
    return deduplicated
