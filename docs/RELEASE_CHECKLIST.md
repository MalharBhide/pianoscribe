# Release checklist (0.1.0 slice)

- [x] Spec, eval protocol, model/data inventory written (Milestone A).
- [x] Upload → progress → score → playback → edit → export → reopen works
      for both modes (E2E probe `/tmp/ps_e2e_probe.py` — rerun on release).
- [x] `pytest backend/tests`: 28 passed.
- [x] `eval.py --tag baseline` reproduces `reports/eval_baseline.json`.
- [x] `check_gate.py --tag baseline`: 40/40 pass (synth-fixture thresholds).
- [x] Duration ladder 5 s → 30 min: no drift, all anchors found.
- [x] Exports + playback render from the saved score version
      (`X-Score-Version` header test; MIDI onset recall 1.00).
- [x] Limits configurable + displayed verbatim (`/api/config` test).
- [x] Model name/version/config + processing version on every score.
- [x] Rollback: redeploy previous image tag; scores re-render from stored
      JSON (model-independent). Drill: `docker compose up --build` with the
      prior tag and reopen an old project — must render identically.
- [x] Monitoring: `/health`, worker stdout stage/progress, per-job
      `timing.json` artifacts, eval reports checked in.
- [x] Known limitations + supported inputs published; no mode called
      production-ready.
- [ ] Real-recording piano corpus evaluated (blocks production claims).
- [ ] Real mixed-song corpus + listening panel run (blocks arrangement claims).
- [ ] Rollback drill executed against a second release (one release exists).
- [ ] `docker compose up --build` verified (daemon unavailable in this
      session; Dockerfile uses the same pinned deps proven on host).
