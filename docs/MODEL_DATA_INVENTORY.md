# Model & Data Inventory (Milestone A)

Verify code, weight, and dataset licenses separately before any release.
Nothing below grants a license; links are provenance, not permission.

## 1. Shipped in this slice (baselines)

| ID | Kind | Source | License | Role |
|---|---|---|---|---|
| `baseline-piano/0.1.0` | DSP (STFT+onset, NumPy/SciPy) | authored here (`audio/models.py`) | project license (MIT) | piano transcription baseline |
| `baseline-arranger/0.1.0` | DSP (melody/bass/chroma/beats) | authored here | MIT | arrangement baseline |
| synth fixtures | generated WAV+MIDI | `scripts/make_fixtures.py` (NumPy+mido) | MIT, no third-party audio | regression + metric tests |

No pretrained weights ship in the slice. No user uploads used for training.

## 2. Evaluated / planned (not shipped; replaceable behind `Transcriber`)

| Candidate | Task | Why considered | License watch-outs |
|---|---|---|---|
| Kong et al. piano transcription (Onsets & Frames, PyTorch) | piano AMT | strong public piano baseline; onset/offset/velocity/pedal heads | code MIT; check weight terms + MAESTRO dataset terms |
| MAESTRO dataset (Google/Magenta) | piano eval/train | aligned audio+MIDI, work-level splits possible | research-use terms; verify commercial use before training |
| MAPS / SMD | piano eval | classic AMT benchmarks | academic licenses; verify each |
| Demucs / HTDemucs (Meta) | separation preprocessing only | strong open separator; measure downstream note-F1 delta | MIT code; weight terms; MUSDB terms if evaluated |
| BeatNet / madmom | beats/tempo | beat tracking for arrangement timing | check per-model license |
| Llama-class assistants (Meta) | constrained score-assistant actions | structured edits/explanations over stable IDs | platform + model terms; backend-only keys |

Rule: adopt only on measured full-path gain with no concealed regression
(chords, melody, repeats, sustains), per the release gate.

## 3. Data policy

- Splits by work + recording identity. Release set untouched.
- Training storage < 25 GB; keep production + rollback + few candidates.
- User uploads: never training/eval without explicit permission.
