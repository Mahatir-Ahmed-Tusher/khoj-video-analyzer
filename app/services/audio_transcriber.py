import logging
import gc
from typing import Dict, Any, List, Tuple, Optional
from faster_whisper import WhisperModel, decode_audio
from app.config import settings

logger = logging.getLogger("khoj-video-analyzer")

class AudioTranscriber:
    """
    Single Active Model Slot manager for faster-whisper models.
    Keeps at most ONE model loaded in RAM (~350MB max) to strictly protect Cloud Run memory.
    Routes to specialized models:
      - Bengali (bn): ammaraldirawi/faster-whisper-small-bn-int8 (default warm model)
      - Hindi (hi): ammaraldirawi/faster-whisper-small-hi-int8 (on-demand swap)
      - English (en): Systran/faster-whisper-tiny.en (ultra-lightweight 39MB)
    """
    _instance: Optional['AudioTranscriber'] = None
    _model: Optional[WhisperModel] = None
    _current_model_name: Optional[str] = None

    @classmethod
    def get_instance(cls) -> 'AudioTranscriber':
        if cls._instance is None:
            cls._instance = AudioTranscriber()
        return cls._instance

    def _resolve_model_name_for_lang(self, lang: Optional[str]) -> Tuple[str, Optional[str]]:
        """
        Map language code to target fine-tuned model and normalized language code.
        Returns (model_name, normalized_lang). When lang is None, returns None as
        normalized_lang to signal auto-detection mode.
        """
        if not lang:
            # Signal auto-detection: returns None to trigger unbiased multilingual detector
            return "", None

        l = lang.strip().lower()
        if l in ("hi", "hindi"):
            return settings.WHISPER_MODEL_HI, "hi"
        elif l in ("en", "english", "en-us", "en-gb"):
            return settings.WHISPER_MODEL_EN, "en"
        elif l in ("bn", "bengali", "bn-bd"):
            return settings.WHISPER_MODEL_BN, "bn"

        # Default fallback to primary Bengali model
        return settings.WHISPER_MODEL_BN, l

    def _get_model_for_detected_lang(self, lang_code: str) -> Tuple[str, str]:
        """
        Given a detected language code from Whisper, return the best model and normalized code.
        """
        l = (lang_code or "").strip().lower()
        if l in ("en", "english"):
            return settings.WHISPER_MODEL_EN, "en"
        elif l in ("hi", "hindi"):
            return settings.WHISPER_MODEL_HI, "hi"
        elif l in ("bn", "bengali"):
            return settings.WHISPER_MODEL_BN, "bn"
        # For any other Indic language, check if closer to Hindi or Bengali
        elif l in ("ur", "pa", "mr", "ne"):
            return settings.WHISPER_MODEL_HI, l
        return settings.WHISPER_MODEL_BN, l

    def initialize_model(self, model_name: Optional[str] = None) -> WhisperModel:
        """
        Pre-warm the primary model into memory on startup.
        """
        target = model_name or settings.WHISPER_MODEL_BN
        return self.get_model(target)

    def get_model(self, model_name: str) -> WhisperModel:
        """
        Retrieve or swap the active Whisper model in memory.
        Enforces single active slot to prevent Cloud Run OOM (RAM stays under 350MB).
        """
        if self._model is not None and self._current_model_name == model_name:
            return self._model

        # Unload previous model from RAM if a different model is requested
        if self._model is not None:
            logger.info(f"Swapping active Whisper model from '{self._current_model_name}' to '{model_name}'...")
            self._model = None
            gc.collect()

        logger.info(
            f"Loading Whisper model '{model_name}' on {settings.WHISPER_DEVICE} "
            f"with {settings.WHISPER_COMPUTE_TYPE} quantization..."
        )
        self._model = WhisperModel(
            model_size_or_path=model_name,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
            download_root=settings.WHISPER_DOWNLOAD_ROOT,
            cpu_threads=settings.WHISPER_CPU_THREADS
        )
        self._current_model_name = model_name
        logger.info(f"Whisper model '{model_name}' active in memory.")
        return self._model

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        initial_prompt: Optional[str] = "খোঁজ, ফ্যাক্ট চেকিং, বাংলা।"
    ) -> Tuple[str, List[Dict[str, Any]], str]:
        """
        Transcribe an audio file into text with timestamps and language handling.
        When language is None, performs a two-phase approach:
          Phase 1: Auto-detect language using unbiased multilingual detector (faster-whisper-tiny)
          Phase 2: Route to specialized fine-tuned model (Bengali, Hindi, or English)
        """
        target_model_name, normalized_lang = self._resolve_model_name_for_lang(language)

        # Phase 1: Rapid unbiased language detection using multilingual detector
        if normalized_lang is None:
            try:
                # Use lightweight unbiased multilingual detector model (Systran/faster-whisper-tiny, 39MB)
                # Fine-tuned single-language models (e.g. small-bn-int8) skew logits to 100% Bengali on Indic speech.
                detector_model = self.get_model("Systran/faster-whisper-tiny")
                audio_array = decode_audio(audio_path)
                auto_lang, auto_prob, _ = detector_model.detect_language(audio_array)

                auto_lang = (auto_lang or "bn").strip().lower()
                logger.info(
                    f"Multilingual detector identified language: '{auto_lang}' (confidence: {auto_prob:.2f})"
                )

                # Determine the correct specialized model for the detected language
                correct_model, normalized_lang = self._get_model_for_detected_lang(auto_lang)

                # Route to specialized model (Bengali, Hindi, or English)
                logger.info(
                    f"Routing to specialized model '{correct_model}' for language '{normalized_lang}'..."
                )
                model = self.get_model(correct_model)
                target_model_name = correct_model

            except Exception as detect_err:
                logger.warning(f"Language auto-detection failed ({detect_err}), defaulting to Bengali.")
                normalized_lang = "bn"
                model = self.get_model(settings.WHISPER_MODEL_BN)
                target_model_name = settings.WHISPER_MODEL_BN
        else:
            model = self.get_model(target_model_name)

        segments_data = []
        full_text_pieces = []
        detected_language = normalized_lang or "unknown"

        try:
            # Only use Bengali initial prompt for Bengali transcription
            prompt_to_use = initial_prompt if (normalized_lang == "bn") else None
            
            # Pass explicit language parameter to model to lock tokenizer script
            lang_param = normalized_lang if normalized_lang in ("bn", "hi", "en") else None

            segments, info = model.transcribe(
                audio_path,
                language=lang_param,
                initial_prompt=prompt_to_use,
                beam_size=2,
                vad_filter=True, # Filter silence and background noise
                vad_parameters=dict(min_silence_duration_ms=500)
            )

            detected_language = info.language or normalized_lang or "unknown"
            logger.info(f"Speech transcribed in language '{detected_language}' (prob: {info.language_probability:.2f}) using model '{target_model_name}'.")

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
