import os
from pydantic import BaseModel
from dotenv import load_dotenv

# Load local environment variables from .env if present
load_dotenv()

# Configure cache and temp paths to avoid running out of disk space on system drive
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_CACHE_DIR = os.path.join(BASE_DIR, "hf_cache")
LOCAL_TEMP_DIR = os.path.join(BASE_DIR, "tmp")

if not os.getenv("HF_HOME"):
    os.environ["HF_HOME"] = LOCAL_CACHE_DIR

class Settings(BaseModel):
    # Service settings
    SERVICE_NAME: str = "khoj-video-analyzer"
    BASE_DIR: str = BASE_DIR
    LOCAL_CACHE_DIR: str = LOCAL_CACHE_DIR
    LOCAL_TEMP_DIR: str = LOCAL_TEMP_DIR
    PORT: int = int(os.getenv("PORT", "8080"))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    
    # Whisper Speech-to-Text settings (Single active slot per language)
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "ammaraldirawi/faster-whisper-small-bn-int8")
    WHISPER_MODEL_BN: str = os.getenv("WHISPER_MODEL_BN", "ammaraldirawi/faster-whisper-small-bn-int8")
    WHISPER_MODEL_HI: str = os.getenv("WHISPER_MODEL_HI", "ammaraldirawi/faster-whisper-small-hi-int8")
    WHISPER_MODEL_EN: str = os.getenv("WHISPER_MODEL_EN", "Systran/faster-whisper-tiny.en")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "default")
    WHISPER_DOWNLOAD_ROOT: str = os.getenv("WHISPER_DOWNLOAD_ROOT", LOCAL_CACHE_DIR)
    WHISPER_CPU_THREADS: int = int(os.getenv("WHISPER_CPU_THREADS", "4"))
    
    # LLM Bengali Post-Processing (Primary: Google Gemini, Fallback: OpenRouter Nemotron)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_PRIMARY_MODEL: str = os.getenv("GEMINI_PRIMARY_MODEL", "gemini-3.5-flash-lite")
    GEMINI_FALLBACK_MODEL: str = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    NEMOTRON_PRIMARY_MODEL: str = os.getenv("NEMOTRON_PRIMARY_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
    NEMOTRON_FALLBACK_MODEL: str = os.getenv("NEMOTRON_FALLBACK_MODEL", "nvidia/nemotron-3.5-super-vl:free")
    ENABLE_BENGALI_REFINER: bool = os.getenv("ENABLE_BENGALI_REFINER", "true").lower() == "true"
    
    # Video OCR Settings (Dedicated Bangla OCR Cloudflare Worker Gateway)
    BANGLA_OCR_API_URL: str = os.getenv("BANGLA_OCR_API_URL", "https://bangla-ocr-api.mahatirtusher.workers.dev/ocr")
    OCR_LANGUAGES: str = os.getenv("OCR_LANGUAGES", "ben+eng")
    FRAME_SAMPLE_INTERVAL_SECONDS: float = float(os.getenv("FRAME_SAMPLE_INTERVAL_SECONDS", "3.0"))
    MAX_FRAMES_TO_OCR: int = int(os.getenv("MAX_FRAMES_TO_OCR", "8"))
    
    # Video Guardrail Settings
    MAX_VIDEO_DURATION_SECONDS: int = int(os.getenv("MAX_VIDEO_DURATION_SECONDS", "900"))  # 15 minutes max
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
    
    # Cloudflare R2 Temporary Storage Settings (Auto-purged after analysis)
    R2_ACCOUNT_ID: str = os.getenv("R2_ACCOUNT_ID", "")
    R2_BUCKET_NAME: str = os.getenv("R2_BUCKET_NAME", "khoj-videos")
    R2_ACCESS_KEY_ID: str = os.getenv("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY: str = os.getenv("R2_SECRET_ACCESS_KEY", "")
    R2_PUBLIC_URL: str = os.getenv("R2_PUBLIC_URL", "")
    AUTO_DELETE_R2_MEDIA: bool = os.getenv("AUTO_DELETE_R2_MEDIA", "true").lower() == "true"
    
    # Anti-bot User-Agents (Mirroring khoj-content-service crawler)
    DEFAULT_USER_AGENT: str = os.getenv(
        "DEFAULT_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    SOCIAL_BOT_USER_AGENT: str = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"

    # YouTube & Anti-bot Egress Settings
    YT_COOKIES_TEXT: str = os.getenv("YT_COOKIES_TEXT", "")
    YT_COOKIES_PATH: str = os.getenv("YT_COOKIES_PATH", "")
    YT_PROXY: str = os.getenv("YT_PROXY", "").strip().strip('"').strip("'")

settings = Settings()
