"""WorldPop gridded population, read as a demand surface for an area of interest."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.windows import from_bounds

from .. import handbook
from ..provenance import Ledger, Pull

CACHE = Path("cache/raster")


def latest_url(iso3: str) -> tuple[str, str]:
    """Return (geotiff url, population year) for the most recent WorldPop grid."""
    hb = handbook.load("worldpop")
    url = hb.query["rest"].format(iso3=iso3.upper())
    data = requests.get(url, timeout=90).json()["data"]
    newest = sorted(data, key=lambda d: int(d["popyear"]))[-1]
    tif = next(f for f in newest["files"] if f.endswith(".tif"))
    return tif, newest["popyear"]


def ensure_raster(iso3: str) -> tuple[Path, str, bool]:
    """The national grid, downloaded once and thereafter read from the cache.

    Returns (path, population year, whether this call downloaded it). The server
    refuses range requests, so a windowed read over HTTP is not available and the
    file is cached whole.

    The index lookup that finds the newest population year is itself a network
    call, and it used to run before the cache was consulted. A cached grid was
    therefore unreachable without a connection: a run with every byte it needed
    already on disk failed at the index, several hundred megabytes into a cache
    whose whole purpose is to make a run repeatable offline. Where the index
    cannot be reached and a cached grid for this country exists, that grid is used
    and its population year read back from the filename.

    Where neither is available the run stops. The population surface is the
    denominator of every coverage figure in the assessment, and there is nothing
    defensible to put in its place.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        url, year = latest_url(iso3)
    except Exception as exc:
        cached = sorted(CACHE.glob(f"{iso3.lower()}_ppp_*.tif"))
        if not cached:
            raise RuntimeError(
                f"the WorldPop index could not be reached "
                f"({type(exc).__name__}: {str(exc)[:120]}) and no cached population "
                f"grid for {iso3.upper()} was found under {CACHE}. The population "
                f"surface is the denominator of every coverage figure in the "
                f"assessment, so the run stops rather than proceed without one."
            ) from exc
        dest = cached[-1]
        return dest, dest.stem.split("_")[-1], False

    dest = CACHE / Path(url).name
    if dest.exists():
        return dest, year, False
    with requests.get(url, stream=True, timeout=1800) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    return dest, year, True


def demand_grid(
    iso3: str,
    bounds: tuple[float, float, float, float],
    ledger: Ledger,
    coarsen: int = 5,
    min_people: float = 1.0,
) -> pd.DataFrame:
    """Population cells inside `bounds` as (lat, lon, pop) rows.

    `coarsen` aggregates the 100 m grid into blocks to keep the cost matrix
    tractable; 5 gives roughly 500 m cells, which is well under the walking
    radius the water domain uses and so does not bias coverage.
    """
    hb = handbook.load("worldpop")
    path, year, downloaded = ensure_raster(iso3)

    pull = Pull(
        source="WorldPop",
        handbook="worldpop",
        endpoint=hb.endpoint,
        query=f"{iso3.upper()} ppp {year}, bbox "
              f"{float(bounds[0]):.4f} {float(bounds[1]):.4f} {float(bounds[2]):.4f} {float(bounds[3]):.4f} "
              f"(WGS 84), aggregated {coarsen}x{coarsen}",
        licence=hb.licence,
        note=f"population year {year}, ~{coarsen * 100} m aggregated cells",
    )
    if downloaded:
        pull.downloaded(path)
    else:
        pull.served_from_cache(path)

    minx, miny, maxx, maxy = bounds
    with rasterio.open(path) as src:
        window = from_bounds(minx, miny, maxx, maxy, src.transform)
        arr = src.read(1, window=window, masked=True).filled(0.0)
        wt = src.window_transform(window)

    arr = np.where(arr < 0, 0.0, arr)  # clip_negative

    h, w = arr.shape
    h2, w2 = h // coarsen * coarsen, w // coarsen * coarsen
    block = arr[:h2, :w2].reshape(h2 // coarsen, coarsen, w2 // coarsen, coarsen).sum(axis=(1, 3))

    # Counted in aggregated cells throughout, so the returned, retained and
    # discarded figures in the retrieval record share one unit. The number of
    # source raster cells read is carried in the note instead.
    pull.rows_raw = int(block.size)
    pull.note = (f"population year {year}; {arr.size:,} source cells at ~100 m read "
                 f"over the window, aggregated {coarsen}x{coarsen} into "
                 f"{block.size:,} cells of ~{coarsen * 100} m")

    rows, cols = np.nonzero(block >= min_people)
    pop = block[rows, cols]
    # Cell centres in the coarsened grid.
    xs, ys = rasterio.transform.xy(
        wt, (rows * coarsen + coarsen / 2.0), (cols * coarsen + coarsen / 2.0)
    )

    df = pd.DataFrame({"lat": np.asarray(ys), "lon": np.asarray(xs), "pop": pop})
    pull.drop("cells below the population floor", int(block.size - len(df)))
    pull.rows_clean = len(df)
    ledger.add(pull)
    return df
