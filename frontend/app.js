/**
 * Khoj Video Analyzer - Frontend Application Controller
 * Connects to live Google Cloud Run backend or local development server.
 */

// Default Cloud Run Production Backend URL
const DEFAULT_BACKEND_URL = "https://khoj-video-analyzer-ju6xye7j6q-uc.a.run.app";

let currentBackendUrl = localStorage.getItem("KHOJ_BACKEND_URL") || DEFAULT_BACKEND_URL;
let activeMode = "url"; // 'url' or 'upload'
let selectedFile = null;
let currentResultData = null;
let timerInterval = null;
let timerStartTime = 0;

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("backendUrlInput").value = currentBackendUrl;
  checkBackendHealth();
  setupPasteButton();
  setupDropzone();
});

// Switch between URL and File upload modes
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

// Health check ping to backend
async function checkBackendHealth() {
  const dot = document.getElementById("statusDot");
  const text = document.getElementById("statusText");

  dot.className = "status-dot";
  text.textContent = "Checking Cloud Run...";

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
      const data = await res.json();
      const ping = Math.round(performance.now() - t0);
      dot.className = "status-dot online";
      text.textContent = `Online (${ping}ms)`;
    } else {
      dot.className = "status-dot offline";
      text.textContent = `Server Error (${res.status})`;
    }
  } catch (err) {
    dot.className = "status-dot offline";
    text.textContent = "Offline / Unreachable";
  }
}

// Quick Sample Video Loader
function loadSample(url) {
  switchTab("url");
  const input = document.getElementById("videoUrlInput");
  input.value = url;
  input.focus();
}

// Clipboard Paste
function setupPasteButton() {
  const pasteBtn = document.getElementById("pasteBtn");
  const urlInput = document.getElementById("videoUrlInput");

  pasteBtn.addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        urlInput.value = text.trim();
      }
    } catch (e) {
      alert("Please allow clipboard access or paste with Ctrl+V.");
    }
  });
}

// Drag & Drop Setup
function setupDropzone() {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  const fileInfo = document.getElementById("selectedFileInfo");

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
    if (files.length > 0) {
      handleFileSelected(files[0]);
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      handleFileSelected(e.target.files[0]);
    }
  });
}

function handleFileSelected(file) {
  selectedFile = file;
  const fileInfo = document.getElementById("selectedFileInfo");
  const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
  fileInfo.innerHTML = `📄 <strong>${file.name}</strong> (${sizeMb} MB) selected`;
  fileInfo.style.display = "block";
}

