# Competitive notes: SongScription vs Piano Scribe

Researched 2026-10-26 from songscription.ai (homepage, FAQ, model blog
post, competitor comparison). This is a product/UX study, not a copy of
their code or models.

## What SongScription does well

- **Instrument-first models.** Piano is the flagship; other instruments get
  dedicated models. Piano accuracy is the most mature; anything else ships
  labeled beta. Honest per-instrument framing.
- **Model pipeline (their words):** neural nets trained on aligned
  audio+MIDI pairs estimating, per pitch per frame, sounding/onset/loudness
  — then instrument separation for mixes, beats/key/chords/timing cleanup,
  then arrangement into a final score.
- **Arrangement levels + regeneration.** Piano covers at 5 difficulty
  levels, each a fresh arrangement, regenerable in ~30 s. Piano+vocal and
  lead sheets with chord symbols.
- **Correction-first editor.** Piano roll built for reviewing and fixing
  notes; they state outright this matters more than the last percent of
  model accuracy. Exports: PDF, MIDI, MusicXML, Guitar Pro.
- **Annie assistant.** Plain-words edits (transpose, key/time changes, fix
  notes, lyrics/dynamics/repeats, insert bars, fingering/chords display,
  compose intro/countermelody), everything undoable, "look over changes
  before export".
- **Fingering + chord symbols** on roll and score.
- **Input breadth.** MP3/WAV/FLAC/AAC/M4A, video files, YouTube/TikTok/
  Instagram links, in-browser recording, MIDI/MusicXML import, photo/PDF
  sheet music (OMR).
- **Zero-upload trial.** Example gallery (Bach, Chopin, Joplin…) and free
  30-second transcriptions, no card.
- **Honest accuracy page.** Best on clean single instruments; dense mixes
  need cleanup; models improve over time, re-run old files.

## What we adopted (0.2.0)

| Idea | Our version |
|---|---|
| Example gallery | `/api/samples` + "Try an example" cards on home |
| MIDI import | `POST /api/imports` → editable project, hands/chords/fingering derived |
| In-browser recording | MediaRecorder → WAV-encoded client-side → normal upload |
| Arrangement regeneration | Re-arrange at any density / re-transcribe → new score version, one Undo back |
| Chord symbols | Detected in both modes; on staff SVG, piano roll lane, MusicXML harmony |
| Fingering | Heuristic suggestions on staff, roll, MusicXML; toggleable; labeled suggested |
| Assistant display edits | Show/hide chords + fingering; whole-score transpose |
| FLAC input | Magic-byte sniff + miniaudio decode |
| Multi-level voicing | Density kept (Simple/Standard/Detailed) with regeneration instead of more levels |

## Deliberately not adopted

- **URL imports** (YouTube etc.): rights questions, heavy deps (yt-dlp +
  ffmpeg), network fragility. Roadmap only.
- **Other instruments, tabs, OMR, Guitar Pro:** out of product scope
  (piano-only MVP; no guitar tabs per spec).
- **Payments/tiers:** out of MVP scope.

## Model roadmap toward parity (gated by our eval, not by hype)

1. Replace DSP piano baseline with a neural Onsets-and-Frames-style model
   (per inventory candidates). Accept only on full-path gate improvement
   with no concealed chord/melody/repeat/sustain regression.
2. Add separation *measurement*: run arrangement with/without a separator,
   keep it only if note-F1 improves.
3. Real piano + mixed-song corpora + listening panel before any
   production-accuracy claim — same honesty bar SongScription sets.
