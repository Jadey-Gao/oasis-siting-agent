"""GADM administrative boundaries.

Until this module existed the area of interest was an envelope drawn around the
retrieved records, padded by a fixed number of degrees. That is not a district.
It admits population from neighbouring districts into the denominator, excludes
parts of the district where nothing happens to be recorded, and leaves the
boundary-effect check measuring the edge of an arbitrary rectangle.

The per-level JSON is a few hundred kilobytes where the national GeoPackage is
tens of megabytes, so the level is fetched on its own rather than the whole
country database.
"""
from __future__ import annotations

import io
import unicodedata
import zipfile
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .. import handbook
from ..provenance import Ledger, Pull

CACHE = Path("cache/boundaries")
URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{iso3}_{level}.json.zip"


def _normalise(name: str) -> str:
    """Fold a place name for matching across sources.

    GADM and WPdx+ both carry harmonised district names and they are not the
    same harmonisation. Casing, accents, hyphens and the occasional apostrophe
    differ. Nothing is ever matched on a fold alone without reporting it.
    """
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    for ch in "-'’.,()":
        s = s.replace(ch, " ")
    return " ".join(s.lower().split())


def fetch_level(iso3: str, level: int, ledger: Ledger | None = None) -> gpd.GeoDataFrame:
    """Download one administrative level for a country, cached."""
    hb = handbook.load("gadm")
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"gadm41_{iso3.upper()}_{level}.gpkg"

    pull = Pull(
        source="GADM",
        handbook="gadm",
        endpoint=URL.format(iso3=iso3.upper(), level=level),
        query=f"{iso3.upper()} administrative level {level}",
        licence=hb.licence,
    )

    if dest.exists():
        gdf = gpd.read_file(dest)
        pull.served_from_cache(dest)
    else:
        r = requests.get(pull.endpoint, timeout=300)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            member = next(n for n in z.namelist() if n.endswith(".json"))
            gdf = gpd.read_file(io.BytesIO(z.read(member)))
        gdf.to_file(dest, driver="GPKG")
        pull.downloaded(dest)

    pull.rows_raw = len(gdf)
    before = len(gdf)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    pull.drop("empty geometry", before - len(gdf))

    invalid = ~gdf.geometry.is_valid
    if invalid.any():
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].buffer(0)
        pull.note = (pull.note + "; " if pull.note else "") + \
                    f"{int(invalid.sum())} invalid polygons repaired"

    pull.rows_clean = len(gdf)
    if ledger is not None:
        ledger.add(pull)
    return gdf.reset_index(drop=True)


def district(
    iso3: str, name: str, ledger: Ledger | None = None,
    levels: tuple[int, ...] = (1, 2, 3),
    points_lat=None, points_lon=None,
) -> tuple[Polygon | MultiPolygon, dict]:
    """The polygon for one named district, chosen by which extent the register
    actually occupies rather than by which level is searched first.

    A place name can exist at more than one administrative level: many Ugandan
    districts contain a county of the same name, so a search that stops at its
    first hit returns a fragment of the district under the district's own name.
    Taking the first match found Gulu county, holding two per cent of the
    records the register places in Gulu district.

    Where the caller supplies the register's own coordinates, every level is
    searched and the one whose polygon contains the largest share of those
    records wins. Correspondence decides, not numbering. Without coordinates the
    levels are tried in order and the choice is marked as unverified.
    """
    tried: list[str] = []
    found: list[dict] = []

    for level in levels:
        try:
            gdf = fetch_level(iso3, level, ledger if level == levels[0] else None)
        except Exception as exc:
            tried.append(f"level {level}: unavailable ({str(exc)[:60]})")
            continue

        col = f"NAME_{level}"
        if col not in gdf.columns:
            tried.append(f"level {level}: no {col}")
            continue

        exact = gdf[gdf[col].astype(str) == str(name)]
        folded = gdf[gdf[col].map(_normalise) == _normalise(name)]
        hit, how = (exact, "exact") if len(exact) == 1 else (folded, "normalised")

        if len(hit) == 1:
            row = hit.iloc[0]
            parents = [str(row[f"NAME_{k}"]) for k in range(1, level)
                       if f"NAME_{k}" in gdf.columns and row.get(f"NAME_{k}")]
            found.append({
                "geometry": row.geometry, "level": level, "how": how,
                "matched": str(row[col]),
                "gid": str(row.get(f"GID_{level}", "")),
                "within": " > ".join(parents),
            })
        elif len(hit) > 1:
            tried.append(f"level {level}: {len(hit)} units share this name "
                         f"({sorted(str(v) for v in hit[col])})")
        else:
            tried.append(f"level {level}: not found among {len(gdf)} units")

    if not found:
        near: list[str] = []
        try:
            g = fetch_level(iso3, levels[0])
            first = _normalise(name).split()[0]
            near = sorted({str(v) for v in g[f"NAME_{levels[0]}"] if first in _normalise(v)})[:8]
        except Exception:
            pass
        raise KeyError(
            f"no unit named {name!r} in {iso3} at levels {levels}. " + "; ".join(tried)
            + (f". Closest by first word: {near}" if near else ""))

    have_points = points_lat is not None and points_lon is not None and len(points_lat) > 0
    if have_points:
        for f in found:
            f["agreement"] = agreement(points_lat, points_lon, f["geometry"])
        found.sort(key=lambda f: f["agreement"]["share_inside"], reverse=True)
        chosen = found[0]
        others = [f"level {f['level']} holds {f['agreement']['share_inside']:.0%}"
                  for f in found[1:]]
    else:
        chosen = found[0]
        others = [f"level {f['level']}" for f in found[1:]]

    match = {
        "requested": name,
        "matched": chosen["matched"],
        "how": chosen["how"],
        "level": chosen["level"],
        "gid": chosen["gid"],
        "within": chosen["within"],
        "levels_tried": tried,
        "levels_matched": [f["level"] for f in found],
        "chosen_by": "share of register records contained" if have_points else "level order, unverified",
    }
    if have_points:
        match["agreement"] = chosen["agreement"]

    notes = []
    if chosen["how"] == "normalised":
        notes.append(f"GADM records the name as {chosen['matched']!r}; matched after "
                     f"folding case, accents and punctuation")
    if len(found) > 1:
        notes.append(
            f"the name matches a unit at {len(found)} administrative levels; level "
            f"{chosen['level']} was taken because "
            + (f"it contains {chosen['agreement']['share_inside']:.0%} of the register's "
               f"records against {', '.join(others)}"
               if have_points else
               f"it was searched first, and the choice is unverified ({', '.join(others)} "
               f"also match by name)"))
    if chosen["level"] != 1 and chosen["within"]:
        notes.append(f"it sits within {chosen['within']}")
    if notes:
        match["note"] = "; ".join(notes)

    return chosen["geometry"], match


