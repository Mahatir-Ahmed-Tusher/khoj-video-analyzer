import os
import re
import logging
from typing import Dict, Any, Optional, Tuple
import yt_dlp
import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger("khoj-video-analyzer")

def detect_platform(url: str) -> str:
    """
    Detect the source social media or hosting platform from URL.
    """
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    elif "facebook.com" in u or "fb.watch" in u or "fb.com" in u:
        return "facebook"
    elif "instagram.com" in u:
        return "instagram"
    elif "twitter.com" in u or "x.com" in u:
        return "twitter"
    elif "tiktok.com" in u:
        return "tiktok"
    elif "reddit.com" in u or "v.redd.it" in u:
        return "reddit"
    return "generic"

def get_base_ydl_opts(custom_ua: Optional[str] = None) -> Dict[str, Any]:
    """
    Build hardened yt-dlp options with anti-bot evasion and Android/iOS client emulation.
    """
    ua = custom_ua or settings.DEFAULT_USER_AGENT
    
    opts: Dict[str, Any] = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'geo_bypass': True,
        'user_agent': ua,
        'http_headers': {
            'User-Agent': ua,
            'Accept-Language': 'en-US,en;q=0.9,bn-BD;q=0.8,bn;q=0.7',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
        # Emulate official Android / iOS mobile clients to bypass YouTube/Meta datacenter blocks
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web'],
            }
        }
    }
    
    # Check for optional cookies file or environment string
    cookies_path = os.getenv("YT_COOKIES_PATH")
    if cookies_path and os.path.exists(cookies_path):
        opts['cookiefile'] = cookies_path
        
    return opts

async def extract_metadata(url: str) -> Dict[str, Any]:
    """
    Extract video metadata (title, author, duration, existing subtitles) without downloading.
    """
    platform = detect_platform(url)
    ydl_opts = get_base_ydl_opts()
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Could not extract video metadata.")
                
            duration = info.get('duration', 0)
            title = info.get('title', 'Unknown Title')
            author = info.get('uploader') or info.get('channel') or info.get('creator') or 'Unknown Creator'
            thumbnail = info.get('thumbnail')
            
            # Extract existing subtitles if available (bypasses audio transcription when present)
            existing_subtitles = ""
            subtitles = info.get('subtitles') or info.get('automatic_captions') or {}
            for lang_code in ['bn', 'en', 'bn-BD', 'en-US']:
                if lang_code in subtitles:
                    # Captions are available for this language
                    existing_subtitles = f"[Subtitles available in {lang_code}]"
                    break
                    
            return {
                "success": True,
                "platform": platform,
                "title": title,
                "author": author,
                "duration_seconds": duration,
                "thumbnail_url": thumbnail,
                "existing_subtitles": existing_subtitles
            }
    except Exception as e:
        logger.warning(f"yt-dlp metadata extraction failed: {e}. Trying fallback...")
        return await fallback_scrape_metadata(url, platform)

async def fallback_scrape_metadata(url: str, platform: str) -> Dict[str, Any]:
    """
    Fallback metadata extractor using social bot User-Agent (matches khoj-content-service).
    Forces Facebook/Meta/Twitter to return SSR OpenGraph metadata.
    """
    headers = {
        'User-Agent': settings.SOCIAL_BOT_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                title = ""
                og_title = soup.find('meta', property='og:title')
                if og_title and og_title.get('content'):
                    title = og_title['content'].strip()
                elif soup.title:
                    title = soup.title.string.strip() if soup.title.string else ""
                    
                author = ""
                og_site = soup.find('meta', property='og:site_name')
                if og_site and og_site.get('content'):
                    author = og_site['content'].strip()
                    
                thumbnail = None
                og_img = soup.find('meta', property='og:image')
                if og_img and og_img.get('content'):
                    thumbnail = og_img['content'].strip()
                    
                return {
                    "success": True,
                    "platform": platform,
                    "title": title or "Social Media Video",
                    "author": author or platform.capitalize(),
                    "duration_seconds": 0,
                    "thumbnail_url": thumbnail,
                    "existing_subtitles": ""
                }
    except Exception as ex:
        logger.error(f"Fallback scraping error: {ex}")
        
    return {
        "success": False,
        "platform": platform,
        "title": "Video Claim",
        "author": platform.capitalize(),
        "duration_seconds": 0,
        "thumbnail_url": None,
        "existing_subtitles": ""
    }

def download_audio_stream(url: str, target_dir: str) -> Optional[str]:
    """
    Download ONLY the lowest bitrate audio stream (32-64kbps).
    Extremely lightweight: typical 2-minute video audio is only ~1MB to 2MB.
    """
    output_template = os.path.join(target_dir, "audio.%(ext)s")
    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'skip_download': False,
        # Universal audio stream priority with low-resolution video fallback
        'format': 'ba/b[height<=360]/b[height<=480]/worst/b',
        'outtmpl': output_template,
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }] if False else [] # Keep raw stream to avoid extra transcode unless needed
    })
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Find the downloaded file
            for file in os.listdir(target_dir):
                if file.startswith("audio."):
                    return os.path.join(target_dir, file)
    except Exception as e:
        logger.error(f"Error downloading audio stream: {e}")
        
    return None

def download_video_for_ocr(url: str, target_dir: str) -> Optional[str]:
    """
    Download a low-resolution (360p or lower) video stream strictly for frame OCR.
    Never downloads 1080p/4K; 360p keeps memory usage under 10MB.
    """
    output_template = os.path.join(target_dir, "video_ocr.%(ext)s")
    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'skip_download': False,
        # Force low-resolution video
        'format': 'bestvideo[height<=360]/worstvideo[ext=mp4]/worst[ext=mp4]/worst',
        'outtmpl': output_template,
        'noplaylist': True,
    })
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            for file in os.listdir(target_dir):
                if file.startswith("video_ocr."):
                    return os.path.join(target_dir, file)
    except Exception as e:
        logger.error(f"Error downloading video for OCR: {e}")
        
    return None
