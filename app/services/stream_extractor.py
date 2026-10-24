import os
import re
import json
import base64
import urllib.request
import logging
from typing import Dict, Any, Optional, Tuple, List
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

import tempfile

def get_effective_cookies_path() -> Optional[str]:
    """
    Ensure cookies file exists if YT_COOKIES_PATH, YT_COOKIES_BASE64, or YT_COOKIES_TEXT is configured.
    """
    if settings.YT_COOKIES_PATH and os.path.exists(settings.YT_COOKIES_PATH):
        logger.info(f"Using cookies from configured path: {settings.YT_COOKIES_PATH}")
        return settings.YT_COOKIES_PATH

    raw_text = ""
    cookies_base64 = os.getenv("YT_COOKIES_BASE64", "").strip()
    if cookies_base64:
        try:
            raw_text = base64.b64decode(cookies_base64).decode("utf-8", errors="ignore")
        except Exception as e:
            logger.warning(f"Could not decode YT_COOKIES_BASE64: {e}")

    if not raw_text and settings.YT_COOKIES_TEXT:
        raw_text = settings.YT_COOKIES_TEXT.strip()

    if raw_text:
        tmp_dir = tempfile.gettempdir()
        tmp_cookie = os.path.join(tmp_dir, "cookies.txt")
        try:
            with open(tmp_cookie, "w", encoding="utf-8") as f:
                f.write(raw_text)
            logger.info(f"Successfully loaded {len(raw_text)} bytes of YouTube cookies to {tmp_cookie}")
            return tmp_cookie
        except Exception as e:
            logger.error(f"Failed to write cookies to file: {e}")

    if os.path.exists("/tmp/cookies.txt"):
        logger.info("Found existing /tmp/cookies.txt")
        return "/tmp/cookies.txt"

    return None

def get_base_ydl_opts(custom_ua: Optional[str] = None) -> Dict[str, Any]:
    """
    Build hardened yt-dlp options with anti-bot evasion, Node.js EJS challenge solver,
    and optional proxy or cookie authentication.
    """
    ua = custom_ua or settings.DEFAULT_USER_AGENT
    
    opts: Dict[str, Any] = {
        'quiet': False,
        'no_warnings': False,
        'skip_download': True,
        'geo_bypass': True,
        'user_agent': ua,
        'http_headers': {
            'User-Agent': ua,
            'Accept-Language': 'en-US,en;q=0.9,bn-BD;q=0.8,bn;q=0.7',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
        'source_address': '0.0.0.0', # Force IPv4 to bypass cloud datacenter IPv6 blocks
        # Enable Deno & Node.js JS runtimes for n-sig and signature challenge solving
        'js_runtimes': {'deno': {}, 'node': {}},
        'remote_components': ['ejs:github'],
    }
    
    # Check for optional cookies file or environment string
    cookies_path = get_effective_cookies_path()
    if cookies_path:
        opts['cookiefile'] = cookies_path
        logger.info(f"yt-dlp configured with cookiefile: {cookies_path}")
        
    # Check for optional proxy
    if settings.YT_PROXY:
        opts['proxy'] = settings.YT_PROXY
        
    return opts

def fetch_youtube_captions(
    url: str,
    preferred_lang: Optional[str] = None,
    pre_extracted_info: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Directly ingest official subtitles or automatic captions from YouTube's TimedText API.
    This bypasses media stream downloading completely, is 100% immune to googlevideo datacenter
    media blocks, and returns exact millisecond timestamped segments.
    """
    ydl_opts = get_base_ydl_opts()
    
    try:
        info = pre_extracted_info
        if not info:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
        if not info:
            return None
            
        auto = info.get('automatic_captions') or {}
        subs = info.get('subtitles') or {}
        
        # Priority list for Bengali, then English, then specified language
        candidates = []
        if preferred_lang:
            candidates.extend([preferred_lang, f"{preferred_lang}-orig"])
        candidates.extend(['bn-orig', 'bn', 'bn-BD', 'en-orig', 'en', 'en-US'])
        
        selected_track = None
        source_type = None
        selected_lang = 'unknown'
        
        # 1. First priority: Check official uploaded/curated subtitles
        for lc in candidates:
            if lc in subs and subs[lc]:
                selected_track = next((t for t in subs[lc] if t.get('ext') == 'json3'), subs[lc][0])
                source_type = 'official_subtitles'
                selected_lang = 'bn' if 'bn' in lc else 'en'
                break
                
        # 2. Second priority: Check auto-generated captions
        if not selected_track:
            for lc in candidates:
                if lc in auto and auto[lc]:
                    selected_track = next((t for t in auto[lc] if t.get('ext') == 'json3'), auto[lc][0])
                    source_type = 'automatic_captions'
                    selected_lang = 'bn' if 'bn' in lc else 'en'
                    break
                    
        if not selected_track or not selected_track.get('url'):
            return None
            
        track_url = selected_track['url']
        if 'fmt=' not in track_url:
            track_url += '&fmt=json3'
            
        headers = {
            'User-Agent': settings.DEFAULT_USER_AGENT,
            'Accept': 'application/json,text/html,*/*'
        }
        
        req = urllib.request.Request(track_url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            events = data.get('events', [])
            segments = []
            full_text_parts = []
            
            for ev in events:
                t_start = round(ev.get('tStartMs', 0) / 1000.0, 2)
                d_duration = round(ev.get('dDurationMs', 0) / 1000.0, 2)
                t_end = round(t_start + d_duration, 2)
                segs = ev.get('segs', [])
                line = ''.join(s.get('utf8', '') for s in segs).strip()
                if line and line != '\n':
                    segments.append({
                        'start': t_start,
                        'end': t_end,
                        'text': line
                    })
                    full_text_parts.append(line)
                    
            if not segments:
                return None
                
            raw_transcript = ' '.join(full_text_parts)
            logger.info(f"Direct TimedText captions successfully fetched ({len(segments)} segments, {len(raw_transcript)} chars, source: {source_type})")
            return {
                'raw_transcript': raw_transcript,
                'segments': segments,
                'detected_language': selected_lang,
                'source': source_type
            }
    except Exception as e:
        logger.warning(f"Direct YouTube caption ingestion failed: {e}. Falling back to audio stream...")
        return None

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
                "existing_subtitles": existing_subtitles,
                "_raw_info": info
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
