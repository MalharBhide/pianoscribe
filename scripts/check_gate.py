#!/usr/bin/env python3
"""Compare reports/eval_<tag>.json against docs/release_gate.yaml.

Exit 0 when every gate passes; exit 1 with named failures otherwise.
Thresholds are requirements under test, not accuracy claims.
Usage: python scripts/check_gate.py [--tag baseline]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load_gate():
    import yaml
    with open(os.path.join(ROOT, "docs", "release_gate.yaml")) as f:
        return yaml.safe_load(f)


def check(tag):
    gate = load_gate()
    path = os.path.join(ROOT, "reports", f"eval_{tag}.json")
    with open(path) as f:
        rep = json.load(f)
    fails, passes = [], []
    by_mode = {}
    for r in rep["results"]:
        by_mode.setdefault(r["mode"], []).append(r)
    if rep.get("failures"):
        fails.append(f"{len(rep['failures'])} eval run(s) crashed")
    # piano gates: worst recording must clear floors (no hiding behind averages)
    for r in by_mode.get("piano", []):
        f_ = r["final"]
        g = gate["piano"]
        wid = f"{r['fixture']}"
        for key, cond, msg in [
            ("note_f1", f_["f1"] >= g["note_f1_min"], f"F1 {f_['f1']} < {g['note_f1_min']}"),
            ("precision", f_["precision"] >= g["note_precision_min"], f"prec {f_['precision']} < {g['note_precision_min']}"),
            ("onset_p95", f_["onset_err_sec"]["p95"] * 1000 <= g["onset_p95_ms_max"], "onset p95 over ceiling"),
            ("repeats", f_["repeat_rate"] >= g["repeat_preservation_min"], f"repeat {f_['repeat_rate']} < {g['repeat_preservation_min']}"),
            ("chords", f_["chord_recovery"] >= g["chord_recovery_min"], f"chord {f_['chord_recovery']} < {g['chord_recovery_min']}"),
            ("clutter", f_["extra_per_sec"] <= g["max_extra_per_sec"], f"extras/s {f_['extra_per_sec']} > {g['max_extra_per_sec']}"),
        ]:
            (passes if cond else fails).append(f"piano/{wid}/{key}" + ("" if cond else f": {msg}"))
        rt = r.get("midi_roundtrip", {})
        if rt.get("onset_recall", 0) >= 0.99:
            passes.append(f"piano/{wid}/export_match")
        else:
            fails.append(f"piano/{wid}/export_match: MIDI onset recall {rt.get('onset_recall')}")
    for r in by_mode.get("arrangement", []):
        f_ = r["final"]
        g = gate["arrangement"]
        wid = f"{r['fixture']}/{r['density']}"
        # Arrangements are judged on non-harmonic clutter: extra notes that
        # are chord tones of the local reference harmony are valid voicings.
        for key, cond, msg in [
            ("melody_f1", f_["melody_f1"] >= g["melody_f1_min"], f"melody F1 {f_['melody_f1']} < {g['melody_f1_min']}"),
            ("clutter", f_["nonharmonic_extra_per_sec"] <= g["max_extra_per_sec"],
             f"non-harmonic extras/s {f_['nonharmonic_extra_per_sec']} > {g['max_extra_per_sec']}"),
            ("onset_p95", f_["onset_err_sec"]["p95"] * 1000 <= g["onset_p95_ms_max"], "onset p95 over ceiling"),
        ]:
            (passes if cond else fails).append(f"arrangement/{wid}/{key}" + ("" if cond else f": {msg}"))
    print(f"tag={tag} tol={rep.get('tol_sec')} passes={len(passes)} fails={len(fails)}")
    for x in fails:
        print("FAIL", x)
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="baseline")
    sys.exit(check(ap.parse_args().tag))
