/**
 * Khoj Video Analyzer - Enterprise Application Controller
 * Handles video analysis pipeline requests, live progress monitoring,
 * and comprehensive diagnostic error handling.
 */

// Production Cloud Run API endpoint
const DEFAULT_BACKEND_URL = "https://khoj-video-analyzer-988501677320.us-central1.run.app";

let currentBackendUrl = localStorage.getItem("KHOJ_BACKEND_URL") || DEFAULT_BACKEND_URL;
let activeMode = "url"; // 'url' or 'upload'
let currentPipelineMode = "analyze"; // 'analyze', 'transcribe', or 'ocr'
let selectedFile = null;
let currentResultData = null;
let timerInterval = null;
let timerStartTime = 0;

// Initialize on DOM ready
document.addEventListener("DOMContentLoaded", () => {
  const backendInput = document.getElementById("backendUrlInput");
  if (backendInput) backendInput.value = currentBackendUrl;
  checkBackendHealth();
  setupPasteButton();
  setupSampleChips();
  setupDropzone();
});

// Pipeline Mode Switcher (Full Analysis vs Transcribe Only vs Visual OCR Only)
function switchPipelineMode(mode) {
  currentPipelineMode = mode;
  ["analyze", "transcribe", "ocr"].forEach(m => {
    const card = document.getElementById("modeCard" + m.charAt(0).toUpperCase() + m.slice(1));
    const radio = card ? card.querySelector("input[type='radio']") : null;
    if (card) {
      if (m === mode) {
        card.classList.add("active");
        if (radio) radio.checked = true;
      } else {
        card.classList.remove("active");
        if (radio) radio.checked = false;
      }
    }
  });

  const langGroup = document.getElementById("optLanguageGroup");
  const ocrGroup = document.getElementById("optOcrFramesGroup");

  if (mode === "transcribe") {
    if (langGroup) langGroup.style.display = "flex";
    if (ocrGroup) ocrGroup.style.display = "none";
  } else if (mode === "ocr") {
    if (langGroup) langGroup.style.display = "none";
    if (ocrGroup) ocrGroup.style.display = "flex";
  } else {
    // analyze (default)
    if (langGroup) langGroup.style.display = "flex";
    if (ocrGroup) ocrGroup.style.display = "none";
  }
}
window.switchPipelineMode = switchPipelineMode;

// Mode switcher: Video URL vs Direct File Upload
function switchTab(mode) {
  activeMode = mode;
  const tabUrlBtn = document.getElementById("tabUrlBtn");
  const tabUploadBtn = document.getElementById("tabUploadBtn");
  const urlContainer = document.getElementById("urlInputContainer");
  const uploadContainer = document.getElementById("fileUploadContainer");

  if (mode === "url") {
    tabUrlBtn.classList.add("active");
    tabUploadBtn.classList.remove("active");
    urlContainer.style.display = "block";
    uploadContainer.style.display = "none";
  } else {
    tabUploadBtn.classList.add("active");
    tabUrlBtn.classList.remove("active");
    urlContainer.style.display = "none";
    uploadContainer.style.display = "block";
  }
}

// Check backend service health and response latency
async function checkBackendHealth() {
  const dot = document.getElementById("statusDot");
  const text = document.getElementById("statusText");
  if (!dot || !text) return;

  dot.className = "status-dot";
  text.textContent = "Connecting...";

  const t0 = performance.now();
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8000);

    const res = await fetch(`${currentBackendUrl}/health`, {
      method: "GET",
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (res.ok) {
      const ping = Math.round(performance.now() - t0);
      dot.className = "status-dot online";
      text.textContent = `Online (${ping}ms)`;
    } else {
      dot.className = "status-dot offline";
      text.textContent = `Error (${res.status})`;
    }
  } catch (err) {
    dot.className = "status-dot offline";
    text.textContent = "Offline / Unreachable";
  }
}

// Load pre-configured sample URL for quick testing
function loadSample(url) {
  switchTab("url");
  const input = document.getElementById("videoUrlInput");
  if (input) {
    input.value = url;
    input.focus();
  }
}

