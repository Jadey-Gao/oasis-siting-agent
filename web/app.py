"""HTTP surface. Routes translate a request into a call, and a result into JSON.

Nothing here decides anything. Anything that decides belongs in `session.py`;
anything that runs belongs in `runner.py`. Keeping this file empty of judgement
is what makes it possible to say the web layer cannot influence an analysis.

Only the endpoints needed to watch a run exist so far. The rest arrive with
`session.py`, once the interview's shape is settled:

    GET  /                            the page
    POST /api/interview               open an interview for one district
    GET  /api/interview/{id}          everything the page renders
    POST /api/interview/{id}/say      the officer speaks; returns a turn id
    GET  /api/interview/{id}/turn/{n} SSE, the agent's turn as it happens
    POST /api/run                     prepare a run; returns its id
    GET  /api/run/{id}/stream         SSE, the CLI's stages as they happen
    GET  /api/run/{id}/results        results.json, the only source for a figure
    GET  /api/run/{id}/document/{fmt} the compiled PDF, when there is one

A run is prepared and then streamed, rather than started on POST, because
EventSource can only issue a GET. The consequence is that a run begins when the
browser opens the stream and ends if it closes it, which is the behaviour a
single-operator demonstration wants.

Serve with:

    uvicorn web.app:app --reload --port 8000
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import claude, emit, geo, prompts, runner, session
from .runner import REPO, RunSpec

SESSIONS = REPO / "sessions"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="siting interview", version="0.1")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Prepared but not yet streamed, and finished. In-process and deliberately so:
# a session's durable state is the YAML on disk, not this.
_pending: dict[str, RunSpec] = {}
_finished: dict[str, dict[str, Any]] = {}

# Every run_dir this process has itself resolved from server-held state (never
# from a request path), keyed by its basename. `run_file` below serves figures
# only out of directories registered here, so a request can never glob or walk
# its way into a run this process did not itself produce.
_run_dirs: dict[str, Path] = {}


def _known_run_dir(raw: str) -> Path:
    p = Path(raw)
    _run_dirs[p.name] = p
    return p


class AnswerIn(BaseModel):
    key: str
    value: Any
    decided_by: str
    reason: str
    reason_original: str | None = None
    language: str | None = None


class RunIn(BaseModel):
    country: str
    adm2: str
    iso3: str
    domain: str = "water"
    mode: str = "manual"
    budget: int | None = None
    fmt: str = "both"
    force_issue: bool = False
    # Either supply the answers and let emit write the file, or point at one
    # that already exists in the repository.
    answers: list[AnswerIn] | None = None
    decisions_path: str | None = None
    overrides: list[dict[str, Any]] | None = None


def _repo_path(rel: str) -> Path:
    """A repository-relative path, and only that. Refuses to escape the repo."""
    p = (REPO / rel).resolve()
    if not p.is_file() or REPO not in p.parents:
        raise HTTPException(400, f"no such file in this repository: {rel}")
    return p


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/log")
def log_page() -> FileResponse:
    """The run log on its own, without an interview around it.

    Kept as a page because it is the shortest route to the two things a visitor
    with no district of their own should see: a real run staged as it happens,
    and the refusal that a run with nothing recorded produces.
    """
    return FileResponse(STATIC / "log.html")


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    """Assessments produced through an interview, for the history rail.

    One entry per session, not one per run directory: an interview can
    legitimately run the analysis more than once (a deferred decision that
    only becomes askable once a plan exists, an officer's override — each
    re-runs and lands in its own timestamped directory under
    `sessions/<id>/runs/`, and nothing is overwritten). Those intermediate
    runs stay on disk as the audit trail, but only the session's own
    `last_run` — the same one its own page would show if reopened — is
    listed here. `resolve_place` and `scout` never set `last_run` (they run
    at `--mode auto --budget 1` into `resolving/` and `scouting/`, to read
    back a handful of fields, not to produce a plan), so this also excludes
    those without any path-name filtering.

    Each one carries a full conversation in `sessions/<id>/session.json` that
    a click can reopen. A run started outside an interview (the standalone
    `/api/run` path, or the CLI writing to the repository's own `runs/`) has
    no conversation behind it, so there would be nothing to show beyond the
    files already reachable from `RUN_RECORD.md` — it is left out of this
    list entirely rather than shown as a dead end.
    """
    out = []
    for sf in SESSIONS.glob("*/session.json"):
        session_id = sf.parent.name
        if session_id == "_pricing":
            continue
        try:
            raw = json.loads(sf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        last_run = raw.get("last_run") or {}
        if not last_run.get("run_dir"):
            continue
        run_dir = _known_run_dir(last_run["run_dir"])
        try:
            doc = runner.results(run_dir)
        except (OSError, json.JSONDecodeError):
            continue
        r, plan, review = doc.get("run", {}), doc.get("plan", {}), doc.get("review", {})
        out.append({
            "session_id": session_id,
            "run_id": r.get("id", run_dir.name),
            "district": f"{doc['scope']['adm2']}, {doc['scope']['country']}",
            "created": r.get("generated_at"),
            "sites": len(plan.get("sites", [])) if plan else None,
            "score": review.get("weighted"),
            "issued": review.get("decision") == "issue" or bool(runner.documents(run_dir)),
            "mtime": run_dir.stat().st_mtime,
        })
    out.sort(key=lambda h: h["mtime"], reverse=True)
    for h in out:
        del h["mtime"]
    return out


@app.get("/api/run/{run_id}/record")
def run_record(run_id: str) -> FileResponse:
    f = _run_dir(run_id) / "RUN_RECORD.md"
    if not f.exists():
        raise HTTPException(404, "this run has no RUN_RECORD.md")
    return FileResponse(f, media_type="text/markdown", filename=f.name)


@app.post("/api/run")
def prepare(req: RunIn) -> dict[str, Any]:
    """Write the session's files and hold the run until its stream is opened."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{req.adm2.lower().replace(' ', '-')}-{stamp}-{uuid.uuid4().hex[:6]}"
    sess = SESSIONS / run_id
    sess.mkdir(parents=True, exist_ok=True)

    decisions: Path | None = None
    written: emit.Written | None = None
    if req.answers:
        try:
            written = emit.write_decisions(
                sess / "decisions.yaml",
                [emit.Answer(**a.model_dump()) for a in req.answers],
                mode=req.mode, district=req.adm2,
            )
        except emit.Refused as exc:
            raise HTTPException(422, str(exc)) from exc
        decisions = written.path
    elif req.decisions_path:
        decisions = _repo_path(req.decisions_path)

    overrides: Path | None = None
    if req.overrides:
        try:
            emit.write_overrides(sess / "overrides.yaml", req.overrides,
                                 district=req.adm2)
        except emit.Refused as exc:
            raise HTTPException(422, str(exc)) from exc
        overrides = sess / "overrides.yaml"

    _pending[run_id] = RunSpec(
        country=req.country, adm2=req.adm2, iso3=req.iso3, domain=req.domain,
        out_dir=sess / "runs", decisions=decisions, overrides=overrides,
        mode=req.mode, budget=req.budget, fmt=req.fmt,
        force_issue=req.force_issue,
    )
    return {
        "id": run_id,
        "stages": list(runner.STAGES),
        "command": " ".join(_pending[run_id].command()),
        "decisions": {
            "recorded": list(written.recorded) if written else [],
            "outstanding": list(written.outstanding) if written else [],
        } if written else None,
    }


@app.get("/api/run/{run_id}/stream")
async def stream(run_id: str) -> StreamingResponse:
    spec = _pending.pop(run_id, None)
    if spec is None:
        raise HTTPException(404, "no run is waiting under that id")

    async def events():
        async for ev in runner.stream(spec, transcript=SESSIONS / run_id / "run.log"):
            if ev.kind == "done" and ev.data:
                _finished[run_id] = ev.data
            yield f"data: {json.dumps(ev.to_dict(), ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Without this a reverse proxy buffers the stream and the staging is
            # lost, which is the same failure `-u` prevents on the other side.
            "X-Accel-Buffering": "no",
        },
    )


