"""Fetch the warmed retrieval cache.

The only slow retrieval in this system is the population raster. WorldPop ships
one per country and refuses HTTP range requests, so it has to be pulled whole:
108 MB for Uganda, 462 MB for Tanzania. A first run without them spends that
download before it can say anything.

The cache is therefore published separately, and is not in this repository.
580 MB of Git LFS would exhaust a free GitHub account's entire quota — 1 GB of
storage and 1 GB of transfer a month — and the second person to clone would be
refused. It lives on the Hugging Face Hub instead, where a dataset repository
carries it at no cost.

    python scripts/fetch_cache.py            # everything, about 580 MB
    python scripts/fetch_cache.py --country UGA   # Uganda only, about 12 MB + 108 MB
    python scripts/fetch_cache.py --check    # report what is present, fetch nothing

Nothing here is required. Without a cache the analysis still runs; it just goes
to WorldPop, GADM, WPdx and the friction surface itself, and takes as long as
that takes. Skipping this step costs time, never correctness.

The cached files are open data under their own licences, recorded in
`handbooks/*.yaml`: WorldPop and WPdx CC BY 4.0, the friction surface CC BY 4.0,
GADM free for academic and non-commercial use. Attribution belongs to them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ID = "YibinGao/oasis-siting-cache"
CACHE = Path(__file__).resolve().parent.parent / "cache"

# What a country needs, as glob patterns. The raster is the expensive one; the
# rest are kilobytes and would refetch in seconds if they were missing.
BY_COUNTRY: dict[str, tuple[str, ...]] = {
    "UGA": ("raster/uga_*.tif", "boundaries/gadm41_UGA_*.gpkg",
            "wpdx_uganda_*.parquet", "friction/*.tif"),
    "TZA": ("raster/tza_*.tif", "boundaries/gadm41_TZA_*.gpkg",
            "wpdx_tanzania_*.parquet", "friction/*.tif"),
}


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def report() -> int:
    """What is on disk. Says nothing about whether it is current."""
    if not CACHE.exists():
        print(f"no cache directory at {CACHE}")
        return 1
    # snapshot_download keeps its own bookkeeping under cache/.cache/. It is not
    # data and listing it only obscures what was actually fetched.
    files = sorted(p for p in CACHE.rglob("*") if p.is_file()
                   and not any(part.startswith(".")
                               for part in p.relative_to(CACHE).parts))
    if not files:
        print(f"{CACHE} is empty")
        return 1
    total = sum(p.stat().st_size for p in files)
    print(f"{CACHE}  —  {len(files)} files, {_human(total)}\n")
    for p in files:
        print(f"  {_human(p.stat().st_size):>9}  {p.relative_to(CACHE)}")
    return 0


def fetch(country: str | None) -> int:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("this needs the Hugging Face client:\n\n    pip install huggingface_hub\n",
              file=sys.stderr)
        return 2

    patterns = None
    if country:
        key = country.upper()
        if key not in BY_COUNTRY:
            print(f"no cache is published for {country}. Known: "
                  f"{', '.join(sorted(BY_COUNTRY))}.\n"
                  "Any other country is retrieved live on the first run.",
                  file=sys.stderr)
            return 2
        patterns = list(BY_COUNTRY[key])

    print(f"fetching from https://huggingface.co/datasets/{REPO_ID}")
    if patterns:
        print(f"  limited to {country.upper()}: {', '.join(patterns)}")
    print("  the population raster is the large one; this may take a few minutes\n")

    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        local_dir=str(CACHE),
        allow_patterns=patterns,
    )
    print()
    return report()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--country", metavar="ISO3",
                    help="fetch one country's cache only (UGA, TZA)")
    ap.add_argument("--check", action="store_true",
                    help="report what is already present and fetch nothing")
    args = ap.parse_args()
    return report() if args.check else fetch(args.country)


if __name__ == "__main__":
    raise SystemExit(main())
