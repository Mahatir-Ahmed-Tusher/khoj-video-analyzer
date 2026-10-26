"""
Unit & Integration Test Suite for Khoj Video Analyzer.
Verifies configuration, anti-bot platform detection, text deduplication,
FastAPI routing, and module imports.
"""
import sys
import unittest

class TestKhojVideoAnalyzer(unittest.TestCase):

    def test_01_config_loading(self):
        from app.config import settings
        self.assertEqual(settings.SERVICE_NAME, "khoj-video-analyzer")
        self.assertIn("bn", settings.WHISPER_MODEL_SIZE)
        self.assertEqual(settings.OCR_LANGUAGES, "ben+eng")
        self.assertGreaterEqual(settings.MAX_VIDEO_DURATION_SECONDS, 300)
        self.assertTrue(settings.AUTO_DELETE_R2_MEDIA)
        print("[PASS] Config loaded successfully with Bengali model and ephemeral R2 storage.")

    def test_02_platform_detection(self):
        from app.services.stream_extractor import detect_platform
        self.assertEqual(detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "youtube")
        self.assertEqual(detect_platform("https://youtu.be/dQw4w9WgXcQ"), "youtube")
        self.assertEqual(detect_platform("https://www.facebook.com/reel/123456789"), "facebook")
        self.assertEqual(detect_platform("https://fb.watch/abcdef/"), "facebook")
        self.assertEqual(detect_platform("https://www.instagram.com/reel/Cx123abc/"), "instagram")
        self.assertEqual(detect_platform("https://twitter.com/user/status/123456"), "twitter")
        self.assertEqual(detect_platform("https://x.com/user/status/123456"), "twitter")
        self.assertEqual(detect_platform("https://www.tiktok.com/@user/video/123456"), "tiktok")
        self.assertEqual(detect_platform("https://v.redd.it/abcdef123"), "reddit")
        print("[PASS] Platform detector correctly identified all social media providers.")

    def test_03_text_cleaning_and_deduplication(self):
        from app.utils.text_cleaner import clean_ocr_text, deduplicate_on_screen_frames, build_factcheck_summary
        
        # Test OCR text cleaning
        raw_bengali = "  \n\t ব্রেকিং   নিউজ: সরকার   নতুন পদক্ষেপ নিয়েছে!!!  \n"
        cleaned = clean_ocr_text(raw_bengali)
        self.assertIn("ব্রেকিং নিউজ: সরকার নতুন পদক্ষেপ নিয়েছে", cleaned)
        
        # Test deduplication of consecutive identical frames (e.g. static banner lasting 6 seconds)
        mock_frames = [
            {"timestamp": "00:02", "text": "ব্রেকিং নিউজ: সরকার নতুন পদক্ষেপ নিয়েছে"},
            {"timestamp": "00:04", "text": "ব্রেকিং নিউজ: সরকার নতুন পদক্ষেপ নিয়েছে"},
            {"timestamp": "00:06", "text": "ব্রেকিং নিউজ: সরকার নতুন পদক্ষেপ নিয়েছে!"},
            {"timestamp": "00:10", "text": "সরাসরি ঢাকা থেকে বিশেষ প্রতিবেদন"},
            {"timestamp": "00:12", "text": "সরাসরি ঢাকা থেকে বিশেষ প্রতিবেদন"}
        ]
        
        deduped = deduplicate_on_screen_frames(mock_frames, similarity_threshold=0.75)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["start"], "00:02")
        self.assertEqual(deduped[0]["end"], "00:06")
        self.assertIn("ব্রেকিং নিউজ", deduped[0]["text"])
        
        self.assertEqual(deduped[1]["start"], "00:10")
        self.assertEqual(deduped[1]["end"], "00:12")
        self.assertIn("সরাসরি ঢাকা", deduped[1]["text"])
        
        # Test summary synthesis
        summary = build_factcheck_summary(
            title="Viral Claim Video",
            platform="youtube",
            author="Test Channel",
            spoken_transcript="প্রধানমন্ত্রী আজ সংসদে এই বিষয়ে বক্তব্য দিয়েছেন।",
            on_screen_texts=deduped
        )
        self.assertIn("Video Title: Viral Claim Video", summary)
        self.assertIn("Spoken Transcript", summary)
        self.assertIn("ব্রেকিং নিউজ", summary)
        print("[PASS] OCR text cleaning, fuzzy frame deduplication, and factcheck synthesis verified.")

    def test_04_fastapi_app_initialization(self):
        from app.main import app
        routes = [route.path for route in app.routes]
        self.assertIn("/", routes)
        self.assertIn("/health", routes)
        self.assertIn("/analyze", routes)
        self.assertIn("/analyze/upload", routes)
        print("[PASS] FastAPI app initialized with all required endpoints: /, /health, /analyze, /analyze/upload.")

    def test_05_modules_and_dependencies(self):
        import yt_dlp
        import faster_whisper
        import cv2
        import pytesseract
        import PIL
        import httpx
        import bs4
        print("[PASS] Core AI and processing libraries (yt-dlp, faster-whisper, opencv, pytesseract, httpx) verified.")

if __name__ == "__main__":
    unittest.main()
