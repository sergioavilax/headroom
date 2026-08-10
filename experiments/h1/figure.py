"""The safety curve, rendered as SVG in the console's own visual language.

BUILD_PLAN §P8's gate: *"the H1 curve as both committed JSON and a chart in the repo's
visual language"*. The JSON is `results/h1_curve.json`; this is the chart.

**Form.** Two quantities over one continuous axis is a line chart, and the story is the
*relationship* between them: raising the threshold sheds hits, and the question is whether
it sheds the wrong ones first. Two panels — the primary embedding and H-060's
tail-stripped sensitivity check — share a y scale so the boilerplate effect is a difference
in shape rather than a difference in axis.

**Colour by job, not by slot** (the `dataviz` method). "Served from cache" is an ordinary
series and takes `--series-1`. "Silently wrong" is a **state**, so it takes the reserved
`--critical` step and ships with a word beside it — no reader has to know which red means
what. One legend serves both panels, so identity is never carried by hue alone. It replaced
per-panel end-labels, which ran off the right edge of the second panel — found by rendering
the figure and looking at it, which is the last step of the method and the one that catches
what a validator cannot.

**The palette is not re-picked here.** These are the console's own tokens, validated in
Phase 7 against this exact surface (`#131417`): worst-pair CVD ΔE 8.4, worst-pair
normal-vision ΔE 19.3, every step ≥ 3:1 (H-057). Re-choosing them for a second surface
would be how one system becomes two.

**No hover layer, deliberately.** This is a committed figure rather than a page: the
interactive obligation is met by `results/h1_curve.json`, which is the table view of every
point drawn here, and by `results/h1_probes.json`, which is the per-probe drill-down.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Final

from experiments.provenance import RESULTS_DIR, read_json

__all__ = ["FIGURE_PATH", "main", "render"]

FIGURE_PATH: Final = RESULTS_DIR / "h1_curve.svg"

# The console's tokens (ui/app/globals.css), used unchanged.
GROUND: Final = "#000000"
SURFACE: Final = "#131417"
LINE: Final = "#26282f"
LINE_SOFT: Final = "#1c1e23"
INK_1: Final = "#f4f4f5"
INK_2: Final = "#a8a9b4"
INK_3: Final = "#7c7e8a"
INK_4: Final = "#55575f"
SERIES_1: Final = "#3987e5"
CRITICAL: Final = "#d03b3b"

MONO: Final = "ui-monospace, SFMono-Regular, Menlo, monospace"
SANS: Final = "ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"

WIDTH: Final = 980
HEIGHT: Final = 560
PANEL_TOP: Final = 196
PANEL_HEIGHT: Final = 250
PANEL_WIDTH: Final = 400
PANEL_GAP: Final = 56
LEFT: Final = 62

SHIPPED_DEFAULT: Final = 0.9


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _panel(
    *,
    x0: int,
    curve: list[dict[str, Any]],
    probes: int,
    title: str,
    subtitle: str,
    show_y_labels: bool,
) -> list[str]:
    parts: list[str] = []
    t_min, t_max = float(curve[0]["threshold"]), float(curve[-1]["threshold"])
    y0 = PANEL_TOP
    y1 = PANEL_TOP + PANEL_HEIGHT

    def sx(threshold: float) -> float:
        return float(x0 + (threshold - t_min) / (t_max - t_min) * PANEL_WIDTH)

    def sy(count: float) -> float:
        return y1 - (count / probes) * PANEL_HEIGHT

    parts.append(
        f'<text x="{x0}" y="{y0 - 40}" fill="{INK_1}" font-family="{SANS}" font-size="14" '
        f'font-weight="600">{_escape(title)}</text>'
    )
    parts.append(
        f'<text x="{x0}" y="{y0 - 23}" fill="{INK_3}" font-family="{SANS}" font-size="11.5">'
        f"{_escape(subtitle)}</text>"
    )

    # Recessive gridlines, one step off the surface, solid — never dashed (H-057).
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = y1 - fraction * PANEL_HEIGHT
        parts.append(
            f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + PANEL_WIDTH}" y2="{y:.1f}" '
            f'stroke="{LINE if fraction == 0 else LINE_SOFT}" stroke-width="1"/>'
        )
        if show_y_labels:
            parts.append(
                f'<text x="{x0 - 10}" y="{y + 4:.1f}" fill="{INK_4}" font-family="{MONO}" '
                f'font-size="10.5" text-anchor="end">{round(fraction * 100)}%</text>'
            )

    for threshold in (0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99):
        x = sx(threshold)
        parts.append(
            f'<text x="{x:.1f}" y="{y1 + 18}" fill="{INK_4}" font-family="{MONO}" '
            f'font-size="10.5" text-anchor="middle">{threshold:.2f}</text>'
        )

    # The shipped default, annotated below the axis so nothing above the plot can collide
    # with it — the two panels' subtitles sit exactly where an inline label wanted to be.
    xd = sx(SHIPPED_DEFAULT)
    parts.append(
        f'<line x1="{xd:.1f}" y1="{y0}" x2="{xd:.1f}" y2="{y1}" stroke="{INK_4}" '
        f'stroke-width="1" stroke-dasharray="2 3"/>'
    )
    parts.append(
        f'<text x="{xd:.1f}" y="{y1 + 32}" fill="{INK_3}" font-family="{SANS}" font-size="10" '
        f'text-anchor="middle">shipped default</text>'
    )

    for key, colour, label in (
        ("hits", SERIES_1, "served from cache"),
        ("silent_wrong_answer", CRITICAL, "silently wrong"),
    ):
        points = " ".join(f"{sx(row['threshold']):.1f},{sy(row[key]):.1f}" for row in curve)
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"><title>{_escape(label)}</title>'
            f"</polyline>"
        )
        last = curve[-1]
        parts.append(
            f'<circle cx="{sx(last["threshold"]):.1f}" cy="{sy(last[key]):.1f}" r="3" '
            f'fill="{colour}" stroke="{SURFACE}" stroke-width="2"/>'
        )
    return parts


def _legend(x: int, y: int) -> list[str]:
    """One legend for both panels: a swatch and a word, so no state is carried by hue.

    Shared rather than per-panel because the two panels plot the same two series, and the
    per-panel direct labels this replaces ran off the right edge of the second one — which
    is what looking at the rendered figure is for.
    """
    parts: list[str] = []
    offset = 0
    for colour, label in ((SERIES_1, "served from cache"), (CRITICAL, "silently wrong")):
        parts.append(
            f'<rect x="{x + offset}" y="{y - 9}" width="10" height="10" rx="2" fill="{colour}"/>'
        )
        parts.append(
            f'<text x="{x + offset + 16}" y="{y}" fill="{INK_2}" font-family="{SANS}" '
            f'font-size="11.5">{_escape(label)}</text>'
        )
        offset += 26 + int(len(label) * 6.2)
    return parts


def render(curve_data: dict[str, Any]) -> str:
    corpus = curve_data["corpus"]
    prompt = curve_data["spaces"]["prompt"]["combined"]
    body = curve_data["spaces"]["body"]["combined"]
    probes = int(prompt["curve"][0]["probes"])
    family = "novel questions" if corpus["stage"] == "family_b_only" else "paraphrases + novel"
    at_default = prompt["at_shipped_default"]
    tau = prompt["tau_zero"]

    headline = (
        f"At the shipped 0.90 threshold {at_default['hits']} of {probes} are answered from "
        f"cache, and {at_default['silent_wrong_answer']} of those answers are wrong."
    )
    verdict = (
        "No threshold in 0.70-0.99 reaches zero wrong answers."
        if tau is None
        else f"Zero wrong answers from {tau:.3f} upward."
    )

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" '
        f'aria-label="{_escape(headline + " " + verdict)}">',
        f"<title>{_escape('Headroom H1 — the semantic-cache safety curve')}</title>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{GROUND}"/>',
        f'<rect x="20" y="20" width="{WIDTH - 40}" height="{HEIGHT - 40}" rx="10" '
        f'fill="{SURFACE}" stroke="{LINE}" stroke-width="1"/>',
        f'<text x="{LEFT}" y="56" fill="{INK_1}" font-family="{SANS}" font-size="19" '
        f'font-weight="600">The semantic-cache safety curve</text>',
        f'<text x="{LEFT}" y="78" fill="{INK_2}" font-family="{SANS}" font-size="12.5">'
        f"{_escape(headline)}</text>",
        f'<text x="{LEFT}" y="96" fill="{INK_2}" font-family="{SANS}" font-size="12.5">'
        f"{_escape(verdict)}</text>",
    ]
    parts += _legend(LEFT, 132)

    parts += _panel(
        x0=LEFT,
        curve=prompt["curve"],
        probes=probes,
        title="the prompt as sent",
        subtitle="what the gateway embeds — primary",
        show_y_labels=True,
    )
    parts += _panel(
        x0=LEFT + PANEL_WIDTH + PANEL_GAP,
        curve=body["curve"],
        probes=probes,
        title="question only",
        subtitle="answer-format tail stripped — sensitivity check",
        show_y_labels=False,
    )

    parts.append(
        f'<text x="{LEFT}" y="{HEIGHT - 52}" fill="{INK_3}" font-family="{SANS}" '
        f'font-size="10.5">cosine similarity threshold · {probes} {_escape(family)} probes '
        f"over {corpus['questions']} Backline questions · {_escape(corpus['embedding_model'])}"
        f"</text>"
    )
    parts.append(
        f'<text x="{LEFT}" y="{HEIGHT - 36}" fill="{INK_4}" font-family="{MONO}" '
        f'font-size="9.5">corpus {corpus["corpus_hash"][:16]} · every point in '
        f"results/h1_curve.json</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h1.figure",
        description="Render the H1 curve as SVG in the console's design language.",
    )
    parser.add_argument("--curve", default=str(RESULTS_DIR / "h1_curve.json"))
    parser.add_argument("--out", default=str(FIGURE_PATH))
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(read_json(Path(args.curve))), encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
