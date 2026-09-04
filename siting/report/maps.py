"""Figure factory.

Maps are rendered here at a fixed width and handed to Typst as images, so the
document keeps one typographic grid instead of fighting a second layout engine.
QGIS is the intended upgrade for cartographic polish (see render_with_qgis);
this matplotlib path is the dependency-free default so the pipeline runs on a
machine without QGIS installed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, LogNorm
from matplotlib.patches import Rectangle

INK = "#14201f"
MUTED = "#5a6763"
MACHINE = "#0e5b56"
HUMAN = "#8e5b16"
STOP = "#9c3222"
RULE = "#c9d1cd"

# Served population reads as quiet grey; the gap reads warm, because the gap is
# the subject of the document.
# Served population must be distinguishable from empty land, so this ramp starts
# at a grey that is actually visible on paper rather than at white. A ramp
# beginning at #ffffff renders populated-but-served cells as the page itself, and
# a legend entry with no visible counterpart is a legend that lies.
CMAP_ALL = LinearSegmentedColormap.from_list("served", ["#dfe4e2", "#a9b5b1", "#687672"])
CMAP_GAP = LinearSegmentedColormap.from_list("gap", ["#f6ead6", "#e0b877", "#a8701c"])

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Georgia", "DejaVu Serif"],
    "text.color": INK,
    "axes.edgecolor": RULE,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "figure.dpi": 220,
})


def _figsize(bounds, width_in=6.4, max_h=6.2):
    minx, miny, maxx, maxy = bounds
    lat_mid = np.radians((miny + maxy) / 2)
    w_km = (maxx - minx) * 111.32 * np.cos(lat_mid)
    h_km = (maxy - miny) * 111.32
    h = width_in * (h_km / max(w_km, 1e-6))
    if h > max_h:
        return width_in * max_h / h, max_h
    return width_in, max(h, 2.6)


def _frame(ax, bounds):
    minx, miny, maxx, maxy = bounds
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor(RULE)
        s.set_linewidth(0.8)
    ax.set_aspect(1 / np.cos(np.radians((miny + maxy) / 2)))


def _grid(demand, bounds, mask=None):
    """Rebin the demand cells into a raster so the population reads as a surface
    rather than as thousands of identical dots. Bin size follows the source cell
    size, so the picture never invents detail the data does not have."""
    minx, miny, maxx, maxy = bounds
    lon = demand["lon"].to_numpy(float)
    lat = demand["lat"].to_numpy(float)
    w = demand["weight"].to_numpy(float)
    if mask is not None:
        lon, lat, w = lon[mask], lat[mask], w[mask]
    step = np.median(np.diff(np.unique(np.round(demand["lat"].to_numpy(float), 6))))
    step = float(step) if np.isfinite(step) and step > 0 else (maxy - miny) / 160
    ny = max(24, int((maxy - miny) / step))
    nx = max(24, int((maxx - minx) / step))
    h, _, _ = np.histogram2d(lat, lon, bins=[ny, nx],
                             range=[[miny, maxy], [minx, maxx]], weights=w)
    return h


def _classified(ax, served, unserved, bounds, zorder=1, gamma=0.5, floor=0.35):
    """One layer, two classes, no overlap.

    Drawing served and unserved population as two translucent ramps on the same
    frame cannot work: a render cell almost always contains some of each, the
    upper layer wins everywhere, and the legend ends up describing a distinction
    the reader cannot see. This encodes the classes as hue and the population as
    intensity, so each cell says one thing and the legend is true.
    """
    total = served + unserved
    if total.max() <= 0:
        return
    v = np.power(np.clip(total / total.max(), 0, 1), gamma)

    warm_wins = unserved >= served
    rgba = np.zeros(total.shape + (4,))
    rgba[warm_wins] = CMAP_GAP(v[warm_wins])
    rgba[~warm_wins] = CMAP_ALL(v[~warm_wins])
    rgba[..., 3] = np.where(total > 0, np.clip(v * 1.3, floor, 1.0), 0.0)

    minx, miny, maxx, maxy = bounds
    ax.imshow(rgba, origin="lower", extent=(minx, maxx, miny, maxy),
              zorder=zorder, interpolation="nearest", aspect="auto")


def _imshow(ax, arr, bounds, cmap, alpha=1.0, zorder=1, gamma=0.45, floor=0.12):
    """Draw a population surface with a transparency ramp, so empty land stays
    empty instead of being tinted the lightest colour of the ramp."""
    if arr.max() <= 0:
        return
    minx, miny, maxx, maxy = bounds
    v = arr / arr.max()
    v = np.power(np.clip(v, 0, 1), gamma)
    rgba = cmap(v)
    rgba[..., 3] = np.where(arr > 0, np.clip(v * 1.25, floor, 1.0) * alpha, 0.0)
    ax.imshow(rgba, origin="lower", extent=(minx, maxx, miny, maxy),
              zorder=zorder, interpolation="nearest", aspect="auto")


def _scale_bar(ax, bounds):
    minx, miny, maxx, maxy = bounds
    span_km = (maxx - minx) * 111.32 * np.cos(np.radians((miny + maxy) / 2))
    km = next(k for k in (1, 2, 5, 10, 20, 50, 100) if k >= span_km / 6)
    dlon = km / (111.32 * np.cos(np.radians((miny + maxy) / 2)))
    x0 = minx + (maxx - minx) * 0.05
    y0 = miny + (maxy - miny) * 0.045
    h = (maxy - miny) * 0.006
    ax.add_patch(Rectangle((x0, y0), dlon, h, facecolor=INK, edgecolor="none", zorder=8))
    ax.text(x0 + dlon / 2, y0 + h * 2.6, f"{km} km", ha="center", va="bottom",
            fontsize=6, color=INK, zorder=8,
            path_effects=[pe.withStroke(linewidth=2, foreground="white")])


def _north(ax, bounds):
    minx, miny, maxx, maxy = bounds
    x = maxx - (maxx - minx) * 0.045
    y = maxy - (maxy - miny) * 0.075
    ax.annotate("N", xy=(x, y), xytext=(x, y - (maxy - miny) * 0.045),
                ha="center", va="center", fontsize=7, color=INK, zorder=8,
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=0.8))


def _credit(ax, text):
    ax.text(0.0, -0.022, text, transform=ax.transAxes, fontsize=5.6,
            color=MUTED, ha="left", va="top")


def _legend(ax, handles):
    ax.legend(handles=handles, loc="upper left", fontsize=6, frameon=True,
              framealpha=0.94, edgecolor=RULE, borderpad=0.45,
              handletextpad=0.5, labelspacing=0.35).set_zorder(9)


def situation(inst, working, broken, out: Path, credit: str) -> Path:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    b = inst.scope["bounds"]
    fig, ax = plt.subplots(figsize=_figsize(b))
    _frame(ax, b)

    # Two population layers on one frame only read as two if the served layer is
    # drawn where the unserved one is not. Drawn as overlapping ramps the warm
    # layer swamps the grey one everywhere and the legend describes a distinction
    # the reader cannot see.
    _classified(ax,
                _grid(inst.demand, b, mask=inst.baseline_covered),
                _grid(inst.demand, b, mask=~inst.baseline_covered), b, zorder=1)

    # Facility markers sit over the class layer, and where they crowd they hide
    # the very population the layer is there to show. Size and halo scale with
    # how many there are, so a district with two thousand points does not paper
    # over its own map.
    n = max(len(working) + len(broken), 1)
    size = float(np.interp(n, [50, 500, 2000, 6000], [9.0, 6.0, 3.0, 1.6]))
    halo = float(np.interp(n, [50, 500, 2000], [0.35, 0.2, 0.0]))
    ax.scatter(broken["lon"], broken["lat"], s=size * 0.9, marker="x", c=STOP,
               linewidths=min(0.55, size * 0.12), zorder=4)
    ax.scatter(working["lon"], working["lat"], s=size, marker="o", c=MACHINE,
               edgecolors="white" if halo else "none", linewidths=halo, zorder=5)

    _legend(ax, [
        Patch(facecolor="#a9b5b1", edgecolor="none", label="Mostly within the service radius"),
        Patch(facecolor="#e0b877", edgecolor="none", label="Mostly beyond the service radius"),
        Line2D([], [], marker="o", ls="", ms=3.2, mfc=MACHINE, mec="white", label="Water point, serving"),
        Line2D([], [], marker="x", ls="", ms=3.6, mec=STOP, mew=0.9, label="Water point, not serving"),
    ])
    _scale_bar(ax, b)
    _north(ax, b)
    fig.savefig(out, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return out


def plan(inst, sol, working, out: Path, credit: str, site_ids: dict) -> Path:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    b = inst.scope["bounds"]
    fig, ax = plt.subplots(figsize=_figsize(b))
    _frame(ax, b)

    _classified(ax,
                _grid(inst.demand, b, mask=inst.baseline_covered),
                _grid(inst.demand, b, mask=~inst.baseline_covered), b,
                zorder=1, floor=0.3)

    ax.scatter(working["lon"], working["lat"], s=3.2, marker="o", c="#9aa8a4",
               linewidths=0, zorder=3)

    lats = inst.candidates.lat.iloc[sol.sites].to_numpy()
    lons = inst.candidates.lon.iloc[sol.sites].to_numpy()
    r_deg = inst.scope["radius_m"] / 111_320.0
    lat_scale = 1 / np.cos(np.radians((b[1] + b[3]) / 2))
    for x, y in zip(lons, lats):
        ax.add_patch(plt.matplotlib.patches.Ellipse(
            (x, y), 2 * r_deg * lat_scale, 2 * r_deg, facecolor=MACHINE, alpha=0.18,
            edgecolor=MACHINE, linewidth=0.9, zorder=5))

    # The radius circle must remain visible. Where it is smaller than a legible
    # numbered marker, the number moves outside on a leader line rather than the
    # marker growing until it hides the quantity the figure exists to show.
    fig_w_in = fig.get_size_inches()[0]
    r_pts = (2 * r_deg * lat_scale / max(b[2] - b[0], 1e-9)) * fig_w_in * 72.0
    dot = min(max(r_pts * 0.28, 2.0), 9.0)
    ax.scatter(lons, lats, s=dot ** 2, marker="o", c=MACHINE, edgecolors="white",
               linewidths=0.4, zorder=6)

    label_inside = r_pts > 13
    off = (b[3] - b[1]) * 0.018
    for n, (x, y) in enumerate(zip(lons, lats), start=1):
        if label_inside:
            ax.annotate(str(n), (x, y), fontsize=5.0, color="white", ha="center",
                        va="center", zorder=8, weight="bold")
        else:
            ax.annotate(str(n), xy=(x, y), xytext=(x, y + off), fontsize=5.6,
                        color=INK, ha="center", va="bottom", zorder=8, weight="bold",
                        path_effects=[pe.withStroke(linewidth=1.8, foreground="white")],
                        arrowprops=dict(arrowstyle="-", color=INK, lw=0.4,
                                        shrinkA=0, shrinkB=dot * 0.5))

    _legend(ax, [
        Patch(facecolor="#e0b877", edgecolor="none", label="Mostly beyond the service radius"),
        Line2D([], [], marker="o", ls="", ms=5, mfc=MACHINE, mec="white", label="Recommended site, ranked"),
        Patch(facecolor=MACHINE, alpha=0.2, edgecolor=MACHINE, label="Proposed service radius"),
        Line2D([], [], marker="o", ls="", ms=2.4, mfc="#9aa8a4", mec="none", label="Existing point, serving"),
    ])
    _scale_bar(ax, b)
    _north(ax, b)
    fig.savefig(out, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return out


def framework(out: Path, objective_short: str | None = None) -> Path:
    """The four-slot diagram. Drawn, not decorated: it shows that four domains
    fill identical slots, which is the claim the document rests on."""
    fig, ax = plt.subplots(figsize=(7.0, 2.5))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 34)
    ax.axis("off")

    domains = ["Water points", "Health access", "Disease burden", "Air monitoring"]
    weights = ["pop x gap distance", "pop x travel time", "pop x prevalence", "pop x exposure"]
    for i, (dm, wt) in enumerate(zip(domains, weights)):
        y = 28 - i * 7.6
        ax.add_patch(Rectangle((0, y - 2.6), 22, 5.2, facecolor="white",
                               edgecolor=MUTED, linewidth=0.7))
        ax.text(1.4, y + 0.5, dm, fontsize=7.2, va="center", color=INK, weight="bold")
        ax.text(1.4, y - 1.5, wt, fontsize=5.8, va="center", color=MUTED)
        ax.annotate("", xy=(37, 17), xytext=(22.6, y),
                    arrowprops=dict(arrowstyle="-|>", color=MACHINE, lw=0.7,
                                    connectionstyle="arc3,rad=0.12"))

    ax.add_patch(Rectangle((37, 4), 30, 26, facecolor="#eef2f0",
                           edgecolor=MACHINE, linewidth=0.9))
    ax.text(38.6, 26.6, "Problem instance", fontsize=7.6, weight="bold", color=INK)
    for j, slot in enumerate(["demand      w", "candidates  V", "coverage    rule", "budget      b"]):
        ax.text(38.6, 21.5 - j * 4.0, slot, fontsize=6.4, color=MUTED, family="monospace")
    ax.text(38.6, 5.6, "same four slots, every domain", fontsize=5.8,
            color=MUTED, style="italic")

    ax.annotate("", xy=(76, 17), xytext=(67.6, 17),
                arrowprops=dict(arrowstyle="-|>", color=MACHINE, lw=0.8))
    ax.add_patch(Rectangle((76, 12), 24, 10, facecolor="white",
                           edgecolor=MACHINE, linewidth=0.9))
    ax.text(77.4, 18.4, "Solver", fontsize=7.4, weight="bold", color=INK)
    ax.text(77.4, 15.2, "union coverage", fontsize=6.0, color=MUTED)
    ax.text(77.4, 13.2, objective_short or "objective from the register",
            fontsize=6.0, color=MUTED)

    fig.savefig(out, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)
    return out


def render_with_qgis(project: Path, layout: str, out: Path) -> Path | None:
    """Upgrade path. Runs QGIS as a separate process on purpose: QGIS is GPL-2.0
    and keeping it behind a process boundary keeps this package's licence clean."""
    exe = Path("C:/Program Files/QGIS 3.44/bin/qgis_process-qgis.bat")
    if not exe.exists():
        return None
    subprocess.run(
        [str(exe), "run", "native:printlayouttopdf",
         f"--LAYOUT={layout}", f"--OUTPUT={out}", f"--PROJECT_PATH={project}"],
        check=True, capture_output=True,
    )
    return out
