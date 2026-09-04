"""Generator-evaluator separation: the scoring reviewer.

NORA enforces that the entity generating content never evaluates its own output,
and scores manuscripts on five dimensions with a weighted floor. The same
mechanism applies here, but the dimensions are different in kind: this system
produces a decision, not a manuscript, so novelty and clarity are the wrong
questions. What a district officer needs to know before acting is whether the
data was adequate, whether the method fitted the question, whether the spatial
reasoning holds, whether the account is complete enough to be audited, and
whether the output is actually usable.

Context isolation is the point of the module boundary. The reviewer receives
`results.json` and nothing else: not the instance, not the solver, not the
override reasons, not this module's caller. It cannot see how the plan was
reached, only what is claimed and what evidence is offered for it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# Weighted average must clear ACCEPT_AT and every dimension must clear its floor.
DIMENSIONS: dict[str, dict[str, Any]] = {
    "data adequacy": {
        "weight": 0.25, "floor": 5.0,
        "asks": "Are the sources authoritative, current enough for the claim, and "
                "sufficient in coverage for the area of interest?",
    },
    "method fitness": {
        "weight": 0.20, "floor": 5.0,
        "asks": "Does the location model answer the question that was asked, and "
                "is its optimality bounded and stated?",
    },
    "spatial rigour": {
        "weight": 0.25, "floor": 6.0,
        "asks": "Projected CRS, union coverage, aggregation sensitivity, boundary "
                "exposure, equity: are the required diagnostics present and passing?",
    },
    "accountability": {
        "weight": 0.20, "floor": 6.0,
        "asks": "Does every figure resolve to a recorded retrieval, is every human "
                "override recorded with its reason and its cost, and are source "
                "anomalies disclosed rather than resolved silently?",
    },
    "actionability": {
        "weight": 0.10, "floor": 4.0,
        "asks": "Can an officer act on this: coordinates, a ranking whose order "
                "matters, a stated basis per site, and honest limitations?",
    },
}

# Retained only for a results file written before `review_floor` became a
# recorded decision. A current run reads its floor from its own register, which
# is carried in the results file the reviewer is handed.
ACCEPT_AT = 6.5


@dataclass
class Score:
    dimension: str
    value: float
    weight: float
    floor: float
    basis: str

    @property
    def passes(self) -> bool:
        return self.value >= self.floor

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passes"] = self.passes
        return d


@dataclass
class Review:
    scores: list[Score]
    weighted: float
    decision: str                      # "issue" | "revise"
    reviewer: str
    accept_at: float = ACCEPT_AT
    action_items: list[str] = field(default_factory=list)

    @property
    def may_issue(self) -> bool:
        return self.decision == "issue"

    @property
    def below_floor(self) -> list[Score]:
        """The dimensions that did not clear their own floor."""
        return [s for s in self.scores if not s.passes]

    @property
    def why_not_issued(self) -> str:
        """Why the decision was `revise`, naming the condition that actually failed.

        Two independent conditions gate an issue: the weighted score must clear
        the floor this district recorded, and every dimension must clear its own.
        Reporting either failure as "below the floor" was false whenever a
        dimension failed while the weighted score sat above it, which is the
        commoner of the two and the one an officer is least able to guess.
        """
        if self.may_issue:
            return ""
        parts = []
        if self.weighted < self.accept_at:
            parts.append(f"the weighted score is {self.weighted:.2f}, below the "
                         f"{self.accept_at} recorded for this district")
        for s in self.below_floor:
            parts.append(f"{s.dimension} scores {s.value:.1f} against a floor "
                         f"of {s.floor:.1f}")
        return "; ".join(parts) or "the reviewer did not recommend issue"

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer": self.reviewer,
            "weighted": round(self.weighted, 2),
            "accept_at": self.accept_at,
            "decision": self.decision,
            "why_not_issued": self.why_not_issued,
            "scores": [s.to_dict() for s in self.scores],
            "action_items": self.action_items,
            "dimensions": {k: {"weight": v["weight"], "floor": v["floor"], "asks": v["asks"]}
                           for k, v in DIMENSIONS.items()},
        }


# --------------------------------------------------------------------------- #
# deterministic reviewer
# --------------------------------------------------------------------------- #

def _score_rules(doc: dict[str, Any]) -> list[Score]:
    """A reviewer that reads only the results file, and can be re-run by anyone.

    Deliberately not an LLM by default: the scores below are reproducible from
    the same input, which is what makes a review floor meaningful rather than a
    number that moves between runs.
    """
    checks = {c["check"]: c for c in doc["evaluation"]}
    levels = {k: v["level"] for k, v in checks.items()}
    n_pass = sum(1 for v in levels.values() if v == "pass")
    n_flag = sum(1 for v in levels.values() if v == "flag")
    scope, base, plan = doc["scope"], doc["baseline"], doc["plan"]
    # Read out of the account rather than passed in, so the reviewer still sees
    # only this file and nothing about how the run reached it.
    stances = {d["key"]: d.get("stance", "")
               for d in doc.get("decisions", [])}
    out: list[Score] = []

    # --- data adequacy --------------------------------------------------- #
    age = float(scope.get("median_record_age_years") or 0)
    v = 9.0
    notes = []
    if stances.get("data_currency_accepted") == "yes":
        # The age is not hidden: the data currency check reports it either way and
        # quotes the officer's reason. What their acceptance changes is whether the
        # account is fit to act on, which is the question this score asks.
        notes.append(f"median record {age:.1f} years old, put to the accountable "
                     f"officer and accepted with a recorded reason")
    elif age > 10:
        v -= 4.0; notes.append(f"median record {age:.1f} years old, no position recorded")
    elif age > 5:
        v -= 2.0; notes.append(f"median record {age:.1f} years old, no position recorded")
    elif age > 2:
        v -= 1.0; notes.append(f"median record {age:.1f} years old, no position recorded")
    if len(doc["provenance"]) < 2:
        v -= 2.0; notes.append("fewer than two independent sources")
    unrec = [a for a in doc.get("anomalies", []) if a["kind"] == "semantics"]
    if unrec:
        v -= 0.5; notes.append(f"{len(unrec)} semantic anomalies in the register")
    out.append(Score("data adequacy", max(0.0, min(10.0, v)),
                     DIMENSIONS["data adequacy"]["weight"], DIMENSIONS["data adequacy"]["floor"],
                     "; ".join(notes) or "sources current and sufficient"))

    # --- method fitness --------------------------------------------------- #
    v = 7.5
    # Read from the plan rather than asserted here: the reviewer used to describe
    # every run as greedy maximum covering, including runs solved for the
    # worst-served population.
    notes = [plan.get("guarantee") or "the plan records no optimality guarantee"]
    bench = doc.get("benchmark") or {}
    if bench.get("status") == "solved":
        v += 1.5; notes.append("exact solver benchmark present")
    else:
        notes.append(f"no exact benchmark ({bench.get('status', 'not run')})")
    if levels.get("budget") == "flag":
        v -= 1.0; notes.append("budget not fully placed")
    # The basis is a recorded decision, so read it. Matching the substring
    # "travel time" against `coverage_rule` matched neither wording the domain
    # adapter produces, so it docked every run and then told the reader the
    # opposite of what `coverage_basis` says two fields away.
    basis = scope.get("coverage_basis")
    if basis == "walking_time":
        notes.append("reach measured as walking time from the local friction value "
                     "at each candidate, not as a least-cost traverse")
    elif basis == "straight_line":
        v -= 1.0
        notes.append("reach measured as straight-line distance rather than walking "
                     "time over terrain")
    else:
        # Absent is not the same as straight-line. An account that does not say
        # which basis it used is worth the same dock and a different sentence.
        v -= 1.0
        notes.append("the account does not record which basis reach was measured on")
    out.append(Score("method fitness", max(0.0, min(10.0, v)),
                     DIMENSIONS["method fitness"]["weight"], DIMENSIONS["method fitness"]["floor"],
                     "; ".join(notes)))

    # --- spatial rigour --------------------------------------------------- #
    required = ["coordinate reference system", "coverage arithmetic",
                "aggregation sensitivity", "boundary effect", "equity",
                "cartographic consistency"]
    present = [r for r in required if r in levels]
    v = 10.0 - 2.0 * (len(required) - len(present))
    for r in present:
        if levels[r] == "flag":
            v -= 1.0
        elif levels[r] == "reject":
            v -= 4.0
    notes = [f"{len(present)} of {len(required)} required diagnostics present"]
    if "unprojected" in str(scope.get("crs", "")):
        v -= 4.0; notes.append("no projected CRS")
    else:
        notes.append(f"distances in {scope.get('crs')}")
    out.append(Score("spatial rigour", max(0.0, min(10.0, v)),
                     DIMENSIONS["spatial rigour"]["weight"], DIMENSIONS["spatial rigour"]["floor"],
                     "; ".join(notes)))

    # --- accountability --------------------------------------------------- #
    v = 6.0
    notes = []
    if levels.get("provenance") == "pass":
        v += 2.0; notes.append("every figure resolves to a recorded retrieval")
    if doc.get("anomalies"):
        v += 1.0; notes.append(f"{len(doc['anomalies'])} source anomalies disclosed")
    if doc.get("audit"):
        priced = all("delta_covered" in a for a in doc["audit"])
        if priced:
            v += 1.0; notes.append(f"{len(doc['audit'])} overrides recorded with their cost")
        unreasoned = [a for a in doc["audit"] if not (a.get("reason") or "").strip()]
        if unreasoned:
            v -= 3.0; notes.append(f"{len(unreasoned)} overrides without a reason")
    if not doc["run"].get("provenance_hash"):
        v -= 2.0; notes.append("no provenance hash")
    out.append(Score("accountability", max(0.0, min(10.0, v)),
                     DIMENSIONS["accountability"]["weight"], DIMENSIONS["accountability"]["floor"],
                     "; ".join(notes) or "no accountability record"))

    # --- actionability ---------------------------------------------------- #
    v = 5.0
    notes = []
    if plan["sites"]:
        v += 2.0; notes.append(f"{len(plan['sites'])} sites with coordinates and a stated basis")
    if doc.get("curve"):
        v += 1.5; notes.append("marginal coverage curve supports a budget decision")
    if doc.get("sensitivity"):
        v += 1.0; notes.append(f"{len(doc['sensitivity'])} sensitivity scenarios")
    if base["covered_share"] >= plan["covered_share"]:
        v -= 4.0; notes.append("the plan does not improve on the baseline")
    out.append(Score("actionability", max(0.0, min(10.0, v)),
                     DIMENSIONS["actionability"]["weight"], DIMENSIONS["actionability"]["floor"],
                     "; ".join(notes)))

    return out


def _actions(scores: list[Score], doc: dict[str, Any]) -> list[str]:
    """What would have to change for a failing dimension to clear its floor."""
    items: list[str] = []
    for s in scores:
        if s.passes:
            continue
        if s.dimension == "data adequacy":
            items.append("Obtain a more recent survey of the register, or state in the "
                         "summary that findings describe the surveyed state only.")
        elif s.dimension == "method fitness":
            if doc["scope"].get("coverage_basis") == "walking_time":
                items.append("Run the exact solver benchmark to bound the gap between "
                             "the greedy plan and the optimum.")
            else:
                items.append("Run the exact solver benchmark, or adopt walking time "
                             "over a friction surface in place of straight-line "
                             "distance.")
        elif s.dimension == "spatial rigour":
            failing = [c["check"] for c in doc["evaluation"] if c["level"] != "pass"]
            items.append("Resolve the failing diagnostics before issue: " + ", ".join(failing))
        elif s.dimension == "accountability":
            items.append("Every override requires a recorded reason, and every figure a "
                         "recorded retrieval.")
        elif s.dimension == "actionability":
            items.append("The plan must improve on the baseline and carry per-site basis "
                         "statements.")
    return items


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def review(results_path: Path, reviewer: str = "rules") -> Review:
    """Score a run from its results file alone.

    `results_path` rather than the objects themselves, on purpose: the reviewer
    is handed the same artefact a reader would get, so it cannot score the
    intention behind the plan, only the account given of it.
    """
    doc = json.loads(Path(results_path).read_text(encoding="utf-8"))
    # The floor is the district's, recorded in its decisions file and carried
    # here in the account. The reviewer applies it; it does not set it.
    floor = next((float(d["value"]) for d in doc.get("decisions", [])
                  if d["key"] == "review_floor"), ACCEPT_AT)

    if reviewer == "llm":
        scores, name = _score_llm(doc)
    else:
        scores, name = _score_rules(doc), "deterministic rules over results.json"

    weighted = sum(s.value * s.weight for s in scores)
    floors_met = all(s.passes for s in scores)
    decision = "issue" if (weighted >= floor and floors_met) else "revise"
    return Review(scores=scores, weighted=weighted, accept_at=floor,
                  decision=decision, reviewer=name,
                  action_items=_actions(scores, doc))


def _score_llm(doc: dict[str, Any]) -> tuple[list[Score], str]:
    """Optional model-backed reviewer, in an isolated context.

    Requires ANTHROPIC_API_KEY. Falls back to the deterministic reviewer rather
    than failing the run, and says which was used either way, because a review
    floor that silently changes reviewer is not a floor.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return _score_rules(doc), "deterministic rules (no ANTHROPIC_API_KEY; llm reviewer unavailable)"

    try:
        import anthropic
    except ImportError:
        return _score_rules(doc), "deterministic rules (anthropic package not installed)"

    # The reviewer sees the account, never the reasoning that produced it.
    redacted = {k: v for k, v in doc.items() if k not in ("exhibits", "overrides_source")}
    rubric = "\n".join(
        f"- {k} (weight {v['weight']}, floor {v['floor']}): {v['asks']}"
        for k, v in DIMENSIONS.items())

    prompt = (
        "You are reviewing a siting recommendation prepared for a district "
        "government. You did not produce it and you cannot amend it. Score it on "
        "each dimension from 0 to 10 and give a one-sentence basis for each.\n\n"
        f"{rubric}\n\n"
        "Be sceptical. Reward disclosure of weakness; penalise a claim whose "
        "evidence is not in the document. Reply as JSON: "
        '{"scores":[{"dimension":"...","value":0.0,"basis":"..."}]}\n\n'
        f"Document:\n{json.dumps(redacted, ensure_ascii=False)[:60000]}"
    )

    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model="claude-sonnet-5", max_tokens=2000,
            messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text
        start, end = text.find("{"), text.rfind("}") + 1
        parsed = json.loads(text[start:end])
    except Exception as exc:
        return _score_rules(doc), f"deterministic rules (llm reviewer failed: {str(exc)[:80]})"

    scores = []
    for item in parsed.get("scores", []):
        dim = item.get("dimension", "").strip()
        if dim not in DIMENSIONS:
            continue
        scores.append(Score(dim, float(item.get("value", 0)),
                            DIMENSIONS[dim]["weight"], DIMENSIONS[dim]["floor"],
                            str(item.get("basis", ""))[:300]))
    if len(scores) != len(DIMENSIONS):
        return _score_rules(doc), "deterministic rules (llm reviewer returned an incomplete rubric)"
    return scores, "claude-sonnet-5 in an isolated context, results.json only"
