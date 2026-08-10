"use client";

import { barPath, barPathH, niceCeiling } from "@/lib/series";

/**
 * Every chart in this console, hand-rolled as inline SVG.
 *
 * No charting library, and that is a decision rather than an omission (H-057): the four
 * forms below are perhaps three hundred lines between them, while a library would be a
 * dependency whose defaults — its own type scale, its own palette, its rounded-both-ends
 * bars and its dashed gridlines — would have to be fought back to this design language
 * one override at a time. The mark specs are followed exactly and on purpose: bars capped
 * at 24px with a 4px rounded data end and a square baseline, a 2px surface gap between
 * touching fills, hairline solid gridlines one step off the surface, no number on every
 * point, and text in ink tokens rather than in the series colour.
 *
 * Every chart here has a table beside it or under it somewhere in the same view, so no
 * value is reachable only by hovering.
 */

/** The surface doing the separating — one consistent width across every stack. */
const GAP = 2;

/**
 * Lay a stack out before drawing it.
 *
 * Written as a pre-pass rather than a running offset inside the render's `map`, because
 * a variable reassigned while React is rendering is a variable React is entitled to see
 * in a different state than you expect. The geometry is a pure function of the values;
 * computing it as one keeps the render a pure function too.
 */
function stackLayout(
  values: readonly number[],
  span: number,
  ceiling: number,
): { offset: number; size: number; drawn: number; last: boolean }[] {
  const visible = values.map((value, index) => ({ value, index })).filter(({ value }) => value > 0);
  let cursor = 0;
  return visible.map(({ value }, position) => {
    const size = (value / ceiling) * span;
    const offset = cursor;
    cursor += size;
    const last = position === visible.length - 1;
    return { offset, size, drawn: Math.max(1, size - (last ? 0 : GAP)), last };
  });
}

// --- stacked bars over time ------------------------------------------------------------

export type StackSeries = { key: string; label: string; colour: string };
export type StackBucket = { at: number; values: Record<string, number>; label?: string };

