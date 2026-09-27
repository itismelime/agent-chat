# agent-chat: local models (runtime and Model Hub)

Status: draft for review, 2026-09-27. Roadmap feature 6, part 1 of 3:
1. **this spec:** a tuned local Ollama and a Model Hub on the page;
2. talking local LLMs: a model joins a project and answers in the chat;
3. local agents: OpenCode agents with the chat tools, started from the page.

Taken from `~/Projects/local-ai-chat` (the user's project), its local-LLM
part only: the Ollama runtime and tuning, the coordinator's `think: false`
calls and GGUF import, and the Model Hub's fit test, recommendations,
benchmark and context tuning. SillyTavern, ComfyUI, image models and the
GPU hand-off between them are not taken.

## Goal

- `./install.sh` gives agent-chat its own tuned Ollama, or points it at an
  existing one.
- On the page, the user finds a GGUF model on Hugging Face that fits the
  GPU, gets it with one click, benchmarks it, and sees it tuned and
  unloaded when idle.

## Non-goals (this spec)

Local models in the chat (part 2), local agents (part 3), image models,
Civitai, several GPUs, AMD/Intel GPUs (Ollama may still work; the fit test
reads `nvidia-smi` only), automatic GPU hand-off with local-ai-chat.

## Runtime

**Own Ollama (default).** `install.sh` downloads the pinned release
`https://github.com/ollama/ollama/releases/download/v0.34.2/ollama-linux-amd64.tar.zst`
(the version local-ai-chat runs on an RTX 5070 Ti), checks its SHA-256
(fixed in `install.sh`), and unpacks it to `<clone>/runtime/ollama/`
(git-ignored, about 2.1 GB). A rerun skips the download when that version
is already unpacked. It needs `curl`, `tar` and `zstd`.

Models live in `${XDG_DATA_HOME:-~/.local/share}/agent-chat/ollama-models/`.

The user service `agent-chat-ollama.service` runs `ollama serve` with:

```
OLLAMA_HOST=127.0.0.1:11436   OLLAMA_MODELS=<data>/ollama-models
OLLAMA_NUM_PARALLEL=1         OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_CONTEXT_LENGTH=32768   OLLAMA_KEEP_ALIVE=5m
OLLAMA_FLASH_ATTENTION=1      OLLAMA_KV_CACHE_TYPE=q8_0
```

Port 11436, so it never clashes with local-ai-chat's 11434 or its
coordinator's 11435. `--uninstall` removes the service and `runtime/`, not
the models.

**External Ollama (setting).** `./install.sh --ollama-url <url>` writes
`{"ollama_url": "<url>"}` to `${XDG_CONFIG_HOME:-~/.config}/agent-chat/config.json`
and skips the download and the service; `--ollama-url own` switches back.
The URL must be `http://127.0.0.1:<port>` or `http://localhost:<port>`.
The agent-chat service reads the setting at start.

**GPU sharing.** With its own Ollama, agent-chat and local-ai-chat compete
for video memory when both have a model loaded. Nothing coordinates them;
a failed load shows Ollama's message with that hint (see Failure behaviour).

## Model Hub (service)

New module `agentchat/models.py`. All Ollama calls go to the configured URL;
chat-style calls use the native `/api/chat` with `"think": false` (Ollama's
OpenAI endpoint ignores it, and hybrid reasoning models would otherwise
think before every answer).

**GPU.** `nvidia-smi --query-gpu=name,memory.total,memory.used
--format=csv,noheader,nounits`, summed over GPUs. Missing: capacity unknown.

**Search.** `GET https://huggingface.co/api/models?filter=gguf&sort=trendingScore
&direction=-1&limit=30[&search=<q>]` with `expand[]` = downloads, likes,
tags, trendingScore, lastModified, siblings; then
`GET https://huggingface.co/api/models/<id>?blobs=true` per result for file
sizes (8 at a time). Results are cached for 10 minutes. This and downloads
are the only internet access, and only when the user searches or gets a
model.

**Rating** (local-ai-chat's rules, ported):

- *Best file:* `.gguf` files that are not support files (`mmproj`,
  `imatrix`, `vae`, `clip`, `lora`, … per local-ai-chat's `SUPPORT_FILE`)
  and not split or partial (`-00001-of-00003.gguf`, `.part`, `mtp`).
  Prefer files with `size × 1.2 ≤ GPU memory × 0.9`; among those the best
  quantisation (F16/F32 100, Q8 92, Q6_K 86, Q5_K_M 82, Q5_K_S 79, Q5 76,
  Q4_K_M 73, Q4_K_S 70, IQ4/Q4 68, IQ3/Q3 57, IQ2/Q2 46, other 60), then
  the larger file.
- *Fit:* `ratio = size × 1.2 / GPU memory`: ≤ 0.55 "Plenty of room" (45
  points), ≤ 0.75 "Comfortable" (40), ≤ 0.9 "Good fit" (33), ≤ 1 "Tight
  fit" (20), else "Won't fit GPU memory" (0); unknown size or GPU "Unknown
  fit" (10).
- *Score* 0–100 = fit + compatibility (25 with a usable file, else 0) +
  community (log-scaled downloads, likes, trending; up to 20) + freshness
  (≤ 30 days 10, ≤ 180 8, ≤ 365 6, ≤ 730 3, older 1, unknown 2). Label:
  ≥ 85 Excellent, ≥ 70 Good, ≥ 50 Fair, else Poor.

**Get (import).** Only `https://huggingface.co/<id>/resolve/<rev>/<file>`
for a `.gguf` file whose name is a plain file name; model name without
spaces, not starting with `.`. Free disk space must be at least
3.1 × the file size (Ollama copies it into its own layers). The file is
downloaded to `<data>/imports/` while hashing SHA-256, uploaded with
`POST /api/blobs/sha256:<hex>` (skipped if `HEAD` finds it), then created
with `POST /api/create {"model", "files": {<file>: <digest>}}`. The
temporary file is always deleted afterwards.

**Pull.** `POST /api/pull {"model": <name>}` for Ollama-library names.

**Benchmark.** Loads the model and asks it
`Reply with exactly: benchmark ok` (`think: false`), reporting load +
generate seconds, tokens per second (`eval_count / eval_duration`), and
video memory used (from `nvidia-smi` before and after).

**Tuning.** Recommended context: ≥ 12 GiB GPU memory 32768, ≥ 8 GiB 16384,
else 8192; capped by the model's `<arch>.context_length` from
`POST /api/show`; rounded down to a multiple of 64, at least 512. Stored per
model in `<data>/models.json` as `{"<model>": {"num_ctx", "override"}}`; the
user can set an override. Parts 2 and 3 send `num_ctx` with every request.

**Unloading.** The 5-minute keep-alive unloads an idle model. **Unload now**
sends `POST /api/generate {"model", "keep_alive": 0}` for each loaded model
(`GET /api/ps`). Part 3 also unloads when a local agent stops.

**Jobs.** Import, pull and benchmark run in background threads as jobs
`{"id", "kind", "model", "state": running|done|failed|cancelled,
"completed", "total", "message", "result"}`, kept in memory (the last 50).
An import or pull can be cancelled; one of each kind runs at a time per model.

## API

All under the core protections (Host, Origin, `X-Agent-Chat`, JSON bodies,
own Unix user only).

| Method and path | Does |
|---|---|
| `GET /api/models/status` | Ollama URL, own/external, reachable, version; GPU name, total and used memory |
| `GET /api/models` | installed models: name, size, loaded, fit verdict, `num_ctx`, override |
| `GET /api/models/search?q=` | rated Hugging Face results, best first |
| `POST /api/models/import {url, filename, model}` | start an import job |
| `POST /api/models/pull {model}` | start a pull job |
| `POST /api/models/benchmark {model}` | start a benchmark job |
| `POST /api/models/delete {model}` | delete an installed model |
| `POST /api/models/unload {}` | unload every loaded model |
| `POST /api/models/tune {model, num_ctx}` | set (or clear with `null`) the context override |
| `GET /api/models/jobs` | jobs, newest first |
| `POST /api/models/jobs/<id>/cancel {}` | cancel a running import or pull |

## Page

A **Models** button at the bottom of the sidebar opens a panel (like the
terminal panel). Its header shows Ollama (own or external, reachable,
version) and the GPU (name, used / total). Tabs:

- **Installed:** name, size, Loaded badge, fit verdict, context (with an
  edit field for the override), buttons Benchmark and Delete (confirms);
  **Unload now** above the list.
- **Get models:** a search box; results sorted by score, each with label,
  score, fit verdict, best file and its size, downloads, and **Get**
  (filled in: model name from the repository and quantisation, e.g.
  `qwen3.5-9b:q4_k_m`, editable); a field to pull by Ollama name.
- **Jobs:** each job with a progress bar, message and Cancel; a finished
  benchmark shows its numbers.

The panel refreshes every 2 s while open.

## Failure behaviour

- Ollama unreachable: the panel says so and names the fix
  (`systemctl --user start agent-chat-ollama`, or the external URL setting).
- Hugging Face unreachable or rate-limited: the search shows the error; the
  last cached results stay.
- Not enough disk space: the import job fails before downloading.
- Download fails or is cancelled: the job fails or is cancelled and the
  temporary file is deleted.
- A model does not load (usually not enough video memory, for example
  local-ai-chat has one loaded): Ollama's message, plus "free video memory
  (another program may have a model loaded) or pick a smaller file".
- Bad input (URL not on huggingface.co, unsafe file name, bad model name,
  context outside 512–131072): `400`.

## Testing

- Rating and best file: ported from local-ai-chat's
  `test-recommendations.mjs` cases, plus boundaries of every fit verdict.
- Context recommendation: GPU sizes around 8 and 12 GiB, model limits.
- Import against a fake Hugging Face server and a fake Ollama server (both
  local `http.server`s): hash, blob upload skipped when present, create,
  disk-space refusal, cancel deletes the temporary file, URL and file name
  refusals.
- Jobs, API routes and settings (own / external URL).
- `install.sh` with a fake download: checksum mismatch refused, rerun skips
  the download, `--ollama-url` writes the setting and skips the service,
  `--uninstall` keeps the models.
- One real test against a running Ollama with a tiny model (skipped when
  none answers).
- Firefox: the panel against the fake servers.