// Clipboard Paste Handler
function setupPasteButton() {
  const pasteBtn = document.getElementById("pasteBtn");
  const urlInput = document.getElementById("videoUrlInput");
  if (!pasteBtn || !urlInput) return;

  pasteBtn.addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) urlInput.value = text.trim();
    } catch (e) {
      alert("Please allow clipboard permission or paste using Ctrl+V / Cmd+V.");
    }
  });
}

// Quick Sample Loader (Global)
function loadSample(url) {
  const urlInput = document.getElementById("videoUrlInput");
  if (!urlInput) return;
  urlInput.value = url;
  urlInput.dispatchEvent(new Event("input", { bubbles: true }));
  urlInput.focus();
  // Visual pulse feedback on input field
  urlInput.style.borderColor = "#2781DB";
  urlInput.style.boxShadow = "0 0 0 4px rgba(39, 129, 219, 0.22)";
  setTimeout(() => {
    urlInput.style.borderColor = "";
    urlInput.style.boxShadow = "";
  }, 450);
}
window.loadSample = loadSample;

function setupSampleChips() {
  document.querySelectorAll(".sample-chip[data-sample-url]").forEach((chip) => {
    chip.addEventListener("click", (e) => {
      e.preventDefault();
      const url = chip.getAttribute("data-sample-url");
      if (url) loadSample(url);
    });
  });
}

// Drag & Drop File Upload Handler
function setupDropzone() {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  if (!dropzone || !fileInput) return;

  ["dragenter", "dragover"].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    }, false);
  });

  ["dragleave", "drop"].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    }, false);
  });

  dropzone.addEventListener("drop", (e) => {
    const files = e.dataTransfer.files;
    if (files.length > 0) handleFileSelected(files[0]);
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) handleFileSelected(e.target.files[0]);
  });
}

function handleFileSelected(file) {
  selectedFile = file;
  const fileInfo = document.getElementById("selectedFileInfo");
  if (!fileInfo) return;

  const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
  fileInfo.innerHTML = `Selected: <strong>${file.name}</strong> (${sizeMb} MB)`;
  fileInfo.style.display = "block";
}

// Main Pipeline Controller
async function startAnalysis() {
  const analyzeBtn = document.getElementById("analyzeBtn");
  const loadingPanel = document.getElementById("loadingPanel");
  const errorPanel = document.getElementById("errorPanel");
  const resultsSection = document.getElementById("resultsSection");

  const language = document.getElementById("selectLanguage") ? document.getElementById("selectLanguage").value || null : null;
  const ocrFramesSelect = document.getElementById("selectOcrFrames");
  const maxFrames = ocrFramesSelect ? parseInt(ocrFramesSelect.value || "16") : 16;

  let url = "";
  if (activeMode === "url") {
    url = document.getElementById("videoUrlInput").value.trim();
    if (!url) {
      alert("Please provide a valid social media video link.");
      return;
    }
  } else {
    if (!selectedFile) {
      alert("Please select a video or audio file to analyze.");
      return;
    }
  }

  // Set running state
  analyzeBtn.disabled = true;
  analyzeBtn.innerHTML = `
    <svg class="svg-inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"/>
      <path d="M12 6v6l4 2"/>
    </svg>
    Processing Pipeline...
  `;
  loadingPanel.style.display = "block";
  if (errorPanel) errorPanel.style.display = "none";
  resultsSection.style.display = "none";

  populateActiveTasks(currentPipelineMode, language, maxFrames);
  startElapsedTimer();

  try {
    let response;
    let endpoint = "";

    if (activeMode === "url") {
      if (currentPipelineMode === "transcribe") {
        endpoint = `${currentBackendUrl}/transcribe`;
        response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: url, language: language })
        });
      } else if (currentPipelineMode === "ocr") {
        endpoint = `${currentBackendUrl}/ocr`;
        response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: url, max_frames: maxFrames })
        });
      } else {
        endpoint = `${currentBackendUrl}/analyze`;
        response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: url,
            extract_audio: true,
            extract_ocr: true,
            language: language
          })
        });
      }
    } else {
      const formData = new FormData();
      formData.append("file", selectedFile);

      if (currentPipelineMode === "transcribe") {
        endpoint = `${currentBackendUrl}/transcribe/upload`;
        if (language) formData.append("language", language);
        response = await fetch(endpoint, {
          method: "POST",
          body: formData
        });
      } else if (currentPipelineMode === "ocr") {
        endpoint = `${currentBackendUrl}/ocr/upload`;
        formData.append("max_frames", maxFrames.toString());
        response = await fetch(endpoint, {
          method: "POST",
          body: formData
        });
      } else {
        endpoint = `${currentBackendUrl}/analyze/upload`;
        if (language) formData.append("language", language);
        response = await fetch(endpoint, {
          method: "POST",
          body: formData
        });
      }
    }

    stopElapsedTimer();

    if (!response.ok) {
      const rawText = await response.text();
      handleAnalysisError(response.status, response.statusText, rawText, endpoint);
      return;
    }

    const data = await response.json();
    currentResultData = data;
    renderResults(data);

  } catch (error) {
    stopElapsedTimer();
    handleAnalysisError(0, "Network / Client Error", error.message || error.toString());
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.innerHTML = `
      <svg class="svg-inline" viewBox="0 0 24 24" fill="currentColor">
        <polygon points="5 3 19 12 5 21 5 3"/>
      </svg>
      Start Analysis
    `;
    loadingPanel.style.display = "none";
  }
}

