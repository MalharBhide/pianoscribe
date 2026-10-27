/* Piano Scribe studio frontend (dependency-free).
 * Served as /app.js; typed contracts live in app.ts.
 */
(function () {
"use strict";
/* ---------- tiny utils ---------- */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtT = (t) => { t = Math.max(0, t || 0); return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`; };
const fmtDur = (t) => { t = Math.round(t || 0); return t >= 60 ? `${Math.floor(t / 60)}m ${t % 60}s` : `${t}s`; };
const NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const noteName = (p) => `${NOTE_NAMES[p % 12]}${Math.floor(p / 12) - 1}`;

const S = {
  view: "home", cfg: null, projects: [],
  pid: null, proj: null, score: null,
  selection: new Set(), tab: "score", zoom: 100, follow: true,
  showChords: true, showFing: true, handFilter: "both",
  loop: null, playing: false, source: "piano", speed: 1, t: 0, raf: 0,
  actx: null, master: null, gainL: null, gainR: null, playStartCtx: 0, playStartT: 0,
  schedUntil: 0,
  pendingAction: null, chat: [], pollTimer: 0, scrubbing: false,
  file: null, recording: null, hasAudio: true, workerOk: true, rollDragged: false,
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    const err = new Error(msg); err.status = r.status; throw err;
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.blob();
}

/* ---------- toasts ---------- */
function toast(msg, kind) {
  const box = $("toasts");
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  const span = document.createElement("span");
  span.textContent = msg;
  const x = document.createElement("button");
  x.textContent = "×"; x.setAttribute("aria-label", "Dismiss");
  x.onclick = () => el.remove();
  el.append(span, x); box.append(el);
  setTimeout(() => { if (el.isConnected) el.remove(); }, kind === "err" ? 8000 : 4500);
}

/* ---------- modal ---------- */
function openModal(html, opts) {
  opts = opts || {};
  closeModal();
  const back = document.createElement("div");
  back.className = "mback";
  const m = document.createElement("div");
  m.className = "modal"; m.setAttribute("role", "dialog"); m.setAttribute("aria-modal", "true");
  m.innerHTML = html;
  back.append(m); $("modalRoot").append(back);
  const close = () => back.remove();
  back.addEventListener("mousedown", (e) => { if (e.target === back && !opts.sticky) close(); });
  m.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
  const first = m.querySelector("button,input"); if (first) first.focus();
  return { back, modal: m, close };
}
function closeModal() { $("modalRoot").innerHTML = ""; }
function confirmDialog(title, body, okLabel) {
  return new Promise((resolve) => {
    const { modal, close } = openModal(
      `<h2>${esc(title)}</h2><p class="muted">${esc(body)}</p>
       <div class="btnrow"><button class="primary" id="cfOk">${esc(okLabel || "Confirm")}</button>
       <button id="cfNo">Cancel</button></div>`, { sticky: true });
    modal.querySelector("#cfOk").onclick = () => { close(); resolve(true); };
    modal.querySelector("#cfNo").onclick = () => { close(); resolve(false); };
  });
}
function promptModal(title, initial) {
  return new Promise((resolve) => {
    const { modal, close } = openModal(
      `<h2>${esc(title)}</h2>
       <div class="askrow"><input type="text" id="prIn" maxlength="120"><button class="primary" id="prOk">Save</button></div>`,
      { sticky: true });
    const inp = modal.querySelector("#prIn");
    inp.value = initial || "";
    const done = (v) => { close(); resolve(v); };
    modal.querySelector("#prOk").onclick = () => done(inp.value.trim());
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") done(inp.value.trim()); if (e.key === "Escape") done(null); });
    inp.focus(); inp.select();
  });
}
const GUIDE_HTML = `<h2>Guide</h2>
<h3>Which mode?</h3><p><strong>Piano recording</strong> keeps performed notes, chords, and rhythm as close as possible. <strong>Song or band</strong> builds a playable arrangement from the melody, bass, and chords — blue notes are arrangement choices, not detections.</p>
<h3>Corrections beat re-uploads</h3><p>Click any note to select it, fix pitch and timing in the Edit tab (or drag notes in Piano roll), and re-export. One <kbd>Ctrl Z</kbd> undoes anything, including the assistant's changes.</p>
<h3>Chords &amp; fingers</h3><p>Chord symbols come from the recording's harmony; finger numbers are suggestions to adjust to your hand. Toggle both in the studio toolbar or ask the assistant.</p>
<h3>Honest limits</h3><p>Clean piano transcribes best. Dense commercial mixes over-detect and need cleanup — use Song-or-band mode there. Drum-only audio is skipped on MIDI import. Fingering is a heuristic starting point.</p>
<div class="btnrow"><button data-close>Close</button></div>`;
const SHORTCUTS_HTML = `<h2>Keyboard shortcuts</h2><table>
<tr><td><kbd>Space</kbd></td><td>Play / pause</td></tr>
<tr><td><kbd>←</kbd> <kbd>→</kbd></td><td>Seek 5 seconds</td></tr>
<tr><td><kbd>1</kbd> / <kbd>2</kbd></td><td>Staff / Piano roll view</td></tr>
<tr><td><kbd>Del</kbd></td><td>Delete selected notes</td></tr>
<tr><td><kbd>Ctrl Z</kbd> / <kbd>Ctrl Y</kbd></td><td>Undo / redo</td></tr>
<tr><td><kbd>L</kbd></td><td>Toggle loop on selection</td></tr>
<tr><td><kbd>F</kbd></td><td>Toggle follow playback</td></tr>
<tr><td><kbd>Esc</kbd></td><td>Close dialog / clear selection</td></tr>
<tr><td><kbd>?</kbd></td><td>This list</td></tr></table>
<div class="btnrow"><button data-close>Close</button></div>`;

/* ---------- router ---------- */
const CRUMBS = { home: ["New score", ""], progress: ["Processing", ""], studio: ["Studio", ""], library: ["Library", ""] };
function go(view) {
  if (view === "guide") { openModal(GUIDE_HTML); return; }
  S.view = view;
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== "view-" + view; });
  document.querySelectorAll("#sideNav .navbtn").forEach((b) => b.classList.toggle("active", b.dataset.go === view));
  $("crumbTitle").textContent = CRUMBS[view][0];
  $("crumbSub").textContent = CRUMBS[view][1];
  $("dock").hidden = view !== "studio";
  if (view === "library") refreshLibrary();
  if (view === "home") refreshRecent();
}
document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));

/* ---------- config ---------- */
async function loadConfig() {
  try {
    const c = await api("/api/config");
    S.cfg = c;
    const acc = c.accepted.join(", ").toUpperCase();
    $("limitsLine").textContent = `Limits: ${c.max_upload_mb} MB per file · up to ${c.max_duration_min} minutes · ${acc}${c.mp3_supported ? "" : " (MP3 unavailable on this server)"}`;
    $("sideLimits").textContent = `${c.max_upload_mb} MB · ${c.max_duration_min} min`;
    $("sideVer").textContent = "v" + c.app_version;
  } catch (e) { $("limitsLine").textContent = "Could not reach the server. Is it running?"; }
  checkWorker();
}
async function checkWorker() {
  // Surface a dead/missing background worker before the user waits on a job
  // that will never start — the common "transcription never happens" cause.
  try {
    const r = await api("/ready");
    S.workerOk = r.status === "ready";
    const el = $("workerWarn");
    if (S.workerOk) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = "The background worker isn't running, so new transcriptions will wait in line until one starts. " +
      "Start it with <code>docker compose up</code> (or <code>python -m piano_scribe.worker</code> without Docker), then come back here.";
  } catch (e) { S.workerOk = false; }
}

/* ---------- home: upload ---------- */
function setFile(f) {
  S.file = f || null;
  $("dz").style.display = f ? "none" : "";
  $("fileMeta").hidden = !f;
  $("startBtn").disabled = !f;
  if (f) {
    $("fileName").textContent = f.name;
    $("fileDetail").textContent = `${(f.size / 1048576).toFixed(1)} MB · ${(document.querySelector('input[name=mode]:checked') || {}).value === "arrangement" ? "Song or band" : "Piano recording"}`;
  }
  $("homeErr").textContent = "";
}
function modeVal() { return (document.querySelector('input[name=mode]:checked') || {}).value || "piano"; }
async function startUpload() {
  if (!S.file) return;
  $("homeErr").textContent = ""; $("startBtn").disabled = true;
  const fd = new FormData();
  fd.append("file", S.file);
  fd.append("mode", modeVal());
  fd.append("density", $("density").value);
  fd.append("title", S.file.name.replace(/\.[^.]+$/, ""));
  try {
    const job = await api("/api/uploads", { method: "POST", body: fd });
    const label = S.file ? `“${S.file.name}”` : "";
    setFile(null);
    openProgress(job.job_id, label, true);
  } catch (e) { $("homeErr").textContent = e.message; }
  finally { $("startBtn").disabled = !S.file; }
}

/* ---------- home: record in browser ---------- */
async function toggleRecord() {
  if (S.recording) { S.recording.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    toast("Recording isn't supported in this browser.", "err"); return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = ["audio/webm", "audio/mp4"].find((m) => window.MediaRecorder.isTypeSupported(m)) || "";
    const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks = [];
    mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    const t0 = Date.now();
    $("recBtn").classList.add("rec"); $("recBtn").textContent = "■ Stop recording";
    $("recStatus").textContent = "Recording… 0s";
    const tick = setInterval(() => { $("recStatus").textContent = `Recording… ${Math.round((Date.now() - t0) / 1000)}s`; }, 500);
    S.recording = {
      stop: () => {
        mr.onstop = async () => {
          clearInterval(tick);
          stream.getTracks().forEach((t) => t.stop());
          $("recBtn").classList.remove("rec"); $("recBtn").textContent = "● Record in browser";
          S.recording = null;
          try {
            $("recStatus").textContent = "Encoding…";
            const buf = await new Blob(chunks).arrayBuffer();
            const actx = new (window.AudioContext || window.webkitAudioContext)();
            const audio = await actx.decodeAudioData(buf);
            const wav = encodeWav(audio.getChannelData(0), audio.sampleRate);
            await actx.close();
            setFile(new File([wav], `recording-${new Date().toISOString().slice(11, 19).replace(/:/g, "")}.wav`, { type: "audio/wav" }));
            $("recStatus").textContent = "Ready — press Transcribe.";
            toast("Recording ready. Press Transcribe.", "ok");
          } catch (e) { $("recStatus").textContent = ""; toast("Could not process that recording: " + e.message, "err"); }
        };
        mr.stop();
      },
    };
    mr.start();
  } catch (e) { toast("Microphone unavailable: " + e.message, "err"); }
}
function encodeWav(samples, sampleRate) {
  const n = samples.length, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const wstr = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  wstr(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); wstr(8, "WAVE"); wstr(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); wstr(36, "data");
  v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 32767, true);
  return new Blob([buf], { type: "audio/wav" });
}

/* ---------- home: MIDI import ---------- */
async function importMidi(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file); fd.append("title", file.name.replace(/\.[^.]+$/, ""));
  try {
    toast("Importing MIDI…");
    const d = await api("/api/imports", { method: "POST", body: fd });
    toast("MIDI imported.", "ok");
    await openProject(d.project_id);
  } catch (e) { toast("MIDI import failed: " + e.message, "err"); }
}

/* ---------- home: samples + recent ---------- */
async function loadSamples() {
  const grid = $("sampleGrid");
  try {
    const d = await api("/api/samples");
    grid.innerHTML = "";
    d.samples.forEach((s) => {
      const b = document.createElement("button");
      b.className = "sample"; b.disabled = !s.available;
      b.innerHTML = `<strong>${esc(s.title)}</strong><small>${esc(s.blurb)}</small>
        <span class="go">${s.available ? `Transcribe · ${fmtDur(s.duration_sec || 0)} · ${s.mode === "arrangement" ? "Song or band" : "Piano"} →` : "Unavailable"}</span>`;
      b.onclick = () => transcribeSample(s);
      grid.append(b);
    });
  } catch (e) { grid.innerHTML = `<p class="muted">Examples unavailable right now.</p>`; }
}
async function transcribeSample(s) {
  try {
    const d = await api(`/api/samples/${s.id}/transcribe`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: s.mode, density: "standard" }) });
    openProgress(d.job_id, s.title, false);
  } catch (e) { toast("Could not start the example: " + e.message, "err"); }
}
async function refreshRecent() {
  try {
    const d = await api("/api/projects");
    S.projects = d.projects;
    const box = $("recentList");
    box.innerHTML = "";
    if (!d.projects.length) { box.innerHTML = "<p class='muted'>No scores yet — your first one will appear here.</p>"; return; }
    d.projects.slice(0, 5).forEach((p) => {
      const b = document.createElement("button");
      b.className = "recent";
      b.innerHTML = `<svg class="ic"><use href="#i-lib"/></svg><div><strong>${esc(p.title)}</strong>
        <small>${p.mode === "arrangement" ? "Song or band" : "Piano"} · ${p.note_count} notes · ${fmtDur(p.duration_sec)}</small></div>`;
      b.onclick = () => openProject(p.id);
      box.append(b);
    });
  } catch (e) {}
}

/* ---------- progress view ---------- */
const STAGES = [["queued", "Queued"], ["decode", "Decoding audio"], ["transcribe", "Finding notes"], ["notation", "Writing notation"], ["done", "Opening score"]];
function stageIndex(job) {
  if (job.status === "done") return 4;
  if (job.status === "failed") return -1;
  const s = (job.stage || "").toLowerCase();
  if (s.includes("notation")) return 3;
  if (s.includes("transcribe") || s.includes("notes") || s.includes("window") || s.includes("melody") || s.includes("chord") || s.includes("bass")) return 2;
  if (s.includes("decod") || s.includes("spectrum") || s.includes("starting")) return 1;
  return 0;
}
function openProgress(jid, label, isUpload) {
  go("progress");
  stop();
  $("progFile").textContent = label || "Your recording";
  $("progErr").textContent = "";
  clearInterval(S.pollTimer);
  let polls = 0, queuedSince = Date.now();
  const poll = async () => {
    try {
      const j = await api("/api/jobs/" + jid);
      polls++;
      if (j.status !== "queued") queuedSince = Date.now();
      else if (Date.now() - queuedSince > 8000 && polls % 5 === 0) {
        try { const r = await api("/ready"); S.workerOk = r.status === "ready"; } catch (e) { S.workerOk = false; }
        if (!S.workerOk) $("progErr").textContent = "Still waiting in line: no background worker is running. Start one with “docker compose up” (or “python -m piano_scribe.worker” without Docker) and this job will begin automatically.";
        else $("progErr").textContent = "";
      } else if (j.status !== "queued" && j.status !== "failed") $("progErr").textContent = "";
      $("progMode").textContent = `${j.mode === "arrangement" ? "Song-or-band arrangement" : "Piano transcription"} · ${j.density}`;
      const idx = stageIndex(j);
      const ol = $("stageList"); ol.innerHTML = "";
      STAGES.forEach(([key, name], i) => {
        const li = document.createElement("li");
        li.className = idx < 0 ? "" : i < idx ? "done" : i === idx ? "doing" : "";
        li.innerHTML = `<span class="st">${i < idx && idx >= 0 ? "✓" : ""}</span><span>${name}</span>`;
        ol.append(li);
      });
      const pct = Math.round((j.progress || 0) * 100);
      $("progBar").firstElementChild.style.width = pct + "%";
      $("progBar").setAttribute("aria-valuenow", pct);
      $("progPct").textContent = pct + "%";
      $("progNote").textContent = (j.attempts > 1 ? `Attempt ${j.attempts} · ` : "") + (j.stage || j.status);
      if (j.status === "done" && j.project_id) {
        clearInterval(S.pollTimer);
        toast("Score ready.", "ok");
        await openProject(j.project_id);
        return;
      }
      if (j.status === "failed") {
        clearInterval(S.pollTimer);
        $("progErr").textContent = "This one didn't work: " + (j.error || "unknown error") +
          " Try a cleaner or shorter recording, or another example.";
        return;
      }
    } catch (e) { $("progErr").textContent = "Lost contact with the server. The job may still be running — check the Library in a minute."; clearInterval(S.pollTimer); }
  };
  poll();
  S.pollTimer = setInterval(poll, 1200);
  $("progBack").onclick = () => { clearInterval(S.pollTimer); go(isUpload ? "home" : "home"); };
}

/* ---------- studio: open + render ---------- */
async function openProject(pid) {
  stop(); clearInterval(S.pollTimer);
  try {
    const d = await api("/api/projects/" + pid);
    S.pid = pid; S.proj = d; S.score = d.score;
    S.selection.clear(); S.loop = null; S.t = 0; S.chat = []; S.pendingAction = null;
    $("prjTitle").value = d.title;
    $("modeBadge").innerHTML = d.mode === "arrangement"
      ? '<span class="pill arr">Song or band · arrangement</span>'
      : '<span class="pill">Piano · transcription</span>';
    $("expVer").textContent = `Exports match score v${d.score.version}`;
    $("orig").src = d.audio_url;
    S.hasAudio = d.playable_audio !== false;
    S.source = "piano";
    $("srcPiano").setAttribute("aria-pressed", "true");
    $("srcOrig").setAttribute("aria-pressed", "false");
    $("srcOrig").disabled = !S.hasAudio;
    $("srcOrig").title = S.hasAudio ? "" : "No playable recording on this score (e.g. MIDI import)";
    $("chatLog").innerHTML = "";
    if (!d.score.notes.length) {
      say("ai", "I couldn't find confident notes in this recording, so the score is empty. A clearer recording — or the other mode on the home page — usually works better.");
      toast("No confident notes found — the score is empty.", "err");
    } else {
      say("ai", "Ask about this score — chords, transposing, simplifying, looping. I'll propose each change before making it.");
    }
    renderChips();
    refreshScoreView(d, true);
    setTab("score");
    go("studio");
    $("crumbSub").textContent = d.title;
    markSaved();
  } catch (e) { toast("Could not open that project: " + e.message, "err"); go("library"); }
}
function refreshScoreView(d, keepSel) {
  const ids = keepSel ? [...S.selection] : [];
  S.score = d.score;
  S.selection = new Set(ids.filter((id) => S.score.notes.some((x) => x.id === id)));
  $("scoreMeta").textContent = `${S.score.notes.length} notes · v${S.score.version} · ${fmtDur(dur())}`;
  $("expVer").textContent = `Exports match score v${S.score.version}`;
  $("scoreWrap").innerHTML = d.svg;
  wireScore();
  renderRoll();
  applyDisplayToggles();
  updateSelUI(); updateTime(); updateCursor(); renderDetails();
  markSaved();
}
function markSaved() {
  const t = new Date();
  $("saveState").textContent = S.score ? `Saved · v${S.score.version} · ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}` : "";
}
function setTab(tab) {
  S.tab = tab;
  $("tabScore").setAttribute("aria-selected", tab === "score");
  $("tabRoll").setAttribute("aria-selected", tab === "roll");
  $("scoreWrap").hidden = tab !== "score";
  $("rollWrap").hidden = tab !== "roll";
  if (tab === "roll") renderRoll();
  updateCursor();
}

/* score svg wiring */
function wireScore() {
  const wrap = $("scoreWrap");
  wrap.querySelectorAll(".notehead").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = el.getAttribute("data-note");
      if (e.shiftKey) { S.selection.has(id) ? S.selection.delete(id) : S.selection.add(id); }
      else { S.selection = new Set([id]); }
      seek(parseFloat(el.getAttribute("data-time") || "0"));
      updateSelUI(); audition(id);
    });
  });
  wrap.querySelectorAll(".measure-hit").forEach((el) => {
    el.addEventListener("click", (e) => {
      const t0 = parseFloat(el.getAttribute("data-t0") || "0");
      const t1 = parseFloat(el.getAttribute("data-t1") || "0");
      if (e.shiftKey) {
        S.selection = new Set(S.score.notes.filter((x) => x.start_sec >= t0 - 1e-6 && x.start_sec < t1 - 1e-6).map((x) => x.id));
        updateSelUI();
      } else seek(t0 + 0.001);
    });
  });
  const svg = wrap.querySelector("svg");
  if (svg) {
    svg.style.width = S.zoom + "%";
    const ln = document.createElementNS("http://www.w3.org/2000/svg", "line");
    ln.setAttribute("id", "cursorLine");
    ln.setAttribute("x1", -10); ln.setAttribute("x2", -10);
    ln.setAttribute("y1", 0); ln.setAttribute("y2", 0);
    svg.appendChild(ln);
  }
  applyHandFilter();
  updateCursor();
}
function applyDisplayToggles() {
  const svg = $("scoreWrap").querySelector("svg");
  if (svg) {
    svg.classList.toggle("hide-chords", !S.showChords);
    svg.classList.toggle("hide-fing", !S.showFing);
  }
  $("chordsBtn").setAttribute("aria-pressed", S.showChords);
  $("fingerBtn").setAttribute("aria-pressed", S.showFing);
  renderRoll();
}
function applyHandFilter() {
  const f = S.handFilter;
  document.querySelectorAll("#scoreWrap .notehead").forEach((el) => {
    const n = S.score.notes.find((x) => x.id === el.getAttribute("data-note"));
    el.style.opacity = n && (f === "both" || n.hand === f) ? "" : "0.15";
  });
}

/* piano roll */
const ROLL = { gutter: 64, ruler: 26, rowH: 13 };
function rollGeom() {
  const notes = S.score.notes;
  const d = Math.max(dur(), 1);
  const lo = Math.max(21, Math.min(...notes.map((n) => n.pitch), 60) - 2);
  const hi = Math.min(108, Math.max(...notes.map((n) => n.pitch), 72) + 2);
  const px = Math.min(140, Math.max(2.5, 1400 / d));
  return { lo, hi, px, d, W: ROLL.gutter + d * px + 30, H: ROLL.ruler + (hi - lo + 1) * ROLL.rowH + 8 };
}
function renderRoll() {
  const box = $("rollWrap");
  if (!S.score) { box.innerHTML = ""; return; }
  const g = rollGeom(), NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${g.W} ${g.H}`);
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Piano roll");
  const yOf = (p) => ROLL.ruler + (g.hi - p) * ROLL.rowH;
  const xOf = (t) => ROLL.gutter + t * g.px;
  // key rows
  for (let p = g.lo; p <= g.hi; p++) {
    const black = [1, 3, 6, 8, 10].includes(p % 12);
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", 0); r.setAttribute("y", yOf(p));
    r.setAttribute("width", g.W); r.setAttribute("height", ROLL.rowH);
    r.setAttribute("fill", black ? "#eceff3" : "#ffffff");
    r.setAttribute("stroke", "#e2e7ee"); r.setAttribute("stroke-width", "0.5");
    svg.append(r);
    if (p % 12 === 0) {
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", 8); t.setAttribute("y", yOf(p) + 10);
      t.setAttribute("font-size", "9"); t.setAttribute("fill", "#8a93a0");
      t.textContent = noteName(p); svg.append(t);
    }
  }
  // ruler + beat grid
  const beats = (S.score.tempo.beats || []).filter((b) => b <= g.d);
  beats.forEach((b) => {
    const l = document.createElementNS(NS, "line");
    l.setAttribute("x1", xOf(b)); l.setAttribute("x2", xOf(b));
    l.setAttribute("y1", ROLL.ruler); l.setAttribute("y2", g.H);
    l.setAttribute("stroke", "#d4dbe4"); l.setAttribute("stroke-width", "1");
    svg.append(l);
  });
  const step = g.d > 120 ? 10 : g.d > 30 ? 5 : 1;
  for (let t = 0; t <= g.d; t += step) {
    const tx = document.createElementNS(NS, "text");
    tx.setAttribute("x", xOf(t)); tx.setAttribute("y", 15);
    tx.setAttribute("font-size", "9"); tx.setAttribute("fill", "#8a93a0");
    tx.textContent = fmtT(t); svg.append(tx);
  }
  // chord lane
  if (S.showChords) {
    (S.score.chords || []).forEach((c) => {
      const tx = document.createElementNS(NS, "text");
      tx.setAttribute("x", xOf(c.start_sec) + 2); tx.setAttribute("y", 15);
      tx.setAttribute("font-size", "10"); tx.setAttribute("font-weight", "bold");
      tx.setAttribute("fill", "#17407f");
      tx.textContent = chordText(c.label); svg.append(tx);
    });
  }
  // seek background
  const bg = document.createElementNS(NS, "rect");
  bg.setAttribute("x", ROLL.gutter); bg.setAttribute("y", ROLL.ruler);
  bg.setAttribute("width", g.d * g.px); bg.setAttribute("height", g.H - ROLL.ruler);
  bg.setAttribute("fill", "transparent"); bg.style.cursor = "pointer";
  bg.addEventListener("click", (e) => {
    const r = svg.getBoundingClientRect();
    const frac = (e.clientX - r.left) / r.width;
    seek(Math.max(0, ((frac * g.W) - ROLL.gutter) / g.px));
  });
  svg.append(bg);
  // blocks
  S.score.notes.forEach((n) => {
    if (S.handFilter !== "both" && n.hand !== S.handFilter) return;
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", xOf(n.start_sec)); r.setAttribute("y", yOf(n.pitch) + 1);
    r.setAttribute("width", Math.max(3, (n.end_sec - n.start_sec) * g.px));
    r.setAttribute("height", ROLL.rowH - 2); r.setAttribute("rx", 2);
    r.setAttribute("class", `rollblk ${n.hand}` + (S.selection.has(n.id) ? " sel" : ""));
    r.setAttribute("data-note", n.id);
    r.setAttribute("opacity", 0.55 + 0.45 * (n.velocity / 127));
    const title = document.createElementNS(NS, "title");
    title.textContent = `${noteName(n.pitch)} · ${n.start_sec.toFixed(2)}s · ${n.hand === "L" ? "Left" : "Right"}${n.finger && S.showFing ? ` · finger ${n.finger}` : ""}`;
    r.append(title);
    r.addEventListener("click", (e) => {
      e.stopPropagation();
      if (S.rollDragged) { S.rollDragged = false; return; }
      if (e.shiftKey) { S.selection.has(n.id) ? S.selection.delete(n.id) : S.selection.add(n.id); }
      else { S.selection = new Set([n.id]); }
      seek(n.start_sec); updateSelUI(); audition(n.id); renderRoll();
    });
    r.addEventListener("pointerdown", (e) => startRollDrag(e, n, svg, g));
    svg.append(r);
    if (n.finger && S.showFing && (n.end_sec - n.start_sec) * g.px > 16) {
      const ft = document.createElementNS(NS, "text");
      ft.setAttribute("x", xOf(n.start_sec) + 3); ft.setAttribute("y", yOf(n.pitch) + 10);
      ft.setAttribute("font-size", "9"); ft.setAttribute("fill", "#fff");
      ft.setAttribute("pointer-events", "none"); ft.textContent = n.finger;
      svg.append(ft);
    }
  });
  const cur = document.createElementNS(NS, "line");
  cur.setAttribute("id", "rollCursor");
  cur.setAttribute("y1", 0); cur.setAttribute("y2", g.H);
  cur.setAttribute("stroke", "#c62828"); cur.setAttribute("stroke-width", "2");
  svg.append(cur);
  box.innerHTML = ""; box.append(svg);
  updateCursor();
}
const PC_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
const Q_SUFFIX = { maj: "", min: "m", dim: "dim", aug: "aug", "7": "7", maj7: "maj7", m7: "m7" };
function chordText(label) {
  const m = /^(\d+):(\w+)$/.exec(label || "");
  if (!m || !(m[2] in Q_SUFFIX)) return "";
  return PC_NAMES[+m[1] % 12] + Q_SUFFIX[m[2]];
}
function startRollDrag(e, n, svg, g) {
  e.preventDefault();
  const startX = e.clientX, startY = e.clientY;
  const origS = n.start_sec, origE = n.end_sec, origP = n.pitch;
  const live = () => { const s = $("rollWrap").querySelector("svg"); return s ? s.getBoundingClientRect().width / g.W : 1; };
  const move = (ev) => {
    const k = live() || 1;
    const dt = (ev.clientX - startX) / k / g.px;
    const dp = Math.round((startY - ev.clientY) / k / ROLL.rowH);
    n.start_sec = Math.max(0, +(origS + dt).toFixed(2));
    n.end_sec = Math.max(n.start_sec + 0.05, +(origE + dt).toFixed(2));
    n.pitch = Math.max(21, Math.min(108, origP + dp));
    renderRoll();
  };
  const up = async (ev) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    // restore + persist through the server so history/undo stay correct
    const patch = { pitch: n.pitch, start_sec: n.start_sec, end_sec: n.end_sec };
    n.start_sec = origS; n.end_sec = origE; n.pitch = origP;
    if (patch.pitch !== origP || Math.abs(patch.start_sec - origS) > 0.005) {
      S.rollDragged = true;
      await noteOp({ op: "update", id: n.id, patch, label: "drag note" });
    } else renderRoll();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

/* ---------- playback ---------- */
function dur() {
  if (!S.score) return 0;
  const a = $("orig").duration;
  const n = S.score.notes.reduce((m, x) => Math.max(m, x.end_sec), 0);
  return Math.max(n, isFinite(a) && a > 0 ? a : 0, 1);
}
function updateTime() {
  $("timeCur").textContent = fmtT(S.t);
  $("timeTot").textContent = fmtT(dur());
  if (!S.scrubbing) $("seek").value = dur() > 0 ? Math.round((S.t / dur()) * 1000) : 0;
}
function ensureCtx() {
  if (!S.actx) {
    S.actx = new (window.AudioContext || window.webkitAudioContext)();
    S.master = S.actx.createGain(); S.master.connect(S.actx.destination);
    S.gainL = S.actx.createGain(); S.gainR = S.actx.createGain();
    S.gainL.connect(S.master); S.gainR.connect(S.master);
    applyGains();
  }
  if (S.actx.state === "suspended") S.actx.resume();
  return S.actx;
}
function applyGains() {
  if (!S.actx) return;
  S.gainL.gain.value = $("muteL").checked ? 0 : $("gainL").value / 100;
  S.gainR.gain.value = $("muteR").checked ? 0 : $("gainR").value / 100;
}
function pianoTone(ctx, dest, pitch, t0, durS, vel) {
  const f = 440 * Math.pow(2, (pitch - 69) / 12);
  const g = ctx.createGain();
  const peak = 0.22 * (vel / 100);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(Math.max(0.001, peak), t0 + 0.012);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + Math.max(0.09, durS));
  [[1, 1, "triangle"], [2, 0.35, "sine"], [3, 0.18, "sine"], [4.01, 0.08, "sine"]].forEach(([m, a, ty]) => {
    const o = ctx.createOscillator();
    o.type = ty; o.frequency.value = f * m;
    const og = ctx.createGain(); og.gain.value = a;
    o.connect(og); og.connect(g); o.start(t0); o.stop(t0 + Math.max(0.1, durS) + 0.05);
  });
  g.connect(dest);
}
function schedulePiano(fromT) {
  const ctx = ensureCtx();
  const t0 = ctx.currentTime + 0.06;
  S.playStartCtx = t0; S.playStartT = fromT;
  const horizon = fromT + 90;
  S.score.notes.forEach((x) => {
    if (x.end_sec <= fromT || x.start_sec >= horizon) return;
    const dest = x.hand === "L" ? S.gainL : S.gainR;
    const start = Math.max(x.start_sec, fromT);
    pianoTone(ctx, dest, x.pitch, t0 + (start - fromT) / S.speed,
      (x.end_sec - start) / S.speed, x.velocity);
  });
  S.schedUntil = horizon;
}
function schedulePianoMore() {
  // Top up the schedule window during long playback so a 30-minute score
  // never instantiates tens of thousands of oscillators at once.
  const ctx = S.actx;
  if (!ctx || !S.score) return;
  const horizon = Math.min(dur(), S.schedUntil + 60);
  S.score.notes.forEach((x) => {
    if (x.start_sec < S.schedUntil || x.start_sec >= horizon) return;
    const when = S.playStartCtx + (x.start_sec - S.playStartT) / S.speed;
    if (when < ctx.currentTime - 0.1) return;
    const dest = x.hand === "L" ? S.gainL : S.gainR;
    pianoTone(ctx, dest, x.pitch, when, (x.end_sec - x.start_sec) / S.speed, x.velocity);
  });
  S.schedUntil = horizon;
}
function stopVoices() { if (S.actx) { S.actx.close().catch(() => {}); S.actx = null; } }
function setPlayIcon() {
  $("play").innerHTML = `<svg class="ic"><use href="#i-${S.playing ? "pause" : "play"}"/></svg>`;
}
function audition(id) {
  // Preview one note immediately (clicking through the score), even stopped.
  if (!S.score || S.playing) return;
  const n = S.score.notes.find((x) => x.id === id);
  if (!n) return;
  try {
    const ctx = ensureCtx(); applyGains();
    const dest = n.hand === "L" ? S.gainL : S.gainR;
    pianoTone(ctx, dest, n.pitch, ctx.currentTime + 0.03,
      Math.min(Math.max(n.end_sec - n.start_sec, 0.15), 1.2), n.velocity);
  } catch (e) {}
}
function play() {
  if (!S.score) return;
  ensureCtx(); applyGains();
  S.speed = $("speed").value / 100;
  if (S.source === "original") {
    const a = $("orig");
    a.playbackRate = S.speed;
    a.currentTime = Math.min(S.t, Math.max(0, dur() - 0.05));
    a.play().catch((e) => toast("Original audio failed: " + e.message, "err"));
  } else schedulePiano(S.t);
  S.playing = true; setPlayIcon();
  cancelAnimationFrame(S.raf);
  let lastBar = -1;
  const tick = () => {
    if (!S.playing) return;
    if (S.source === "original") S.t = $("orig").currentTime;
    else if (S.actx) {
      S.t = S.playStartT + (S.actx.currentTime - S.playStartCtx) * S.speed;
      if (S.t > S.schedUntil - 30 && S.schedUntil < dur()) schedulePianoMore();
    }
    if (S.loop && S.t >= S.loop.b) seek(S.loop.a);
    if (S.t >= dur()) { stop(); seek(0); return; }
    updateTime(); updateCursor();
    if (S.follow && S.tab === "score") {
      const m = currentMeasureEl();
      const idx = m ? +m.getAttribute("data-measure") : -1;
      if (idx !== lastBar && m) { lastBar = idx; m.scrollIntoView({ block: "nearest" }); }
    }
    S.raf = requestAnimationFrame(tick);
  };
  tick();
}
function stop() {
  S.playing = false;
  cancelAnimationFrame(S.raf);
  setPlayIcon();
  try { $("orig").pause(); } catch (e) {}
  stopVoices();
}
function seek(t) {
  if (!S.score) return;
  S.t = Math.max(0, Math.min(t, dur()));
  if (S.playing) {
    if (S.source === "original") $("orig").currentTime = S.t;
    else { stopVoices(); ensureCtx(); applyGains(); schedulePiano(S.t); }
  }
  updateTime(); updateCursor();
}
function currentMeasureEl() {
  return [...document.querySelectorAll("#scoreWrap .measure-hit")].find((el) =>
    S.t >= parseFloat(el.getAttribute("data-t0")) && S.t < parseFloat(el.getAttribute("data-t1")));
}
function updateCursor() {
  const ln = document.getElementById("cursorLine");
  if (ln) {
    const m = currentMeasureEl();
    if (!m) { ln.setAttribute("x1", -10); ln.setAttribute("x2", -10); }
    else {
      const t0 = parseFloat(m.getAttribute("data-t0")), t1 = parseFloat(m.getAttribute("data-t1"));
      const frac = Math.max(0, Math.min(1, (S.t - t0) / Math.max(1e-6, t1 - t0)));
      const x = parseFloat(m.getAttribute("x")) + frac * parseFloat(m.getAttribute("width"));
      ln.setAttribute("x1", x); ln.setAttribute("x2", x);
      ln.setAttribute("y1", m.getAttribute("y"));
      ln.setAttribute("y2", parseFloat(m.getAttribute("y")) + parseFloat(m.getAttribute("height")));
    }
  }
  const rc = document.getElementById("rollCursor");
  if (rc && S.score && !$("rollWrap").hidden) {
    const g = rollGeom();
    const x = ROLL.gutter + Math.min(S.t, g.d) * g.px;
    rc.setAttribute("x1", x); rc.setAttribute("x2", x);
  }
}
function updateLoopBtn() {
  $("loopBtn").setAttribute("aria-pressed", !!S.loop);
  $("loopLabel").textContent = S.loop ? `${fmtT(S.loop.a)}–${fmtT(S.loop.b)}` : "";
}
function toggleLoop() {
  if (S.loop || !S.score) { S.loop = S.loop ? null : S.score ? defaultLoop() : null; }
  else S.loop = defaultLoop();
  updateLoopBtn();
}
function defaultLoop() {
  const sel = S.score.notes.filter((x) => S.selection.has(x.id));
  if (sel.length) {
    const a = Math.min(...sel.map((x) => x.start_sec)), b = Math.max(...sel.map((x) => x.end_sec));
    return { a, b: Math.max(a + 0.5, b) };
  }
  return { a: Math.max(0, S.t - 2), b: Math.min(dur(), S.t + 8) };
}

