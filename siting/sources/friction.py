"""Walking travel time over the Malaria Atlas Project friction surface.

Straight-line distance is a stand-in for travel, and a poor one where a river, a
ridge or the absence of a path stands between a household and a water point.
This module retrieves a published friction surface so that reach can be measured
in minutes of walking instead.

The walking-only surface is the right one here. The motorised surface assumes
road access and would understate the time a household on foot actually spends,
which is the quantity a service radius is trying to bound.

Two readings of that surface live in this module, and only one of them is in use.

`sample` reads the friction value at a point, and `compile.coverage_by_travel_time`
uses it to turn the friction at each candidate into an effective local speed,
then tests coverage as a reach in metres that varies by candidate. Every coverage
figure this system reports on the walking-time basis is built that way. It
represents the ground at the site and not the ground in between, so a river
between a candidate and a settlement does not reduce the reach the model claims:
conservative where the candidate itself sits on difficult ground, optimistic
where it sits on easy ground beside an obstacle. The error is not one-sided and
the assessment must not describe it as though it were.

`travel_time_from` is the method that would be correct, an anisotropic least-cost
accumulation with `skimage.graph.MCP_Geometric`, eight-connected and correctly
weighted for diagonal steps, which is what the World Bank's GOSTnetsraster and
WHO's AccessMod both do. **Nothing calls it.** One accumulation per origin is not
tractable at the several thousand candidates a district lattice produces. It is
kept as the implementation a future version would move to if the candidate count
were brought low enough to afford it.

This docstring claimed the least-cost path was what ran, for as long as it was
not. The approximation is disclosed in the assessment and in
handbooks/map_friction.yaml, and this file now says the same thing they do.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.transform import rowcol
from rasterio.warp import Resampling, reproject

from .. import handbook
from ..provenance import Ledger, Pull

CACHE = Path("cache/friction")
WCS = "https://data.malariaatlas.org/geoserver/Accessibility/ows"

SURFACES = {
    "walking": "Accessibility__202001_Global_Walking_Only_Friction_Surface",
    "motorised": "Accessibility__202001_Global_Motorized_Friction_Surface",
}


def fetch(
    bounds: tuple[float, float, float, float],
    ledger: Ledger | None = None,
    surface: str = "walking",
    pad_deg: float = 0.05,
) -> tuple[np.ndarray, rasterio.Affine, Pull]:
    """Retrieve the friction surface over an area of interest.

    Values are minutes required to traverse one metre of the cell. The window is
    padded so that a candidate sitting on the edge of the area of interest still
    has surface beneath it to be sampled from. Were the least-cost traverse in
    use, the padding would also matter for the reason this docstring used to give
    on its own: a path may reasonably leave the district and come back, and
    clipping to the boundary would forbid a route that exists.
    """
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of {sorted(SURFACES)}")

    hb = handbook.load("map_friction")
    CACHE.mkdir(parents=True, exist_ok=True)
    minx, miny, maxx, maxy = bounds
    minx, miny = minx - pad_deg, miny - pad_deg
    maxx, maxy = maxx + pad_deg, maxy + pad_deg

    slug = f"{surface}_{minx:.3f}_{miny:.3f}_{maxx:.3f}_{maxy:.3f}.tif".replace("-", "m")
    dest = CACHE / slug

    pull = Pull(
        source="MAP friction surface",
        handbook="map_friction",
        endpoint=WCS,
        query=f"{SURFACES[surface]}, bbox {minx:.4f} {miny:.4f} {maxx:.4f} {maxy:.4f} (WGS 84)",
        licence=hb.licence,
        note=f"{surface}-only friction, minutes per metre",
    )

    if not dest.exists():
        r = requests.get(WCS, timeout=300, params={
            "service": "WCS", "version": "2.0.1", "request": "GetCoverage",
            "coverageId": SURFACES[surface], "format": "image/geotiff",
            "subset": [f"Long({minx},{maxx})", f"Lat({miny},{maxy})"],
        })
        r.raise_for_status()
        if not r.content.startswith((b"II", b"MM")):
            raise RuntimeError(
                "the friction service did not return a GeoTIFF; "
                f"first bytes were {r.content[:40]!r}")
        dest.write_bytes(r.content)
        pull.downloaded(dest)
    else:
        pull.served_from_cache(dest)

    with rasterio.open(dest) as src:
        arr = src.read(1, masked=True)
        transform = src.transform
        pull.rows_raw = int(arr.size)

    filled = arr.filled(np.nan)
    bad = ~np.isfinite(filled) | (filled <= 0)
    pull.drop("cells with no friction value, treated as impassable", int(bad.sum()))
    filled[bad] = np.inf
    pull.rows_clean = int((~bad).sum())

    if ledger is not None:
        ledger.add(pull)
    return filled, transform, pull


def travel_time_from(
    friction: np.ndarray,
    transform: rasterio.Affine,
    origins_lat: np.ndarray,
    origins_lon: np.ndarray,
) -> np.ndarray:
    """Minutes of walking from the nearest origin to every cell.

    One accumulation from all origins at once rather than one per origin: the
    least-cost surface from a set of sources already gives, for each cell, the
    time from whichever source is cheapest to reach.

    Not currently called by any coverage path. See the module docstring for what
    runs instead, and why.
    """
    from skimage.graph import MCP_Geometric

    h, w = friction.shape
    # Cell traverse cost in minutes: minutes per metre times the cell's width on
    # the ground. Latitude-corrected, because a degree of longitude is shorter
    # than a degree of latitude everywhere but the equator.
    lat_mid = transform.f + transform.e * h / 2.0
    metres_x = abs(transform.a) * 111_320.0 * max(np.cos(np.radians(lat_mid)), 0.2)
    metres_y = abs(transform.e) * 111_320.0
    cell_m = (metres_x + metres_y) / 2.0

    cost = friction * cell_m
    cost[~np.isfinite(cost)] = np.inf

    rows, cols = rowcol(transform, np.asarray(origins_lon, float), np.asarray(origins_lat, float))
    rows, cols = np.atleast_1d(rows), np.atleast_1d(cols)
    keep = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
    starts = list(zip(rows[keep].tolist(), cols[keep].tolist()))
    if not starts:
        return np.full(friction.shape, np.inf)

    mcp = MCP_Geometric(cost, fully_connected=True)
    acc, _ = mcp.find_costs(starts)
    return np.asarray(acc, dtype=float)


def sample(surface: np.ndarray, transform: rasterio.Affine,
           lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Read a raster at scattered points, infinity outside the raster."""
    h, w = surface.shape
    rows, cols = rowcol(transform, np.asarray(lon, float), np.asarray(lat, float))
    rows, cols = np.atleast_1d(rows), np.atleast_1d(cols)
    out = np.full(len(rows), np.inf)
    ok = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
    out[ok] = surface[rows[ok], cols[ok]]
    return out


def calibrate(friction: np.ndarray, radius_m: float) -> dict[str, float]:
    """What a straight-line radius corresponds to in minutes over this terrain.

    Reported so a district that has chosen a distance can see the walking time it
    implies here, and decide whether that is what it meant.
    """
    finite = friction[np.isfinite(friction)]
    if finite.size == 0:
        return {}
    return {
        "median_minutes_per_km": float(np.median(finite) * 1000),
        "p90_minutes_per_km": float(np.quantile(finite, 0.9) * 1000),
        "radius_m": radius_m,
        "median_minutes_at_radius": float(np.median(finite) * radius_m),
        "p90_minutes_at_radius": float(np.quantile(finite, 0.9) * radius_m),
    }
