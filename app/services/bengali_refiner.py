import logging
import httpx
from typing import Tuple
from app.config import settings

logger = logging.getLogger("khoj-video-analyzer")

GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

REFINER_PROMPT = """You are a senior Bengali language editor for a fact-checking journalism system.
The following text is raw spoken speech-to-text / captions from a video. It lacks proper punctuation, has run-on sentences, and may have minor phonetic spelling inaccuracies.

Your task:
1. Add proper Bengali punctuation (দাঁড়ি ।, কমা ,, জিজ্ঞাসা চিহ্ন ?) and fix obvious phonetic spelling errors in standard Bengali (বাংলা). If the input is in Romanized Bengali (Banglish) or corrupted script, convert it accurately into natural standard Bengali script (বাংলা বর্ণমালা).
2. STRICTLY PRESERVE all facts, numbers, dates, names, assertions, claims, and the speaker's exact tone.
3. DO NOT summarize, DO NOT delete any sentence, and DO NOT add new information or hallucinate facts.
4. Output ONLY the polished, correct Bengali text without any introduction, greetings, or explanations.

Raw Transcript:
{raw_text}

Polished Bengali Transcript:"""

class BengaliRefiner:
    """
    LLM-powered post-processing service for Bengali speech/caption transcripts.
    Primary: Google Gemini (gemini-3.5-flash-lite / gemini-flash-lite-latest) for zero-hallucination,
             large context (1M tokens), and fast response (< 3s).
    Fallback: OpenRouter Nemotron (nvidia/nemotron-3-ultra-550b-a55b:free / nvidia/nemotron-3.5-super-vl:free).
    Graceful degradation: If all LLMs fail, returns original raw text seamlessly.
    """

    @classmethod
    async def refine(cls, raw_text: str) -> Tuple[str, str]:
        """
        Refines raw Bengali transcript using primary Gemini models with Nemotron fallback.
        Returns:
            Tuple[refined_text, model_used]
        """
        if not raw_text or len(raw_text.strip()) < 5:
            return raw_text, "original_transcript"

        prompt = REFINER_PROMPT.format(raw_text=raw_text.strip())

        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. Primary Attempt: Google Gemini (Fast, reliable, high-fidelity Bengali)
            if settings.GEMINI_API_KEY:
                gemini_models = [
                    settings.GEMINI_PRIMARY_MODEL,
                    settings.GEMINI_FALLBACK_MODEL
                ]
                for model_name in gemini_models:
                    if not model_name:
                        continue
                    try:
                        logger.info(f"Refining Bengali transcript using Gemini '{model_name}'...")
                        gemini_url = f"{GEMINI_API_URL_TEMPLATE.format(model=model_name)}?key={settings.GEMINI_API_KEY}"
                        gemini_payload = {
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "temperature": 0.1
                            }
                        }
                        headers = {
                            "Content-Type": "application/json",
                            "User-Agent": "Mozilla/5.0"
                        }

                        resp = await client.post(gemini_url, headers=headers, json=gemini_payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                refined_content = " ".join(p.get("text", "") for p in parts if "text" in p).strip()
                                if refined_content:
                                    logger.info(f"Bengali transcript polished successfully with Gemini '{model_name}'.")
                                    return refined_content, model_name
                        else:
                            logger.warning(
                                f"Gemini '{model_name}' returned HTTP {resp.status_code}: {resp.text[:150]}. Trying next candidate..."
                            )
                    except Exception as gemini_err:
                        logger.warning(f"Error during Gemini refinement with '{model_name}': {gemini_err}. Trying next candidate...")

            # 2. Secondary Fallback Attempt: OpenRouter Nemotron
            if settings.OPENROUTER_API_KEY:
                openrouter_models = [
                    settings.NEMOTRON_PRIMARY_MODEL,
                    settings.NEMOTRON_FALLBACK_MODEL
                ]
                headers = {
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://khoj-factchecker.local",
                    "X-Title": "Khoj Video Analyzer",
                    "User-Agent": "Mozilla/5.0"
                }

                for model_name in openrouter_models:
                    if not model_name:
                        continue
                    try:
                        logger.info(f"Fallback: Refining Bengali transcript using OpenRouter '{model_name}'...")
                        payload = {
                            "model": model_name,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.1
                        }

                        resp = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            choices = data.get("choices", [])
                            if choices:
                                refined_content = choices[0].get("message", {}).get("content", "").strip()
                                if refined_content:
                                    logger.info(f"Bengali transcript polished successfully with OpenRouter '{model_name}'.")
                                    return refined_content, model_name
                        else:
                            logger.warning(
                                f"OpenRouter '{model_name}' returned HTTP {resp.status_code}: {resp.text[:150]}. Trying fallback..."
                            )
                    except Exception as or_err:
                        logger.warning(f"Error during OpenRouter refinement with '{model_name}': {or_err}. Trying fallback...")

        # 3. Graceful degradation: Both Gemini and OpenRouter failed or not configured
        logger.info("All LLM refiners bypassed or unavailable. Preserving original raw transcript.")
        return raw_text, "original_transcript"

bengali_refiner = BengaliRefiner()