// Elapsed timer and genuine dynamic status messaging
function startElapsedTimer() {
  timerStartTime = Date.now();
  const timerElem = document.getElementById("elapsedTimer");
  if (timerElem) timerElem.textContent = "0.0s elapsed";

  updateLiveStatus(0);

  if (timerInterval) clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    const secFloat = (Date.now() - timerStartTime) / 1000;
    if (timerElem) timerElem.textContent = `${secFloat.toFixed(1)}s elapsed`;
    updateLiveStatus(Math.floor(secFloat));
  }, 200);
}

function stopElapsedTimer() {
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
}

// Populate scope of tasks actively executing on the backend
function populateActiveTasks(mode, language, maxFrames) {
  const container = document.getElementById("activeTasksGrid");
  if (!container) return;

  const isAudioActive = mode === "analyze" || mode === "transcribe";
  const isOcrActive = mode === "analyze" || mode === "ocr";
  const langLabel = language ? (language === "bn" ? "Bengali" : language.toUpperCase()) : "Auto-Detect";

  container.innerHTML = `
    <div class="active-task-item ${isAudioActive ? 'active' : 'disabled'}">
      <svg class="svg-inline-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
        <line x1="12" x2="12" y1="19" y2="22"/>
      </svg>
      <span>Speech-to-Text</span>
      <span class="task-tag ${isAudioActive ? 'active' : 'disabled'}">${isAudioActive ? (mode === 'transcribe' ? 'Speech Only' : 'Active') : 'Skipped'}</span>
    </div>
    <div class="active-task-item ${isOcrActive ? 'active' : 'disabled'}">
      <svg class="svg-inline-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
      <span>Visual OCR</span>
      <span class="task-tag ${isOcrActive ? 'active' : 'disabled'}">${isOcrActive ? `${maxFrames || 16} Frames` : 'Skipped'}</span>
    </div>
    <div class="active-task-item active">
      <svg class="svg-inline-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"/>
        <polyline points="12 6 12 12 16 14"/>
      </svg>
      <span>Endpoint</span>
      <span class="task-tag active">POST /${mode}</span>
    </div>
  `;
}

