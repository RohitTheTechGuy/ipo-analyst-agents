"""Report charts (plan 5.8): matplotlib figures rendered to SVG and embedded inline as data URIs.

Each chart plots verified numbers only; a chart with fewer than two points to show returns None and the
template says why. Figure objects are used directly (no pyplot), so there is no global backend state.
"""
from __future__ import annotations

import base64
import io
import textwrap

from matplotlib.figure import Figure

from ipo_agents.calc import to_crore
from ipo_agents.contracts import MULTIPLE_LABELS, Fact, Financials, Valuation

# Categorical slots 1-3 of the validated reference palette (dataviz skill), print/light mode.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")
PEER = "#b4b2a9"  # recessive grey: peers recede, the IPO stands out
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e2dc"


def _crore(f: Fact) -> float | None:
    if f.value is None:
        return None
    try:
        return to_crore(f)
    except ValueError:
        return None


def _axes(fig: Figure, ylabel: str):
    ax = fig.add_subplot()
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=8)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    return ax


def _svg(fig: Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _grouped_bars(labels: list[str], series: list[tuple[str, list[float | None]]], ylabel: str) -> str | None:
    if sum(v is not None for _, values in series for v in values) < 2:
        return None
    fig = Figure(figsize=(6.4, 2.6))
    ax = _axes(fig, ylabel)
    width = 0.8 / len(series)
    for i, (name, values) in enumerate(series):
        xs = [x + (i - (len(series) - 1) / 2) * width for x in range(len(labels))]
        pts = [(x, v) for x, v in zip(xs, values) if v is not None]
        ax.bar([x for x, _ in pts], [v for _, v in pts], width * 0.92, color=SERIES[i], label=name)
    ax.set_xticks(range(len(labels)), [textwrap.fill(label, 16) for label in labels])
    ax.legend(frameon=False, fontsize=8, ncols=len(series), loc="upper left", bbox_to_anchor=(0, 1.18))
    return _svg(fig)


def _chronological(financials: Financials):
    # RHPs print the latest period first; charts read left to right, oldest first.
    return list(reversed(financials.periods))


def financial_trend(financials: Financials | None) -> str | None:
    """Revenue, EBITDA and PAT per period, ₹ crore."""
    if financials is None or not financials.periods:
        return None
    periods = _chronological(financials)
    return _grouped_bars([p.label for p in periods], [
        ("Revenue", [_crore(p.revenue_from_operations) for p in periods]),
        ("EBITDA", [_crore(p.ebitda) for p in periods]),
        ("PAT", [_crore(p.pat) for p in periods]),
    ], "₹ crore")


def pat_vs_cfo(financials: Financials | None) -> str | None:
    """PAT against cash from operations per period, ₹ crore: profit that never turns into cash shows here."""
    if financials is None or not financials.periods:
        return None
    periods = _chronological(financials)
    return _grouped_bars([p.label for p in periods], [
        ("PAT", [_crore(p.pat) for p in periods]),
        ("Cash from operations", [_crore(p.cfo) for p in periods]),
    ], "₹ crore")


def peer_multiple(valuation: Valuation | None) -> str | None:
    """The headline multiple for each peer with a value, the IPO marked, and the peer median as a line."""
    h = valuation.headline if valuation else None
    if h is None:
        return None
    peers = valuation.rhp_peers if valuation.peer_set == "rhp" else valuation.suggested_peers
    rows = [(p.name, p.multiples[h.multiple].value) for p in peers
            if h.multiple in p.multiples and p.multiples[h.multiple].value is not None]
    rows.append(("IPO (upper band)", h.ipo.value))
    if len(rows) < 2:
        return None
    label = MULTIPLE_LABELS[h.multiple]
    fig = Figure(figsize=(6.4, 0.45 * len(rows) + 0.8))
    ax = fig.add_subplot()
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=8, length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    names = [textwrap.shorten(n, 34) for n, _ in rows]
    values = [v for _, v in rows]
    colors = [PEER] * (len(rows) - 1) + [SERIES[0]]
    ax.barh(range(len(rows)), values, 0.6, color=colors)
    ax.set_yticks(range(len(rows)), names)
    ax.invert_yaxis()
    for y, v in enumerate(values):
        ax.annotate(f"{v:.1f}×", (v, y), xytext=(3, 0), textcoords="offset points", va="center",
                    fontsize=8, color=INK, fontweight="bold" if y == len(rows) - 1 else "normal")
    if h.median.value is not None:
        ax.axvline(h.median.value, color=INK, linewidth=1, linestyle="--")
        ax.annotate(f"peer median {h.median.value:.1f}×", (h.median.value, -0.6), xytext=(3, 0),
                    textcoords="offset points", fontsize=8, color=MUTED)
    ax.set_xlabel(f"{label}, times", color=MUTED, fontsize=8)
    return _svg(fig)
