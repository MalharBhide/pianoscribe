/* Piano Scribe frontend — typed contracts shared with app.js.
 * app.js is dependency-free and hand-maintained; keep these in sync.
 * Gate (when Node exists): `tsc --noEmit app.ts` must pass with strict on.
 */
interface ScoreNote {
  id: string; pitch: number; start_sec: number; end_sec: number;
  velocity: number; hand: "L" | "R"; role: string; provenance: string;
  beat_onset: number | null; beat_dur: number | null;
  finger: number | null;
  evidence: Record<string, unknown>;
}
interface TempoMap { beats: number[]; bpm: number; beats_per_bar: number; }
interface ScoreChord {
  start_sec: number; end_sec: number; label: string; score: number; root: number;
}
interface Score {
  notes: ScoreNote[]; tempo: TempoMap; time_sig: string; title: string;
  mode: "piano" | "arrangement"; chords: ScoreChord[];
  raw_detections: unknown[];
  processing: Record<string, unknown>; version: number;
}
interface ProjectPayload {
  id: string; title: string; mode: string; audio_url: string;
  score: Score; svg: string;
}
interface ProjectRow {
  id: string; title: string; mode: string; note_count: number;
  duration_sec: number; updated_at: number;
}
interface Sample {
  id: string; title: string; file: string; blurb: string;
  mode: "piano" | "arrangement"; available: boolean; duration_sec: number | null;
}
interface Job {
  id: string; status: string; mode: string; density: string;
  progress: number; stage: string; error: string; project_id: string; attempts: number;
}
interface AssistAction { type: string; [k: string]: unknown; }
interface AssistReply { reply: string; actions: AssistAction[]; }
interface LoopRegion { a: number; b: number; }
type ViewName = "home" | "progress" | "studio" | "library";
interface AppState {
  view: ViewName;
  pid: string | null; score: Score | null;
  selection: Set<string>; tab: "score" | "roll";
  zoom: number; follow: boolean; showChords: boolean; showFing: boolean;
  loop: LoopRegion | null;
  playing: boolean; source: "piano" | "original"; speed: number; t: number;
  pendingAction: AssistAction | null;
}

function fmtT(t: number): string {
  t = Math.max(0, t || 0);
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}
function chordText(label: string): string {
  const PC = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  const Q: Record<string, string> = { maj: "", min: "m", dim: "dim", aug: "aug", "7": "7", maj7: "maj7", m7: "m7" };
  const m = /^(\d+):(\w+)$/.exec(label || "");
  if (!m || !(m[2] in Q)) return "";
  return PC[+m[1] % 12] + Q[m[2]];
}

export { fmtT, chordText };
export type { Score, ScoreNote, TempoMap, ScoreChord, ProjectPayload, ProjectRow, Sample, Job, AssistReply, LoopRegion, ViewName, AppState };