// Progress status updater based on verified execution duration and active mode
function updateLiveStatus(sec) {
  const statusElem = document.getElementById("liveStatusText");
  if (!statusElem) return;

  if (currentPipelineMode === "transcribe") {
    if (sec < 5) {
      statusElem.textContent = "Downloading audio stream only (zero video download for speed)...";
    } else if (sec < 18) {
      statusElem.textContent = "Transcribing speech with Faster-Whisper int8 (Bangla/Multilingual)...";
    } else if (sec < 35) {
      statusElem.textContent = "Polishing Bangla typography and diacritics with Gemini refiner...";
    } else {
      statusElem.textContent = `Transcribing extended audio stream (${sec}s)...`;
    }
  } else if (currentPipelineMode === "ocr") {
    if (sec < 6) {
      statusElem.textContent = "Fetching 360p lightweight video stream (zero audio download)...";
    } else if (sec < 18) {
      statusElem.textContent = "Sampling keyframes and dispatching neural OCR requests...";
    } else if (sec < 35) {
      statusElem.textContent = "Deduplicating consecutive on-screen text overlays & banners...";
    } else {
      statusElem.textContent = `Extracting on-screen text overlays (${sec}s)...`;
    }
  } else {
    // Composite /analyze
    if (sec < 6) {
      statusElem.textContent = "Connecting to backend and fetching media streams...";
    } else if (sec < 20) {
      statusElem.textContent = "Decoding audio & video streams and running Faster-Whisper...";
    } else if (sec < 45) {
      statusElem.textContent = "Performing neural speech-to-text and sampling video frames...";
    } else if (sec < 90) {
      statusElem.textContent = "Synthesizing combined fact-check analysis on Cloud Run...";
    } else {
      statusElem.textContent = `Extended media analysis running (${sec}s). Awaiting final response...`;
    }
  }
}

// Error diagnostic handler with actionable explanations
function handleAnalysisError(status, statusText, rawBody, usedEndpoint) {
  const errorPanel = document.getElementById("errorPanel");
  const errorTitle = document.getElementById("errorTitle");
  const errorCodeBadge = document.getElementById("errorCodeBadge");
  const errorReasonText = document.getElementById("errorReasonText");
  const errorTipsList = document.getElementById("errorTipsList");
  const errorRawBox = document.getElementById("errorRawBox");

  if (!errorPanel) {
    alert(`Error (${status}): ${rawBody}`);
    return;
  }

  let parsedDetail = "";
  try {
    const json = JSON.parse(rawBody);
    parsedDetail = json.detail || json.message || "";
  } catch (e) {
    parsedDetail = rawBody;
  }

  let title = "Analysis Failed";
  let badge = `HTTP ${status || "CLIENT"}: ${statusText || "ERROR"}`;
  let reason = "";
  let tips = [];

  const lowerBody = (rawBody || "").toLowerCase();

  if (status === 504 || lowerBody.includes("upstream request timeout") || lowerBody.includes("timeout")) {
    title = "Gateway / Request Timeout";
    badge = "HTTP 504: UPSTREAM TIMEOUT";
    reason = "The media is extensive, or backend processing exceeded Cloud Run's allotted request timeframe. The load balancer severed the connection before completion.";
    tips = [
      "Switch to 'Transcribe Only' mode—speech recognition alone runs ~3x to 5x faster because it bypasses video downloads and frame OCR.",
      "For videos exceeding 10 minutes, consider analyzing specific excerpt clips.",
      "Switch to a local development instance (127.0.0.1:8080) for unrestricted processing durations."
    ];
  } else if (status === 503 || lowerBody.includes("service unavailable")) {
    title = "Service Temporarily Unavailable";
    badge = "HTTP 503: COLD START / BUSY";
    reason = "The Cloud Run container is currently starting up from cold sleep or scaling compute instances.";
    tips = [
      "Wait 10 to 15 seconds, then click 'Start Analysis' again.",
      "Once an instance is warm, subsequent requests execute with low latency."
    ];
  } else if (status === 400 || status === 422) {
    title = "Invalid Media Request";
    badge = `HTTP ${status}: BAD REQUEST`;
    reason = parsedDetail || "The provided URL is unsupported, private, or protected behind platform authentication barriers.";
    tips = [
      "Confirm the video URL plays publicly without an active user session in incognito mode.",
      "Ensure standard permalinks from YouTube, Facebook, Twitter/X, or TikTok are used.",
      "Use the 'Direct Upload' tab to analyze downloaded media files directly."
    ];
  } else if (status === 0 || lowerBody.includes("failed to fetch")) {
    title = "Backend Connection Failed";
    badge = "NETWORK / CORS ERROR";
    reason = "The browser cannot establish a connection to the Cloud Run service. Network connectivity or CORS policy may be blocking the request.";
    tips = [
      "Check your network connection.",
      "Ensure the service status indicator in the top right shows 'Online'.",
      "Click the status pill to verify the backend API endpoint URL."
    ];
  } else {
    title = `Server Processing Error (${status})`;
    reason = parsedDetail || "An unexpected error occurred during backend media stream handling.";
    tips = [
      "Verify the media URL and attempt analysis again.",
      "Try 'Transcribe Only' or 'Visual OCR Only' to isolate the failing pipeline step."
    ];
  }

  errorTitle.textContent = title;
  errorCodeBadge.textContent = badge;
  errorReasonText.textContent = reason;

  errorTipsList.innerHTML = tips.map(tip => `<li>${tip}</li>`).join("");
  const ep = usedEndpoint || `${currentBackendUrl}/${currentPipelineMode}`;
  errorRawBox.textContent = `Endpoint: ${ep}\nStatus: ${status} (${statusText})\n\nRaw Response:\n${rawBody || parsedDetail || "No response body"}`;

  errorPanel.style.display = "block";
  errorPanel.scrollIntoView({ behavior: "smooth", block: "center" });
}