def _run_dir(run_id: str) -> Path:
    done = _finished.get(run_id)
    if done and done.get("run_dir"):
        return _known_run_dir(done["run_dir"])

    # Not in memory — either this process was restarted, or the run predates
    # it. A standalone run's durable state is `sessions/<run_id>/runs/`,
    # exactly like an interview's, so it can still be found on disk. `run_id`
    # is a single path segment (FastAPI forbids "/" in it), but ".." alone is
    # still a valid segment, so the resolved candidate must stay under
    # SESSIONS before it is trusted.
    runs_dir = (SESSIONS / run_id / "runs").resolve()
    if SESSIONS.resolve() not in runs_dir.parents or not runs_dir.is_dir():
        raise HTTPException(404, "that run has not finished, or produced nothing")
    candidates = sorted((d for d in runs_dir.glob("*") if (d / "results.json").exists()),
                        key=lambda d: d.stat().st_mtime, reverse=True)
    if not candidates:
        raise HTTPException(404, "that run has not finished, or produced nothing")
    return _known_run_dir(candidates[0])


@app.get("/api/run/{run_id}/results")
def results(run_id: str) -> dict[str, Any]:
    """The run's own account. Every figure the page shows resolves to this."""
    return runner.results(_run_dir(run_id))


@app.get("/api/run/{run_id}/document/{fmt}")
def document(run_id: str, fmt: str) -> FileResponse:
    docs = runner.documents(_run_dir(run_id))
    if fmt not in docs:
        raise HTTPException(404, f"this run compiled {sorted(docs) or 'nothing'}")
    return FileResponse(docs[fmt], media_type="application/pdf",
                        filename=docs[fmt].name)


