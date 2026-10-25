import os
import re
import json
import base64
import urllib.request
import xml.etree.ElementTree as ET
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
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web']
            }
        }
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

def extract_youtube_video_id(url: str) -> Optional[str]:
    """
    Extract standard 11-character YouTube video ID from various URL formats.
    """
    patterns = [
        r'(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def fetch_youtube_innertube(video_id: str, preferred_lang: Optional[str] = None) -> Dict[str, Any]:
    """
    Directly query YouTube InnerTube Android Player API for metadata and caption tracks.
    Bypasses BotGuard/PO Token restrictions on cloud datacenter IPs.
    """
    player_url = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
    payload = {
        "context": {
            "client": {
                "clientName": "ANDROID",
                "clientVersion": "20.10.38",
                "hl": preferred_lang or "bn",
                "gl": "BD"
            }
        },
        "videoId": video_id
    }
    
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 14; US) gzip",
        "Accept-Language": "en-US,en;q=0.9,bn;q=0.8"
    }
    
    try:
        req = urllib.request.Request(
            player_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        
        # Try direct connection first (Android mobile client is universally trusted on direct IPs)
        data = None
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as direct_err:
            logger.debug(f"Direct InnerTube request failed ({direct_err}). Checking proxy fallback...")

        # If direct failed or returned playability bot challenge, try via proxy if configured
        if not data or data.get("playabilityStatus", {}).get("status") != "OK":
            if settings.YT_PROXY:
                try:
                    proxy_handler = urllib.request.ProxyHandler({
                        'http': settings.YT_PROXY,
                        'https': settings.YT_PROXY
                    })
                    opener = urllib.request.build_opener(proxy_handler)
                    with opener.open(req, timeout=12) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                except Exception as proxy_err:
                    logger.debug(f"Proxy InnerTube request failed ({proxy_err}).")

        if not data:
            return {"success": False, "reason": "Failed to connect to YouTube player API."}

        playability = data.get("playabilityStatus", {}).get("status")
        if playability != "OK":
            reason = data.get("playabilityStatus", {}).get("reason", "Unknown error")
            return {"success": False, "reason": reason}
            
        details = data.get("videoDetails", {})
        title = details.get("title", "")
        author = details.get("author", "")
        duration_seconds = int(details.get("lengthSeconds", 0))
        thumbnails = details.get("thumbnail", {}).get("thumbnails", [])
        thumbnail_url = thumbnails[-1].get("url") if thumbnails else None
        
        caption_tracks = (
            data.get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
        
        if not caption_tracks:
            return {
                "success": True,
                "has_captions": False,
                "video_id": video_id,
                "title": title,
                "author": author,
                "duration_seconds": duration_seconds,
                "thumbnail_url": thumbnail_url,
                "raw_transcript": None,
                "segments": [],
                "detected_language": "unknown"
            }
            
        candidates = []
        if preferred_lang:
            candidates.append(preferred_lang.lower())
        candidates.extend(["bn", "bn-bd", "hi", "en", "en-us"])
        
        selected_track = None
        for cand in candidates:
            for track in caption_tracks:
                lc = track.get("languageCode", "").lower()
                if cand == lc or lc.startswith(cand):
                    selected_track = track
                    break
            if selected_track:
                break
                
        if not selected_track:
            selected_track = caption_tracks[0]
            
        base_url = selected_track.get("baseUrl")
        detected_lang = selected_track.get("languageCode", "unknown")
        
        caption_req = urllib.request.Request(base_url, headers={"User-Agent": "Mozilla/5.0"})
        xml_content = ""
        # Direct fetch first (signed URL has ip=0.0.0.0 and avoids shared proxy rate-limits)
        try:
            with urllib.request.urlopen(caption_req, timeout=10) as c_resp:
                xml_content = c_resp.read().decode("utf-8", errors="ignore")
        except Exception as direct_err:
            logger.info(f"Direct timedtext fetch failed: {direct_err}. Trying via opener/proxy...")
            with opener.open(caption_req, timeout=12) as c_resp:
                xml_content = c_resp.read().decode("utf-8", errors="ignore")
            
        root = ET.fromstring(xml_content)
        segments = []
        text_parts = []
        
        for elem in root.findall(".//text"):
            start = float(elem.attrib.get("start", 0))
            dur = float(elem.attrib.get("dur", 0))
            end = round(start + dur, 2)
            txt = (elem.text or "").strip()
            txt = re.sub(r'&amp;', '&', txt)
            txt = re.sub(r'&quot;', '"', txt)
            txt = re.sub(r'&#39;', "'", txt)
            txt = re.sub(r'&lt;', '<', txt)
            txt = re.sub(r'&gt;', '>', txt)
            if txt:
                segments.append({"start": start, "end": end, "text": txt})
                text_parts.append(txt)
                
        if not segments:
            for p in root.findall(".//p"):
                t_ms = int(p.attrib.get("t", 0))
                d_ms = int(p.attrib.get("d", 0))
                start = round(t_ms / 1000.0, 2)
                end = round((t_ms + d_ms) / 1000.0, 2)
                p_text = "".join(p.itertext()).strip()
                if p_text:
                    segments.append({"start": start, "end": end, "text": p_text})
                    text_parts.append(p_text)
                    
        raw_transcript = " ".join(text_parts)
        return {
            "success": True,
            "has_captions": len(segments) > 0,
            "video_id": video_id,
            "title": title,
            "author": author,
            "duration_seconds": duration_seconds,
            "thumbnail_url": thumbnail_url,
            "raw_transcript": raw_transcript if segments else None,
            "segments": segments,
            "detected_language": detected_lang
        }
    except Exception as ex:
        logger.warning(f"InnerTube Android player query failed: {ex}")
        return {"success": False, "reason": str(ex)}

def fetch_youtube_captions(
    url: str,
    preferred_lang: Optional[str] = None,
    pre_extracted_info: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Directly ingest official subtitles or automatic captions from YouTube.
    First checks InnerTube Android API, then falls back to TimedText scraping.
    """
    # 1. First priority: Direct InnerTube Android client
    vid = extract_youtube_video_id(url)
    if vid:
        it_res = fetch_youtube_innertube(vid, preferred_lang)
        if it_res.get("success") and it_res.get("has_captions") and it_res.get("raw_transcript"):
            logger.info(f"InnerTube Android captions fetched ({len(it_res['segments'])} segments, {len(it_res['raw_transcript'])} chars)")
            return {
                'raw_transcript': it_res["raw_transcript"],
                'segments': it_res["segments"],
                'detected_language': it_res["detected_language"],
                'source': 'innertube_android_captions'
            }

    # 2. Secondary fallback: yt-dlp extracted subtitles if pre_extracted_info provided
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
        
        candidates = []
        if preferred_lang:
            candidates.extend([preferred_lang, f"{preferred_lang}-orig"])
        candidates.extend(['bn-orig', 'bn', 'bn-BD', 'en-orig', 'en', 'en-US'])
        
        selected_track = None
        source_type = None
        selected_lang = 'unknown'
        
        for lc in candidates:
            if lc in subs and subs[lc]:
                selected_track = next((t for t in subs[lc] if t.get('ext') == 'json3'), subs[lc][0])
                source_type = 'official_subtitles'
                selected_lang = 'bn' if 'bn' in lc else 'en'
                break
                
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
    
    # 1. Fast path for YouTube: InnerTube Android API (Instant, BotGuard-free)
    if platform == "youtube":
        vid = extract_youtube_video_id(url)
        if vid:
            it_res = fetch_youtube_innertube(vid)
            if it_res.get("success"):
                logger.info(f"InnerTube Android metadata extracted for video {vid} (has_captions: {it_res.get('has_captions')})")
                return {
                    "success": True,
                    "platform": platform,
                    "video_id": vid,
                    "title": it_res.get("title") or "YouTube Video",
                    "author": it_res.get("author") or "YouTube Creator",
                    "duration_seconds": it_res.get("duration_seconds", 0),
                    "thumbnail_url": it_res.get("thumbnail_url"),
                    "existing_subtitles": "[Captions available]" if it_res.get("has_captions") else "",
                    "_innertube_result": it_res,
                    "_raw_info": None
                }
                
    # 2. General path: yt-dlp extractor
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
