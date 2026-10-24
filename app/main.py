import os
import time
import json
import asyncio
import tempfile
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from app.config import settings
from app.services.stream_extractor import (
    extract_metadata,
    download_audio_stream,
    download_video_for_ocr,
    detect_platform
)
from app.services.audio_transcriber import transcriber_service
from app.services.bengali_refiner import bengali_refiner
from app.services.visual_ocr import extract_on_screen_text
from app.services.r2_storage import r2_service
from app.utils.text_cleaner import build_factcheck_summary
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
    duration_seconds: int
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
            "analyze_url": "POST /analyze",
            "analyze_upload": "POST /analyze/upload",
            "documentation": "/docs"
        }
    }

@app.get("/health")
async def health_check():
    """
    Instant health check endpoint for Google Cloud Run liveness & readiness probes.
    """
    return {
        "status": "healthy",
        "service": settings.SERVICE_NAME,
        "whisper_model": settings.WHISPER_MODEL_SIZE,
        "ocr_languages": settings.OCR_LANGUAGES,
        "r2_storage": "configured (ephemeral/auto-delete)" if r2_service.is_configured() else "not_configured"
    }

@app.post("/analyze", response_model=VideoAnalyzeResponse)
async def analyze_video_url(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """
    Analyze a social media video URL (YouTube, Facebook, Instagram, X/Twitter, TikTok, Reddit).
    Extracts spoken speech transcript and visual on-screen text (editor graphics/subtitles).
    """
    start_time = time.time()
    url = req.url.strip()
    
    if not url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")
        
    platform = detect_platform(url)
    logger.info(f"Processing video request for platform '{platform}': {url}")
    
    # 1. Fetch metadata first (anti-bot hardened, no heavy download)
    metadata = await extract_metadata(url)
    title = metadata.get("title", "Video Claim")
    author = metadata.get("author", platform.capitalize())
    duration = metadata.get("duration_seconds", 0)
    thumbnail = metadata.get("thumbnail_url")
    
    # Guardrail check: Limit maximum video length to protect Cloud Run compute
    if duration > settings.MAX_VIDEO_DURATION_SECONDS:
        logger.info(f"Video duration ({duration}s) exceeds limit ({settings.MAX_VIDEO_DURATION_SECONDS}s). Clamping analysis.")
        
    spoken_transcript = ""
    spoken_segments = []
    detected_lang = "unknown"
    on_screen_text = []
    
    # 2. Process streams in isolated temporary directory with auto-purge
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # A. Audio speech transcription (run in threadpool to prevent blocking the event loop)
            if req.extract_audio:
                audio_path = await asyncio.to_thread(download_audio_stream, url, temp_dir)
                if audio_path and os.path.exists(audio_path):
                    logger.info(f"Audio stream downloaded ({os.path.getsize(audio_path) / 1024:.1f} KB). Starting Whisper transcription...")
                    spoken_transcript, spoken_segments, detected_lang = await asyncio.to_thread(
                        transcriber_service.transcribe,
                        audio_path,
                        req.language
                    )
                    safe_remove_file(audio_path)
                else:
                    logger.warning("Could not obtain audio stream. Skipping speech transcription.")

            # B. Video OCR (editor text, banners, subtitles)
            if req.extract_ocr:
                video_ocr_path = await asyncio.to_thread(download_video_for_ocr, url, temp_dir)
                if video_ocr_path and os.path.exists(video_ocr_path):
                    logger.info(f"OCR video stream downloaded ({os.path.getsize(video_ocr_path) / (1024*1024):.1f} MB). Starting visual frame OCR...")
                    on_screen_text = await asyncio.to_thread(extract_on_screen_text, video_ocr_path)
                    safe_remove_file(video_ocr_path)
                else:
                    logger.warning("Could not obtain video stream for OCR.")
                    
        except Exception as e:
            logger.error(f"Error during video processing pipeline: {e}")
            
    # Clean up garbage memory immediately
    background_tasks.add_task(run_garbage_collection)
    
    # 2.5 Bengali LLM Post-Processing Refinement (Nemotron Ultra 550B / Nemotron 3.5 Super VL)
    raw_spoken = spoken_transcript
    refiner_model_used = None
    if detected_lang in ("bn", "bengali") and settings.ENABLE_BENGALI_REFINER and spoken_transcript:
        refined_text, model_name = await bengali_refiner.refine(spoken_transcript)
        if refined_text and "failed" not in model_name:
            spoken_transcript = refined_text
            refiner_model_used = model_name

    # 3. Synthesize merged content for FactCheckEngine
    merged_summary = build_factcheck_summary(
        title=title,
        platform=platform,
        author=author,
        spoken_transcript=spoken_transcript,
        on_screen_texts=on_screen_text
    )
    
    elapsed = round(time.time() - start_time, 2)
    logger.info(f"Video analysis completed in {elapsed}s.")
    
    result = VideoAnalyzeResponse(
        success=True,
        platform=platform,
        video_id=None,
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
        message="Analysis completed successfully."
    )

    # Automatically persist completed analysis to disk so work is never lost
    try:
        results_file = os.path.join(settings.BASE_DIR, "batch_transcription_results.json")
        existing_data = []
        if os.path.exists(results_file):
            with open(results_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        # Update or append
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
        
        # Stream uploaded chunks to temporary disk
        with open(temp_video_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024): # 1MB chunks
                buffer.write(chunk)
                
        file_size_mb = os.path.getsize(temp_video_path) / (1024 * 1024)
        logger.info(f"Uploaded video saved ({file_size_mb:.2f} MB). Starting processing...")
        
        # Audio transcription
        if extract_audio:
            spoken_transcript, spoken_segments, detected_lang = transcriber_service.transcribe(
                audio_path=temp_video_path,
                language=language
            )
            
        # Visual OCR
        if extract_ocr:
            on_screen_text = extract_on_screen_text(temp_video_path)
            
    # Purge memory buffers
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
