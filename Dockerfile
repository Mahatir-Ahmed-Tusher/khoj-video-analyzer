FROM python:3.11-slim-bookworm

# Set environment flags
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8080 \
    HOST=0.0.0.0 \
    WHISPER_MODEL_SIZE=ammaraldirawi/faster-whisper-small-bn-int8 \
    WHISPER_DEVICE=cpu \
    WHISPER_COMPUTE_TYPE=default \
    WHISPER_CPU_THREADS=2 \
    ENABLE_BENGALI_REFINER=true \
    NEMOTRON_PRIMARY_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free \
    NEMOTRON_FALLBACK_MODEL=nvidia/nemotron-3.5-super-vl:free \
    OCR_LANGUAGES=ben+eng

# Install system dependencies: FFmpeg, Tesseract with Bengali and English packages, and minimal graphics libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-ben \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    ca-certificates \
    curl \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download the fine-tuned Bengali Whisper model into the image cache so cold starts are instant (< 1s)
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('ammaraldirawi/faster-whisper-small-bn-int8', device='cpu', compute_type='default')"

# Copy application source code
COPY app/ ./app/

# Expose standard Cloud Run port
EXPOSE 8080

# Run FastAPI with Uvicorn
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