/* ---------- selection + editing ---------- */
function updateSelUI() {
  document.querySelectorAll("#scoreWrap .notehead").forEach((el) =>
    el.classList.toggle("sel", S.selection.has(el.getAttribute("data-note"))));
  const sel = S.score ? S.score.notes.filter((x) => S.selection.has(x.id)) : [];
  $("selInfo").textContent = !sel.length ? "Nothing selected. Click a note (Shift-click adds; Shift-click a measure takes it all)."
    : sel.length === 1 ? `${noteName(sel[0].pitch)} · ${sel[0].hand === "L" ? "Left" : "Right"} hand · ${sel[0].provenance}`
    : `${sel.length} notes selected.`;
  ["applyEdit", "delSel", "trUp", "trDown"].forEach((id) => $(id).disabled = !sel.length);
  if (sel.length === 1) {
    const x = sel[0];
    $("ePitch").value = x.pitch; $("eStart").value = x.start_sec.toFixed(2);
    $("eEnd").value = x.end_sec.toFixed(2); $("eVel").value = x.velocity;
    $("eHand").value = x.hand; $("eFinger").value = x.finger || "";
  }
  updateLoopBtn();
}
async function noteOp(body) {
  $("edErr").textContent = ""; $("edOk").textContent = "";
  $("saveState").textContent = "Saving…";
  try {
    const d = await api(`/api/projects/${S.pid}/notes`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    refreshScoreView(d, true);
    $("edOk").textContent = body.op === "undo" ? "Undone." : body.op === "redo" ? "Redone." : "Saved.";
  } catch (e) { $("edErr").textContent = e.message; $("saveState").textContent = ""; }
}
function renderDetails() {
  const n = S.score.notes;
  const lh = n.filter((x) => x.hand === "L").length;
  const proc = S.score.processing || {};
  const rows = [
    ["Mode", S.proj.mode === "arrangement" ? "Song-or-band arrangement" : "Piano transcription"],
    ["Notes", `${n.length} (${n.length - lh} right · ${lh} left)`],
    ["Length", fmtDur(dur())], ["Chords detected", (S.score.chords || []).length],
    ["Tempo", `${Math.round(S.score.tempo.bpm)} bpm${(S.score.tempo.beats || []).length ? "" : " (assumed)"}`],
    ["Model", `${proc.model || "?"} ${proc.model_version || ""}`],
    ["Score version", "v" + S.score.version],
  ];
  $("detailsDl").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  $("procInfo").textContent = JSON.stringify(proc, null, 1);
}

/* ---------- assistant ---------- */
const CHIP_TEXTS = [
  "Explain the chord in measure 1",
  "Transpose everything down 2 semitones",
  "Simplify the left hand in measures 1–4",
  "Loop this at 75% speed",
];
function renderChips() {
  const box = $("chips"); box.innerHTML = "";
  CHIP_TEXTS.forEach((c) => {
    const b = document.createElement("button");
    b.className = "chip"; b.textContent = c;
    b.onclick = () => { $("ask").value = c; ask(); };
    box.append(b);
  });
}
function say(who, text) {
  S.chat.push({ who, text });
  const d = document.createElement("div");
  d.className = "msg " + who; d.textContent = text;
  $("chatLog").append(d);
  $("chatLog").scrollTop = $("chatLog").scrollHeight;
}
async function ask() {
  const text = $("ask").value.trim();
  if (!text || !S.pid) return;
  $("ask").value = "";
  say("you", text);
  $("actionCard").hidden = true; S.pendingAction = null;
  const thinking = document.createElement("div");
  thinking.className = "msg ai"; thinking.textContent = "…";
  $("chatLog").append(thinking);
  try {
    const d = await api(`/api/projects/${S.pid}/assistant`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, selection: [...S.selection] }) });
    thinking.remove(); say("ai", d.reply);
    const a = (d.actions || [])[0];
    if (!a) return;
    if (a.type === "loop") {
      const sel = S.score.notes.filter((x) => S.selection.has(x.id));
      const r = sel.length ? [Math.min(...sel.map((x) => x.start_sec)), Math.max(...sel.map((x) => x.end_sec))]
        : [Math.max(0, S.t - 2), Math.min(dur(), S.t + 6)];
      S.loop = { a: r[0], b: Math.max(r[0] + 0.5, r[1]) };
      $("speed").value = Math.round(a.speed * 100);
      $("speedV").textContent = $("speed").value + "%";
      updateLoopBtn();
    } else if (a.type === "display") {
      await api(`/api/projects/${S.pid}/assistant/apply`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: a }) });
      if (a.key === "chords") S.showChords = a.value; else S.showFing = a.value;
      applyDisplayToggles();
    } else {
      S.pendingAction = a;
      $("actionText").textContent = `Proposed change: ${summarizeAction(a)} Applies to the saved score; undo is available.`;
      $("actionCard").hidden = false;
    }
  } catch (e) { thinking.remove(); say("ai", "Something went wrong: " + e.message); }
}
function summarizeAction(a) {
  if (a.type === "transpose") return `transpose ${a.ids.length} note(s) by ${a.semitones > 0 ? "+" : ""}${a.semitones}`;
  if (a.type === "transpose_all") return `transpose all ${S.score.notes.length} notes by ${a.semitones > 0 ? "+" : ""}${a.semitones}`;
  if (a.type === "simplify_lh") return `simplify left hand, measures ${a.m0}–${a.m1} (remove ${a.drop_ids.length} tones)`;
  return a.type;
}
async function applyPending() {
  if (!S.pendingAction) return;
  try {
    const d = await api(`/api/projects/${S.pid}/assistant/apply`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: S.pendingAction }) });
    say("ai", d.message || (d.ok ? "Applied." : "Not applied."));
    if (d.ok && d.score) refreshScoreView(d, true);
  } catch (e) { say("ai", "Apply failed: " + e.message); }
  $("actionCard").hidden = true; S.pendingAction = null;
}

