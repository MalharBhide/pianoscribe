/* Piano Scribe frontend (dependency-free).
 * Served as /app.js; typed source is app.ts (kept in sync; `tsc --noEmit` gate).
 */
(function () {
"use strict";
const $ = (id) => document.getElementById(id);
const S = {
  pid: null, score: null, svg: "", selection: new Set(), loop: null,
  playing: false, source: "piano", speed: 1, t: 0, raf: 0,
  actx: null, voices: [], playStartCtx: 0, playStartT: 0, pendingAction: null,
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.blob();
}

/* ---------- config / limits ---------- */
async function loadConfig() {
  try {
    const c = await api("/api/config");
    S.cfg = c;
    $("limits").textContent =
      `Limits: ${c.max_upload_mb} MB per file · up to ${c.max_duration_min} minutes · ` +
      `accepted: ${c.accepted.join(", ").toUpperCase()} · MP3 ${c.mp3_supported ? "supported" : "unavailable (WAV only)"}`;
  } catch (e) { $("limits").textContent = "Could not load server limits."; }
}

/* ---------- upload + progress ---------- */
async function upload(file) {
  $("upErr").textContent = "";
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("mode", $("mode").value);
  fd.append("density", $("density").value);
  fd.append("title", file.name.replace(/\.[^.]+$/, ""));
  let job;
  try { job = await api("/api/uploads", { method: "POST", body: fd }); }
  catch (e) { $("upErr").textContent = "Upload failed: " + e.message; return; }
  $("progress").style.display = "block";
  pollJob(job.job_id);
}
async function pollJob(jid) {
  try {
    const j = await api("/api/jobs/" + jid);
    $("progress").firstElementChild.style.width = Math.round((j.progress || 0) * 100) + "%";
    $("stage").textContent = `Status: ${j.status} — ${j.stage || ""} (${Math.round((j.progress || 0) * 100)}%)`;
    if (j.status === "done" && j.project_id) {
      $("stage").textContent = "Done. Opening score…";
      await openProject(j.project_id);
      $("progress").style.display = "none";
      refreshProjects();
      return;
    }
    if (j.status === "failed") {
      $("upErr").textContent = "Processing failed: " + (j.error || "unknown error");
      $("progress").style.display = "none";
      return;
    }
    setTimeout(() => pollJob(jid), 1000);
  } catch (e) { $("upErr").textContent = "Progress check failed: " + e.message; }
}

/* ---------- projects / score ---------- */
async function refreshProjects() {
  try {
    const d = await api("/api/projects");
    const ul = $("projects");
    ul.innerHTML = "";
    if (!d.projects.length) { ul.innerHTML = "<li class='muted'>No projects yet.</li>"; return; }
    d.projects.forEach((p) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.textContent = `${p.title} (${p.mode}) — ${new Date(p.updated_at * 1000).toLocaleString()}`;
      a.href = "#"; a.onclick = (e) => { e.preventDefault(); openProject(p.id); };
      li.appendChild(a); ul.appendChild(li);
    });
  } catch (e) {}
}
async function openProject(pid) {
  stop();
  const d = await api("/api/projects/" + pid);
  S.pid = pid; S.score = d.score; S.svg = d.svg; S.selection.clear(); S.loop = null; S.t = 0;
  $("projTitle").textContent = d.title;
  const n = d.score.notes.length;
  $("scoreMeta").textContent = `${n} notes · score v${d.score.version}`;
  $("modeBadge").innerHTML = d.mode === "arrangement"
    ? '<span class="pill arr">Song-to-piano arrangement <small>· melody + bass + harmony → piano</small></span>'
    : '<span class="pill">Piano transcription <small>· performed notes preserved</small></span>';
  $("procInfo").textContent = JSON.stringify(d.score.processing, null, 1);
  $("orig").src = d.audio_url;
  $("scoreWrap").innerHTML = d.svg;
  wireScore();
  updateSelUI();
  ["play", "stopBtn", "expMid", "expXml", "expPdf"].forEach((id) => $(id).disabled = false);
  updateTime();
}
function refreshScoreView(d) {
  S.score = d.score; S.svg = d.svg;
  S.selection = new Set([...S.selection].filter((id) => S.score.notes.some((x) => x.id === id)));
  $("scoreMeta").textContent = `${S.score.notes.length} notes · score v${S.score.version}`;
  $("scoreWrap").innerHTML = d.svg;
  wireScore(); updateSelUI(); updateTime();
}
function wireScore() {
  const wrap = $("scoreWrap");
  wrap.querySelectorAll(".notehead").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = el.getAttribute("data-note");
      if (e.shiftKey) { S.selection.has(id) ? S.selection.delete(id) : S.selection.add(id); }
      else { S.selection = new Set([id]); }
      seek(parseFloat(el.getAttribute("data-time") || "0"));
      updateSelUI();
    });
  });
  wrap.querySelectorAll(".measure-hit").forEach((el) => {
    el.addEventListener("click", (e) => {
      const t0 = parseFloat(el.getAttribute("data-t0") || "0");
      if (e.shiftKey) { // select measure notes
        const t1 = parseFloat(el.getAttribute("data-t1") || "0");
        S.selection = new Set(S.score.notes.filter((x) => x.start_sec >= t0 - 1e-6 && x.start_sec < t1 - 1e-6).map((x) => x.id));
        updateSelUI();
      } else seek(t0 + 0.001);
    });
  });
  // cursor line
  const svg = wrap.querySelector("svg");
  if (svg) {
    const ln = document.createElementNS("http://www.w3.org/2000/svg", "line");
    ln.setAttribute("id", "cursorLine"); ln.setAttribute("x1", "-10");
    ln.setAttribute("x2", "-10"); ln.setAttribute("y1", "0"); ln.setAttribute("y2", "0");
    svg.appendChild(ln);
  }
  updateCursor();
}
function updateSelUI() {
  document.querySelectorAll(".notehead").forEach((el) => {
    el.classList.toggle("sel", S.selection.has(el.getAttribute("data-note")));
  });
  const sel = S.score ? S.score.notes.filter((x) => S.selection.has(x.id)) : [];
  $("selInfo").textContent = sel.length ? `${sel.length} note(s) selected.` :
    "Click a note in the score to select it (Shift-click adds; Shift-click a measure selects it).";
  ["applyEdit", "delSel", "trUp", "trDown"].forEach((id) => $(id).disabled = !sel.length);
  if (sel.length === 1) {
    const x = sel[0];
    $("ePitch").value = x.pitch; $("eStart").value = x.start_sec.toFixed(2);
    $("eEnd").value = x.end_sec.toFixed(2); $("eVel").value = x.velocity; $("eHand").value = x.hand;
  }
  updateLoopBtn();
}

