/* Piano Scribe frontend — typed source of truth.
 * app.js is the hand-compiled equivalent served until `tsc` is available.
 * Gate (when Node exists): `tsc --noEmit app.ts` must pass with strict on.
 */
interface ScoreNote {
  id: string; pitch: number; start_sec: number; end_sec: number;
  velocity: number; hand: "L" | "R"; role: string; provenance: string;
  beat_onset: number | null; beat_dur: number | null;
  evidence: Record<string, unknown>;
}
interface TempoMap { beats: number[]; bpm: number; beats_per_bar: number; }
interface Score {
  notes: ScoreNote[]; tempo: TempoMap; time_sig: string; title: string;
  mode: "piano" | "arrangement"; raw_detections: unknown[];
  processing: Record<string, unknown>; version: number;
}
interface ProjectPayload {
  id: string; title: string; mode: string; audio_url: string;
  score: Score; svg: string;
}
interface AssistAction { type: string; [k: string]: unknown; }
interface AssistReply { reply: string; actions: AssistAction[]; }
interface LoopRegion { a: number; b: number; }
interface AppState {
  pid: string | null; score: Score | null;
  selection: Set<string>; loop: LoopRegion | null;
  playing: boolean; source: "piano" | "original"; speed: number; t: number;
  raf: number; actx: AudioContext | null;
  playStartCtx: number; playStartT: number;
  pendingAction: AssistAction | null;
}

const S: AppState = {
  pid: null, score: null, selection: new Set(), loop: null,
  playing: false, source: "piano", speed: 1, t: 0, raf: 0,
  actx: null, playStartCtx: 0, playStartT: 0, pendingAction: null,
};

function el(id: string): HTMLElement {
  const n = document.getElementById(id);
  if (!n) throw new Error(`missing element #${id}`);
  return n;
}
function fmt(t: number): string {
  t = Math.max(0, t);
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}

export { S, el, fmt };
export type { Score, ScoreNote, TempoMap, ProjectPayload, AssistReply, LoopRegion };
