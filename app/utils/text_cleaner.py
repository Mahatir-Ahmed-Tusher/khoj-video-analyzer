import re
from difflib import SequenceMatcher
from typing import List, Dict, Any, Optional

def clean_ocr_text(raw_text: str) -> str:
    """
    Clean raw OCR string by removing excessive whitespace, standalone noise characters,
    and trailing punctuation while preserving Bengali characters and digits.
    """
    if not raw_text:
        return ""
        
    if "*[No text detected]*" in raw_text or "No text detected" in raw_text:
        return ""
    
    # Replace multiple spaces, tabs, or newlines with a single space
    cleaned = re.sub(r'[\r\n\t]+', ' ', raw_text)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
    
    # Filter out common single-character OCR noise artifacts
    if len(cleaned) < 2 and not re.search(r'[\u0980-\u09FFa-zA-Z0-9]', cleaned):
        return ""
        
    return cleaned

def calculate_similarity(a: str, b: str) -> float:
    """
    Calculate normalized string similarity ratio between 0.0 and 1.0.
    """
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def deduplicate_on_screen_frames(frames: List[Dict[str, Any]], similarity_threshold: float = 0.78) -> List[Dict[str, Any]]:
    """
    Deduplicate consecutive video frames containing identical or near-identical text
    (e.g., persistent headline banners or tickers across multiple seconds).
    Groups them into timestamp intervals: {'start': '00:02', 'end': '00:08', 'text': '...'}
    """
    if not frames:
        return []
    
    deduplicated = []
    current_entry = None
    
    for frame in frames:
        text = frame.get("text", "").strip()
        timestamp = frame.get("timestamp", "00:00")
        
        if not text:
            continue
            
        if current_entry is None:
            current_entry = {
                "start": timestamp,
                "end": timestamp,
                "text": text
            }
        else:
            similarity = calculate_similarity(current_entry["text"], text)
            if similarity >= similarity_threshold:
                # Text is continuing on screen; update the end timestamp
                current_entry["end"] = timestamp
                # If current text is slightly longer/better OCR result, keep the longer version
                if len(text) > len(current_entry["text"]):
                    current_entry["text"] = text
            else:
                # New distinct text detected on screen
                deduplicated.append(current_entry)
                current_entry = {
                    "start": timestamp,
                    "end": timestamp,
                    "text": text
                }
                
    if current_entry is not None:
        deduplicated.append(current_entry)
        
    return deduplicated

def build_factcheck_summary(
    title: str,
    platform: str,
    author: str,
    spoken_transcript: str,
    on_screen_texts: List[Dict[str, Any]],
    source_caption: Optional[str] = None
) -> str:
    """
    Synthesize video title, source caption, spoken audio transcript,
    and visual on-screen texts into a unified context summary formatted
    for the Khoj fact-checking engine.
    """
    sections = []
    
    if title:
        sections.append(f"Video Title: {title}")
    if author:
        sections.append(f"Source/Author: {author} ({platform.capitalize()})")
    if source_caption and source_caption.strip():
        sections.append(f"\nInformation about that video collected from the source:\n{source_caption.strip()}")
        
    if spoken_transcript and spoken_transcript.strip():
        sections.append(f"\n[Spoken Transcript (Audio)]:\n{spoken_transcript.strip()}")
        
    if on_screen_texts:
        screen_lines = []
        for entry in on_screen_texts:
            time_label = entry['start'] if entry['start'] == entry['end'] else f"{entry['start']}-{entry['end']}"
            screen_lines.append(f"- [{time_label}] {entry['text']}")
        sections.append(f"\n[On-Screen Visual Text (Video OCR)]:\n" + "\n".join(screen_lines))
        
    return "\n".join(sections)

def format_paragraphs(segments: List[Dict[str, Any]]) -> str:
    """
    Format segments into natural, readable paragraphs inspired by video-to-transcript.
    Signals:
      (a) pause gap >= 1.5s between segments
      (b) discourse topic marker starting a segment once paragraph >= 180 chars
      (c) paragraph length > 750 chars ending at sentence boundary (. ? ! ।)
    """
    if not segments:
        return ""
    
    PAUSE_BREAK_S = 1.5
    MARKER_MIN_PARA_CHARS = 180
    MAX_PARA_CHARS = 750
    TOPIC_MARKERS = (
        "so ", "so,", "and then", "now ", "now,", "okay", "anyway",
        "but ", "next ", "alright", "তবে", "সুতরাং", "এদিকে", "এরপর",
        "কিন্তু", "তাহলে", "আসলে", "তো ", "এখন "
    )
    
    paragraphs, current = [], []
    cur_len = 0
    prev_end = None
    
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        breaks = False
        if current:
            gap = float(seg.get("start_seconds", 0) or 0) - (prev_end or 0.0)
            lowered = text.lower()
            prev_text = current[-1]
            if gap >= PAUSE_BREAK_S:
                breaks = True
            elif cur_len >= MARKER_MIN_PARA_CHARS and any(lowered.startswith(m) for m in TOPIC_MARKERS):
                breaks = True
            elif cur_len > MAX_PARA_CHARS and prev_text.rstrip()[-1:] in ".?!।":
                breaks = True
        if breaks:
            paragraphs.append(" ".join(current))
            current, cur_len = [], 0
        current.append(text)
        cur_len += len(text) + 1
        prev_end = float(seg.get("end_seconds", 0) or 0)
        
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs).strip()