/* ---------- playback ---------- */
function dur() {
  if (!S.score) return 0;
  const a = $("orig").duration;
  const n = S.score.notes.reduce((m, x) => Math.max(m, x.end_sec), 0);
  return Math.max(n, isFinite(a) && a > 0 ? a : 0, 1);
}
function fmt(t) {
  t = Math.max(0, t); const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
function updateTime() { $("time").textContent = `${fmt(S.t)} / ${fmt(dur())}`; }
function ensureCtx() {
  if (!S.actx) S.actx = new (window.AudioContext || window.webkitAudioContext)();
  if (S.actx.state === "suspended") S.actx.resume();
  return S.actx;
}
function pianoTone(ctx, dest, pitch, t0, durS, vel) {
  const f = 440 * Math.pow(2, (pitch - 69) / 12);
  const g = ctx.createGain();
  const peak = 0.22 * (vel / 100);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(Math.max(0.001, peak), t0 + 0.012);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + Math.max(0.09, durS));
  [[1, 1], [2, 0.35], [3, 0.18], [4.01, 0.08]].forEach(([m, a]) => {
    const o = ctx.createOscillator();
    o.type = m === 1 ? "triangle" : "sine"; o.frequency.value = f * m;
    const og = ctx.createGain(); og.gain.value = a;
    o.connect(og); og.connect(g); o.start(t0); o.stop(t0 + Math.max(0.1, durS) + 0.05);
  });
  g.connect(dest);
}
function schedulePiano(fromT) {
  const ctx = ensureCtx();
  const t0 = ctx.currentTime + 0.05;
  S.playStartCtx = t0; S.playStartT = fromT;
  const muteL = $("muteL").checked, muteR = $("muteR").checked;
  S.score.notes.forEach((x) => {
    if ((x.hand === "L" && muteL) || (x.hand === "R" && muteR)) return;
    if (x.end_sec <= fromT) return;
    const start = Math.max(x.start_sec, fromT);
    pianoTone(ctx, ctx.destination, x.pitch, t0 + (start - fromT) / S.speed,
      (x.end_sec - start) / S.speed, x.velocity);
  });
}
function stopVoices() {
  if (S.actx) { S.actx.close().catch(() => {}); S.actx = null; }
  S.voices = [];
}
function play() {
  if (!S.score) return;
  S.source = $("src").value; S.speed = $("speed").value / 100;
  ensureCtx();
  if (S.source === "original") {
    const a = $("orig");
    a.playbackRate = S.speed; a.currentTime = Math.min(S.t, dur() - 0.05);
    a.play().catch((e) => { $("upErr").textContent = "Original audio playback failed: " + e.message; });
  } else schedulePiano(S.t);
  S.playing = true; $("play").textContent = "⏸ Pause";
  cancelAnimationFrame(S.raf);
  const tick = () => {
    if (!S.playing) return;
    if (S.source === "original") S.t = $("orig").currentTime;
    else S.t = S.playStartT + (S.actx.currentTime - S.playStartCtx) * S.speed;
    if (S.loop && S.t >= S.loop.b) { seek(S.loop.a); if (S.playing) { if (S.source === "piano") { stopVoices(); ensureCtx(); schedulePiano(S.t); } } }
    if (S.t >= dur()) { stop(); return; }
    updateTime(); updateCursor();
    S.raf = requestAnimationFrame(tick);
  };
  tick();
}
function stop() {
  S.playing = false;
  cancelAnimationFrame(S.raf);
  $("play").textContent = "▶ Play";
  try { $("orig").pause(); } catch (e) {}
  stopVoices();
}
function seek(t) {
  S.t = Math.max(0, Math.min(t, dur()));
  if (S.playing) {
    if (S.source === "original") $("orig").currentTime = S.t;
    else { stopVoices(); ensureCtx(); schedulePiano(S.t); }
  }
  updateTime(); updateCursor();
}
function updateCursor() {
  const ln = document.getElementById("cursorLine");
  if (!ln) return;
  const m = [...document.querySelectorAll(".measure-hit")].find((el) =>
    S.t >= parseFloat(el.getAttribute("data-t0")) && S.t < parseFloat(el.getAttribute("data-t1")));
  if (!m) { ln.setAttribute("x1", -10); ln.setAttribute("x2", -10); return; }
  const t0 = parseFloat(m.getAttribute("data-t0")), t1 = parseFloat(m.getAttribute("data-t1"));
  const frac = Math.max(0, Math.min(1, (S.t - t0) / Math.max(1e-6, t1 - t0)));
  const x = parseFloat(m.getAttribute("x")) + frac * parseFloat(m.getAttribute("width"));
  ln.setAttribute("x1", x); ln.setAttribute("x2", x);
  ln.setAttribute("y1", m.getAttribute("y")); ln.setAttribute("y2", parseFloat(m.getAttribute("y")) + parseFloat(m.getAttribute("height")));
}
function updateLoopBtn() {
  $("loopBtn").textContent = S.loop ? `Loop: ${S.loop.a.toFixed(1)}–${S.loop.b.toFixed(1)}s` : "Loop: off";
  $("loopBtn").setAttribute("aria-pressed", S.loop ? "true" : "false");
}

