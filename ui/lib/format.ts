/**
 * Turning the admin API's answers into things a human reads.
 *
 * **Money arrives as a string and stays one until the last possible moment.** The gateway
 * serialises `NUMERIC` as `"0.000011500000"` precisely so a double never touches it
 * (H-024, and `format_usd`); a console that did `parseFloat` on arrival would undo that
 * in the first line of the first component. So the sum below is done on integer
 * picodollars — the same 1e-12 quantum the budget gate stores (H-030) — and only the
 * *rendering* is lossy, deliberately and visibly.
 */

/** 1e-12 USD: `metering.cost.USD_QUANTUM`, and what a picodollar integer counts. */
const PICO_PLACES = 12;

/** Parse `"0.000011500000"` into integer picodollars. Exact for anything the ledger holds. */
export function toPicos(usd: string | null | undefined): bigint {
  if (usd === null || usd === undefined || usd === "") return 0n;
  const negative = usd.startsWith("-");
  const digits = negative ? usd.slice(1) : usd;
  const [whole = "0", fraction = ""] = digits.split(".");
  const padded = (fraction + "0".repeat(PICO_PLACES)).slice(0, PICO_PLACES);
  const value = BigInt(whole || "0") * 10n ** BigInt(PICO_PLACES) + BigInt(padded || "0");
  return negative ? -value : value;
}

/** Sum a column of money exactly. Every "total spend" in this console goes through here. */
export function sumUsd(values: readonly (string | null | undefined)[]): bigint {
  return values.reduce<bigint>((total, value) => total + toPicos(value), 0n);
}

export function picosToNumber(picos: bigint): number {
  return Number(picos) / 1e12;
}

/**
 * Render picodollars to a fixed number of places, **without a float in the middle**.
 *
 * `Number(34_500_000n) / 1e12` is 0.0000345, and `(0.0000345).toFixed(6)` is `"0.000034"`
 * — because the binary double nearest that decimal is a hair *below* it, so the half
 * rounds down. That is a display bug of the same family as the one the whole money
 * pipeline exists to avoid, arriving in the last line of the last file. Rounding
 * half-up on the integer instead costs eight lines and cannot be wrong.
 */
function fixedFromPicos(picos: bigint, places: number): string {
  const negative = picos < 0n;
  const absolute = negative ? -picos : picos;
  const scale = 10n ** BigInt(PICO_PLACES - places);
  const rounded = (absolute + scale / 2n) / scale;
  const unit = 10n ** BigInt(places);
  const fraction = (rounded % unit).toString().padStart(places, "0");
  return `${negative ? "-" : ""}${rounded / unit}${places > 0 ? `.${fraction}` : ""}`;
}

/**
 * Money for a human. Small figures keep enough places to stay non-zero, because this
 * gateway's canonical request costs $0.0000115 and rounding it to `$0.00` would render
 * the entire mock-driven demo as free.
 */
export function money(usd: string | null | undefined, options?: { compact?: boolean }): string {
  if (usd === null || usd === undefined) return "—";
  return moneyFromPicos(toPicos(usd), options);
}

export function moneyFromPicos(picos: bigint, options?: { compact?: boolean }): string {
  const absolute = picos < 0n ? -picos : picos;
  if (absolute === 0n) return "$0.00";
  if (options?.compact && absolute >= 1_000n * 10n ** 12n) {
    return `$${(picosToNumber(picos) / 1000).toFixed(1)}K`;
  }
  if (absolute >= 10n ** 12n) return `$${fixedFromPicos(picos, 2)}`;
  if (absolute >= 10n ** 10n) return `$${fixedFromPicos(picos, 4)}`;
  if (absolute >= 10n ** 6n) return `$${fixedFromPicos(picos, 6)}`;
  // Below a millionth of a cent, places stop being readable and the magnitude is the
  // only thing worth saying.
  return `$${picosToNumber(picos).toExponential(2)}`;
}

/** Counts, thousands-separated; compact past five figures so a tile never wraps. */
export function count(value: number | null | undefined, options?: { compact?: boolean }): string {
  if (value === null || value === undefined) return "—";
  if (options?.compact && Math.abs(value) >= 100000) {
    return `${(value / 1000).toFixed(0)}K`;
  }
  if (options?.compact && Math.abs(value) >= 10000) {
    return `${(value / 1000).toFixed(1)}K`;
  }
  return value.toLocaleString("en-US");
}

/** Milliseconds, at a precision that matches what the number is worth reading to. */
export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 10000) return `${(value / 1000).toFixed(1)} s`;
  if (value >= 100) return `${value.toFixed(0)} ms`;
  if (value >= 1) return `${value.toFixed(1)} ms`;
  return `${value.toFixed(3)} ms`;
}

export function percent(part: number, whole: number, places = 1): string {
  if (!whole) return "—";
  return `${((part / whole) * 100).toFixed(places)}%`;
}

export function shortId(value: string | null | undefined, keep = 8): string {
  if (!value) return "—";
  return value.length <= keep ? value : `${value.slice(0, keep)}…`;
}

/** `hr_bc06ff13…` — enough of a request id to match against a terminal, never all of it. */
export function requestId(value: string): string {
  return value.length <= 14 ? value : `${value.slice(0, 14)}…`;
}

export function clock(iso: string): string {
  const when = new Date(iso);
  return when.toLocaleTimeString("en-GB", { hour12: false });
}

export function stamp(iso: string): string {
  const when = new Date(iso);
  return `${when.toLocaleDateString("en-CA")} ${when.toLocaleTimeString("en-GB", { hour12: false })}`;
}

/** "4s ago" — the live view's clock, and the only place relative time is used. */
export function ago(iso: string, now = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 60) return `${Math.round(value / 60)}m`;
  return `${value.toFixed(value < 10 ? 1 : 0)}s`;
}
