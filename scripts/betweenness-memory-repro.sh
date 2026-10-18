#!/usr/bin/env bash
# Exact algo.betweenness over 3,000 nodes: peak memory with and without other (unconnected) nodes in the graph.
# Usage: repro.sh <filler_nodes> <samplingSize>
set -u
FILLER=$1; K=$2; NAME=bc-repro-$$
docker run -d --rm --name $NAME -m 6g falkordb/falkordb:v4.14.10 >/dev/null
q() { docker exec $NAME redis-cli GRAPH.QUERY g "$1" >/dev/null; }
for _ in $(seq 1 30); do docker exec $NAME redis-cli PING 2>/dev/null | grep -q PONG && break; sleep 1; done
q "UNWIND range(0, 2999) AS i CREATE (:Service {id: i})"
q "CREATE INDEX FOR (s:Service) ON (s.id)"
q "UNWIND range(1, 8500) AS i WITH toInteger(rand() * 3000) AS a, toInteger(rand() * 3000) AS b WHERE a <> b
   MATCH (x:Service {id: a}), (y:Service {id: b}) CREATE (x)-[:CALLS]->(y)"
[ "$FILLER" -gt 0 ] && q "UNWIND range(1, $FILLER) AS i CREATE (:Filler {id: i})"
before=$(docker exec $NAME redis-cli INFO memory | tr -d '\r' | awk -F: '/^used_memory_peak:/{print $2}')
t0=$(python3 -c 'import time; print(time.time())')
out=$(docker exec $NAME redis-cli GRAPH.QUERY g "CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['CALLS'], samplingSize: $K, samplingSeed: 1}) YIELD node, score RETURN count(node)" 2>&1 | head -3 | tr '\n' ' ')
t1=$(python3 -c 'import time; print(time.time())')
state=$(docker inspect -f '{{.State.Status}} oom={{.State.OOMKilled}}' $NAME 2>/dev/null || echo "container gone (killed)")
after=$(docker exec $NAME redis-cli INFO memory 2>/dev/null | tr -d '\r' | awk -F: '/^used_memory_peak:/{print $2}')
printf 'filler=%-7s samplingSize=%-5s peak before=%-6s MB after=%-6s MB  %5.1f s  %s  [%s]\n' "$FILLER" "$K" "$((before/1048576))" "$(( ${after:-0}/1048576 ))" "$(python3 -c "print($t1-$t0)")" "$state" "$out"
docker rm -f $NAME >/dev/null 2>&1