function dismissError() {
  const errorPanel = document.getElementById("errorPanel");
  if (errorPanel) errorPanel.style.display = "none";
}

function toggleTechnicalDetails() {
  const box = document.getElementById("errorRawBox");
  const arrow = document.getElementById("techDetailsArrow");
  if (!box) return;
  const isHidden = box.style.display === "none";
  box.style.display = isHidden ? "block" : "none";
  if (arrow) arrow.textContent = isHidden ? "▲" : "▼";
}

function copyErrorDetails() {
  const box = document.getElementById("errorRawBox");
  if (!box) return;
  navigator.clipboard.writeText(box.textContent).then(() => {
    alert("Technical diagnostics copied to clipboard.");
  }).catch(() => {
    alert("Failed to copy. Please manually select and copy the text box.");
  });
}

// Render Results to UI
function renderResults(data) {
  const resultsSection = document.getElementById("resultsSection");
  resultsSection.style.display = "block";

  // Metadata Display
  document.getElementById("resTitle").textContent = data.title || "Untitled Video";
  document.getElementById("resPlatform").textContent = (data.platform || "Video").toUpperCase();
  
  const durSec = data.duration_seconds || 0;
  const mins = Math.floor(durSec / 60);
  const secs = Math.floor(durSec % 60);
  document.getElementById("resDuration").textContent = durSec > 0 ? `${mins}m ${secs}s (${durSec}s)` : "Dynamic / Live";

  document.getElementById("resProcessingTime").textContent = `${(data.processing_time_seconds || 0).toFixed(2)}s`;

  // Transcripts
  const isTranscribeMode = currentPipelineMode === "transcribe";
  const isOcrMode = currentPipelineMode === "ocr";

  let fallbackNotice = "(No speech detected in audio stream)";
  if (isOcrMode) {
    fallbackNotice = "(Speech transcription was not requested in Visual OCR Only mode)";
  } else if (data.message && data.message.includes("Could not download")) {
    fallbackNotice = `Notice: ${data.message}`;
  }

  const polishedText = isOcrMode ? fallbackNotice : (data.spoken_transcript || fallbackNotice);
  const rawText = isOcrMode ? fallbackNotice : (data.raw_spoken_transcript || data.spoken_transcript || fallbackNotice);
  
  document.getElementById("resPolishedText").textContent = polishedText;
  document.getElementById("resRawText").textContent = rawText;

  // Refiner Badge
  const badge = document.getElementById("nemotronBadge");
  if (isOcrMode) {
    badge.textContent = `OCR Mode (${data.frames_analyzed || 0} Frames)`;
    badge.style.display = "inline-block";
  } else if (data.refiner_model) {
    badge.textContent = data.refiner_model.replace("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite");
    badge.style.display = "inline-block";
  } else {
    badge.textContent = "Unrefined Whisper";
  }

  // Segments
  const segmentsList = document.getElementById("resSegmentsList");
  segmentsList.innerHTML = "";
  const segments = data.spoken_segments || [];

  if (!isOcrMode && segments.length > 0) {
    document.getElementById("segmentsCard").style.display = "block";
    segments.forEach(seg => {
      const row = document.createElement("div");
      row.className = "segment-item";
      
      const sStart = formatTime(seg.start);
      const sEnd = formatTime(seg.end);
      
      row.innerHTML = `
        <span class="segment-time">${sStart} - ${sEnd}</span>
        <span class="segment-text">${escapeHtml(seg.text)}</span>
      `;
      segmentsList.appendChild(row);
    });
  } else {
    document.getElementById("segmentsCard").style.display = "none";
  }

  // OCR
  const ocrElem = document.getElementById("resOcrText");
  const ocrList = data.on_screen_text || [];
  if (isTranscribeMode) {
    document.getElementById("ocrCard").style.display = "block";
    ocrElem.innerHTML = `<span style="color: var(--text-muted); font-style: italic;">Visual OCR was not requested in Transcribe Only mode (zero video download).</span>`;
  } else if (Array.isArray(ocrList) && ocrList.length > 0) {
    document.getElementById("ocrCard").style.display = "block";
    ocrElem.innerHTML = ocrList.map(item => {
      if (typeof item === "string") return `<div>• ${escapeHtml(item)}</div>`;
      return `<div style="margin-bottom: 0.35rem;"><span class="segment-time">${item.timestamp || '00:00'}</span> <strong>${escapeHtml(item.text || '')}</strong></div>`;
    }).join("");
  } else {
    document.getElementById("ocrCard").style.display = "block";
    ocrElem.textContent = "No visual on-screen text or banners identified in sampled frames.";
  }

  // Smooth scroll into results
  resultsSection.scrollIntoView({ behavior: "smooth" });
}

