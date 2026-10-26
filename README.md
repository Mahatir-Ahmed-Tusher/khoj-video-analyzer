# Khoj Video Analyzer

> **High-throughput, multilingual video & audio transcription and visual OCR microservice designed for Bengali fact-checking journalism.**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110.0-009688.svg?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![Faster-Whisper](https://img.shields.io/badge/Faster--Whisper-CTranslate2%20Int8-orange.svg)](https://github.com/SYSTRAN/faster-whisper)
[![NVIDIA Nemotron](https://img.shields.io/badge/LLM-NVIDIA%20Nemotron%20550B-76B900.svg?logo=nvidia)](https://openrouter.ai/models/nvidia/nemotron-3-ultra-550b-a55b:free)
[![Cloud Run](https://img.shields.io/badge/Google%20Cloud-Cloud%20Run%20Ready-4285F4.svg?logo=google-cloud)](https://cloud.google.com/run)

---

## Overview

The **Khoj Video Analyzer** is a backend service developed for automated fact-checking of social media video claims. It extracts spoken dialogue and on-screen text from video URLs (YouTube, Facebook Reels, Instagram, TikTok) and uploaded media, processing them through a fine-tuned Bengali speech recognition engine paired with an advanced LLM editor to eliminate ASR phonetic errors.

---

## Key Features

- **Multi-Platform Stream Extraction**: Downloads audio/video streams on-the-fly from YouTube, Facebook, Instagram, TikTok, and direct media URLs via `yt-dlp` with automated bot-detection evasion and mobile client emulation.
- **Bengali-Optimized ASR**: Uses `ammaraldirawi/faster-whisper-small-bn-int8` via CTranslate2 for ultra-fast, accurate CPU transcription of Bengali speech without hallucination loops.
- **LLM-Powered Grammar Polishing**: Integrates OpenRouter's `nvidia/nemotron-3-ultra-550b-a55b:free` (with automatic fallback to `nvidia/nemotron-3.5-super-vl:free`) to correct missing diacritics, broken words, and transliterated Romanized Bengali (Banglish) into natural standard Bengali.
- **On-Screen Optical Character Recognition (OCR)**: Extracts overlaid banners, tickers, headlines, and subtitles from sampled video frames using Tesseract OCR with `ben+eng` bilingual support.
- **Ephemeral Cloudflare R2 Buffering**: Offloads heavy video media files to Cloudflare R2 temporary storage, automatically deleting files upon analysis completion to maintain minimal local disk footprint.
- **Asynchronous Non-Blocking Pipeline**: CPU-intensive tasks run in background thread pools (`asyncio.to_thread`), keeping health endpoints and concurrent requests 100% responsive.

---

## Architecture Flow

```
User Video URL / Upload
         │
         ▼
┌─────────────────────────────────┐
│ Stream Extractor (yt-dlp)       │──► Direct Audio Extraction (m4a/mp3)
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ Faster-Whisper Bengali (Int8)   │──► Raw Spoken Transcript (Bengali ASR)
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ NVIDIA Nemotron 550B (LLM)      │──► Grammatically Correct Bengali
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ Video Frame OCR (Tesseract)     │──► On-Screen Text & Headlines
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ JSON Response                   │──► Spoken text, OCR text, timestamps
└─────────────────────────────────┘
```

---

## System Requirements

- **Python**: 3.10 or 3.11
- **FFmpeg**: Required for audio stream decoding and frame slicing.
- **Tesseract OCR**: Required with Bengali (`tesseract-ocr-ben`) and English (`tesseract-ocr-eng`) language packs.
- **RAM**: Minimum 2 GB (4 GB recommended for concurrent video processing).
- **CPU**: 2 or more CPU cores recommended.

---

## Quickstart

### Option 1: Local Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/khoj-video-analyzer.git
   cd khoj-video-analyzer
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   # On Linux/macOS:
   source venv/bin/activate
   # On Windows:
   .\venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Install system packages:**
   - **Debian/Ubuntu**:
     ```bash
     sudo apt-get update && sudo apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-ben tesseract-ocr-eng
     ```
   - **macOS (Homebrew)**:
     ```bash
     brew install ffmpeg tesseract tesseract-lang
     ```
   - **Windows**: Install [FFmpeg](https://ffmpeg.org/download.html) and [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (select Bengali language data during setup), then add both to your system `PATH`.

5. **Configure environment:**
   ```bash
   cp .env.example .env
   ```
   Add your `OPENROUTER_API_KEY` in `.env` to enable Nemotron post-processing.

6. **Start the server:**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
   ```
   Interactive documentation will be available at [http://localhost:8080/docs](http://localhost:8080/docs).

---

### Option 2: Docker Setup

Build and run the pre-configured container (includes FFmpeg and Tesseract pre-installed):

```bash
docker build -t khoj-video-analyzer .
docker run -p 8080:8080 --env-file .env khoj-video-analyzer
```

---

## Environment Variables Reference

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `PORT` | `8080` | Port on which the FastAPI application listens. |
| `HOST` | `0.0.0.0` | Host binding interface. |
| `WHISPER_MODEL_SIZE` | `ammaraldirawi/faster-whisper-small-bn-int8` | Hugging Face repository or model size for Faster-Whisper. |
| `WHISPER_DEVICE` | `cpu` | Execution hardware: `cpu` or `cuda`. |
| `WHISPER_COMPUTE_TYPE` | `default` | Quantization type: `default` or `int8`. |
| `WHISPER_CPU_THREADS` | `2` | Number of CPU threads allocated for Whisper inference. |
| `OPENROUTER_API_KEY` | `""` | OpenRouter API Key for NVIDIA Nemotron LLM post-processing. |
| `NEMOTRON_PRIMARY_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b:free` | Primary LLM model for polishing raw Bengali ASR. |
| `NEMOTRON_FALLBACK_MODEL` | `nvidia/nemotron-3.5-super-vl:free` | High-speed fallback model if primary hits quota or latency limits. |
| `ENABLE_BENGALI_REFINER` | `true` | Toggle for LLM post-processing refinement (`true`/`false`). |
| `OCR_LANGUAGES` | `ben+eng` | Tesseract language codes for on-screen text extraction. |
| `FRAME_SAMPLE_INTERVAL_SECONDS` | `2.0` | Video frame sampling rate for OCR. |
| `MAX_VIDEO_DURATION_SECONDS` | `900` | Maximum allowed video length (15 minutes). |
| `R2_ACCOUNT_ID` | `""` | *(Optional)* Cloudflare R2 Account ID. |
| `R2_BUCKET_NAME` | `khoj-videos` | *(Optional)* Cloudflare R2 Bucket Name. |
| `R2_ACCESS_KEY_ID` | `""` | *(Optional)* Cloudflare R2 Access Key ID. |
| `R2_SECRET_ACCESS_KEY` | `""` | *(Optional)* Cloudflare R2 Secret Access Key. |
| `AUTO_DELETE_R2_MEDIA` | `true` | Automatically delete uploaded media from R2 after analysis. |

---

## API Reference

### 1. Dedicated Speech-to-Text (`POST /transcribe`)
Downloads **ONLY** the audio stream and runs Faster-Whisper + Gemini Bengali refiner. Zero video download, zero visual OCR for maximum throughput and speed (~10-25s).

- **Endpoint**: `POST /transcribe`
- **Body**:
  ```json
  {
    "url": "https://youtube.com/shorts/tQf4koxUjZs",
    "language": null
  }
  ```
- **Direct Upload**: `POST /transcribe/upload` (multipart/form-data with `file`)

### 2. Dedicated On-Screen Visual OCR (`POST /ocr`)
Downloads 360p lightweight video and extracts on-screen text overlays and banners across 16-24 sampled frames. Zero audio download, zero Whisper speech execution.

- **Endpoint**: `POST /ocr`
- **Body**:
  ```json
  {
    "url": "https://www.facebook.com/share/r/1Et9iC3U9L/",
    "max_frames": 16,
    "interval_seconds": 2.5
  }
  ```
- **Direct Upload**: `POST /ocr/upload` (multipart/form-data with `file`)

### 3. Full Multi-Modal Analysis (`POST /analyze`)
Composite pipeline combining speech transcription, on-screen OCR, and unified fact-check summary synthesis.

- **Endpoint**: `POST /analyze`
- **Body**:
  ```json
  {
    "url": "https://youtu.be/TE5ZJB-6gVE",
    "extract_audio": true,
    "extract_ocr": true,
    "language": null
  }
  ```
- **Direct Upload**: `POST /analyze/upload` (multipart/form-data with `file`)

### 4. Health & Service Catalog
- **Root Catalog**: `GET /` (lists all available endpoints and API metadata)
- **Health Probe**: `GET /health` (returns engine status and model configurations)

---

## Deployment on Google Cloud Run

This service is optimized to run inside Google Cloud Run's Always-Free tier:

```bash
gcloud run deploy khoj-video-analyzer \
  --source . \
  --region us-central1 \
  --platform managed \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 10 \
  --min-instances 0 \
  --max-instances 3 \
  --allow-unauthenticated \
  --set-env-vars OPENROUTER_API_KEY="your-api-key"
```

---

## License

This project is open-source and available under the [MIT License](LICENSE).