export function StackedBars({
  buckets,
  series,
  height = 132,
  ariaLabel,
  ticks,
}: {
  buckets: StackBucket[];
  series: StackSeries[];
  height?: number;
  ariaLabel: string;
  /** Bucket indices to label on the x-axis. Sparse on purpose — usually first and last. */
  ticks?: number[];
}) {
  const width = 1000;
  const plot = height;
  const totals = buckets.map((bucket) =>
    series.reduce((sum, entry) => sum + (bucket.values[entry.key] ?? 0), 0),
  );
  const ceiling = niceCeiling(Math.max(1, ...totals));
  const slot = width / Math.max(1, buckets.length);
  const barWidth = Math.min(24, Math.max(2, slot - 2));

  return (
    <div style={{ position: "relative" }}>
      {/* The axis labels are HTML, not SVG text, and that is a bug fix rather than a
          preference: the plot stretches to its container with `preserveAspectRatio:
          none`, which scales x and y independently — and any text inside it gets squashed
          or stretched by the same factor. In a half-width card the ticks came out
          illegible. Outside the SVG they are ordinary text at the ordinary size. */}
      <span className="tick" style={{ position: "absolute", top: 2, left: 2 }}>
        {ceiling}
      </span>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${plot}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel}
        style={{ height: plot }}
      >
        {/* Two hairlines, solid, one step off the surface: the ceiling and the baseline.
            Anything denser competes with the data for the reader's attention. */}
        <line x1={0} y1={0.5} x2={width} y2={0.5} stroke="var(--line-soft)" strokeWidth={1} />
        <line
          x1={0}
          y1={plot - 0.5}
          x2={width}
          y2={plot - 0.5}
          stroke="var(--line)"
          strokeWidth={1}
        />
        {buckets.map((bucket, index) => {
        const total = totals[index] ?? 0;
        if (total <= 0) return null;
        const x = index * slot + (slot - barWidth) / 2;
        const present = series.filter((entry) => (bucket.values[entry.key] ?? 0) > 0);
        // The gap is taken out of each segment, never drawn over it: a stroke around a
        // mark would add ink that is not data.
        const layout = stackLayout(
          present.map((entry) => bucket.values[entry.key] ?? 0),
          plot,
          ceiling,
        );
        return (
          <g key={bucket.at}>
            {present.map((entry, position) => {
              const geometry = layout[position];
              if (!geometry) return null;
              return (
                <path
                  key={entry.key}
                  d={barPath(
                    x,
                    plot - geometry.offset - geometry.size,
                    barWidth,
                    geometry.drawn,
                    geometry.last ? 4 : 0,
                  )}
                  fill={entry.colour}
                />
              );
            })}
            <title>{`${bucket.label ?? new Date(bucket.at).toLocaleTimeString("en-GB", { hour12: false })} — ${total} request${total === 1 ? "" : "s"}`}</title>
            {/* A hit target the full slot wide, so hovering never means landing on a
                two-pixel bar. */}
            <rect x={index * slot} y={0} width={slot} height={plot} fill="transparent" />
          </g>
          );
        })}
      </svg>
      {(ticks ?? []).length > 0 && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: 5,
            minHeight: 12,
          }}
        >
          {(ticks ?? []).map((index) => {
            const bucket = buckets[index];
            return (
              <span className="tick" key={index}>
                {bucket
                  ? (bucket.label ??
                    new Date(bucket.at).toLocaleTimeString("en-GB", { hour12: false }))
                  : ""}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- one-series bars, for a stat tile ----------------------------------------------------

export function Sparkbars({
  values,
  height = 34,
  colour = "var(--accent)",
  ariaLabel,
}: {
  values: number[];
  height?: number;
  colour?: string;
  ariaLabel: string;
}) {
  const width = 240;
  const ceiling = niceCeiling(Math.max(1, ...values));
  const slot = width / Math.max(1, values.length);
  const barWidth = Math.max(1.5, slot - 2);
  return (
    <svg
      className="chart"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={ariaLabel}
      style={{ height }}
    >
      {values.map((value, index) => {
        if (value <= 0) return null;
        const barHeight = Math.max(1.5, (value / ceiling) * height);
        return (
          <path
            key={index}
            d={barPath(index * slot + (slot - barWidth) / 2, height - barHeight, barWidth, barHeight, 2)}
            fill={colour}
          />
        );
      })}
    </svg>
  );
}

// --- horizontal rank bars ---------------------------------------------------------------

export type RankRow = { key: string; label: string; value: number; display: string };

/**
 * Magnitude, ranked. One hue for every bar, deliberately: colouring each bar
 * darker-where-bigger would double-encode the length as hue and burn the only free
 * channel on information the bar already carries.
 */
export function RankBars({ rows, ariaLabel }: { rows: RankRow[]; ariaLabel: string }) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  return (
    <div role="img" aria-label={ariaLabel} style={{ display: "grid", gap: 10 }}>
      {rows.map((row) => (
        <div key={row.key}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 12,
              fontSize: 12,
              marginBottom: 4,
            }}
          >
            <span style={{ color: "var(--ink-2)", overflow: "hidden", textOverflow: "ellipsis" }}>
              {row.label}
            </span>
            <span className="num" style={{ color: "var(--ink-1)" }}>
              {row.display}
            </span>
          </div>
          <svg
            className="chart"
            viewBox="0 0 1000 8"
            preserveAspectRatio="none"
            style={{ height: 8 }}
            aria-hidden="true"
          >
            <rect x={0} y={0} width={1000} height={8} rx={4} fill="var(--surface-3)" />
            {row.value > 0 && (
              <path
                d={barPathH(0, 0, Math.max(8, (row.value / max) * 1000), 8, 4)}
                fill="var(--accent)"
              />
            )}
          </svg>
        </div>
      ))}
    </div>
  );
}

// --- a single proportion bar -------------------------------------------------------------

export type Proportion = { key: string; label: string; value: number; colour: string };

/**
 * Part-to-whole in one row — the cache's five dispositions, a chain's outcomes.
 *
 * A bar rather than a donut: the segments here are routinely close in size, and close
 * values in a donut are exactly what a donut cannot show.
 */
export function ProportionBar({
  parts,
  height = 10,
  ariaLabel,
}: {
  parts: Proportion[];
  height?: number;
  ariaLabel: string;
}) {
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  const width = 1000;
  if (total <= 0) {
    return (
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ height }} role="img" aria-label={ariaLabel}>
        <rect x={0} y={0} width={width} height={height} rx={height / 2} fill="var(--surface-3)" />
      </svg>
    );
  }
  const visible = parts.filter((part) => part.value > 0);
  const layout = stackLayout(
    visible.map((part) => part.value),
    width,
    total,
  );
  return (
    <svg
      className="chart"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      style={{ height }}
      role="img"
      aria-label={ariaLabel}
    >
      {visible.map((part, index) => {
        const geometry = layout[index];
        if (!geometry) return null;
        const first = index === 0;
        const drawn = Math.max(2, geometry.drawn);
        return (
          <g key={part.key}>
            <path
              d={
                first || geometry.last
                  ? roundedEnd(geometry.offset, drawn, height, height / 2, first, geometry.last)
                  : `M${geometry.offset} 0h${drawn}v${height}h${-drawn}z`
              }
              fill={part.colour}
            />
            <title>{`${part.label}: ${part.value}`}</title>
          </g>
        );
      })}
    </svg>
  );
}

