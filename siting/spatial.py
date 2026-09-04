"""Coordinate reference systems and projected distance.

Working in decimal degrees is the first item on NORA's list of common spatial
mistakes (Zhou et al. 2026, Section 5.2), and this system committed it: every
distance was a great-circle approximation on the sphere and every buffer was a
degree offset. The functions here pick an appropriate projected CRS for the area
of interest and compute distances in that CRS, so a metre in the analysis is a
metre on the ground.

Great-circle distance is retained only as a cross-check, and the disagreement
between the two is reported rather than assumed to be negligible.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import CRS, Transformer


@dataclass
class Projection:
    """The projected CRS chosen for one area of interest, and why."""
    crs: CRS
    epsg: int
    name: str
    rationale: str

    @property
    def label(self) -> str:
        return f"EPSG:{self.epsg} ({self.name})"


def utm_for(bounds: tuple[float, float, float, float]) -> Projection:
    """Pick the UTM zone containing the centroid of the area of interest.

    UTM is conformal and preserves local distance to within about one part in
    2,500 across a zone, which is well inside the tolerance of a service-radius
    analysis. It is the right choice for a district-sized area; a national or
    continental analysis would need an equal-area projection instead.
    """
    minx, miny, maxx, maxy = bounds
    lon_c, lat_c = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    zone = int((lon_c + 180) // 6) + 1
    north = lat_c >= 0
    epsg = (32600 if north else 32700) + zone

    span_deg = maxx - minx
    rationale = (
        f"Area of interest spans {span_deg:.2f} degrees of longitude and is "
        f"centred at {lat_c:.3f}, {lon_c:.3f}, which falls in UTM zone {zone}"
        f"{'N' if north else 'S'}. UTM is conformal and preserves local distance "
        f"to about one part in 2,500 within a zone, which is well inside the "
        f"tolerance of a service-radius analysis."
    )
    if span_deg > 6:
        rationale += (
            " The area of interest is wider than a single UTM zone, so distances "
            "near its edges carry more scale distortion than the figure above."
        )
    return Projection(CRS.from_epsg(epsg), epsg, f"WGS 84 / UTM zone {zone}{'N' if north else 'S'}", rationale)


def to_projected(
    lat: np.ndarray, lon: np.ndarray, proj: Projection
) -> tuple[np.ndarray, np.ndarray]:
    """Geographic coordinates to projected easting and northing, in metres."""
    tf = Transformer.from_crs("EPSG:4326", proj.crs, always_xy=True)
    x, y = tf.transform(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    return np.asarray(x), np.asarray(y)


def planar_distance_m(
    x1: np.ndarray, y1: np.ndarray, x2: np.ndarray, y2: np.ndarray
) -> np.ndarray:
    """Euclidean distance in a projected CRS. Metres in, metres out."""
    return np.hypot(np.subtract.outer(x1, x2) if x1.ndim == 1 and x2.ndim == 1 else x1 - x2,
                    np.subtract.outer(y1, y2) if y1.ndim == 1 and y2.ndim == 1 else y1 - y2)


def within_radius(
    dx: np.ndarray, dy: np.ndarray, radius_m: float
) -> np.ndarray:
    """Radius test without taking a square root, which is the expensive part."""
    return (dx * dx + dy * dy) <= radius_m * radius_m


def crs_disagreement(
    lat: np.ndarray, lon: np.ndarray, proj: Projection, sample: int = 400
) -> dict[str, float]:
    """How far projected distance departs from great-circle over this area.

    Reported in the assessment rather than assumed away: if the two agree to
    within a fraction of a percent the choice of CRS is not load-bearing, and if
    they do not, the reader should know before acting on a service radius.
    """
    from .clean import haversine_m

    n = len(lat)
    if n < 2:
        return {"pairs": 0, "max_relative_error": 0.0, "median_relative_error": 0.0}

    rng = np.random.default_rng(0)
    k = min(sample, n)
    a = rng.choice(n, k, replace=False)
    b = rng.choice(n, k, replace=False)
    keep = a != b
    a, b = a[keep], b[keep]

    great = haversine_m(lat[a], lon[a], lat[b], lon[b])
    x, y = to_projected(lat, lon, proj)
    plane = np.hypot(x[a] - x[b], y[a] - y[b])

    ok = great > 1.0
    rel = np.abs(plane[ok] - great[ok]) / great[ok]
    return {
        "pairs": int(ok.sum()),
        "max_relative_error": float(rel.max()) if rel.size else 0.0,
        "median_relative_error": float(np.median(rel)) if rel.size else 0.0,
    }