// Main Analysis Starter
async function startAnalysis() {
  const analyzeBtn = document.getElementById("analyzeBtn");
  const loadingPanel = document.getElementById("loadingPanel");
  const resultsSection = document.getElementById("resultsSection");

  const extractAudio = document.getElementById("chkAudio").checked;
  const extractOcr = document.getElementById("chkOcr").checked;
  const language = document.getElementById("selectLanguage").value || null;

  if (activeMode === "url") {
    const url = document.getElementById("videoUrlInput").value.trim();
    if (!url) {
      alert("অনুগ্রহ করে একটি ইউটিউব, ফেসবুক বা ভিডিও লিঙ্ক পেস্ট করুন।");
      return;
    }
  } else {
    if (!selectedFile) {
      alert("অনুগ্রহ করে একটি ভিডিও বা অডিও ফাইল নির্বাচন করুন।");
      return;
    }
  }

  // UI State: Running
  analyzeBtn.disabled = true;
  analyzeBtn.innerHTML = `<span>⏳</span> বিশ্লেষণ চলছে...`;
  loadingPanel.style.display = "block";
  resultsSection.style.display = "none";

  resetPipelineSteps();
  startElapsedTimer();

  try {
    let response;
    if (activeMode === "url") {
      const url = document.getElementById("videoUrlInput").value.trim();
      simulateProgress();

      response = await fetch(`${currentBackendUrl}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: url,
          extract_audio: extractAudio,
          extract_ocr: extractOcr,
          language: language
        })
      });
    } else {
      // Direct File Upload
      const formData = new FormData();
      formData.append("file", selectedFile);
      if (language) formData.append("language", language);

      simulateProgress();
      response = await fetch(`${currentBackendUrl}/analyze/upload`, {
        method: "POST",
        body: formData
      });
    }

    stopElapsedTimer();

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || `Server responded with status ${response.status}`);
    }

    const data = await response.json();
    currentResultData = data;
    renderResults(data);

  } catch (error) {
    stopElapsedTimer();
    alert(`প্রসেসিং এরর: ${error.message}\n\nআপনার ইন্টারনেট কানেকশন বা ভিডিও লিঙ্ক চেক করুন।`);
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.innerHTML = `<span>🚀</span> বিশ্লেষণ শুরু করুন (Start Analysis)`;
    loadingPanel.style.display = "none";
  }
}

// Elapsed timer
function startElapsedTimer() {
  timerStartTime = Date.now();
  const timerElem = document.getElementById("elapsedTimer");
  timerElem.textContent = "0.0s elapsed";

  if (timerInterval) clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    const sec = ((Date.now() - timerStartTime) / 1000).toFixed(1);
    timerElem.textContent = `${sec}s elapsed`;
  }, 100);
}

function stopElapsedTimer() {
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
}

// Visual Stepper Simulator
function resetPipelineSteps() {
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(`step${i}`);
    el.className = "step-item";
  }
  document.getElementById("step1").classList.add("active");
}

function simulateProgress() {
  setTimeout(() => {
    document.getElementById("step1").className = "step-item completed";
    document.getElementById("step2").className = "step-item active";
  }, 3500);

  setTimeout(() => {
    document.getElementById("step2").className = "step-item completed";
    document.getElementById("step3").className = "step-item active";
  }, 12000);

  setTimeout(() => {
    document.getElementById("step3").className = "step-item completed";
    document.getElementById("step4").className = "step-item active";
  }, 22000);
}

// Render Results to UI
function renderResults(data) {
  const resultsSection = document.getElementById("resultsSection");
  resultsSection.style.display = "block";

  // Metadata
  document.getElementById("resTitle").textContent = data.title || "Untitled Video";
  document.getElementById("resPlatform").textContent = (data.platform || "Video").toUpperCase();
  
  const durSec = data.duration_seconds || 0;
  const mins = Math.floor(durSec / 60);
  const secs = Math.floor(durSec % 60);
  document.getElementById("resDuration").textContent = durSec > 0 ? `${mins}m ${secs}s (${durSec}s)` : "Live / Dynamic";

  document.getElementById("resProcessingTime").textContent = `⚡ ${(data.processing_time_seconds || 0).toFixed(2)}s`;

  // Transcripts
  const fallbackNotice = data.message && data.message.includes("Could not download") 
    ? `⚠️ ${data.message}`
    : "(কোনো বক্তব্য শনাক্ত হয়নি)";

  const polishedText = data.spoken_transcript || fallbackNotice;
  const rawText = data.raw_spoken_transcript || fallbackNotice;
  
  document.getElementById("resPolishedText").textContent = polishedText;
  document.getElementById("resRawText").textContent = rawText;

  // Refiner Badge
  const badge = document.getElementById("nemotronBadge");
  if (data.refiner_model && data.refiner_model.includes("nemotron")) {
    badge.textContent = "NVIDIA Nemotron 550B Polished";
    badge.style.display = "inline-block";
  } else {
    badge.textContent = "Raw Whisper";
  }

  // Segments
  const segmentsList = document.getElementById("resSegmentsList");
  segmentsList.innerHTML = "";
  const segments = data.spoken_segments || [];

  if (segments.length > 0) {
    document.getElementById("segmentsCard").style.display = "block";
    segments.forEach(seg => {
      const row = document.createElement("div");
      row.className = "segment-row";
      row.innerHTML = `
        <span class="segment-time">${seg.start || '00:00'} ➔ ${seg.end || '00:00'}</span>
        <span class="bengali-text" style="font-size: 0.98rem;">${seg.text}</span>
      `;
      segmentsList.appendChild(row);
    });
  } else {
    document.getElementById("segmentsCard").style.display = "none";
  }

  // OCR
  const ocrElem = document.getElementById("resOcrText");
  const ocrList = data.on_screen_text || [];
  if (Array.isArray(ocrList) && ocrList.length > 0) {
    document.getElementById("ocrCard").style.display = "block";
    ocrElem.innerHTML = ocrList.map(item => {
      if (typeof item === "string") return `<div>• ${item}</div>`;
      return `<div><span class="segment-time">${item.timestamp || ''}</span> <strong>${item.text || ''}</strong></div>`;
    }).join("");
  } else {
    ocrElem.textContent = "ভিডিওতে কোনো অন-স্ক্রিন টাইটেল বা টিক্কার টেক্সট পাওয়া যায়নি।";
  }

  // Scroll smoothly to results
  resultsSection.scrollIntoView({ behavior: "smooth" });
}

// Copy single text element
function copyText(elementId) {
  const text = document.getElementById(elementId).textContent;
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    alert("কপি করা হয়েছে!");
  });
}

// Copy full Fact-Check Dossier in Markdown
function copyFullReport() {
  if (!currentResultData) return;
  const d = currentResultData;

  const report = `## Khoj Video Fact-Check Report
- **Title**: ${d.title || 'N/A'}
- **Platform**: ${d.platform || 'N/A'}
- **Duration**: ${d.duration_seconds || 0}s
- **Processed in**: ${(d.processing_time_seconds || 0).toFixed(2)}s

### Spoken Transcript (NVIDIA Nemotron Polished):
${d.spoken_transcript || 'None'}

### Raw Whisper ASR:
${d.raw_spoken_transcript || 'None'}

### On-Screen OCR Text:
${JSON.stringify(d.on_screen_text || [], null, 2)}
`;

  navigator.clipboard.writeText(report).then(() => {
    alert("সম্পূর্ণ ফ্যাক্ট-চেক রিপোর্ট মার্কডাউন ফরম্যাটে কপি হয়েছে!");
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
  modal.style.display = (modal.style.display === "flex") ? "none" : "flex";
}

function closeSettingsIfOutside(e) {
  if (e.target.id === "settingsModal") {
    toggleSettingsModal();
  }
}

function setPresetBackend(url) {
  document.getElementById("backendUrlInput").value = url;
}

function saveBackendUrl() {
  const val = document.getElementById("backendUrlInput").value.trim().replace(/\/$/, "");
  if (!val) return;
  currentBackendUrl = val;
  localStorage.setItem("KHOJ_BACKEND_URL", val);
  toggleSettingsModal();
  checkBackendHealth();
  alert(`Backend updated to:\n${currentBackendUrl}`);
}
