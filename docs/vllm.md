# Running vLLM on this machine

Operational notes for the two local vLLM instances that BUILD_PLAN L5 names as launch
providers — the Qwen3.6-27B pair on the operator's dual 4090s. Everything here was
learned during Phase 1's live smoke and would otherwise live only in a terminal
scrollback, which is where hard-won facts go to die.

Every claim below is tagged **VERIFIED** (observed by running it on this machine) or
**UNTESTED** (reasoned, not yet executed). The distinction is the point of the file:
BUILD_PLAN §0.4 makes the difference between an assumption and a verified fact a thing
this project tracks rather than blurs.

Phase 6 needs two instances at once (the kill-a-GPU failover demo), and Phase 8's H2
needs one of them alongside Backline's whole stack. Both of those depend on facts in
here, so it is written before either.

**Updated at the Phase 6 gate (2026-08-09).** The GPU-pinning workaround that shipped here
as **UNTESTED** has been run and works, and the two-instance topology it exists for is now
the standing layout — see *GPU selection* and *The standing two-instance topology* below.

---

## The known-good launch command — VERIFIED

```bash
docker run --rm --gpus all \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -p 8010:8000 \
  vllm/vllm-openai:latest \
  --model cyankiwi/Qwen3.6-27B-AWQ-INT4 \
  --gpu-memory-utilization 0.92 \
  --max-model-len 8192 \
  --enforce-eager \
  --tool-call-parser qwen3_xml \
  --reasoning-parser qwen3 \
  --limit-mm-per-prompt '{"image": 0, "video": 0}'
```

Served at `http://localhost:8010/v1`. This is the single-instance form; for the standing
**two**-instance layout add `--name` and `-e CUDA_VISIBLE_DEVICES=<UUID>` per the topology
section below, which is what Phase 6's chain and demo assume.

Point Headroom at it with either spelling of the base URL — with or without the trailing
`/v1` — because `normalize_base_url` trims one (docs/DECISIONS.md H-011):

```bash
make up   # the smoke provisions a tenant and key, and reads its ledger row back
VLLM_BASE_URL=http://localhost:8010 uv run pytest -m live -k vllm -v
```

Since 2026-08-09 the smoke needs the compose stack as well as the URL: it creates (or
reuses) a `live-smoke` tenant, mints itself a virtual key — Phase 2 made one mandatory on
every `/v1/*` request — and asserts the ledger row it produced is attributed to that
tenant. It prints the request id and the `curl` to read the row back; do that *before*
the next `make test`, which truncates the ledger (docs/DECISIONS.md H-029).

From **inside** the compose gateway container, `localhost` is the container. Use
`host.docker.internal:8010`, which `docker-compose.yml` already maps.

### Why each non-obvious flag

**`--tool-call-parser qwen3_xml`** — VERIFIED. The Qwen3 family emits tool calls in an
XML-ish envelope, not the Hermes JSON format. With `--tool-call-parser hermes` the
server starts, serves, and answers normally; it simply **never emits a `tool_calls`
field**, and the model's tool call arrives as prose inside `content`. That is the worst
shape of failure available — no error, no warning, just an agent that has quietly
stopped being able to call tools. Assumption **A6** in BUILD_PLAN §0.4 is exactly this
flag, and Phase 6's demo pre-flight re-checks it.

**`--reasoning-parser qwen3`** — VERIFIED. This checkpoint is a reasoning model. The
parser splits the chain of thought out of `content` into a separate delta field
(`reasoning_content` on this build; some servers spell it `reasoning`). Without it the
thinking text lands in `content` and every caller sees the model muttering. See
*Reasoning-model behaviour* below for what this costs a small `max_tokens`.

**`--limit-mm-per-prompt '{"image": 0, "video": 0}'`** — VERIFIED. The checkpoint is
multimodal-capable; this declares that this server will accept neither images nor video.
Two things follow, and the second is the one that matters:

1. vLLM skips **multimodal profiling** at startup — the pass that runs the vision
   encoder over synthetic worst-case inputs to size its memory. Under `--enforce-eager`
   that profiling is slow enough to dominate boot time.
2. The encoder's reserved budget is freed and goes to **KV cache** instead, which is the
   only thing that buys context length on a 24 GB card.

**`--enforce-eager`** — VERIFIED as necessary here. It disables CUDA-graph capture,
which otherwise reserves additional memory at startup that this card cannot spare
alongside the display. It costs some throughput and is the right trade at 24 GB.

**Port 8010, not 8000** — VERIFIED, and non-negotiable. **8000 belongs to Backline**,
the sibling repo, whose full stack has to run *simultaneously* with this one for Phase
8's H2 experiment (Backline's 133-question suite pointed at Headroom as its provider).
This is the same reasoning that moved Headroom's own host ports off 5432/8000 in
docs/DECISIONS.md H-006 — two projects that cannot boot at the same time make the
headline experiment impossible by construction.

**The HuggingFace cache mount** — VERIFIED. `-v ~/.cache/huggingface:/root/.cache/huggingface`
means the ~16 GB checkpoint is downloaded once, not once per container start. Omit it and
every restart re-downloads.

