#!/usr/bin/env bash
# One command for a judge: build, start, run every test, then the product's own checks on scratch graphs.
#   make judge-check          (or: bash scripts/judge-check.sh)
# Needs Docker only. No API key, no token, nothing remote is touched; the demo graphs are left alone.
set -uo pipefail
cd "$(dirname "$0")/.."

results=()
step() { results+=("$1|$2|$3"); printf '%s  %s: %s\n' "$1" "$2" "$3"; }

echo "Hypocenter judge-check, $(git rev-parse --short HEAD 2>/dev/null || echo 'no git') on $(date -u '+%Y-%m-%d %H:%M UTC')"

if docker compose build -q app >/dev/null 2>&1; then step PASS "build" "app image built"; else step FAIL "build" "docker compose build failed"; fi

docker compose up -d >/dev/null 2>&1
up=""
for _ in $(seq 1 90); do
  if curl -sf http://localhost:8090/api/engine >/dev/null; then up=1; break; fi
  sleep 2
done
if [ -n "$up" ]; then
  info=$(curl -s http://localhost:8090/api/engine | python3 -c 'import json,sys; d=json.load(sys.stdin); print("FalkorDB %s, %s nodes in %d graphs" % (d["version"], format(d["nodes"], ","), len(d["graphs"])))' 2>/dev/null)
  step PASS "app" "serving on http://localhost:8090${info:+ ($info)}"
else
  step FAIL "app" "nothing answered on :8090 within 3 minutes"
fi

tests=$(docker compose run --rm app pytest -q 2>&1 | tail -1)
if echo "$tests" | grep -qE '^[0-9]+ passed' && ! echo "$tests" | grep -qE 'failed|error'; then
  step PASS "tests" "$tests"
else
  step FAIL "tests" "$tests"
fi

checks=$(docker compose run --rm -e FAULTLINE_GRAPH_PREFIX=judgecheck_ app python -m faultline.judge_check 2>/dev/null)
code=$?
echo "$checks" | grep -E '^(PASS|FAIL)' | sed 's/^/    /'
step "$([ $code -eq 0 ] && echo PASS || echo FAIL)" "product checks" "$(echo "$checks" | tail -1)"

echo
printf '%-6s %-16s %s\n' RESULT STEP DETAIL
failed=0
for r in "${results[@]}"; do
  IFS='|' read -r res name detail <<<"$r"
  printf '%-6s %-16s %s\n' "$res" "$name" "$detail"
  [ "$res" = PASS ] || failed=1
done
echo
echo "Not run here: the RCAEval comparison needs ~3.3 GB of data (make eval, or make eval-quick once it is downloaded)."
exit $failed