# --- the interview ------------------------------------------------------- #

class OpenIn(BaseModel):
    where: str    # the officer's own words, e.g. "Ngara, Tanzania" — not yet
                  # checked against anything. The agent resolves it, in the
                  # interview's own opening turn, via the `resolve_place` tool.
    domain: str = "water"
    officer: str


class SayIn(BaseModel):
    text: str


# A turn is prepared and then streamed, for the same reason a run is: EventSource
# can only issue a GET, and a turn is worth watching rather than waiting through.
_turns: dict[str, str | None] = {}


def _session(sid: str) -> session.Session:
    s = session.get(sid)
    if s is None:
        raise HTTPException(404, f"no interview {sid}")
    return s


@app.post("/api/interview")
def open_interview(req: OpenIn) -> dict[str, Any]:
    if not req.officer.strip():
        raise HTTPException(422, "an interview needs the name and role of the "
                                 "officer conducting it; every decision is "
                                 "recorded against it")
    if not req.where.strip():
        raise HTTPException(422, "type a place — the agent resolves it, but it "
                                 "needs something to resolve")
    base = {"where": req.where.strip(), "domain": req.domain,
            "country": "", "adm2": "", "iso3": ""}
    try:
        s = session.create(base, req.officer.strip())
    except claude.Unavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {**s.state(),
            "agents": [{"name": a.name, "tools": list(a.tools),
                        "read_only": a.read_only,
                        "description": a.description.splitlines()[0]
                        if a.description else ""}
                       for a in prompts.agents()],
            "tools": [{"name": t["name"], "description": t["description"]}
                      for t in claude.TOOLS]}


@app.get("/api/interview/{sid}")
def interview_state(sid: str) -> dict[str, Any]:
    return _session(sid).state()


@app.get("/api/interviews")
def interviews() -> list[dict[str, Any]]:
    return session.listing()


@app.post("/api/interview/{sid}/say")
def say(sid: str, req: SayIn) -> dict[str, Any]:
    s = _session(sid)
    tid = f"{sid}:{len(s.iv.utterances)}"
    _turns[tid] = req.text if req.text.strip() else None
    return {"turn": tid}