---

## The `drawais` landmine — VERIFIED

**Use `cyankiwi/Qwen3.6-27B-AWQ-INT4`. Do not use `drawais/Qwen3.6-27B-AWQ-INT4`.**

The `drawais` repack ships a **text-only `config.json`** — the multimodal wrapper config
is missing. vLLM 0.26 loads it, tries to construct the config object, and dies with a
`TypeError` between `Qwen3_5Config` and `Qwen3_5TextConfig`: the loader hands the
text-config class fields the wrapper class expects, or vice versa. The traceback names
transformers internals and looks like a version-skew problem, which is what makes it
expensive — the obvious response is to start bisecting vLLM and transformers versions,
and none of that helps, because the model repository is what is wrong.

The two repacks are otherwise interchangeable in name and size. If a launch that worked
yesterday dies with a `Qwen3_5Config` / `Qwen3_5TextConfig` `TypeError`, check which
repo id is on the command line before touching anything else.

---

## GPU selection: `--gpus all` + `CUDA_VISIBLE_DEVICES`, and nothing else

**Docker's own pinning flags are broken here — VERIFIED as broken.** Both documented
forms were tried and **both placed the model on the wrong physical card**:

```bash
--gpus device=1                                        # wrong card
--gpus device=GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx # also wrong card
```

The container reported the GPU it was asked for; the memory appeared on the other one.
The cause has not been isolated — it is somewhere in the Docker Desktop / WSL2 GPU
passthrough layer, where device ordering is not guaranteed to match `nvidia-smi`'s.

### The fix — **VERIFIED 2026-08-09** (was UNTESTED until Phase 6)

```bash
docker run --gpus all -e CUDA_VISIBLE_DEVICES=GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx ...
```

Give the container **every** GPU and let **CUDA** do the selection by UUID inside it,
rather than asking Docker to pass through a subset. `--gpus device=` filters at the
container-runtime layer, where the ordering is wrong on this machine;
`CUDA_VISIBLE_DEVICES` with a UUID is resolved by the CUDA driver itself, which is the
layer that actually knows which card is which.

**The model landed on the named card on the first try**, confirmed by the uuid-keyed
`nvidia-smi` query below. Corroborated during the Phase 6 build session by inspecting
both running containers: the instance launched this way is pinned to the UUID it names,
while the instance still launched with `--gpus device=<same UUID>` is demonstrably *not*
on that card — the two together are the broken form and the working form running side by
side and landing on different GPUs.

**Use this form for both instances.** `--gpus device=N` and `--gpus device=UUID` are not
"less reliable" here, they are wrong, and a two-instance demo where both servers are
secretly on one card is not a failover demo — it is a very slow single card.

### Always verify placement after launch — VERIFIED as the reliable check

```bash
nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.free --format=csv
```

Run it from the **host**, after the server reports ready. With both instances up, both
cards read ~23 GB used and under 1 GB free:

```
index, uuid, name, memory.used [MiB], memory.free [MiB]
0, GPU-61bdb28e-…, NVIDIA GeForce RTX 4090, 23736 MiB, 407 MiB
1, GPU-ad348511-…, NVIDIA GeForce RTX 4090, 23332 MiB, 811 MiB
```

**`nvidia-smi --query-compute-apps` does not work here — VERIFIED useless.** Under WSL2 it
reports a single virtual PID against both GPUs with `[N/A]` memory, so it cannot map a
container to a card. Per-GPU *memory* is the only reliable signal, which is why the query
above is the one to run.

---

## The standing two-instance topology — VERIFIED

Phase 6's failover chain, and Phase 8's H3 kill demo, both assume this exact layout. It is
what `config/routing.yaml` ships with as the `vllm_a` → `vllm_b` chain, and what
`.env.example` documents.

