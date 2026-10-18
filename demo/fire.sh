#!/usr/bin/env bash
# Plays a live incident into Hypocenter the way production would: CI posts a deploy, then Alertmanager posts
# alerts climbing the call graph. Hypocenter opens an incident by itself and the agents name the cause.
#
#   demo/fire.sh            deploy + three firing alerts  -> an incident is opened and investigated
#   demo/fire.sh resolve    the same alerts, resolved     -> an approved fix is verified by real recovery
#
# APP (default http://localhost:8090) and TENANT (default kestrel) can be overridden.
set -euo pipefail
APP=${APP:-http://localhost:8090}
TENANT=${TENANT:-kestrel}

# The demo world's clock: two hours after it, so these alerts never mix with the seeded incidents.
NOW=$(curl -fsS "$APP/api/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["now"])')
T0=$((NOW + 7200))
iso() { python3 -c "import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$1"; }

alert() {  # fingerprint service summary start [end]
  local status=firing ends='"0001-01-01T00:00:00Z"'
  if [ -n "${5:-}" ]; then status=resolved; ends="\"$(iso "$5")\""; fi
  printf '{"status":"%s","fingerprint":"%s","labels":{"alertname":"HighLatency","service":"%s","severity":"page"},"annotations":{"summary":"%s"},"startsAt":"%s","endsAt":%s}' \
    "$status" "$1" "$2" "$3" "$(iso "$4")" "$ends"
}

TOKEN=(); [ -n "${FAULTLINE_INGEST_TOKEN:-}" ] && TOKEN=(-H "X-Hypocenter-Token: $FAULTLINE_INGEST_TOKEN")
post() { curl -fsS -X POST "$APP/api/$TENANT/$1" -H 'content-type: application/json' ${TOKEN[@]+"${TOKEN[@]}"} --data "$2"; echo; }

if [ "${1:-}" = "resolve" ]; then
  END=$((T0 + 1200))
  post alerts/alertmanager "{\"status\":\"resolved\",\"alerts\":[
    $(alert f1a0c0de00000001 fraud-scoring 'fraud-scoring p99 4.2s (budget 2s)' $((T0 + 180)) $END),
    $(alert f1a0c0de00000002 payments 'payments p99 6.1s, 7% 5xx' $((T0 + 300)) $END),
    $(alert f1a0c0de00000003 checkout 'checkout p99 6.8s (SLO 1.5s)' $((T0 + 420)) $END)]}" | python3 -m json.tool
  exit
fi

echo "== CI: deploy fraud-scoring 2.8.0"
post changes "{\"kind\":\"deploy\",\"service\":\"fraud-scoring\",\"version\":\"2.8.0\",\"author\":\"ade\",\"risk\":\"high\",
  \"summary\":\"fraud-scoring 2.8.0: feature-store client v3, model warm-up on first request\",\"at\":$T0}"
echo "== Alertmanager: three alerts climbing fraud-scoring -> payments -> checkout"
post alerts/alertmanager "{\"status\":\"firing\",\"alerts\":[
  $(alert f1a0c0de00000001 fraud-scoring 'fraud-scoring p99 4.2s (budget 2s)' $((T0 + 180))),
  $(alert f1a0c0de00000002 payments 'payments p99 6.1s, 7% 5xx' $((T0 + 300))),
  $(alert f1a0c0de00000003 checkout 'checkout p99 6.8s (SLO 1.5s)' $((T0 + 420)))]}" | python3 -m json.tool