// Copy single text element
function copyText(elementId) {
  const elem = document.getElementById(elementId);
  if (!elem) return;
  const text = elem.textContent;
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    alert("Transcript copied to clipboard.");
  });
}

// Copy full Fact-Check Report in Markdown
function copyFullReport() {
  if (!currentResultData) return;
  const d = currentResultData;

  const report = `# Fact-Check Analysis Report: ${d.title || 'Untitled Video'}
- **Platform**: ${(d.platform || 'Unknown').toUpperCase()}
- **Duration**: ${d.duration_seconds || 0} seconds
- **Language Detected**: ${d.detected_language || 'unknown'}
- **Analysis Runtime**: ${(d.processing_time_seconds || 0).toFixed(2)}s
- **Speech Model**: Faster-Whisper
- **Refiner Model**: ${d.refiner_model || 'None'}

## Spoken Speech Transcript
${d.spoken_transcript || 'None'}

## Raw Speech Model Output
${d.raw_spoken_transcript || 'None'}

## Visual On-Screen Text (OCR)
${JSON.stringify(d.on_screen_text || [], null, 2)}
`;

  navigator.clipboard.writeText(report).then(() => {
    alert("Full Fact-Check report copied to clipboard (Markdown format).");
  });
}

// Download JSON response
function downloadJsonResult() {
  if (!currentResultData) return;
  const jsonStr = JSON.stringify(currentResultData, null, 2);
  const blob = new Blob([jsonStr], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `khoj_analysis_${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

// Settings Modal Controls
function toggleSettingsModal() {
  const modal = document.getElementById("settingsModal");
  if (!modal) return;
  modal.style.display = (modal.style.display === "flex") ? "none" : "flex";
}

function closeSettingsIfOutside(e) {
  if (e.target.id === "settingsModal") {
    toggleSettingsModal();
  }
}

function setPresetBackend(url) {
  const input = document.getElementById("backendUrlInput");
  if (input) input.value = url;
}

function saveBackendUrl() {
  const val = document.getElementById("backendUrlInput").value.trim().replace(/\/$/, "");
  if (!val) return;
  currentBackendUrl = val;
  localStorage.setItem("KHOJ_BACKEND_URL", val);
  toggleSettingsModal();
  checkBackendHealth();
  alert(`Backend endpoint updated to:\n${currentBackendUrl}`);
}

// Helpers
function formatTime(sec) {
  const s = parseFloat(sec) || 0;
  const m = Math.floor(s / 60);
  const remSec = Math.floor(s % 60);
  return `${m.toString().padStart(2, '0')}:${remSec.toString().padStart(2, '0')}`;
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