/* ---------- editing ---------- */
async function noteOp(body) {
  $("edErr").textContent = ""; $("edOk").textContent = "";
  try {
    const d = await api(`/api/projects/${S.pid}/notes`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    refreshScoreView(d);
    $("edOk").textContent = "Saved.";
  } catch (e) { $("edErr").textContent = e.message; }
}

/* ---------- assistant ---------- */
async function ask() {
  const text = $("ask").value.trim();
  if (!text || !S.pid) return;
  $("assistReply").textContent = "Thinking…";
  $("actionCard").style.display = "none";
  S.pendingAction = null;
  try {
    const d = await api(`/api/projects/${S.pid}/assistant`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, selection: [...S.selection] }) });
    $("assistReply").textContent = d.reply;
    if (d.actions && d.actions.length) {
      const a = d.actions[0];
      if (a.type === "loop") { // playback-only: apply locally
        const sel = S.score.notes.filter((x) => S.selection.has(x.id));
        const r = sel.length ? [Math.min(...sel.map((x) => x.start_sec)), Math.max(...sel.map((x) => x.end_sec))]
          : [Math.max(0, S.t - 2), Math.min(dur(), S.t + 6)];
        S.loop = { a: r[0], b: Math.max(r[0] + 0.5, r[1]) };
        $("speed").value = Math.round(a.speed * 100);
        $("speedV").textContent = $("speed").value + "%";
        S.speed = a.speed; updateLoopBtn();
      } else {
        S.pendingAction = a;
        $("actionText").textContent = "Proposed: " + JSON.stringify(a) + " — applies to the saved score, undo available.";
        $("actionCard").style.display = "block";
      }
    }
  } catch (e) { $("assistReply").textContent = "Assistant error: " + e.message; }
}
async function applyPending() {
  if (!S.pendingAction) return;
  try {
    const d = await api(`/api/projects/${S.pid}/assistant/apply`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: S.pendingAction }) });
    $("assistReply").textContent = d.message || (d.ok ? "Applied." : "Not applied.");
    if (d.ok && d.score) refreshScoreView(d);
  } catch (e) { $("assistReply").textContent = "Apply failed: " + e.message; }
  $("actionCard").style.display = "none"; S.pendingAction = null;
}

