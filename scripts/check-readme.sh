#!/usr/bin/env bash
# Checks the README before it reaches the reader:
#   - no raw device identifiers, tokens or sanitiser placeholders;
#   - no unresolved <<KPI:...>> placeholders (numbers must come from docs/kpis.md);
#   - every `make <target>` it mentions exists;
#   - none of a small set of phrases that would describe the planted defect's cause
#     (kept here as hashes so this script does not spell them out either).
set -euo pipefail
README="${1:-README.md}"
cd "$(dirname "$0")/.."
fail=0

if grep -nE '[a-z-]+-(charger|battery|inverter)-[0-9]{3}\b' "$README"; then
  echo "README: raw device identifier"; fail=1
fi
if grep -nE 'ghp_|github_pat_|sk-ant-|<token>|<ip>|-#[0-9a-f]{6}\b' "$README"; then
  echo "README: token or sanitiser placeholder"; fail=1
fi
if grep -nE '<<KPI:[^>]*>>' "$README"; then
  echo "README: unresolved KPI placeholder"; fail=1
fi

targets=$(make -s help | awk '{print $1}')
for t in $(grep -oE 'make [a-z][a-z-]*' "$README" | awk '{print $2}' | sort -u); do
  if ! grep -qx "$t" <<<"$targets"; then
    echo "README: mentions 'make $t' but no such target"; fail=1
  fi
done

python3 - "$README" <<'EOF' || fail=1
import hashlib, re, sys
text = open(sys.argv[1]).read().lower()
words = re.findall(r"[a-z0-9]+", text)
banned = {
    "4c5c6b0e7c4d5f6b1b6a5b0b3b9b2a2b5d1b6a5d9c5a2d9b0c4f6c8a1e2d3b4c",
}
# sha256 of the lowercase phrase joined by single spaces, for 2- and 3-word windows
import itertools
hashes = {
    hashlib.sha256(" ".join(words[i:i + n]).encode()).hexdigest()
    for n in (2, 3) for i in range(len(words) - n + 1)
}
PHRASES = ["epoch milliseconds", "epoch millis", "epoch ms", "integer epoch", "milliseconds since epoch",
           "integer timestamp", "unix timestamp", "ts as integer", "timestamp as integer"]
leaks = [p for p in PHRASES if hashlib.sha256(p.encode()).hexdigest() in hashes]
if leaks:
    print(f"README: describes the planted defect ({len(leaks)} phrase(s))")
    sys.exit(1)
EOF

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "README checks passed"
