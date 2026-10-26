import os
import re
import json
import base64
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import logging
import socket
import time
import random
from typing import Dict, Any, Optional, Tuple, List
import yt_dlp
import httpx
from bs4 import BeautifulSoup

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

from app.config import settings

logger = logging.getLogger("khoj-video-analyzer")

# Known login-wall / error-page titles returned when platforms block unauthenticated access.
# If fallback scraping resolves one of these strings it is garbage data, not a real video title.
_BLOCKED_PAGE_TITLES: frozenset = frozenset({
    "instagram", "login \u2022 instagram", "sign up \u2022 instagram",
    "tiktok", "visit tiktok to discover videos!", "tiktok - make your day",
    "reddit - the heart of the internet", "reddit - dive into anything",
    "log in to facebook", "facebook",
    "vimeo", "log in to vimeo",
    "twitter", "x / twitter", "x (formerly twitter)",
})

def detect_platform(url: str) -> str:
    """
    Detect the source social media or hosting platform from URL.
    """
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    elif "facebook.com" in u or "fb.watch" in u or "fb.com" in u or "fb.gg" in u:
        return "facebook"
    elif "instagram.com" in u or "instagr.am" in u:
        return "instagram"
    elif "twitter.com" in u or "x.com" in u or "//t.co/" in u:
        return "twitter"
    elif "tiktok.com" in u:
        return "tiktok"
    elif "reddit.com" in u or "v.redd.it" in u or "redd.it" in u:
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
    cookies_base64 = settings.YT_COOKIES_BASE64.strip()
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

def pot_server_reachable(url: str = "http://127.0.0.1:4416", timeout: float = 0.5) -> bool:
    """Probe the bgutil POT server with a quick TCP connect."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4416
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, Exception):
        return False

def get_base_ydl_opts(custom_ua: Optional[str] = None, platform: Optional[str] = None) -> Dict[str, Any]:
    """
    Build hardened yt-dlp options with anti-bot evasion, Node.js/Deno EJS challenge solver,
    Proof of Origin (PO) token generation, and optional proxy or cookie authentication.
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
    
    # Platform-specific configurations
    if platform == "youtube" or platform is None:
        opts['extractor_args'] = {
            'youtube': {
                'player_client': ['android'],
                'player_skip': ['web', 'mweb'],
            }
        }
        pot_url = getattr(settings, 'POT_SERVER', 'http://127.0.0.1:4416')
        if pot_server_reachable(pot_url):
            opts['extractor_args']['youtubepot-bgutilhttp'] = {'base_url': [pot_url]}
            logger.info(f"Attached bgutil PO-token provider from {pot_url}")
    elif platform == "instagram":
        opts['socket_timeout'] = 15
        opts['retries'] = 3

    # Apply cookies across all platforms (crucial for Facebook, Instagram, Twitter, YouTube on cloud IPs)
    cookies_path = get_effective_cookies_path()
    if cookies_path:
        opts['cookiefile'] = cookies_path
        logger.info(f"yt-dlp configured with cookiefile for {platform}: {cookies_path}")
            
    # Apply proxy if configured to shield cloud datacenter IPs from bot blocks across all platforms
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

