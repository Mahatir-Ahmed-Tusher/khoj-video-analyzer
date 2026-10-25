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

### 1. Analyze Video URL
Transcribes dialogue and extracts on-screen OCR text from a social media or direct video URL.

- **Endpoint**: `POST /analyze`
- **Headers**: `Content-Type: application/json`
- **Body**:
  ```json
  {
    "url": "https://youtu.be/TE5ZJB-6gVE?si=taPo9aoUIIHLGU-a",
    "extract_ocr": true
  }
  ```
- **Response**:
  ```json
  {
    "success": true,
    "title": "এআই (AI) কি সত্যিই ব্ল্যাক ম্যাজিক?",
    "author": "Khoj - AI Bengali Factchecker",
    "platform": "youtube",
    "duration_seconds": 48.0,
    "detected_language": "bn",
    "spoken_transcript": "কুন্যাল, এই কোর্সটি রইল? এত চ্যাটজিপিটি যেকোনো করছিলাম... আসলে কি এটাকে ব্ল্যাক ম্যাজিক বা দারিদ্রদের আলামত?...",
    "raw_spoken_transcript": "কুন্যাল, এই কোর্চিস রয়েন? এত চেজিবিটি ইয়েকের করছেলাম... আসুদেইকে ইয়ায়েকে ব্ল্যাক মেজিক বা দারজাদের আলামত?...",
    "refiner_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "timestamped_segments": [
      {
        "start": "00:06",
        "end": "00:08",
        "start_seconds": 6.51,
        "end_seconds": 8.51,
        "text": "কুন্যাল, এই কোর্চিস রয়েন?"
      }
    ],
    "on_screen_text": "এআই কি সত্যি ব্ল্যাক ম্যাজিক?",
    "ocr_frames_analyzed": 24,
    "processing_time_seconds": 32.14
  }
  ```

### 2. Transcribe Audio File
Uploads an audio file directly (`.mp3`, `.m4a`, `.wav`, `.aac`) for fast speech-to-text transcription.

- **Endpoint**: `POST /transcribe-audio`
- **Content-Type**: `multipart/form-data`
- **Form Param**: `file` (Binary audio data)

### 3. Health Check
- **Endpoint**: `GET /health`
- **Response**:
  ```json
  {
    "status": "healthy",
    "model_loaded": true,
    "refiner_enabled": true
  }
  ```

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