@app.get("/api/interview/{sid}/turn/{n}")
async def turn_stream(sid: str, n: int) -> StreamingResponse:
    s = _session(sid)
    tid = f"{sid}:{n}"
    if tid not in _turns:
        raise HTTPException(404, "no turn is waiting under that id")
    said = _turns.pop(tid)

    async def events():
        try:
            async for ev in s.iv.advance_events(said):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except claude.Unavailable as exc:
            # A busy API is not a reason to lose the turn silently — the same
            # turn can be sent again once it clears.
            yield "data: " + json.dumps({
                "kind": "unavailable", "transient": getattr(exc, "transient", False),
                "text": str(exc)}, ensure_ascii=False) + "\n\n"
        finally:
            s.save()
        yield "data: " + json.dumps({"kind": "state", "state": s.state()},
                                    ensure_ascii=False) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/interview/{sid}/card/{key}")
async def card_for_key(sid: str, key: str) -> dict[str, Any]:
    """One named card, priced against this district's own data.

    The frontend calls this with the key the agent itself declared via
    `present_options` this turn — never a guess at what the agent asked, so
    it cannot show a card for a different decision than the one the agent's
    own words are about.
    """
    card = await _session(sid).card_for(key)
    if card is None:
        raise HTTPException(404, f"{key!r} is not a decision this register declares")
    return card


@app.get("/api/interview/{sid}/geo")
def interview_geo(sid: str) -> dict[str, Any]:
    """The map layers for this interview's current plan, and whether they
    reconcile with the account that plan is written in."""
    s = _session(sid)
    if not s.iv.last_run:
        raise HTTPException(404, "this interview has no plan yet")
    return geo.layers(_known_run_dir(s.iv.last_run["run_dir"]))


@app.get("/api/interview/{sid}/document/{fmt}")
def interview_document(sid: str, fmt: str) -> FileResponse:
    """The interview's own plan, compiled — same idea as `/api/run/{id}/document`,
    but for a run produced by `run_assessment` inside this interview rather than
    the standalone `/api/run` flow, which is the only kind `_run_dir` knows."""
    s = _session(sid)
    if not s.iv.last_run:
        raise HTTPException(404, "this interview has no plan yet")
    docs = runner.documents(_known_run_dir(s.iv.last_run["run_dir"]))
    if fmt not in docs:
        raise HTTPException(404, f"this run compiled {sorted(docs) or 'nothing'}")
    return FileResponse(docs[fmt], media_type="application/pdf",
                        filename=docs[fmt].name)


@app.get("/api/interview/{sid}/record")
def interview_record(sid: str) -> FileResponse:
    s = _session(sid)
    if not s.iv.last_run:
        raise HTTPException(404, "this interview has no plan yet")
    f = _known_run_dir(s.iv.last_run["run_dir"]) / "RUN_RECORD.md"
    if not f.exists():
        raise HTTPException(404, "this run has no RUN_RECORD.md")
    return FileResponse(f, media_type="text/markdown", filename=f.name)


@app.get("/api/run-file/{run_id}/{name}")
def run_file(run_id: str, name: str) -> FileResponse:
    """One rendered figure from a run, by name. Nothing else is servable.

    Served only out of `_run_dirs`, which this process populates itself from
    server-held state whenever it resolves a run's directory elsewhere (see
    `_known_run_dir`). `run_id` never touches the filesystem directly, so a
    request cannot glob or traverse its way into a run this process did not
    itself produce, let alone one belonging to another session.
    """
    if name not in {"map_situation.png", "map_plan.png", "fig_framework.png"}:
        raise HTTPException(404, "that is not a figure a run produces")
    run_dir = _run_dirs.get(run_id)
    if run_dir is None:
        raise HTTPException(404, "no such figure")
    p = run_dir / name
    if not p.exists():
        raise HTTPException(404, "no such figure")
    return FileResponse(p, media_type="image/png")
