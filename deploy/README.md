# Distribution notes

For the maintainer. The instructions someone else follows are in the main
`README.md`; this is the reasoning behind them and how to keep the cache
current.

## Why this is not hosted

It was going to be a Hugging Face Docker Space. Hugging Face now bills those:
creating one returns `402 Payment Required` — *"Static Spaces are free for
everyone, but hosting Gradio and Docker Spaces on free cpu-basic requires a PRO
subscription."* A static Space cannot serve this, which runs `uvicorn` and forks
`python -m siting.cli` as a subprocess.

Distributing the image rather than hosting it turned out to be the better
posture anyway, for a reason unrelated to cost. With `SITING_WEB_ENGINE=sdk`,
`data-scout` and `spatial-analyst` hold real Bash inside the container, gated
only by `_gate_tools` in `web/claude.py` — a default-deny PreToolUse hook,
written because the Agent SDK does **not** inherit this repository's
`settings.json` deny rules just from `cwd` (see the comment at
`web/claude.py:66`). A public endpoint would have meant strangers driving that
agent on somebody else's credit. Run locally, each operator brings their own key
and their own container, and the question does not arise.

## The cache lives on Hugging Face, not in git

<https://huggingface.co/datasets/YibinGao/oasis-siting-cache> — 24 files, 609 MB.

It cannot go in the GitHub repository. A free account has **1 GB of LFS storage
and 1 GB of transfer a month**; 580 MB would take most of the storage and allow
roughly one clone a month before the second person is refused. GitHub also
rejects any non-LFS file over 100 MB, and `tza_ppp_2020.tif` is 462 MB.

A Hugging Face dataset repository carries it at no cost, and is not subject to
the Spaces restriction above.

| What | Size | Why it is cached |
|---|---|---|
| `raster/tza_ppp_2020.tif` | 462 MB | WorldPop refuses HTTP range requests, so a country's raster is pulled whole |
| `raster/uga_ppp_2020.tif` | 108 MB | as above |
| `boundaries/gadm41_*.gpkg` | 9 MB | six GADM levels for TZA and UGA |
| `wpdx_*.parquet` | 1.4 MB | the water point registers, per district |
| `friction/*.tif` | 1 MB | walking and motorised friction, per bounding box |

The raster is cached per **country**, not per district, so any Ugandan or
Tanzanian district runs warm — not only the eight with a `wpdx_*.parquet`. A
district in a third country always retrieves live.

### Licences

The cache is redistributed under the terms recorded in `handbooks/*.yaml`:
WorldPop, WPdx and the friction surface are CC BY 4.0; GADM is free for academic
and non-commercial use. Anyone republishing it owes those attributions. Note
that GADM's terms do not grant redistribution outright — that it is published
here is a deliberate decision, not an oversight.

### Refreshing it

```bash
python -m web.prewarm warm      # fetch and price the listed districts locally
hf upload YibinGao/oasis-siting-cache ./cache . --repo-type dataset
```

`web/prewarm.py` writes the priced option tables to `sessions/_pricing/`, which
**are** committed to git — each costs a full analysis run and none of them ever
changes. `sessions/_pricing/work/` is scratch and is not.

## What the deployment work added

| File | |
|---|---|
| `Dockerfile` | python:3.12-slim, plus Node 20 and the Claude Code CLI for the SDK engine. Does not copy `cache/` |
| `compose.yaml` | the one command an operator runs. Bind-mounts `cache/`, `sessions/`, `runs/` so a run's output lands on the host |
| `scripts/fetch_cache.py` | pulls the published cache; `--country UGA` for one country, `--check` to report |
| `.dockerignore` | excludes `cache/` (mounted, not baked) and every run artefact |
| `.gitignore` | `cache/` back out of git; `.DS_Store`, Office lock files and `.claude/settings.local.json` added; the two `sample-runs` PDFs kept despite the blanket `*.pdf` |
| `sessions/.gitignore` | every session directory ignored — they hold verbatim transcripts — except `_pricing/*.json` |
| `requirements.txt` | one line: `huggingface_hub`, used only by the fetch script |
| `README.md` | a "Running the web interview" section. The rest is untouched |

No file under `siting/`, `web/`, `handbooks/`, `skills/` or `.claude/agents/`
was modified. The analysis is exactly what it was.

## Verified on 2026-09-04

Built and run on darwin/arm64, image 3.91 GB:

- all imports resolve — geopandas, rasterio, spopt, pulp, typst, osmnx,
  pandera, access, `skimage.graph.MCP_Geometric`
- CBC solver present, typst module loads
- Node 20.20.2, Claude Code CLI 2.1.197
- container runs as uid 1000; `runs/`, `sessions/`, `cache/`, `harness/logs`
  writable; hooks executable
- `GET /` 200, `/log` 200, `/api/runs` 200, `/static/map.js` 200

Not verified: linux/amd64 (the wheels are all manylinux, so this is very likely
fine), and whether the Claude Code CLI starts cleanly in a container with no TTY
under `SITING_WEB_ENGINE=sdk`. If an interview hangs on its first turn, set
`SITING_WEB_ENGINE=legacy` — the interview works, only real subagent delegation
is lost.