| | host port | GPU | provider name in `config/routing.yaml` | env override |
|---|---|---|---|---|
| **Instance A** | `8010` | one 4090 | `vllm_a` (the chain's primary) | `VLLM_BASE_URL` |
| **Instance B** | `8011` | the other 4090 | `vllm_b` (the fallback) | `VLLM_B_BASE_URL` |

Identical launch flags on both — the pair is deliberately two *independent single-GPU*
servers rather than one tensor-parallel pair (BUILD_PLAN L5), because that is what makes a
`docker kill` on one of them a real failover rather than an outage.

```bash
# read the two UUIDs first; they are what the pinning depends on
nvidia-smi --query-gpu=index,uuid,memory.free --format=csv

# instance A — the chain's primary, port 8010
docker run -d --name vllm-a --gpus all \
  -e CUDA_VISIBLE_DEVICES=GPU-<uuid-of-card-0> \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -p 8010:8000 \
  vllm/vllm-openai:latest \
  --model cyankiwi/Qwen3.6-27B-AWQ-INT4 \
  --gpu-memory-utilization 0.92 --max-model-len 8192 --enforce-eager \
  --tool-call-parser qwen3_xml --reasoning-parser qwen3 \
  --limit-mm-per-prompt '{"image": 0, "video": 0}'

# instance B — the fallback, port 8011, same flags, the other card
docker run -d --name vllm-b --gpus all \
  -e CUDA_VISIBLE_DEVICES=GPU-<uuid-of-card-1> \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -p 8011:8000 \
  vllm/vllm-openai:latest \
  --model cyankiwi/Qwen3.6-27B-AWQ-INT4 \
  --gpu-memory-utilization 0.92 --max-model-len 8192 --enforce-eager \
  --tool-call-parser qwen3_xml --reasoning-parser qwen3 \
  --limit-mm-per-prompt '{"image": 0, "video": 0}'
```

**Name the containers.** The Phase 6 demo is `docker kill vllm-a`, and a demo that starts
with "which one of these is the primary again?" is a demo that goes wrong on camera.

Pre-flight both before any demo — this is assumption **A6** in BUILD_PLAN §0.4:

```bash
for p in 8010 8011; do curl -sS "http://localhost:$p/v1/models" | head -c 120; echo; done
```

Both must answer with `cyankiwi/Qwen3.6-27B-AWQ-INT4`. **Verified 2026-08-09**, both
instances serving with the `qwen3_xml` / `qwen3` parser flags above.

---

## Sizing against free memory, not card capacity — VERIFIED

The desktop card carries **~1.2–5 GB of Windows display memory at all times** — desktop
compositing, browsers, anything with a hardware-accelerated surface, and it moves during
a session rather than staying put.

So `--gpu-memory-utilization` is a fraction of the card's **total**, and the number that
has to fit is what is **free**. On a 24 GB card already holding 4 GB of desktop, `0.92`
asks for ~22 GB of a card with ~20 GB available, and the failure arrives as an OOM
partway through weight loading — after the download, after a minute of waiting.

Check first, every time:

```bash
nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free --format=csv
```

`0.92` is the verified-good value **on the headless card**. The card driving the display
wants less, and how much less depends on what the desktop is doing right now. This is
also why the second instance of Phase 6's pair is the one to be careful about.

---

## Reasoning-model behaviour, and the test it broke — VERIFIED

This checkpoint **spends its token budget on reasoning deltas before it emits any
content**. Phase 1's live smoke asked for `max_tokens: 16` and got back a stream that was
completely well formed — frames in order, `[DONE]` present, no error event — carrying
**no content at all** and `finish_reason: "length"`.

That is not a gateway failure and not a model failure. It is a budget too small to reach
the answer. But at a 16-token ceiling, "the model ran out of budget while thinking" and
"the gateway dropped the content" are indistinguishable from the outside, which is what
made the original assertion (*"some text came back"*) useless.

Consequences, all of them recorded in `docs/PHASE_LOG.md` under *Live smoke — first run
(vLLM)*:

- **Give the smoke room.** `tests/test_live_smoke.py` now asks for `max_tokens: 256` — it
  runs on the operator's own GPUs, so the headroom is free — and asserts
  `finish_reason == "stop"` **and** non-empty content, so an exhausted budget reports
  itself as an exhausted budget.
- **An exhausted budget is a complete stream, not a truncation.** `finish_reason:
  "length"` with `[DONE]` present is a *finished stream* of a *truncated answer*. The two
  must never be confused: the first is `outcome == "ok"`, the second is
  `upstream_stream_cut`, and BUILD_PLAN invariant 6 turns on the difference in Phase 5.
  `tests/test_reasoning_passthrough.py` pins both, keylessly.
- **Reasoning tokens are output tokens the meter cannot see.** They never appear in the
  content stream, only in the usage block (`completion_tokens_details.reasoning_tokens`).
  Phase 3 must read usage from the usage block and never infer it from text — 11 visible
  characters against 63 completion tokens, 57 of them reasoning, in the committed
  fixture.

---

## Quick reference

| Fact | Status |
|---|---|
| The launch command above serves Qwen3.6-27B-AWQ-INT4 on port 8010 | VERIFIED |
| `qwen3_xml` is required; `hermes` fails silently on this family | VERIFIED |
| `--limit-mm-per-prompt` skips vision profiling and frees KV budget | VERIFIED |
| `drawais/…` crashes vLLM 0.26 with a `Qwen3_5Config` TypeError; `cyankiwi/…` works | VERIFIED |
| `--gpus device=N` and `--gpus device=UUID` both pin to the wrong card here | VERIFIED (broken) — **do not use** |
| **`--gpus all` + `CUDA_VISIBLE_DEVICES=<UUID>` pins to the named card** | **VERIFIED 2026-08-09** (was UNTESTED) |
| `nvidia-smi --query-gpu=uuid,memory.used --format=csv` shows the real placement | VERIFIED |
| `nvidia-smi --query-compute-apps` cannot map a container to a card under WSL2 | VERIFIED (useless) |
| Two instances, one per 4090: A on 8010, B on 8011, identical flags | VERIFIED 2026-08-09 |
| Both instances serve with the `qwen3_xml` / `qwen3` parsers (assumption **A6**) | VERIFIED 2026-08-09 |
| The display card holds ~1.2–5 GB at all times; size against free, not total | VERIFIED |
| Small `max_tokens` yields empty content with `finish_reason: "length"` | VERIFIED |