/* ---------- exports ---------- */
async function exportFmt(fmt) {
  try {
    const blob = await api(`/api/projects/${S.pid}/export?format=${fmt}`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${S.pid}.${fmt === "mid" ? "mid" : fmt === "pdf" ? "pdf" : "musicxml"}`;
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (e) { $("upErr").textContent = "Export failed: " + e.message; }
}

/* ---------- wiring ---------- */
function init() {
  loadConfig(); refreshProjects();
  const drop = $("drop"), file = $("file");
  $("browse").onclick = () => file.click();
  file.onchange = () => upload(file.files[0]);
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
  $("play").onclick = () => (S.playing ? (stop(), updateTime(), updateCursor()) : play());
  $("stopBtn").onclick = () => { stop(); seek(0); };
  $("src").onchange = () => { const was = S.playing; stop(); if (was) play(); };
  $("speed").oninput = () => { $("speedV").textContent = $("speed").value + "%"; S.speed = $("speed").value / 100; if (S.playing && S.source === "original") $("orig").playbackRate = S.speed; };
  $("muteL").onchange = $("muteR").onchange = () => { if (S.playing && S.source === "piano") { const t = S.t; stopVoices(); ensureCtx(); schedulePiano(t); S.playing = true; } };
  $("loopBtn").onclick = () => {
    if (S.loop) { S.loop = null; }
    else {
      const sel = S.score ? S.score.notes.filter((x) => S.selection.has(x.id)) : [];
      S.loop = sel.length ? { a: Math.min(...sel.map((x) => x.start_sec)), b: Math.max(...sel.map((x) => x.end_sec)) }
        : { a: Math.max(0, S.t - 2), b: Math.min(dur(), S.t + 8) };
    }
    updateLoopBtn();
  };
  $("orig").addEventListener("loadedmetadata", updateTime);
  $("applyEdit").onclick = () => {
    const ids = [...S.selection]; if (!ids.length) return;
    const patch = { pitch: +$("ePitch").value, start_sec: +$("eStart").value,
      end_sec: +$("eEnd").value, velocity: +$("eVel").value, hand: $("eHand").value };
    (async () => {
      for (const id of ids) await noteOp({ op: "update", id, patch });
    })();
  };
  $("addNote").onclick = () => {
    const t = S.t || 0;
    noteOp({ op: "add", patch: { pitch: +($("ePitch").value || 60), start_sec: t,
      end_sec: t + 0.5, velocity: +($("eVel").value || 80), hand: $("eHand").value } });
  };
  $("delSel").onclick = () => noteOp({ op: "delete", ids: [...S.selection] });
  $("trUp").onclick = () => noteOp({ op: "transpose", ids: [...S.selection], semitones: 1 });
  $("trDown").onclick = () => noteOp({ op: "transpose", ids: [...S.selection], semitones: -1 });
  $("undoBtn").onclick = () => noteOp({ op: "undo" });
  $("redoBtn").onclick = () => noteOp({ op: "redo" });
  $("sendAsk").onclick = ask;
  $("ask").addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
  $("applyAction").onclick = applyPending;
  $("dismissAction").onclick = () => { $("actionCard").style.display = "none"; S.pendingAction = null; };
  $("expMid").onclick = () => exportFmt("mid");
  $("expXml").onclick = () => exportFmt("musicxml");
  $("expPdf").onclick = () => exportFmt("pdf");
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName) && S.score) {
      e.preventDefault(); S.playing ? stop() : play();
    }
  });
}
document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();
