#!/usr/bin/env bash
# A rolling pod replacement, on a laptop, with the §8 drop either present or gone.
#
#   scripts/rollout_repro.sh drain      # preStop touches the sentinel, as the chart now
#                                       # does                            — expect dropped: 0
#   scripts/rollout_repro.sh baseline   # preStop only sleeps, as it did before H-091
#                                       #                                 — expect dropped: 1-2
#
# Phase 10 §8 measured one dropped request per replaced pod on the cluster and raising the
# preStop sleep did not change it (docs/DECISIONS.md H-091). This is that finding at
# laptop scale and $0.00: two gateway containers sharing one database, `endpoints_proxy.py`
# in front of them pinning established connections the way conntrack does, and the real
# load loop across the switch.
#
#   t=0    the loop starts; four streamed requests in flight, all of them on A
#   t=12   Endpoints moves to B. Connections already open to A stay on A.
#          drain arm only: preStop touches A's sentinel, so A starts saying
#          `Connection: close` and its clients retire those connections themselves
#   t=17   SIGTERM to A, which is when Kubernetes sends it — after the preStop hook
#   t=30   the loop ends and prints its summary
#
# The only requests that *can* be lost are the ones written onto a connection established
# to A before t=12, which is the §8 drop with everything else removed from around it.
#
# Needs: Docker, the compose stack's `.env` (for HEADROOM_ADMIN_TOKEN), and nothing else.
# Keyless — every request goes to a `mock-` model (invariant 4).
set -uo pipefail

ARM="${1:-drain}"
if [ "$ARM" != "drain" ] && [ "$ARM" != "baseline" ]; then
  echo "usage: $0 [drain|baseline]" >&2
  exit 2
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
set -a; . ./.env; set +a

FLIP=/tmp/headroom-endpoints-flip
SENTINEL=/tmp/headroom-draining
OUT="${OUT:-/tmp/headroom-rollout-$ARM.json}"
RTT_MS="${RTT_MS:-2}"

cleanup() {
  [ -n "${PROXY:-}" ] && kill "$PROXY" 2>/dev/null
  docker rm -f headroom-gateway-b >/dev/null 2>&1
  rm -f "$FLIP"
}
trap cleanup EXIT

rm -f "$FLIP"
docker rm -f headroom-gateway-b >/dev/null 2>&1

# Wait for a gateway to answer, but never forever: a rig that hangs is worse than one that
# fails, because the operator finds out about the second one.
await_healthz() {
  local port="$1" waited=0
  until curl -sf "localhost:$port/healthz" >/dev/null 2>&1; do
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge 90 ]; then
      echo "gateway on :$port never became healthy — 'docker logs' is the next thing to read" >&2
      exit 1
    fi
  done
}

# A: the pod being replaced. Recreated rather than restarted, because the drain switch
# latches by design and a container that has already drained once would start drained.
docker compose rm -sf gateway >/dev/null 2>&1
docker compose up -d gateway >/dev/null 2>&1
await_healthz 8080

# B: the replacement. Same image, same environment, same database — the two differ in
# nothing a request can see, which is what makes the switch invisible when it works.
# `println` rather than a `\n` escape in the template: the escape renders as the two
# literal characters under some docker versions, which yields an empty array, which under
# `set -u` fails the `docker run` and leaves the health loop above spinning on a container
# that was never created.
mapfile -t ENVARGS < <(docker inspect headroom-gateway-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -v '^PATH=' | grep -v '^$' | sed 's/^/--env=/')
if [ "${#ENVARGS[@]}" -eq 0 ]; then
  echo "could not read gateway A's environment; is the compose stack up?" >&2
  exit 1
fi
docker run -d --name headroom-gateway-b --network headroom_default -p 8081:8000 \
  "${ENVARGS[@]}" headroom-gateway >/dev/null
await_healthz 8081

uv run python scripts/endpoints_proxy.py --rtt-ms "$RTT_MS" --flip-file "$FLIP" &
PROXY=$!
sleep 1

TENANT=$(curl -sS -X POST localhost:8080/admin/tenants \
  -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" -H 'content-type: application/json' \
  -d '{"name":"rollout-repro"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))')
if [ -z "$TENANT" ]; then
  TENANT=$(curl -sS localhost:8080/admin/tenants -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" \
    | python3 -c 'import sys,json; print([t for t in json.load(sys.stdin) if t["name"]=="rollout-repro"][0]["id"])')
fi
KEY=$(curl -sS -X POST localhost:8080/admin/keys \
  -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"rollout-repro\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["key"])')

echo "arm=$ARM  rtt=${RTT_MS}ms  out=$OUT"

# Twenty-four workers rather than §8's four: the cluster had ten minutes and two pod
# replacements to catch the race in, and this has one moment. More pinned connections is
# how a five-second window buys back the same sensitivity.
uv run python scripts/load_loop.py \
  --base-url http://localhost:9000 --key "$KEY" \
  --duration-s 30 --concurrency 24 --interval-ms 50 --stream \
  --label "rollout-repro-$ARM" --out "$OUT" >/dev/null 2>&1 &
LOOP=$!

sleep 12
echo "  t=12  Endpoints -> B"
touch "$FLIP"
if [ "$ARM" = "drain" ]; then
  echo "  t=12  preStop  -> touch A's sentinel"
  docker compose exec -T gateway touch "$SENTINEL"
fi

sleep 5
echo "  t=17  SIGTERM  -> A"
docker stop -t 30 headroom-gateway-1 >/dev/null 2>&1
# 143, and that is correct: uvicorn re-raises the signal it shut down for, so Kubernetes
# renders a perfectly graceful stop as `Error`. H-091's red herring, pinned here.
echo "        A exit code $(docker inspect -f '{{.State.ExitCode}}' headroom-gateway-1) (143 is a clean shutdown — H-091)"

wait $LOOP
LOOP_RC=$?

python3 - "$OUT" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
print(f"  requests={s['requests']} ok={s['ok']} shed={s['shed']} "
      f"DROPPED={s['dropped']} max_gap_ms={s['max_gap_ms']}")
for i in s["incidents"]:
    print(f"    t={i['t_s']:.1f}s  {i['detail']}")
PY

exit $LOOP_RC
