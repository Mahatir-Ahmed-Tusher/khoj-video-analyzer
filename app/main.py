import os
import time
import json
import asyncio
import tempfile
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Tuple
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from app.config import settings
from app.services.stream_extractor import (
    extract_metadata,
    download_audio_stream,
    download_video_for_ocr,
    detect_platform,
    fetch_youtube_captions,
    extract_youtube_video_id,
    get_effective_cookies_path
)
from app.services.audio_transcriber import transcriber_service
from app.services.bengali_refiner import bengali_refiner
from app.services.visual_ocr import extract_on_screen_text
from app.services.r2_storage import r2_service
from app.utils.text_cleaner import build_factcheck_summary, format_paragraphs
from app.utils.cleanup import safe_remove_file, run_garbage_collection

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("khoj-video-analyzer")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown lifecycle manager.
    Pre-loads Whisper speech recognition model for low-latency requests.
    """
    logger.info(f"Starting {settings.SERVICE_NAME} on port {settings.PORT}...")
    try:
        # Pre-initialize Whisper model on background/warmup
        transcriber_service.initialize_model()
    except Exception as e:
        logger.warning(f"Whisper warmup warning (will retry on first request): {e}")
        
    yield
    
    logger.info("Shutting down service and freeing resources...")
    run_garbage_collection()

app = FastAPI(
    title="Khoj Universal Video Analyzer",
    description="High-Speed In-House Video Speech Transcription & On-Screen Visual OCR Service for Fact-Checking",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for universal integration with Khoj Android App, Web App, and Crawlers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TranscribeRequest(BaseModel):
    url: str
    language: Optional[str] = None # None for auto-detection (Bangla/English/Hindi)

class TranscribeResponse(BaseModel):
    success: bool
    platform: str
    video_id: Optional[str] = None
    title: str
    author: str
    duration_seconds: int = 0
    thumbnail_url: Optional[str] = None
    spoken_transcript: str
    raw_spoken_transcript: Optional[str] = None
    refiner_model: Optional[str] = None
    spoken_segments: List[Dict[str, Any]]
    detected_language: str
    processing_time_seconds: float
    message: Optional[str] = None

class OcrRequest(BaseModel):
    url: str
    interval_seconds: Optional[float] = 2.5
    max_frames: Optional[int] = 16

class OcrResponse(BaseModel):
    success: bool
    platform: str
    video_id: Optional[str] = None
    title: str
    author: str
    duration_seconds: int = 0
    thumbnail_url: Optional[str] = None
    on_screen_text: List[Dict[str, Any]]
    combined_visual_text: str
    frames_analyzed: int = 0
    processing_time_seconds: float
    message: Optional[str] = None

class AnalyzeRequest(BaseModel):
    url: str
    extract_audio: bool = True
    extract_ocr: bool = True
    language: Optional[str] = None # None for auto-detection (Bangla/English)

class VideoAnalyzeResponse(BaseModel):
    success: bool
    platform: str
    video_id: Optional[str] = None
    title: str
    author: str
    duration_seconds: int = 0
    thumbnail_url: Optional[str] = None
    spoken_transcript: str
    raw_spoken_transcript: Optional[str] = None
    refiner_model: Optional[str] = None
    spoken_segments: List[Dict[str, Any]]
    on_screen_text: List[Dict[str, Any]]
    merged_content_for_factcheck: str
    detected_language: str
    processing_time_seconds: float
    message: Optional[str] = None

@app.get("/")
async def root():
    return {
        "service": settings.SERVICE_NAME,
        "status": "operational",
        "endpoints": {
            "health": "/health",
            "transcribe": "POST /transcribe",
            "transcribe_upload": "POST /transcribe/upload",
            "ocr": "POST /ocr",
            "ocr_upload": "POST /ocr/upload",
            "analyze": "POST /analyze",
            "analyze_upload": "POST /analyze/upload",
            "documentation": "/docs"
        }
    }

@app.get("/health")
async def health_check():
    """
    Instant health check endpoint for Google Cloud Run liveness & readiness probes.
    """
    cookies_path = get_effective_cookies_path()
    return {
        "status": "healthy",
        "service": settings.SERVICE_NAME,
        "whisper_model": settings.WHISPER_MODEL_SIZE,
        "whisper_compute_type": settings.WHISPER_COMPUTE_TYPE,
        "ocr_languages": settings.OCR_LANGUAGES,
        "cookies": f"loaded ({os.path.getsize(cookies_path)} bytes)" if cookies_path and os.path.exists(cookies_path) else "not_configured",
        "r2_storage": "configured (ephemeral/auto-delete)" if r2_service.is_configured() else "not_configured"
    }

async def _perform_audio_transcription(
    url: str,
    platform: str,
    metadata: Dict[str, Any],
    req_language: Optional[str],
    temp_dir: str
) -> Tuple[str, str, Optional[str], List[Dict[str, Any]], str, bool]:
    spoken_transcript = ""
    spoken_segments = []
    detected_lang = "unknown"
    audio_download_succeeded = False
    refiner_model_used = None

    # If YouTube, check if captions are already present or fetchable via InnerTube / transcript-api
    if platform == "youtube":
        innertube_data = metadata.get("_innertube_result")
        if innertube_data and innertube_data.get("has_captions") and innertube_data.get("raw_transcript"):
            spoken_transcript = innertube_data["raw_transcript"]
            spoken_segments = innertube_data["segments"]
            detected_lang = innertube_data["detected_language"]
            logger.info(f"YouTube transcript instantly ingested via InnerTube metadata cache ({len(spoken_segments)} segments).")
        else:
            caption_result = await asyncio.to_thread(fetch_youtube_captions, url, req_language, metadata.get("_raw_info"))
            if caption_result and caption_result.get("raw_transcript"):
                spoken_transcript = caption_result["raw_transcript"]
                spoken_segments = caption_result["segments"]
                detected_lang = caption_result["detected_language"]
                logger.info(f"YouTube transcript successfully ingested via {caption_result['source']} ({len(spoken_segments)} segments).")

    # If captions not available or non-YouTube platform, proceed to audio stream download
    if not spoken_transcript:
        audio_path = await asyncio.to_thread(download_audio_stream, url, temp_dir)
        if audio_path and os.path.exists(audio_path):
            audio_download_succeeded = True
            logger.info(f"Audio stream downloaded ({os.path.getsize(audio_path) / 1024:.1f} KB). Starting Whisper transcription...")
            spoken_transcript, spoken_segments, detected_lang = await asyncio.to_thread(
                transcriber_service.transcribe,
                audio_path,
                req_language
            )
            safe_remove_file(audio_path)
        else:
            logger.warning("Could not obtain audio stream. Skipping speech transcription.")

    raw_spoken = spoken_transcript
    is_bengali = bool(detected_lang and any(detected_lang.lower().startswith(prefix) for prefix in ("bn", "ben")))
    if is_bengali and settings.ENABLE_BENGALI_REFINER and spoken_transcript:
        word_count = len(spoken_transcript.split())
        if word_count > 1200 and spoken_segments:
            logger.info(f"Bengali transcript is extensive ({word_count} words). Applying fast paragraph formatting.")
            formatted_para = format_paragraphs(spoken_segments)
            if formatted_para:
                spoken_transcript = formatted_para
        else:
            refined_text, model_name = await bengali_refiner.refine(spoken_transcript)
            if refined_text:
                spoken_transcript = refined_text
                if model_name and model_name != "original_transcript":
                    refiner_model_used = model_name
    elif spoken_segments and not refiner_model_used:
        formatted_para = format_paragraphs(spoken_segments)
        if formatted_para:
            spoken_transcript = formatted_para

    return (spoken_transcript, raw_spoken, refiner_model_used, spoken_segments, detected_lang, audio_download_succeeded)

async def _perform_visual_ocr(
    url: str,
    temp_dir: str,
    interval_seconds: Optional[float] = None,
    max_frames: Optional[int] = None
) -> List[Dict[str, Any]]:
    video_ocr_path = await asyncio.to_thread(download_video_for_ocr, url, temp_dir)
    if video_ocr_path and os.path.exists(video_ocr_path):
        logger.info(f"OCR video stream downloaded ({os.path.getsize(video_ocr_path) / (1024*1024):.1f} MB). Starting visual frame OCR...")
        on_screen_text = await asyncio.to_thread(
            extract_on_screen_text,
            video_ocr_path,
            interval_seconds,
            max_frames
        )
        safe_remove_file(video_ocr_path)
        return on_screen_text
    else:
        logger.warning("Could not obtain video stream for OCR.")
        return []

@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_video_url(req: TranscribeRequest, background_tasks: BackgroundTasks):
    """
    Dedicated Speech-to-Text Transcription endpoint.
    Downloads ONLY audio stream and runs Whisper speech recognition + Gemini refinement.
    Zero video download, zero visual OCR for maximum throughput and speed.
    """
    start_time = time.time()
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")

    platform = detect_platform(url)
    logger.info(f"Transcribe request for platform '{platform}': {url}")

    metadata = await extract_metadata(url)
    title = metadata.get("title", "Video Claim")
    author = metadata.get("author", platform.capitalize())
    raw_duration = metadata.get("duration_seconds", 0)
    try:
        duration = int(round(float(raw_duration or 0)))
    except (ValueError, TypeError):
        duration = 0
    thumbnail = metadata.get("thumbnail_url")

    with tempfile.TemporaryDirectory() as temp_dir:
        spoken_transcript, raw_spoken, refiner_model_used, spoken_segments, detected_lang, audio_download_succeeded = (
            await _perform_audio_transcription(url, platform, metadata, req.language, temp_dir)
        )

    background_tasks.add_task(run_garbage_collection)

    if duration == 0 and spoken_segments:
        try:
            duration = int(round(float(spoken_segments[-1].get("end") or 0)))
        except (ValueError, TypeError):
            pass

    elapsed = round(time.time() - start_time, 2)
    resolved_video_id = metadata.get("video_id") or (extract_youtube_video_id(url) if platform == "youtube" else None)

    return TranscribeResponse(
        success=True,
        platform=platform,
        video_id=resolved_video_id,
        title=title,
        author=author,
        duration_seconds=duration,
        thumbnail_url=thumbnail,
        spoken_transcript=spoken_transcript,
        raw_spoken_transcript=raw_spoken if refiner_model_used else spoken_transcript,
        refiner_model=refiner_model_used,
        spoken_segments=spoken_segments,
        detected_language=detected_lang,
        processing_time_seconds=elapsed,
        message=(
            "Audio transcription completed successfully."
            if spoken_transcript
            else "No spoken speech detected in audio stream."
            if audio_download_succeeded
            else "Could not download audio stream from URL (platform anti-bot block or auth required)."
        )
    )

@app.post("/ocr", response_model=OcrResponse)
async def extract_video_ocr(req: OcrRequest, background_tasks: BackgroundTasks):
    """
    Dedicated On-Screen Visual OCR endpoint.
    Downloads 360p video stream and performs multi-frame neural OCR on visual text overlays.
    Zero audio download, zero Whisper model execution for maximum speed and compute isolation.
    """
    start_time = time.time()
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")

    platform = detect_platform(url)
    logger.info(f"Visual OCR request for platform '{platform}': {url}")

    metadata = await extract_metadata(url)
    title = metadata.get("title", "Video Claim")
    author = metadata.get("author", platform.capitalize())
    raw_duration = metadata.get("duration_seconds", 0)
    try:
        duration = int(round(float(raw_duration or 0)))
    except (ValueError, TypeError):
        duration = 0
    thumbnail = metadata.get("thumbnail_url")

    with tempfile.TemporaryDirectory() as temp_dir:
        on_screen_text = await _perform_visual_ocr(
            url=url,
            temp_dir=temp_dir,
            interval_seconds=req.interval_seconds or 2.5,
            max_frames=req.max_frames or 16
        )

    background_tasks.add_task(run_garbage_collection)

    combined_visual_text = " | ".join(t.get("text", "") for t in on_screen_text if t.get("text"))
    elapsed = round(time.time() - start_time, 2)
    resolved_video_id = metadata.get("video_id") or (extract_youtube_video_id(url) if platform == "youtube" else None)

    return OcrResponse(
        success=True,
        platform=platform,
        video_id=resolved_video_id,
        title=title,
        author=author,
        duration_seconds=duration,
        thumbnail_url=thumbnail,
        on_screen_text=on_screen_text,
        combined_visual_text=combined_visual_text,
        frames_analyzed=len(on_screen_text),
        processing_time_seconds=elapsed,
        message=(
            f"Visual OCR extraction completed ({len(on_screen_text)} text segments found)."
            if on_screen_text
            else "No readable text overlays detected in sampled frames."
        )
    )

@app.post("/analyze", response_model=VideoAnalyzeResponse)
async def analyze_video_url(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """
    Full Multi-Modal Video Analysis endpoint (Composite default).
    Analyzes social media video URL (YouTube, Facebook, Instagram, X/Twitter, TikTok, Reddit).
    Extracts spoken speech transcript and visual on-screen text, synthesizing a merged fact-check summary.
    """
    start_time = time.time()
    url = req.url.strip()
    
    if not url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")
        
    platform = detect_platform(url)
    logger.info(f"Processing composite video request for platform '{platform}': {url}")
    
    metadata = await extract_metadata(url)
    title = metadata.get("title", "Video Claim")
    author = metadata.get("author", platform.capitalize())
    raw_duration = metadata.get("duration_seconds", 0)
    try:
        duration = int(round(float(raw_duration or 0)))
    except (ValueError, TypeError):
        duration = 0
    thumbnail = metadata.get("thumbnail_url")
    
    spoken_transcript = ""
    raw_spoken = ""
    refiner_model_used = None
    spoken_segments = []
    detected_lang = "unknown"
    on_screen_text = []
    audio_download_succeeded = False
    
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            if req.extract_audio:
                spoken_transcript, raw_spoken, refiner_model_used, spoken_segments, detected_lang, audio_download_succeeded = (
                    await _perform_audio_transcription(url, platform, metadata, req.language, temp_dir)
                )

            if req.extract_ocr:
                on_screen_text = await _perform_visual_ocr(url, temp_dir)
        except Exception as e:
            logger.error(f"Error during video processing pipeline: {e}")
            
    background_tasks.add_task(run_garbage_collection)
    
    merged_summary = build_factcheck_summary(
        title=title,
        platform=platform,
        author=author,
        spoken_transcript=spoken_transcript,
        on_screen_texts=on_screen_text
    )
    
    if duration == 0 and spoken_segments:
        try:
            duration = int(round(float(spoken_segments[-1].get("end") or 0)))
        except (ValueError, TypeError):
            pass

    elapsed = round(time.time() - start_time, 2)
    logger.info(f"Video analysis completed in {elapsed}s.")
    
    resolved_video_id = metadata.get("video_id") or (extract_youtube_video_id(url) if platform == "youtube" else None)
    
    result = VideoAnalyzeResponse(
        success=True,
        platform=platform,
        video_id=resolved_video_id,
        title=title,
        author=author,
        duration_seconds=duration,
        thumbnail_url=thumbnail,
        spoken_transcript=spoken_transcript,
        raw_spoken_transcript=raw_spoken if refiner_model_used else spoken_transcript,
        refiner_model=refiner_model_used,
        spoken_segments=spoken_segments,
        on_screen_text=on_screen_text,
        merged_content_for_factcheck=merged_summary,
        detected_language=detected_lang,
        processing_time_seconds=elapsed,
        message=(
            "Analysis completed successfully (audio transcription was not requested)."
            if not req.extract_audio
            else "Analysis completed successfully (no spoken speech detected in audio stream)."
            if audio_download_succeeded and not spoken_transcript
            else (
                "Could not download media stream from URL (platform anti-bot block or auth required). "
                "Please use direct media file upload, or switch to your local server (127.0.0.1:8080)."
            )
            if not audio_download_succeeded and req.extract_audio and not spoken_transcript
            else "Analysis completed successfully."
        )
    )

    try:
        results_file = os.path.join(settings.BASE_DIR, "batch_transcription_results.json")
        existing_data = []
        if os.path.exists(results_file):
            with open(results_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        updated = False
        for item in existing_data:
            if item.get("url") == url:
                item["status"] = "success"
                item["data"] = result.model_dump()
                item["elapsed_seconds"] = elapsed
                updated = True
                break
        if not updated:
            existing_data.append({
                "url": url,
                "status": "success",
                "elapsed_seconds": elapsed,
                "data": result.model_dump()
            })
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=2)
    except Exception as save_err:
        logger.warning(f"Failed to auto-save transcription to cache: {save_err}")

    return result

@app.post("/transcribe/upload", response_model=TranscribeResponse)
async def transcribe_uploaded_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    language: Optional[str] = None
):
    """
    Directly transcribe an uploaded audio or video file (Speech-to-Text only).
    """
    start_time = time.time()
    filename = file.filename or "uploaded_media.mp4"
    logger.info(f"Received media upload for transcribe: {filename}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_media_path = os.path.join(temp_dir, filename)
        with open(temp_media_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
                
        spoken_transcript, spoken_segments, detected_lang = transcriber_service.transcribe(
            audio_path=temp_media_path,
            language=language
        )
        
    background_tasks.add_task(run_garbage_collection)
    
    raw_spoken = spoken_transcript
    refiner_model_used = None
    is_bengali = bool(detected_lang and any(detected_lang.lower().startswith(prefix) for prefix in ("bn", "ben")))
    if is_bengali and settings.ENABLE_BENGALI_REFINER and spoken_transcript:
        refined_text, model_name = await bengali_refiner.refine(spoken_transcript)
        if refined_text:
            spoken_transcript = refined_text
            if model_name and model_name != "original_transcript":
                refiner_model_used = model_name

    duration = int(round(float(spoken_segments[-1].get("end") or 0))) if spoken_segments else 0
    elapsed = round(time.time() - start_time, 2)
    
    return TranscribeResponse(
        success=True,
        platform="upload",
        video_id=None,
        title=filename,
        author="User Upload",
        duration_seconds=duration,
        thumbnail_url=None,
        spoken_transcript=spoken_transcript,
        raw_spoken_transcript=raw_spoken if refiner_model_used else spoken_transcript,
        refiner_model=refiner_model_used,
        spoken_segments=spoken_segments,
        detected_language=detected_lang,
        processing_time_seconds=elapsed,
        message="Uploaded audio transcription completed successfully."
    )

@app.post("/ocr/upload", response_model=OcrResponse)
async def ocr_uploaded_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    interval_seconds: Optional[float] = 2.5,
    max_frames: Optional[int] = 16
):
    """
    Directly perform On-Screen Visual OCR on an uploaded video file.
    """
    start_time = time.time()
    filename = file.filename or "uploaded_video.mp4"
    logger.info(f"Received video upload for visual OCR: {filename}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_video_path = os.path.join(temp_dir, filename)
        with open(temp_video_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
                
        on_screen_text = extract_on_screen_text(
            temp_video_path,
            interval_seconds=interval_seconds,
            max_frames=max_frames
        )
        
    background_tasks.add_task(run_garbage_collection)
    
    combined_visual_text = " | ".join(t.get("text", "") for t in on_screen_text if t.get("text"))
    elapsed = round(time.time() - start_time, 2)
    
    return OcrResponse(
        success=True,
        platform="upload",
        video_id=None,
        title=filename,
        author="User Upload",
        duration_seconds=0,
        thumbnail_url=None,
        on_screen_text=on_screen_text,
        combined_visual_text=combined_visual_text,
        frames_analyzed=len(on_screen_text),
        processing_time_seconds=elapsed,
        message=f"Uploaded video visual OCR completed ({len(on_screen_text)} text segments found)."
    )

@app.post("/analyze/upload", response_model=VideoAnalyzeResponse)
async def analyze_uploaded_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    extract_audio: bool = True,
    extract_ocr: bool = True,
    language: Optional[str] = None
):
    """
    Directly analyze an uploaded video file (MP4, WebM, MOV, etc.).
    Useful for locally saved recordings or video claims uploaded directly from user device.
    """
    start_time = time.time()
    filename = file.filename or "uploaded_video.mp4"
    logger.info(f"Received video file upload: {filename} (Content-Type: {file.content_type})")
    
    spoken_transcript = ""
    spoken_segments = []
    detected_lang = "unknown"
    on_screen_text = []
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_video_path = os.path.join(temp_dir, filename)
        
        with open(temp_video_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
                
        file_size_mb = os.path.getsize(temp_video_path) / (1024 * 1024)
        logger.info(f"Uploaded video saved ({file_size_mb:.2f} MB). Starting processing...")
        
        if extract_audio:
            spoken_transcript, spoken_segments, detected_lang = transcriber_service.transcribe(
                audio_path=temp_video_path,
                language=language
            )
            
        if extract_ocr:
            on_screen_text = extract_on_screen_text(temp_video_path)
            
    background_tasks.add_task(run_garbage_collection)
    
    merged_summary = build_factcheck_summary(
        title=filename,
        platform="upload",
        author="User Upload",
        spoken_transcript=spoken_transcript,
        on_screen_texts=on_screen_text
    )
    
    elapsed = round(time.time() - start_time, 2)
    return VideoAnalyzeResponse(
        success=True,
        platform="upload",
        video_id=None,
        title=filename,
        author="User Upload",
        duration_seconds=0,
        thumbnail_url=None,
        spoken_transcript=spoken_transcript,
        spoken_segments=spoken_segments,
        on_screen_text=on_screen_text,
        merged_content_for_factcheck=merged_summary,
        detected_language=detected_lang,
        processing_time_seconds=elapsed,
        message="Uploaded video analysis completed successfully."
    )
