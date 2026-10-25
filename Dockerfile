FROM python:3.11-slim-bookworm

# Set environment flags
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8080 \
    HOST=0.0.0.0 \
    WHISPER_MODEL_SIZE=ammaraldirawi/faster-whisper-small-bn-int8 \
    WHISPER_MODEL_BN=ammaraldirawi/faster-whisper-small-bn-int8 \
    WHISPER_MODEL_HI=ammaraldirawi/faster-whisper-small-hi-int8 \
    WHISPER_MODEL_EN=Systran/faster-whisper-tiny.en \
    WHISPER_DEVICE=cpu \
    WHISPER_COMPUTE_TYPE=default \
    WHISPER_CPU_THREADS=2 \
    ENABLE_BENGALI_REFINER=true \
    GEMINI_PRIMARY_MODEL=gemini-3.5-flash-lite \
    GEMINI_FALLBACK_MODEL=gemini-flash-lite-latest \
    NEMOTRON_PRIMARY_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free \
    NEMOTRON_FALLBACK_MODEL=nvidia/nemotron-3.5-super-vl:free \
    BANGLA_OCR_API_URL=https://bangla-ocr-api.mahatirtusher.workers.dev/ocr \
    OCR_LANGUAGES=ben+eng

# Install system dependencies: FFmpeg, CA certs, Curl, Node.js (Tesseract replaced by Cloudflare Neural OCR Worker)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    ca-certificates \
    curl \
    nodejs \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (yt-dlp default preferred JS runtime for YouTube BotGuard & n-challenge solver)
RUN curl -fsSL https://deno.land/install.sh | sh && \
    ln -s /root/.deno/bin/deno /usr/local/bin/deno

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download fine-tuned Bengali Whisper and multilingual detector into image cache for instant cold starts
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('ammaraldirawi/faster-whisper-small-bn-int8', device='cpu', compute_type='default'); WhisperModel('Systran/faster-whisper-tiny', device='cpu', compute_type='default')"

# Copy application source code
COPY app/ ./app/

# Expose standard Cloud Run port
EXPOSE 8080

# Run FastAPI with Uvicorn
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