function roundedEnd(
  x: number,
  width: number,
  height: number,
  radius: number,
  left: boolean,
  right: boolean,
): string {
  const r = Math.min(radius, width / 2, height / 2);
  const l = left ? r : 0;
  const rr = right ? r : 0;
  return [
    `M${x + l} 0`,
    `H${x + width - rr}`,
    rr ? `a${rr} ${rr} 0 0 1 ${rr} ${rr}` : "",
    `V${height - rr}`,
    rr ? `a${rr} ${rr} 0 0 1 ${-rr} ${rr}` : "",
    `H${x + l}`,
    l ? `a${l} ${l} 0 0 1 ${-l} ${-l}` : "",
    `V${l}`,
    l ? `a${l} ${l} 0 0 1 ${l} ${-l}` : "",
    "z",
  ].join("");
}

// --- the channel strip -------------------------------------------------------------------

export type StripSegment = { key: string; label: string; value: number; colour: string };

/**
 * A budget as a channel strip. **The one flourish this console is allowed** (BUILD_PLAN
 * §P7), and it is not decoration: the audio metaphor is exactly right for the thing being
 * shown. Settled spend and live reservations stack from the bottom like signal; the space
 * above them is *headroom*, which is what the project is named after; and the line at the
 * top is the cap you must not clip.
 *
 * The severity of the fill is a status colour with a word beside it in the caller's
 * markup — never hue alone.
 */
export function ChannelStrip({
  segments,
  capLabel,
  fraction,
  ariaLabel,
}: {
  segments: StripSegment[];
  capLabel: string;
  /** committed ÷ cap. May exceed 1: a cap lowered under its own spend is a real state. */
  fraction: number;
  ariaLabel: string;
}) {
  const height = 108;
  const width = 40;
  const scale = Math.max(1, fraction);
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);
  // The stack is drawn to `fraction / scale` of the strip: at or under the cap the fill
  // is the share of it used; over the cap the strip rescales so the cap line drops into
  // view rather than the fill running off the top.
  const span = total > 0 ? (Math.min(fraction, scale) / scale) * height : 0;
  const layout = stackLayout(
    segments.map((segment) => segment.value),
    span,
    total || 1,
  );
  const visible = segments.filter((segment) => segment.value > 0);

  return (
    <div style={{ display: "flex", gap: 12, alignItems: "stretch" }}>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={ariaLabel}
        style={{ width, height, flex: "none" }}
      >
        <rect x={8} y={0} width={width - 16} height={height} rx={5} fill="var(--surface-3)" />
        {visible.map((segment, index) => {
          const geometry = layout[index];
          if (!geometry) return null;
          return (
            <g key={segment.key}>
              <path
                d={barPath(
                  8,
                  height - geometry.offset - geometry.size,
                  width - 16,
                  Math.max(2, geometry.drawn),
                  geometry.last ? 4 : 0,
                )}
                fill={segment.colour}
              />
              <title>{`${segment.label}: ${segment.value}`}</title>
            </g>
          );
        })}
        {/* The cap. A solid hairline across the whole strip — the level you do not cross. */}
        <line
          x1={2}
          y1={height * (1 - 1 / scale) + 0.5}
          x2={width - 2}
          y2={height * (1 - 1 / scale) + 0.5}
          stroke={fraction >= 1 ? "var(--critical)" : "var(--ink-3)"}
          strokeWidth={1}
        />
      </svg>
      <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
        <div className="tile-label">headroom</div>
        <div>
          <div className="num" style={{ fontSize: 18, letterSpacing: "-0.01em" }}>
            {capLabel}
          </div>
          <div className="tile-foot">of the cap remains</div>
        </div>
      </div>
    </div>
  );
}

// --- a token bucket ----------------------------------------------------------------------

/**
 * A rate-limit bucket: how full, and how long until it is full again. The same meter
 * metaphor as the channel strip, laid on its side because a bucket is read as a level
 * rather than as a signal.
 */
export function BucketMeter({
  available,
  limit,
  ariaLabel,
}: {
  available: number;
  limit: number;
  ariaLabel: string;
}) {
  const fraction = limit > 0 ? Math.max(0, Math.min(1, available / limit)) : 0;
  const colour =
    fraction > 0.5 ? "var(--good)" : fraction > 0.15 ? "var(--warning)" : "var(--critical)";
  return (
    <svg
      className="chart"
      viewBox="0 0 1000 8"
      preserveAspectRatio="none"
      style={{ height: 8 }}
      role="img"
      aria-label={ariaLabel}
    >
      <rect x={0} y={0} width={1000} height={8} rx={4} fill="var(--surface-3)" />
      {fraction > 0 && <path d={barPathH(0, 0, Math.max(8, fraction * 1000), 8, 4)} fill={colour} />}
    </svg>
  );
}

// --- legend ------------------------------------------------------------------------------

export function Legend({ items }: { items: { label: string; colour: string }[] }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span className="legend-item" key={item.label}>
          <span className="legend-swatch" style={{ background: item.colour }} aria-hidden="true" />
          {item.label}
        </span>
      ))}
    </div>
  );
}