/* ---------- library ---------- */
async function refreshLibrary() {
  const grid = $("libGrid");
  try {
    const d = await api("/api/projects");
    S.projects = d.projects;
    $("libCount").textContent = d.projects.length ? `(${d.projects.length})` : "";
    $("libHeadCount").textContent = d.projects.length ? `· ${d.projects.length}` : "";
    paintLibrary();
  } catch (e) { grid.innerHTML = `<p class="muted">Couldn't load the library.</p>`; }
}
function paintLibrary() {
  const q = ($("libSearch").value || "").toLowerCase();
  const sort = $("libSort").value;
  let rows = S.projects.filter((p) => p.title.toLowerCase().includes(q));
  rows.sort((a, b) => sort === "az" ? a.title.localeCompare(b.title)
    : sort === "old" ? a.updated_at - b.updated_at : b.updated_at - a.updated_at);
  const grid = $("libGrid"); grid.innerHTML = "";
  $("libEmpty").hidden = rows.length > 0;
  if (S.projects.length && !rows.length) grid.innerHTML = `<p class="muted">No scores match “${esc(q)}”.</p>`;
  rows.forEach((p) => {
    const card = document.createElement("div");
    card.className = "libcard";
    card.innerHTML = `<h3>${esc(p.title)}</h3>
      <div class="meta"><span class="pill${p.mode === "arrangement" ? " arr" : ""}">${p.mode === "arrangement" ? "Song or band" : "Piano"}</span>
      <span class="muted small">${p.note_count} notes · ${fmtDur(p.duration_sec)}</span></div>
      <div class="muted small">Edited ${new Date(p.updated_at * 1000).toLocaleString()}</div>
      <div class="actions"></div>`;
    const acts = card.querySelector(".actions");
    const mk = (label, fn, cls) => {
      const b = document.createElement("button");
      b.className = "iconbtn" + (cls ? " " + cls : ""); b.innerHTML = label;
      b.onclick = (e) => { e.stopPropagation(); fn(); }; acts.append(b);
    };
    mk("Open", () => openProject(p.id));
    mk("Rename", async () => {
      const t = await promptModal("Rename score", p.title);
      if (!t) return;
      try { await api(`/api/projects/${p.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: t }) }); refreshLibrary(); toast("Renamed.", "ok"); }
      catch (e) { toast(e.message, "err"); }
    });
    mk("Duplicate", async () => {
      try { const d = await api(`/api/projects/${p.id}/duplicate`, { method: "POST" }); refreshLibrary(); toast(`Duplicated as “${d.title}”.`, "ok"); }
      catch (e) { toast(e.message, "err"); }
    });
    mk("Delete", async () => {
      if (!await confirmDialog("Delete score?", `“${p.title}” and its audio will be removed. This can't be undone.`, "Delete")) return;
      try { await api(`/api/projects/${p.id}`, { method: "DELETE" }); refreshLibrary(); toast("Deleted.", "ok"); }
      catch (e) { toast(e.message, "err"); }
    }, "danger");
    card.style.cursor = "pointer";
    card.onclick = () => openProject(p.id);
    grid.append(card);
  });
}

/* ---------- exports + project actions ---------- */
async function exportFmt(fmt) {
  try {
    toast("Preparing export…");
    const blob = await api(`/api/projects/${S.pid}/export?format=${fmt}`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${$("prjTitle").value}.${fmt === "mid" ? "mid" : fmt === "pdf" ? "pdf" : "musicxml"}`;
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (e) { toast("Export failed: " + e.message, "err"); }
}
function closeMenus() { $("exportMenu").hidden = true; $("overMenu").hidden = true;
  $("exportBtn").setAttribute("aria-expanded", "false"); $("overBtn").setAttribute("aria-expanded", "false"); }
function toggleMenu(btn, menu) {
  const open = menu.hidden;
  closeMenus();
  menu.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
}
async function rerun(density, sameSettings) {
  closeMenus();
  try {
    const d = await api(`/api/projects/${S.pid}/rerun`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sameSettings ? {} : { density }) });
    toast(sameSettings ? "Re-transcribing…" : `Re-arranging (${density})…`);
    openProgress(d.job_id, $("prjTitle").value, false);
  } catch (e) { toast("Re-run failed: " + e.message, "err"); }
}

/* ---------- wiring ---------- */
function init() {
  loadConfig(); refreshRecent(); loadSamples();
  const dz = $("dz"), fi = $("fileInput");
  $("dzBrowse").onclick = (e) => { e.stopPropagation(); fi.click(); };
  dz.onclick = () => fi.click();
  dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fi.click(); } };
  fi.onchange = () => setFile(fi.files[0]);
  $("fileClear").onclick = () => { fi.value = ""; setFile(null); };
  ["dragover", "dragenter"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) setFile(f); });
  document.querySelectorAll('input[name=mode]').forEach((r) => r.addEventListener("change", () => setFile(S.file)));
  $("startBtn").onclick = startUpload;
  $("recBtn").onclick = toggleRecord;
  $("midiBtn").onclick = () => $("midiInput").click();
  $("midiInput").onchange = () => { importMidi($("midiInput").files[0]); $("midiInput").value = ""; };
  // studio
  $("prjTitle").addEventListener("change", async () => {
    const t = $("prjTitle").value.trim();
    if (!t || !S.pid) { $("prjTitle").value = S.proj ? S.proj.title : ""; return; }
    try { await api(`/api/projects/${S.pid}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: t }) });
      S.proj.title = t; $("crumbSub").textContent = t; toast("Renamed.", "ok"); }
    catch (e) { toast(e.message, "err"); $("prjTitle").value = S.proj.title; }
  });
  $("tabScore").onclick = () => setTab("score");
  $("tabRoll").onclick = () => setTab("roll");
  $("followBtn").onclick = () => { S.follow = !S.follow; $("followBtn").setAttribute("aria-pressed", S.follow); };
  $("chordsBtn").onclick = () => { S.showChords = !S.showChords; applyDisplayToggles(); };
  $("fingerBtn").onclick = () => { S.showFing = !S.showFing; applyDisplayToggles(); };
  const ZOOMS = [50, 75, 100, 125, 150, 200];
  const setZoom = (z) => { S.zoom = z; $("zoomPct").textContent = z + "%";
    const svg = $("scoreWrap").querySelector("svg"); if (svg) svg.style.width = z + "%"; };
  $("zoomIn").onclick = () => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(S.zoom) + 1)]);
  $("zoomOut").onclick = () => setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(S.zoom) - 1)]);
  $("zoomPct").onclick = () => setZoom(100);
  $("handFilter").onchange = () => { S.handFilter = $("handFilter").value; applyHandFilter(); renderRoll(); };
  $("exportBtn").onclick = (e) => { e.stopPropagation(); toggleMenu($("exportBtn"), $("exportMenu")); };
  $("overBtn").onclick = (e) => { e.stopPropagation(); toggleMenu($("overBtn"), $("overMenu")); };
  document.addEventListener("click", (e) => { if (!e.target.closest(".menuwrap")) closeMenus(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeMenus(); closeModal(); } });
  document.querySelectorAll("#exportMenu [data-exp]").forEach((b) => b.onclick = () => { closeMenus(); exportFmt(b.dataset.exp); });
  document.querySelectorAll("#overMenu [data-rerun]").forEach((b) => b.onclick = () => rerun(b.dataset.rerun, false));
  $("mRetranscribe").onclick = () => rerun(null, true);
  $("mDuplicate").onclick = async () => { closeMenus();
    try { const d = await api(`/api/projects/${S.pid}/duplicate`, { method: "POST" }); toast(`Duplicated as “${d.title}”.`, "ok"); }
    catch (e) { toast(e.message, "err"); } };
  $("mDelete").onclick = async () => { closeMenus();
    if (!await confirmDialog("Delete this score?", "The score and its audio will be removed. This can't be undone.", "Delete")) return;
    try { await api(`/api/projects/${S.pid}`, { method: "DELETE" });
      S.pid = null; S.proj = null; S.score = null; S.t = 0; S.selection.clear();
      $("saveState").textContent = "No score open."; $("crumbSub").textContent = "";
      stop(); go("library"); toast("Deleted.", "ok"); }
    catch (e) { toast(e.message, "err"); } };
  // panel tabs
  const ptabs = [["ptabEdit", "panelEdit"], ["ptabAssist", "panelAssist"], ["ptabDetails", "panelDetails"]];
  ptabs.forEach(([b, p]) => $(b).onclick = () => {
    ptabs.forEach(([b2, p2]) => { $(b2).setAttribute("aria-selected", b2 === b); $(p2).hidden = p2 !== p; });
  });
  // edit
  $("applyEdit").onclick = async () => {
    const ids = [...S.selection]; if (!ids.length) return;
    const patch = { pitch: +$("ePitch").value, start_sec: +$("eStart").value,
      end_sec: +$("eEnd").value, velocity: +$("eVel").value, hand: $("eHand").value };
    const fg = $("eFinger").value;
    patch.finger = fg === "" ? null : +fg;
    for (const id of ids) await noteOp({ op: "update", id, patch });
  };
  $("addNote").onclick = () => {
    const t = S.t || 0;
    noteOp({ op: "add", patch: { pitch: +($("ePitch").value || 60), start_sec: t,
      end_sec: t + 0.5, velocity: +($("eVel").value || 80), hand: $("eHand").value } });
  };
  $("delSel").onclick = async () => {
    if (!S.selection.size) return;
    if (S.selection.size > 12 && !await confirmDialog("Delete notes?", `${S.selection.size} notes will be removed. You can undo this.`, "Delete")) return;
    noteOp({ op: "delete", ids: [...S.selection] });
  };
  $("trUp").onclick = () => noteOp({ op: "transpose", ids: [...S.selection], semitones: 1 });
  $("trDown").onclick = () => noteOp({ op: "transpose", ids: [...S.selection], semitones: -1 });
  $("undoBtn").onclick = () => noteOp({ op: "undo" });
  $("redoBtn").onclick = () => noteOp({ op: "redo" });
  // assistant
  $("sendAsk").onclick = ask;
  $("ask").addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
  $("applyAction").onclick = applyPending;
  $("dismissAction").onclick = () => { $("actionCard").hidden = true; S.pendingAction = null; };
  // library
  $("libSearch").oninput = paintLibrary;
  $("libSort").onchange = paintLibrary;
  // dock
  $("play").onclick = () => (S.playing ? stop() : play());
  $("stopBtn").onclick = () => { stop(); seek(0); };
  $("back5").onclick = () => seek(S.t - 5);
  $("fwd5").onclick = () => seek(S.t + 5);
  $("seek").addEventListener("pointerdown", () => { S.scrubbing = true; });
  $("seek").addEventListener("pointerup", () => { S.scrubbing = false; seek(($("seek").value / 1000) * dur()); });
  $("seek").addEventListener("input", () => { if (S.scrubbing) { S.t = ($("seek").value / 1000) * dur(); updateTime(); updateCursor(); } });
  const src = (which) => { S.source = which;
    $("srcPiano").setAttribute("aria-pressed", which === "piano");
    $("srcOrig").setAttribute("aria-pressed", which === "original");
    if (S.playing) { const t = S.t; stop(); S.t = t; play(); } };
  $("srcPiano").onclick = () => src("piano");
  $("srcOrig").onclick = () => {
    if (!S.hasAudio) { toast("This score has no playable recording — piano playback only.", "err"); return; }
    src("original");
  };
  $("speed").oninput = () => { $("speedV").textContent = $("speed").value + "%"; S.speed = $("speed").value / 100;
    if (S.playing && S.source === "original") $("orig").playbackRate = S.speed; };
  $("loopBtn").onclick = toggleLoop;
  ["muteL", "muteR", "gainL", "gainR"].forEach((id) => $(id).oninput = applyGains);
  $("muteL").onchange = $("muteR").onchange = () => { if (S.playing && S.source === "piano") { const t = S.t; stopVoices(); ensureCtx(); applyGains(); schedulePiano(t); S.playing = true; } };
  $("orig").addEventListener("loadedmetadata", updateTime);
  // top
  $("shortcutsBtn").onclick = () => openModal(SHORTCUTS_HTML);
  // keyboard
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|SELECT|TEXTAREA/.test((document.activeElement || {}).tagName || "");
    if (e.key === "?" && !typing) { openModal(SHORTCUTS_HTML); return; }
    if (e.key === "Escape" && !typing) { if (S.selection.size && S.view === "studio") { S.selection.clear(); updateSelUI(); renderRoll(); } return; }
    if (typing || S.view !== "studio" || !S.score) return;
    if (e.code === "Space") { e.preventDefault(); S.playing ? stop() : play(); }
    else if (e.key === "ArrowLeft") seek(S.t - 5);
    else if (e.key === "ArrowRight") seek(S.t + 5);
    else if (e.key === "1") setTab("score");
    else if (e.key === "2") setTab("roll");
    else if ((e.key === "Delete" || e.key === "Backspace") && S.selection.size) { e.preventDefault(); noteOp({ op: "delete", ids: [...S.selection] }); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); noteOp({ op: "undo" }); }
    else if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) { e.preventDefault(); noteOp({ op: "redo" }); }
    else if (e.key.toLowerCase() === "l") toggleLoop();
    else if (e.key.toLowerCase() === "f") $("followBtn").click();
  });
  updateLoopBtn(); setPlayIcon(); updateTime();
}
document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();


