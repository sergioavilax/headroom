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

Served at `http://localhost:8010/v1`. Point Headroom at it with either spelling of the
base URL — with or without the trailing `/v1` — because `normalize_base_url` trims one
(docs/DECISIONS.md H-011):

```bash
VLLM_BASE_URL=http://localhost:8010 uv run pytest -m live -k vllm -v
```

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

## GPU selection is unreliable under this Docker/WSL2 setup

**UNRELIABLE — VERIFIED as broken.** Both documented forms of GPU pinning were tried and
**both placed the model on the wrong physical card**:

```bash
--gpus device=1                                        # wrong card
--gpus device=GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx # also wrong card
```

The container reported the GPU it was asked for; the memory appeared on the other one.
The cause has not been isolated — it is somewhere in the Docker Desktop / WSL2 GPU
passthrough layer, where device ordering is not guaranteed to match `nvidia-smi`'s.

### Always verify placement after launch — VERIFIED as the reliable check

```bash
nvidia-smi --query-gpu=uuid,memory.used --format=csv
```

Run it from the **host**, after the server reports ready. The card holding ~20 GB is the
one actually serving. Do not trust the flag, do not trust the container's view, and do
not build a two-instance demo on an assumption here — check.

### The candidate fix — UNTESTED

```bash
docker run --gpus all -e CUDA_VISIBLE_DEVICES=GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx ...
```

Give the container every GPU and let **CUDA** do the selection by UUID inside it, rather
than asking Docker to pass through a subset. The reasoning: `--gpus device=` filters at
the container runtime layer, where the ordering appears to be wrong;
`CUDA_VISIBLE_DEVICES` with a UUID is resolved by the CUDA driver itself, which is the
layer that knows which card is which.

**This has not been run.** It has to be settled before **Phase 6**, whose whole demo is
one instance per physical 4090 with a `kill` in the middle — a demo where both instances
are secretly on the same card is not a failover demo, it is a very slow single card.
Verify with the `nvidia-smi` command above, and record the result in `docs/PHASE_LOG.md`
at the Phase 6 gate whichever way it goes.

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
| `--gpus device=N` and `--gpus device=UUID` both pin to the wrong card here | VERIFIED (broken) |
| `nvidia-smi --query-gpu=uuid,memory.used --format=csv` shows the real placement | VERIFIED |
| `--gpus all` + `CUDA_VISIBLE_DEVICES=<UUID>` fixes GPU selection | **UNTESTED** — settle before Phase 6 |
| The display card holds ~1.2–5 GB at all times; size against free, not total | VERIFIED |
| Small `max_tokens` yields empty content with `finish_reason: "length"` | VERIFIED |
