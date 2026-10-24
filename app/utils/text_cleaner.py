import re
from difflib import SequenceMatcher
from typing import List, Dict, Any

def clean_ocr_text(raw_text: str) -> str:
    """
    Clean raw OCR string by removing excessive whitespace, standalone noise characters,
    and trailing punctuation while preserving Bengali characters and digits.
    """
    if not raw_text:
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
    on_screen_texts: List[Dict[str, Any]]
) -> str:
    """
    Synthesize spoken audio transcript and visual on-screen texts into
    a unified context summary formatted for the Khoj fact-checking engine.
    """
    sections = []
    
    if title:
        sections.append(f"Video Title: {title}")
    if author:
        sections.append(f"Source/Author: {author} ({platform.capitalize()})")
        
    if spoken_transcript and spoken_transcript.strip():
        sections.append(f"\n[Spoken Transcript (Audio)]:\n{spoken_transcript.strip()}")
        
    if on_screen_texts:
        screen_lines = []
        for entry in on_screen_texts:
            time_label = entry['start'] if entry['start'] == entry['end'] else f"{entry['start']}-{entry['end']}"
            screen_lines.append(f"- [{time_label}] {entry['text']}")
        sections.append(f"\n[On-Screen Visual Text (Video OCR)]:\n" + "\n".join(screen_lines))
        
    return "\n".join(sections)
