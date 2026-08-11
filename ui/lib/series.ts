/**
 * Series colour, and the shared arithmetic the charts do before they draw anything.
 *
 * **A colour follows the entity, never its rank.** A provider's slot comes from its
 * position in the gateway's own provider list — which is `config/routing.yaml`'s order,
 * stable for the life of the process — so filtering the view, or a provider going quiet
 * during a kill demo, never repaints the survivors. That is the one rule about
 * categorical colour it is easiest to break by accident and impossible to un-see once
 * broken: a reader who learned "vllm_a is blue" must not find blue meaning something
 * else a minute later.
 *
 * Five slots, and no ninth: the palette validated on this console's surface is five
 * adjacent steps deep. A sixth provider folds into "other" rather than being handed a
 * generated hue nobody checked.
 */

export const SERIES_SLOTS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
] as const;

/** Requests no upstream served — a cache hit, a refusal. An absence, so it gets ink. */
export const NO_PROVIDER = "var(--series-none)";

export const OTHER_LABEL = "other";

/**
 * A stable name → colour map for one page's worth of series.
 *
 * `order` is the authority (the provider list as the gateway reports it); anything not
 * in it lands after, alphabetically, so an unknown name is at least deterministic.
 */
export function seriesColours(order: readonly string[]): Map<string, string> {
  const colours = new Map<string, string>();
  order.forEach((name, index) => {
    const slot = SERIES_SLOTS[index];
    colours.set(name, slot ?? NO_PROVIDER);
  });
  return colours;
}

export function colourFor(colours: Map<string, string>, name: string | null): string {
  if (name === null) return NO_PROVIDER;
  return colours.get(name) ?? NO_PROVIDER;
}

/** A y-axis ceiling that lands on a readable number rather than on the data's maximum. */
export function niceCeiling(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 1.5, 2, 3, 4, 5, 7.5, 10]) {
    if (value <= step * magnitude) return step * magnitude;
  }
  return 10 * magnitude;
}

/**
 * A rectangle whose *data end* is rounded and whose baseline end is square.
 *
 * Used for every bar in this console. `radius` is clamped to the bar's own height so a
 * one-pixel sliver does not become a lens.
 */
export function barPath(x: number, y: number, width: number, height: number, radius = 4): string {
  const r = Math.max(0, Math.min(radius, height, width / 2));
  if (r === 0) return `M${x} ${y}h${width}v${height}h${-width}z`;
  return [
    `M${x} ${y + height}`,
    `V${y + r}`,
    `a${r} ${r} 0 0 1 ${r} ${-r}`,
    `h${width - 2 * r}`,
    `a${r} ${r} 0 0 1 ${r} ${r}`,
    `V${y + height}`,
    "z",
  ].join("");
}

/** The same shape lying down — a horizontal bar growing right from a left baseline. */
export function barPathH(x: number, y: number, width: number, height: number, radius = 4): string {
  const r = Math.max(0, Math.min(radius, width, height / 2));
  if (r === 0) return `M${x} ${y}h${width}v${height}h${-width}z`;
  return [
    `M${x} ${y}`,
    `H${x + width - r}`,
    `a${r} ${r} 0 0 1 ${r} ${r}`,
    `V${y + height - r}`,
    `a${r} ${r} 0 0 1 ${-r} ${r}`,
    `H${x}`,
    "z",
  ].join("");
}

/**
 * Bucket rows into fixed time slots, filling the gaps with zeros.
 *
 * The store deliberately does not invent empty buckets — gap-filling belongs to whoever
 * knows the x-domain, and here that is this function, which is drawing "the last N
 * minutes" and therefore knows exactly which minutes those are.
 */
export function timeSlots(endMs: number, slots: number, widthMs: number): number[] {
  const last = Math.floor(endMs / widthMs) * widthMs;
  return Array.from({ length: slots }, (_, index) => last - (slots - 1 - index) * widthMs);
}

/** One UTC day, in milliseconds — the slot width of the history view's chart. */
export const DAY_MS = 86_400_000;

/**
 * A `YYYY-MM-DD` day from `/admin/usage/rollups`, as UTC-midnight milliseconds.
 *
 * Built from the parts rather than handed to `new Date(string)`, which is the whole
 * point of the function. The gateway resolves a rollup's day in UTC and nowhere else
 * (`daily_rollups.day` is the UTC day of `started_at`); a console that let the engine
 * parse the string could put the same day on a different bar depending on where the
 * browser is, and the failure looks like "yesterday's spend is on the wrong column"
 * rather than like a bug. `Date.UTC` has no locale in it at all.
 */
export function utcDayMs(day: string): number {
  const [year, month, date] = day.split("-").map(Number);
  return Date.UTC(year ?? 1970, (month ?? 1) - 1, date ?? 1);
}

/** `2026-08-11` → `11 Aug`, for an axis tick. Formatted in UTC, for `utcDayMs`' reason. */
export function dayLabel(day: string): string {
  return new Date(utcDayMs(day)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