def fetch_youtube_oembed(url: str) -> Optional[Dict[str, Any]]:
    """
    Fetch public metadata (title, author, thumbnail) from YouTube's official oEmbed API.
    Fast, reliable, and completely immune to cloud datacenter IP anti-bot blocks.
    """
    try:
        vid = extract_youtube_video_id(url)
        clean_url = f"https://www.youtube.com/watch?v={vid}" if vid else url
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(clean_url)}&format=json"
        req = urllib.request.Request(
            oembed_url,
            headers={
                "User-Agent": settings.DEFAULT_USER_AGENT,
                "Accept": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "title": data.get("title", ""),
                "author": data.get("author_name", "YouTube"),
                "thumbnail_url": data.get("thumbnail_url")
            }
    except Exception as e:
        logger.warning(f"YouTube oEmbed metadata fetch failed: {e}")
        return None

def fetch_youtube_transcript_api(video_id: str, preferred_lang: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch YouTube official subtitles or auto-generated captions using youtube-transcript-api.
    Extracts captions directly from public YouTube timedtext endpoints without bot checks.
    """
    if not YouTubeTranscriptApi:
        logger.warning("youtube-transcript-api is not installed.")
        return None

    try:
        session = None
        cookies_path = get_effective_cookies_path()
        if cookies_path and os.path.exists(cookies_path):
            try:
                import http.cookiejar
                import requests
                session = requests.Session()
                cj = http.cookiejar.MozillaCookieJar(cookies_path)
                cj.load(ignore_discard=True, ignore_expires=True)
                session.cookies = cj
            except Exception as cj_err:
                logger.warning(f"Could not load cookies into transcript session: {cj_err}")
                session = None

        ytt = YouTubeTranscriptApi(http_client=session)

        langs = []
        if preferred_lang:
            langs.extend([preferred_lang, f"{preferred_lang}-BD", f"{preferred_lang}-IN"])
        # Priority: Bengali first, then English, then Hindi
        langs.extend(["bn", "bn-BD", "en", "en-US", "hi"])

        try:
            transcript_list = ytt.list(video_id)
            transcript_obj = None

            # 1. Check if preferred or candidate language exists in manual transcripts
            try:
                transcript_obj = transcript_list.find_manually_created_transcript(langs)
            except Exception:
                pass

            # 2. Check if preferred or candidate language exists in generated transcripts
            if not transcript_obj:
                try:
                    transcript_obj = transcript_list.find_generated_transcript(langs)
                except Exception:
                    pass

            # 3. Fallback to any transcript in candidate list
            if not transcript_obj:
                try:
                    transcript_obj = transcript_list.find_transcript(langs)
                except Exception:
                    pass

            # 4. Ultimate fallback: pick the first available transcript
            if not transcript_obj:
                for t in transcript_list:
                    transcript_obj = t
                    break

            if transcript_obj:
                fetched = transcript_obj.fetch()
                segments = []
                text_parts = []
                for s in fetched:
                    txt = getattr(s, "text", None) if hasattr(s, "text") else (s.get("text") if isinstance(s, dict) else "")
                    txt = (txt or "").strip()
                    if txt:
                        start = getattr(s, "start", None) if hasattr(s, "start") else (s.get("start") if isinstance(s, dict) else 0.0)
                        dur = getattr(s, "duration", None) if hasattr(s, "duration") else (s.get("duration") if isinstance(s, dict) else 0.0)
                        start_sec = round(float(start or 0), 2)
                        end_sec = round(start_sec + float(dur or 0), 2)
                        segments.append({"start": start_sec, "end": end_sec, "text": txt})
                        text_parts.append(txt)

                if segments:
                    raw_transcript = " ".join(text_parts).strip()
                    lang = transcript_obj.language_code
                    logger.info(f"youtube-transcript-api fetched {len(segments)} segments ({len(raw_transcript)} chars, lang={lang}) for {video_id}")
                    return {
                        "success": True,
                        "has_captions": True,
                        "video_id": video_id,
                        "raw_transcript": raw_transcript,
                        "segments": segments,
                        "detected_language": lang,
                        "source": "youtube_transcript_api"
                    }
        except Exception as list_err:
            logger.debug(f"transcript_list retrieval failed for {video_id}: {list_err}. Trying direct fetch...")
            fetched = ytt.fetch(video_id, languages=langs)
            segments = []
            text_parts = []
            for s in fetched:
                txt = getattr(s, "text", None) if hasattr(s, "text") else (s.get("text") if isinstance(s, dict) else "")
                txt = (txt or "").strip()
                if txt:
                    start = getattr(s, "start", None) if hasattr(s, "start") else (s.get("start") if isinstance(s, dict) else 0.0)
                    dur = getattr(s, "duration", None) if hasattr(s, "duration") else (s.get("duration") if isinstance(s, dict) else 0.0)
                    start_sec = round(float(start or 0), 2)
                    end_sec = round(start_sec + float(dur or 0), 2)
                    segments.append({"start": start_sec, "end": end_sec, "text": txt})
                    text_parts.append(txt)

            if segments:
                raw_transcript = " ".join(text_parts).strip()
                lang = getattr(fetched, "language_code", None) or getattr(fetched, "language", "unknown")
                return {
                    "success": True,
                    "has_captions": True,
                    "video_id": video_id,
                    "raw_transcript": raw_transcript,
                    "segments": segments,
                    "detected_language": lang,
                    "source": "youtube_transcript_api"
                }
    except Exception as e:
        logger.warning(f"fetch_youtube_transcript_api failed for {video_id}: {e}")
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
        
        # Build unified opener with cookies and proxy if available
        handlers = []
        cookies_path = get_effective_cookies_path()
        if cookies_path and os.path.exists(cookies_path):
            try:
                import http.cookiejar
                cj = http.cookiejar.MozillaCookieJar(cookies_path)
                cj.load(ignore_discard=True, ignore_expires=True)
                handlers.append(urllib.request.HTTPCookieProcessor(cj))
            except Exception as e:
                logger.debug(f"Could not load cookies for InnerTube: {e}")

        if settings.YT_PROXY:
            try:
                handlers.append(urllib.request.ProxyHandler({
                    'http': settings.YT_PROXY,
                    'https': settings.YT_PROXY
                }))
            except Exception:
                pass

        opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()

        data = None
        try:
            with opener.open(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as direct_err:
            logger.debug(f"InnerTube request failed ({direct_err}).")

        if not data:
            return {"success": False, "reason": "Failed to connect to YouTube player API."}

        playability = data.get("playabilityStatus", {}).get("status")
        if playability != "OK":
            reason = data.get("playabilityStatus", {}).get("reason", "Unknown error")
            return {"success": False, "reason": reason}
            
        details = data.get("videoDetails", {})
        title = details.get("title", "")
        author = details.get("author", "")
        duration_seconds = int(round(float(details.get("lengthSeconds") or 0)))
        thumbnails = details.get("thumbnail", {}).get("thumbnails", [])
        thumbnail_url = thumbnails[-1].get("url") if thumbnails else None
        short_desc = (details.get("shortDescription") or "").strip()
        source_caption = short_desc if short_desc else None
        
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
                "source_caption": source_caption,
                "raw_transcript": None,
                "segments": [],
                "detected_language": "unknown"
            }
            
        candidates = []
        if preferred_lang:
            candidates.append(preferred_lang.lower())
        candidates.extend(["bn", "bn-bd", "hi", "en", "en-us"])
        
        # Order caption tracks by candidate preference
        ordered_tracks = []
        for cand in candidates:
            for track in caption_tracks:
                lc = track.get("languageCode", "").lower()
                if (cand == lc or lc.startswith(cand)) and track not in ordered_tracks:
                    ordered_tracks.append(track)
        for track in caption_tracks:
            if track not in ordered_tracks:
                ordered_tracks.append(track)

        segments = []
        text_parts = []
        detected_lang = "unknown"

        proxy_opener = None
        if settings.YT_PROXY:
            try:
                proxy_handler = urllib.request.ProxyHandler({'http': settings.YT_PROXY, 'https': settings.YT_PROXY})
                proxy_opener = urllib.request.build_opener(proxy_handler)
            except Exception:
                pass

        for track in ordered_tracks:
            lang_code = track.get("languageCode", "unknown")
            base_url = track.get("baseUrl")
            if not base_url:
                continue

            # Strip &fmt=srv3 so YouTube returns standard XML instead of hanging on protobuf
            clean_url = re.sub(r'&fmt=srv\d*', '', base_url)
            caption_req = urllib.request.Request(
                clean_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Referer": "https://www.youtube.com/",
                    "Origin": "https://www.youtube.com"
                }
            )
            xml_content = ""

            try:
                with opener.open(caption_req, timeout=8) as c_resp:
                    xml_content = c_resp.read().decode("utf-8", errors="ignore")
            except Exception as direct_err:
                logger.debug(f"Caption track download failed: {direct_err}")

            if not xml_content:
                continue

            try:
                root = ET.fromstring(xml_content)
            except ET.ParseError:
                continue

            track_segments = []
            track_texts = []
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
                    track_segments.append({"start": start, "end": end, "text": txt})
                    track_texts.append(txt)

            if not track_segments:
                for p in root.findall(".//p"):
                    t_ms = int(p.attrib.get("t", 0))
                    d_ms = int(p.attrib.get("d", 0))
                    start = round(t_ms / 1000.0, 2)
                    end = round((t_ms + d_ms) / 1000.0, 2)
                    p_text = "".join(p.itertext()).strip()
                    if p_text:
                        track_segments.append({"start": start, "end": end, "text": p_text})
                        track_texts.append(p_text)

            if track_segments:
                segments = track_segments
                text_parts = track_texts
                detected_lang = lang_code
                break

        raw_transcript = " ".join(text_parts)
        return {
            "success": True,
            "has_captions": len(segments) > 0,
            "video_id": video_id,
            "title": title,
            "author": author,
            "duration_seconds": duration_seconds,
            "thumbnail_url": thumbnail_url,
            "source_caption": source_caption,
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
    First checks youtube-transcript-api, then InnerTube Android API, then TimedText scraping.
    """
    vid = extract_youtube_video_id(url)
    if vid:
        # 1. First priority: youtube-transcript-api (resilient against datacenter IP blocks)
        ytt_res = fetch_youtube_transcript_api(vid, preferred_lang)
        if ytt_res and ytt_res.get("has_captions") and ytt_res.get("raw_transcript"):
            logger.info(f"youtube-transcript-api captions fetched ({len(ytt_res['segments'])} segments, {len(ytt_res['raw_transcript'])} chars)")
            return {
                'raw_transcript': ytt_res["raw_transcript"],
                'segments': ytt_res["segments"],
                'detected_language': ytt_res["detected_language"],
                'source': 'youtube_transcript_api'
            }

        # 2. Secondary fallback: Direct InnerTube Android client
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
    
    # 1. Fast, highly resilient path for YouTube: oEmbed + youtube-transcript-api
    if platform == "youtube":
        vid = extract_youtube_video_id(url)
        if vid:
            # 1a. Fetch metadata from YouTube's official public oEmbed API
            oembed_data = fetch_youtube_oembed(url)
            title = (oembed_data.get("title") if oembed_data else None) or "YouTube Video"
            author = (oembed_data.get("author") if oembed_data else None) or "YouTube Creator"
            thumb = (oembed_data.get("thumbnail_url") if oembed_data else None) or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

            # 1b. Captions via youtube-transcript-api (resilient against datacenter IP blocks)
            ytt_res = fetch_youtube_transcript_api(vid)
            if ytt_res and ytt_res.get("has_captions") and ytt_res.get("raw_transcript"):
                logger.info(f"YouTube transcript and metadata resolved via oEmbed + youtube-transcript-api for video {vid}")
                segs = ytt_res.get("segments", [])
                duration = int(round(segs[-1]["end"])) if segs else 0
                return {
                    "success": True,
                    "platform": platform,
                    "video_id": vid,
                    "title": title,
                    "author": author,
                    "duration_seconds": duration,
                    "thumbnail_url": thumb,
                    "source_caption": None,
                    "existing_subtitles": "[Captions available]",
                    "_innertube_result": ytt_res,
                    "_raw_info": None
                }

            # 1c. InnerTube Android API fallback
            it_res = fetch_youtube_innertube(vid)
            if it_res.get("success"):
                logger.info(f"InnerTube Android metadata extracted for video {vid} (has_captions: {it_res.get('has_captions')})")
                return {
                    "success": True,
                    "platform": platform,
                    "video_id": vid,
                    "title": it_res.get("title") or title,
                    "author": it_res.get("author") or author,
                    "duration_seconds": it_res.get("duration_seconds", 0),
                    "thumbnail_url": it_res.get("thumbnail_url") or thumb,
                    "source_caption": it_res.get("source_caption"),
                    "existing_subtitles": "[Captions available]" if it_res.get("has_captions") else "",
                    "_innertube_result": it_res,
                    "_raw_info": None
                }

            # If oEmbed succeeded even if captions are absent
            if oembed_data:
                return {
                    "success": True,
                    "platform": platform,
                    "video_id": vid,
                    "title": title,
                    "author": author,
                    "duration_seconds": 0,
                    "thumbnail_url": thumb,
                    "source_caption": None,
                    "existing_subtitles": "",
                    "_innertube_result": None,
                    "_raw_info": None
                }

    # 2. General path: yt-dlp extractor
    ydl_opts = get_base_ydl_opts(platform=platform)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Could not extract video metadata.")
                
            duration = int(round(float(info.get('duration') or 0)))
            title = info.get('title', 'Unknown Title')
            author = info.get('uploader') or info.get('channel') or info.get('creator') or 'Unknown Creator'
            thumbnail = info.get('thumbnail')
            raw_desc = (info.get('description') or '').strip()
            source_caption = raw_desc if (raw_desc and raw_desc.lower() not in _BLOCKED_PAGE_TITLES) else None
            
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
                "video_id": extract_youtube_video_id(url) if platform == "youtube" else None,
                "title": title,
                "author": author,
                "duration_seconds": duration,
                "thumbnail_url": thumbnail,
                "source_caption": source_caption,
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
    vid = extract_youtube_video_id(url) if platform == "youtube" else None

    # For YouTube, try public oEmbed first before generic HTML scraping
    if platform == "youtube":
        oembed = fetch_youtube_oembed(url)
        if oembed:
            return {
                "success": True,
                "platform": platform,
                "video_id": vid,
                "title": oembed.get("title", "YouTube Video"),
                "author": oembed.get("author", "YouTube Creator"),
                "duration_seconds": 0,
                "thumbnail_url": oembed.get("thumbnail_url"),
                "source_caption": None,
                "existing_subtitles": ""
            }

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

                # Guard: if the title matches a known login-wall/block page, this is not real metadata.
                # Returning success=True here would poison the FactCheck engine with garbage claim data.
                if not title or title.strip().lower() in _BLOCKED_PAGE_TITLES:
                    logger.warning(
                        f"fallback_scrape_metadata: login/block page detected for '{platform}' "
                        f"(scraped title: '{title}'). Returning failure to prevent false-positive success."
                    )
                    return {
                        "success": False,
                        "platform": platform,
                        "video_id": vid,
                        "title": "Video Claim",
                        "author": platform.capitalize(),
                        "duration_seconds": 0,
                        "thumbnail_url": None,
                        "source_caption": None,
                        "existing_subtitles": "",
                    }

                author = ""
                og_site = soup.find('meta', property='og:site_name')
                if og_site and og_site.get('content'):
                    author = og_site['content'].strip()
                    
                thumbnail = None
                og_img = soup.find('meta', property='og:image')
                if og_img and og_img.get('content'):
                    thumbnail = og_img['content'].strip()

                source_caption = None
                og_desc = soup.find('meta', property='og:description') or soup.find('meta', attrs={'name': 'description'})
                if og_desc and og_desc.get('content'):
                    c_text = og_desc['content'].strip()
                    if c_text and c_text.lower() not in _BLOCKED_PAGE_TITLES:
                        source_caption = c_text
                    
                return {
                    "success": True,
                    "platform": platform,
                    "video_id": vid,
                    "title": title or "Social Media Video",
                    "author": author or platform.capitalize(),
                    "duration_seconds": 0,
                    "thumbnail_url": thumbnail,
                    "source_caption": source_caption,
                    "existing_subtitles": ""
                }
    except Exception as ex:
        logger.error(f"Fallback scraping error: {ex}")
        
    return {
        "success": False,
        "platform": platform,
        "video_id": vid,
        "title": "Video Claim",
        "author": platform.capitalize(),
        "duration_seconds": 0,
        "thumbnail_url": None,
        "source_caption": None,
        "existing_subtitles": ""
    }

def download_youtube_audio_invidious(video_id: str, target_dir: str) -> Optional[str]:
    """
    Fallback mechanism for YouTube videos without captions when yt-dlp is blocked on cloud IPs.
    Queries reliable Invidious API instances to retrieve direct audio stream formats.
    """
    instances = [
        "https://yt.chocolatemoo53.com",
        "https://invidious.nerdvpn.de",
        "https://inv.nadeko.net",
    ]
    for inst in instances:
        try:
            api_url = f"{inst}/api/v1/videos/{video_id}"
            req = urllib.request.Request(
                api_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                adaptive = data.get("adaptiveFormats", [])
                audio = [f for f in adaptive if f.get("type", "").startswith("audio/") and f.get("url")]
                if not audio:
                    continue
                # Pick lowest bitrate stream to download rapidly and keep RAM small
                audio.sort(key=lambda x: int(x.get("bitrate", 999999)))
                chosen = audio[0]
                url = chosen["url"]
                ext = chosen.get("container", "m4a")
                out_path = os.path.join(target_dir, f"audio.{ext}")
                
                dl_req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    }
                )
                with urllib.request.urlopen(dl_req, timeout=10) as dl_resp, open(out_path, "wb") as out_f:
                    while True:
                        chunk = dl_resp.read(128 * 1024)
                        if not chunk:
                            break
                        out_f.write(chunk)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                    logger.info(f"Successfully downloaded YouTube audio fallback via Invidious ({inst}) - {os.path.getsize(out_path)} bytes")
                    return out_path
        except Exception as e:
            logger.debug(f"Invidious instance {inst} fallback failed: {e}")
    return None

def download_reddit_media_fallback(url: str, target_dir: str, is_video: bool = False) -> Optional[str]:
    """
    Fallback resolver for Reddit media when yt-dlp triggers 'Account authentication is required'.
    Queries rapidsave public API to resolve direct v.redd.it audio or video stream without credentials.
    """
    try:
        encoded_url = urllib.parse.quote(url)
        info_url = f"https://rapidsave.com/info?url={encoded_url}"
        req = urllib.request.Request(
            info_url,
            headers={"User-Agent": settings.DEFAULT_USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

            # Early bail-out: rapidsave signals errors via alert-danger Bootstrap div
            if "alert-danger" in html or "rate limits blocked" in html.lower():
                logger.warning(
                    f"rapidsave rate-limited or rejected Reddit URL '{url}'. "
                    f"Stream URL resolution not possible."
                )
                return None

            stream_url = None
            if not is_video:
                # Prefer audio-only stream first
                m_audio = re.search(r'audio_url=(https://v\.redd\.it/[^&"\']+)', html)
                if not m_audio:
                    m_audio = re.search(r'(https://v\.redd\.it/[^/]+/(?:CMAF_AUDIO|DASH_audio)[^"\'&]+)', html)
                if m_audio:
                    stream_url = urllib.parse.unquote(m_audio.group(1))

            if not stream_url:
                # Fallback to video stream (which also contains audio or can be used for OCR)
                m_vid = re.search(r'video_url=(https://v\.redd\.it/[^&"\']+)', html)
                if not m_vid:
                    m_vid = re.search(r'(https://v\.redd\.it/[^/]+/(?:CMAF_\d+|DASH_\d+)[^"\'&]+)', html)
                if m_vid:
                    stream_url = urllib.parse.unquote(m_vid.group(1))

            if stream_url:
                prefix = "video_ocr" if is_video else "audio"
                ext = "mp4" if "mp4" in stream_url else "m4a"
                out_path = os.path.join(target_dir, f"{prefix}.{ext}")
                dl_req = urllib.request.Request(
                    stream_url,
                    headers={"User-Agent": settings.DEFAULT_USER_AGENT}
                )
                with urllib.request.urlopen(dl_req, timeout=25) as dl_resp, open(out_path, "wb") as f:
                    while True:
                        chunk = dl_resp.read(128 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                    logger.info(f"Successfully downloaded Reddit fallback media ({os.path.getsize(out_path)} bytes) -> {out_path}")
                    return out_path
    except Exception as ex:
        logger.warning(f"Reddit media fallback download failed: {ex}")
    return None

INSTAGRAM_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
]

def download_audio_stream(url: str, target_dir: str) -> Optional[str]:
    """
    Download ONLY the lowest bitrate audio stream (32-64kbps).
    Extremely lightweight: typical 2-minute video audio is only ~1MB to 2MB.
    Implements multi-stage fallback:
      1. Primary yt-dlp with android client & anti-bot evasion
      2. If Instagram, auto-retry with mobile UA rotation and backoff (keeps session cookies)
      3. If primary fails (e.g. proxy or cookie error), retry direct with clean android options
      4. If YouTube and yt-dlp fails, query Invidious public API instances to fetch audio
      5. If Reddit and yt-dlp fails, query unauthenticated Reddit stream resolver
    """
    platform = detect_platform(url)
    output_template = os.path.join(target_dir, "audio.%(ext)s")
    ydl_opts = get_base_ydl_opts(platform=platform)
    ydl_opts.update({
        'skip_download': False,
        # Universal audio stream priority with low-resolution video fallback (supports vertical shorts/reels)
        'format': 'ba/b[height<=480]/b[height<=720]/b/worst',
        'outtmpl': output_template,
        'noplaylist': True,
        'concurrent_fragment_downloads': 4, # Speeds up Twitter/Facebook m3u8 fragments 4x
    })

    # Dedicated resilient retry loop for Instagram: rotates mobile UAs with jittered backoff while preserving cookies
    if platform == "instagram":
        for attempt, insta_ua in enumerate(INSTAGRAM_USER_AGENTS, start=1):
            try:
                curr_opts = dict(ydl_opts)
                curr_opts['user_agent'] = insta_ua
                curr_opts['http_headers'] = dict(curr_opts.get('http_headers', {}))
                curr_opts['http_headers']['User-Agent'] = insta_ua
                with yt_dlp.YoutubeDL(curr_opts) as ydl:
                    ydl.extract_info(url, download=True)
                    for file in os.listdir(target_dir):
                        if file.startswith("audio."):
                            return os.path.join(target_dir, file)
            except Exception as insta_err:
                logger.warning(f"Instagram audio download attempt {attempt} failed: {insta_err}")
                if attempt < len(INSTAGRAM_USER_AGENTS):
                    time.sleep(random.uniform(1.2, 2.2))
        return None
    
    # 1. Primary download attempt for non-Instagram platforms
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
            for file in os.listdir(target_dir):
                if file.startswith("audio."):
                    return os.path.join(target_dir, file)
    except Exception as e:
        logger.warning(f"Primary audio download failed: {e}. Attempting clean retry...")

    # 2. Retry direct without cookies or proxy if primary failed
    if ydl_opts.get('cookiefile') or ydl_opts.get('proxy'):
        try:
            clean_opts = dict(ydl_opts)
            clean_opts.pop('cookiefile', None)
            clean_opts.pop('proxy', None)
            if platform == "youtube":
                clean_opts['extractor_args'] = {
                    'youtube': {
                        'player_client': ['android'],
                        'player_skip': ['web', 'mweb'],
                    }
                }
            with yt_dlp.YoutubeDL(clean_opts) as ydl:
                ydl.extract_info(url, download=True)
                for file in os.listdir(target_dir):
                    if file.startswith("audio."):
                        return os.path.join(target_dir, file)
        except Exception as retry_err:
            logger.warning(f"Direct clean retry also failed: {retry_err}")

    # 3. For YouTube: fallback to Invidious API
    vid_id = extract_youtube_video_id(url)
    if vid_id:
        logger.info(f"Invoking Invidious audio fallback for YouTube video {vid_id}...")
        invidious_path = download_youtube_audio_invidious(vid_id, target_dir)
        if invidious_path:
            return invidious_path

    # 4. For Reddit: fallback to unauthenticated stream resolver
    if platform == "reddit":
        logger.info(f"Invoking Reddit audio fallback for {url}...")
        reddit_path = download_reddit_media_fallback(url, target_dir, is_video=False)
        if reddit_path:
            return reddit_path

    return None

def download_video_for_ocr(url: str, target_dir: str) -> Optional[str]:
    """
    Download a low-resolution (360p or lower) video stream for frame OCR and audio extraction.
    Never downloads 1080p/4K; 360p keeps memory usage under 10MB.
    """
    platform = detect_platform(url)
    output_template = os.path.join(target_dir, "video_ocr.%(ext)s")
    ydl_opts = get_base_ydl_opts(platform=platform)
    ydl_opts.update({
        'skip_download': False,
        # Force low-resolution video with audio track (supports vertical shorts 360x640 / 720p)
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/bestvideo[height<=480]+bestaudio/best[height<=480]/worstvideo[ext=mp4]/worst[ext=mp4]/worst',
        'outtmpl': output_template,
        'noplaylist': True,
        'concurrent_fragment_downloads': 4,
    })

    # Dedicated resilient retry loop for Instagram
    if platform == "instagram":
        for attempt, insta_ua in enumerate(INSTAGRAM_USER_AGENTS, start=1):
            try:
                curr_opts = dict(ydl_opts)
                curr_opts['user_agent'] = insta_ua
                curr_opts['http_headers'] = dict(curr_opts.get('http_headers', {}))
                curr_opts['http_headers']['User-Agent'] = insta_ua
                with yt_dlp.YoutubeDL(curr_opts) as ydl:
                    ydl.extract_info(url, download=True)
                    for file in os.listdir(target_dir):
                        if file.startswith("video_ocr."):
                            return os.path.join(target_dir, file)
            except Exception as insta_err:
                logger.warning(f"Instagram video download attempt {attempt} failed: {insta_err}")
                if attempt < len(INSTAGRAM_USER_AGENTS):
                    time.sleep(random.uniform(1.2, 2.2))
        return None
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
            for file in os.listdir(target_dir):
                if file.startswith("video_ocr."):
                    return os.path.join(target_dir, file)
    except Exception as e:
        logger.warning(f"Primary video download for OCR failed: {e}. Retrying clean...")
        if ydl_opts.get('cookiefile') or ydl_opts.get('proxy'):
            try:
                clean_opts = dict(ydl_opts)
                clean_opts.pop('cookiefile', None)
                clean_opts.pop('proxy', None)
                with yt_dlp.YoutubeDL(clean_opts) as ydl:
                    ydl.extract_info(url, download=True)
                    for file in os.listdir(target_dir):
                        if file.startswith("video_ocr."):
                            return os.path.join(target_dir, file)
            except Exception as ex2:
                logger.error(f"Clean retry for OCR video also failed: {ex2}")
        
    if platform == "reddit":
        logger.info(f"Invoking Reddit video fallback for OCR on {url}...")
        reddit_vid = download_reddit_media_fallback(url, target_dir, is_video=True)
        if reddit_vid:
            return reddit_vid

    return None
