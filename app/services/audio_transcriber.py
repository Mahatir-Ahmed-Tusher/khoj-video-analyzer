import logging
from typing import Dict, Any, List, Tuple, Optional
from faster_whisper import WhisperModel
from app.config import settings

logger = logging.getLogger("khoj-video-analyzer")

class AudioTranscriber:
    """
    Singleton manager for faster-whisper model.
    Utilizes CTranslate2 8-bit quantization for fast CPU execution with ~250MB memory footprint.
    """
    _instance: Optional['AudioTranscriber'] = None
    _model: Optional[WhisperModel] = None

    @classmethod
    def get_instance(cls) -> 'AudioTranscriber':
        if cls._instance is None:
            cls._instance = AudioTranscriber()
        return cls._instance

    def initialize_model(self) -> None:
        if self._model is None:
            logger.info(
                f"Loading Whisper model '{settings.WHISPER_MODEL_SIZE}' on {settings.WHISPER_DEVICE} "
                f"with {settings.WHISPER_COMPUTE_TYPE} quantization..."
            )
            self._model = WhisperModel(
                model_size_or_path=settings.WHISPER_MODEL_SIZE,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
                download_root=settings.WHISPER_DOWNLOAD_ROOT,
                cpu_threads=settings.WHISPER_CPU_THREADS
            )
            logger.info("Whisper model initialized successfully.")

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        initial_prompt: Optional[str] = "খোঁজ, ফ্যাক্ট চেকিং, বাংলা।"
    ) -> Tuple[str, List[Dict[str, Any]], str]:
        """
        Transcribe an audio file into text with timestamps and language detection.
        Supports automatic code-switching (Bangla + English).
        """
        if self._model is None:
            self.initialize_model()

        segments_data = []
        full_text_pieces = []
        detected_language = "unknown"

        try:
            # beam_size=1 or 2 for maximum speed on Cloud Run CPU
            prompt_to_use = initial_prompt if (language == "bn") else None
            segments, info = self._model.transcribe(
                audio_path,
                language=language,
                initial_prompt=prompt_to_use,
                beam_size=2,
                vad_filter=True, # Voice Activity Detection filters out silence & background noise
                vad_parameters=dict(min_silence_duration_ms=500)
            )

            detected_language = info.language or "unknown"
            logger.info(f"Detected speech language: {detected_language} (prob: {info.language_probability:.2f})")

            for segment in segments:
                text = segment.text.strip()
                if text:
                    full_text_pieces.append(text)
                    start_min = int(segment.start // 60)
                    start_sec = int(segment.start % 60)
                    end_min = int(segment.end // 60)
                    end_sec = int(segment.end % 60)
                    
                    segments_data.append({
                        "start": f"{start_min:02d}:{start_sec:02d}",
                        "end": f"{end_min:02d}:{end_sec:02d}",
                        "start_seconds": round(segment.start, 2),
                        "end_seconds": round(segment.end, 2),
                        "text": text
                    })

            full_transcript = " ".join(full_text_pieces).strip()
            return full_transcript, segments_data, detected_language

        except Exception as e:
            logger.error(f"Error during audio transcription: {e}")
            return "", [], "unknown"

transcriber_service = AudioTranscriber.get_instance()
