"""Build-time data preparation. Never runs while a session is open.

The only real bottleneck in serving this is data acquisition. WorldPop ships a
national population raster — 465 MB for Tanzania — and refuses HTTP range
requests, so it is cached whole. Everything else is small, but every one of them
is a request to somebody else's server, and a demonstration that depends on four
of those staying up at the moment someone is watching is a demonstration that
will fail.

After a warm, a session issues no outward request at all.

    python -m web.prewarm warm                 fetch and price the listed districts
    python -m web.prewarm report               what the cache holds and what it costs

This does not write to `cache/` itself, and needs no exception to the rule in
`web/README.md` that nothing here does. The national rasters still land in
`cache/`, but through the same `python -m siting.cli` subprocess the rest of
`web/` calls; the priced option tables land in `sessions/_pricing/`, which the
contract already allows. All this does is pay for both before somebody is
waiting.

Nothing here destroys anything, and that is deliberate. An earlier version
carried a `crop` command that rewrote a national raster in place, narrowing it
to the districts listed below. It traded a permanent limit on what the
deployment could analyse — a limit recorded nowhere but inside a .tif — for a
few hundred megabytes of disk. Disk is cheap and a silently narrowed area of
interest is not, so the command was removed rather than guarded.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from . import pricing, runner
from .runner import REPO, RunSpec

CACHE = REPO / "cache"

# The districts a deployment carries. Anything else is fetched on demand, and
# the interface says so rather than pretending to be instant.
DISTRICTS: tuple[dict[str, Any], ...] = (
    {"country": "Tanzania", "adm2": "Ngara", "iso3": "TZA", "domain": "water"},
    {"country": "Uganda", "adm2": "Kiryandongo", "iso3": "UGA", "domain": "water"},
    {"country": "Uganda", "adm2": "Masindi", "iso3": "UGA", "domain": "water"},
    {"country": "Tanzania", "adm2": "Namtumbo", "iso3": "TZA", "domain": "water"},
)

# Option pricing is per budget, so a deployment has to choose which budgets open
# instantly. Anything else is priced on demand, which costs about half a minute.
BUDGETS = (3, 5, 10)


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit != "GB" else f"{n:.1f} GB"
        n /= 1024
    return ""


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def report() -> int:
    print("cache")
    total = 0
    for sub in sorted(CACHE.glob("*")):
        size = _dir_size(sub) if sub.is_dir() else sub.stat().st_size
        total += size
        print(f"  {_fmt(size):>10}  {sub.relative_to(REPO)}")
    print(f"  {_fmt(total):>10}  total\n")

    print("priced option tables")
    any_priced = False
    for base in DISTRICTS:
        for b in BUDGETS:
            f = pricing._cache_file(base, b)
            mark = "ok " if f.exists() else "   "
            any_priced = any_priced or f.exists()
            print(f"  {mark} {base['adm2']:<14} budget {b:<3} {f.name}")
    if not any_priced:
        print("  (none; run `warm`)")
    return 0


async def warm(districts=DISTRICTS, budgets=BUDGETS) -> int:
    """One probe per district pulls every source; then price the option tables."""
    for base in districts:
        print(f"\n{base['adm2']}, {base['country']}")
        spec = RunSpec(country=base["country"], adm2=base["adm2"], iso3=base["iso3"],
                       domain=base["domain"], out_dir=REPO / "sessions" / "_prewarm",
                       mode="auto", budget=1, fmt="bundle")
        stage = None
        async for ev in runner.stream(spec):
            if ev.kind == "stage" and ev.stage != stage:
                stage = ev.stage
                print(f"  {stage}")
            if ev.kind == "done" and ev.data:
                print(f"  sources warmed ({ev.data['outcome']})")
        for b in budgets:
            print(f"  pricing at budget {b} ...", end="", flush=True)
            await pricing.price(base, b)
            print(" done")
    print("\nA session now issues no outward request for these districts.")
    return 0


def _main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "report"
    if cmd == "report":
        return report()
    if cmd == "warm":
        return asyncio.run(warm())
    print("\n".join(l[4:] for l in __doc__.splitlines()
                     if l.startswith("    python")))
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