def agreement(points_lat, points_lon, polygon, threshold: float = 0.8) -> dict:
    """Do the register and the boundary describe the same place?

    A name matching is not the same as a unit corresponding. Administrative
    divisions are redrawn, and a boundary source frozen at one vintage carries a
    unit of that name whose extent may bear little relation to the unit a
    register of a later vintage calls by it. Uganda's Kiryandongo was created in
    2010 from Masindi; GADM 4.1 still records a sub-county of that name inside
    the older Masindi, and only two records in five from the modern district fall
    inside it.

    Clipping to a boundary that does not correspond produces an assessment that
    is internally consistent and describes the wrong place, which is worse than
    one that admits its area of interest is approximate. This measures the
    correspondence and reports it rather than assuming a name match is enough.
    """
    import numpy as np
    from shapely import points as shp_points
    from shapely.prepared import prep

    lat = np.asarray(points_lat, dtype=float)
    lon = np.asarray(points_lon, dtype=float)
    if lat.size == 0:
        return {"share_inside": 0.0, "inside": 0, "total": 0,
                "corresponds": False, "threshold": threshold,
                "verdict": "no records to compare against the boundary"}

    pg = prep(polygon)
    pts = shp_points(lon, lat)
    inside = np.fromiter((pg.covers(pt) for pt in pts), dtype=bool, count=len(pts))
    share = float(inside.mean())
    ok = share >= threshold

    return {
        "share_inside": round(share, 4),
        "inside": int(inside.sum()),
        "total": int(len(inside)),
        "corresponds": ok,
        "threshold": threshold,
        "verdict": (
            f"{inside.sum():,} of {len(inside):,} records ({share:.0%}) fall inside the "
            f"matched boundary, at or above the {threshold:.0%} required for the two to "
            f"be treated as the same unit."
            if ok else
            f"only {inside.sum():,} of {len(inside):,} records ({share:.0%}) fall inside "
            f"the matched boundary, below the {threshold:.0%} required. The register and "
            f"the boundary source are describing different extents under the same name, "
            f"most often because the division was redrawn after the boundary source's "
            f"vintage. The boundary is not used."
        ),
    }


def clip_to(gdf_points, polygon) -> tuple:
    """Split a point frame into inside and outside a polygon.

    Both halves are returned. What falls outside the district is not silently
    dropped: it is counted and reported, because a register that places a fifth
    of its records outside the district it names is telling you something.
    """
    from shapely.geometry import Point
    inside = gdf_points.apply(
        lambda r: polygon.covers(Point(float(r["lon"]), float(r["lat"]))), axis=1
    ) if len(gdf_points) else []
    return gdf_points[inside], gdf_points[~inside] if len(gdf_points) else (gdf_points, gdf_points)


def subunits(iso3: str, parent_name: str, parent_level: int,
             ledger: Ledger | None = None) -> gpd.GeoDataFrame:
    """The units one level below a named parent, for reporting coverage by unit.

    Returns an empty frame rather than raising when the parent is already at the
    finest level GADM carries for the country: not every district has mapped
    sub-units, and an assessment can report at district level without them.
    """
    level = parent_level + 1
    try:
        gdf = fetch_level(iso3, level, ledger)
    except Exception:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    pcol = f"NAME_{parent_level}"
    if pcol not in gdf.columns:
        return gdf.iloc[0:0]
    target = _normalise(parent_name)
    return gdf[gdf[pcol].map(_normalise) == target].reset_index(drop=True)
