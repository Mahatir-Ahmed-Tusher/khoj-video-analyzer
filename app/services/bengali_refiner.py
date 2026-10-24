import logging
import httpx
from typing import Tuple
from app.config import settings

logger = logging.getLogger("khoj-video-analyzer")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

REFINER_PROMPT = """You are a senior Bengali language editor for a fact-checking journalism system.
The following text is raw speech-to-text (ASR) output from an audio recording. It may contain broken words, phonetic spelling inaccuracies, missing diacritics, or Romanized Bengali (Banglish).

Your task:
1. Fix phonetic spelling errors, missing letters, and grammatical flow in standard Bengali (বাংলা). If the input is in Romanized Bengali (Banglish) or corrupted script, convert it accurately into natural standard Bengali script (বাংলা বর্ণমালা).
2. Strictly preserve all facts, numbers, dates, names, assertions, and original speaker tone.
3. Do NOT add extra information, do NOT summarize, and do NOT remove claims.
4. Output ONLY the polished, correct Bengali text. No preamble, no quotes, no explanations.

Raw ASR Transcript:
{raw_text}

Polished Bengali Transcript:"""

class BengaliRefiner:
    """
    LLM-powered post-processing service for Bengali ASR speech-to-text outputs.
    Polishes phonetically broken Bengali using NVIDIA Nemotron models via OpenRouter.
    Includes automated fallback from Nemotron Ultra 550B to Nemotron 3.5 Super VL.
    """

    @classmethod
    async def refine(cls, raw_text: str) -> Tuple[str, str]:
        """
        Refines raw Bengali transcript.
        Returns:
            Tuple[refined_text, model_used]
        """
        if not raw_text or len(raw_text.strip()) < 5:
            return raw_text, "none"

        if not settings.OPENROUTER_API_KEY:
            logger.warning("OPENROUTER_API_KEY not configured. Skipping Bengali post-processing refinement.")
            return raw_text, "none"

        models_to_try = [
            settings.NEMOTRON_PRIMARY_MODEL,
            settings.NEMOTRON_FALLBACK_MODEL
        ]

        prompt = REFINER_PROMPT.format(raw_text=raw_text.strip())
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://khoj-factchecker.local",
            "X-Title": "Khoj Video Analyzer"
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            for model_name in models_to_try:
                try:
                    logger.info(f"Refining Bengali transcript using '{model_name}'...")
                    payload = {
                        "model": model_name,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.1
                    }

                    resp = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        choices = data.get("choices", [])
                        if choices:
                            refined_content = choices[0].get("message", {}).get("content", "").strip()
                            if refined_content:
                                logger.info(f"Bengali transcript successfully polished with '{model_name}'.")
                                return refined_content, model_name
                    else:
                        logger.warning(
                            f"Model '{model_name}' returned HTTP {resp.status_code}: {resp.text[:200]}. Trying fallback..."
                        )

                except Exception as ex:
                    logger.warning(f"Error during Bengali refinement with '{model_name}': {ex}. Trying fallback...")

        logger.warning("All Bengali LLM refiners failed. Returning original raw ASR transcript.")
        return raw_text, "failed_raw_preserved"

bengali_refiner = BengaliRefiner()
