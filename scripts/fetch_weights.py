#!/usr/bin/env python3
"""Fetch an optional neural checkpoint with hash verification.

URLs/hashes are REQUIRED arguments, copied by the operator from the
upstream release page — this script hardcodes none, so a stale or
typo'd URL can never silently fetch the wrong weights.

Writes: <model-dir>/checkpoint.pth + manifest.json {name, url, sha256,
upstream_package, fetched_at}. The adapter (neural_models.py) refuses to
run without both files.

NOT YET RUN: sandbox has no egress to weight hosts. First run happens on
a networked machine; record package version via --upstream-package.
Stdlib only.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))
from piano_scribe.neural_models import model_dir  # noqa: E402

MAX_BYTES = 1024 ** 3  # refuse absurd payloads (>1 GiB)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="checkpoint URL from upstream release page")
    ap.add_argument("--sha256", required=True, help="expected hex digest from upstream")
    ap.add_argument("--model-dir", default=None)
    ap.add_argument("--upstream-package", default="unrecorded",
                    help="e.g. piano-transcription==1.0.3 (from pip freeze)")
    ap.add_argument("--name", default="kong-piano")
    args = ap.parse_args()
    dest = args.model_dir or model_dir()
    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, "checkpoint.pth.part")
    final = os.path.join(dest, "checkpoint.pth")
    print(f"GET {args.url}", flush=True)
    h = hashlib.sha256()
    total = 0
    req = urllib.request.Request(args.url, headers={"User-Agent": "pianoscribe-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise SystemExit(f"refusing payload over {MAX_BYTES} bytes")
            h.update(chunk)
            f.write(chunk)
            print(f"  {total / 1e6:.1f} MB", flush=True)
    digest = h.hexdigest()
    if digest != args.sha256.lower():
        os.remove(tmp)
        raise SystemExit(f"SHA256 MISMATCH:\n got      {digest}\n expected {args.sha256.lower()}")
    os.replace(tmp, final)
    manifest = {"name": args.name, "url": args.url, "sha256": digest,
                "upstream_package": args.upstream_package,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "checkpoint": os.path.basename(final)}
    with open(os.path.join(dest, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"verified {total / 1e6:.1f} MB -> {final}")


if __name__ == "__main__":
    main()
