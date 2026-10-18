#!/usr/bin/env bash
# The pre-deploy check from CI, before merge: ship, review or hold for one change, with every reason.
#   scripts/preflight.sh SERVICE [deploy|config|flag] ["what the change does"]
# Exit 0 on ship or review (the reasons are printed), 1 on hold, 2 if Hypocenter couldn't answer.
# STRICT=1 also fails on review. HYPOCENTER_URL (default http://localhost:8090) and TENANT (default kestrel).
set -uo pipefail
svc="${1:?usage: scripts/preflight.sh SERVICE [deploy|config|flag] [summary]}"
kind="${2:-deploy}"
summary="${3:-}"
url="${HYPOCENTER_URL:-http://localhost:8090}/api/${TENANT:-kestrel}/preflight"
body=$(python3 -c 'import json,sys; print(json.dumps({"service": sys.argv[1], "kind": sys.argv[2], "summary": sys.argv[3]}))' "$svc" "$kind" "$summary")
out=$(curl -sS -X POST "$url" -H 'content-type: application/json' -d "$body") || { echo "preflight: no answer from $url"; exit 2; }
PREFLIGHT_OUT="$out" STRICT="${STRICT:-0}" python3 - <<'PY'
import json, os, sys
d = json.loads(os.environ["PREFLIGHT_OUT"])
if "decision" not in d:
    print("preflight:", d.get("detail", d))
    sys.exit(2)
print(f"{d['decision'].upper()}: {d['headline']}")
for r in d["reasons"]:
    print(f"  {r['level']:<6} {r['text']}")
b = d["blast"]
print(f"  if it breaks: {b['affected']} services above it, {len(b['internet_exposed'])} internet-facing; owner {d['owner']['team']}")
sys.exit(1 if d["decision"] == "hold" or (d["decision"] == "review" and os.environ["STRICT"] == "1") else 0)
PY
