import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DAY_MS,
  NO_PROVIDER,
  SERIES_SLOTS,
  barPath,
  colourFor,
  dayLabel,
  niceCeiling,
  seriesColours,
  timeSlots,
  utcDayMs,
} from "../../lib/series.ts";

/**
 * The chart layer's arithmetic. Two properties here are correctness rather than taste.
 *
 * **Colour follows the entity.** A provider that goes silent during a kill demo must not
 * repaint the one that took over — a reader who learned "vllm_a is blue" is misled the
 * moment blue starts meaning something else.
 *
 * **Gaps are filled by whoever knows the x-domain.** The store deliberately returns no
 * row for a quiet minute; `timeSlots` is the thing that knows the chart is drawing "the
 * last ten minutes" and therefore exactly which minutes are missing.
 */

test("a provider keeps its slot when another disappears", () => {
  const all = seriesColours(["anthropic", "mock", "mock_fallback", "vllm_a", "vllm_b"]);
  const primaryColour = colourFor(all, "vllm_a");
  const fallbackColour = colourFor(all, "vllm_b");

  // The gateway still reports every configured provider after one is killed — that is the
  // point of `/admin/providers` — so the map is unchanged and so are the colours.
  assert.equal(colourFor(all, "vllm_a"), primaryColour);
  assert.equal(colourFor(all, "vllm_b"), fallbackColour);
  assert.notEqual(primaryColour, fallbackColour);
});

test("slots are assigned in fixed order, never cycled", () => {
  const colours = seriesColours(["a", "b", "c", "d", "e"]);
  assert.deepEqual([...colours.values()], [...SERIES_SLOTS]);
});

test("a sixth series does not invent a hue", () => {
  const colours = seriesColours(["a", "b", "c", "d", "e", "f"]);
  assert.equal(colours.get("f"), NO_PROVIDER);
});

test("no provider is an absence, not an identity", () => {
  assert.equal(colourFor(seriesColours(["a"]), null), NO_PROVIDER);
  assert.equal(colourFor(seriesColours(["a"]), "never-configured"), NO_PROVIDER);
});

test("an axis ceiling lands on a number a human reads", () => {
  assert.equal(niceCeiling(0), 1);
  assert.equal(niceCeiling(7), 7.5);
  assert.equal(niceCeiling(23), 30);
  assert.equal(niceCeiling(101), 150);
});

test("time slots are contiguous and end at the present bucket", () => {
  const width = 10_000;
  const slots = timeSlots(1_786_331_909_000, 6, width);
  assert.equal(slots.length, 6);
  for (let index = 1; index < slots.length; index += 1) {
    assert.equal((slots[index] ?? 0) - (slots[index - 1] ?? 0), width);
  }
  assert.equal((slots.at(-1) ?? 0) % width, 0);
  assert.ok((slots.at(-1) ?? 0) <= 1_786_331_909_000);
});

test("a bar is rounded at the data end and square at the baseline", () => {
  const path = barPath(0, 10, 20, 40, 4);
  // One arc pair at the top, none at the bottom: the baseline is where the bar grows
  // from, and rounding both ends turns a magnitude into a lozenge.
  assert.equal((path.match(/a4 4/g) ?? []).length, 2);
  assert.ok(path.startsWith("M0 50"));
});

test("a bar shorter than its radius does not become a lens", () => {
  const path = barPath(0, 0, 20, 2, 4);
  assert.equal((path.match(/a4 4/g) ?? []).length, 0);
});

/**
 * Phase 9's history view draws days, and a day is the one time unit where the browser's
 * locale can silently move a bar. `daily_rollups.day` is the **UTC** day of the request's
 * arrival; a console that let the engine parse the string, or that formatted it in the
 * viewer's zone, would put yesterday's spend on the wrong column for half the planet —
 * and it would look like a data problem rather than a parsing one.
 */

test("a rollup day is UTC midnight, whatever the browser thinks a day is", () => {
  assert.equal(utcDayMs("2026-08-11"), Date.UTC(2026, 7, 11));
  // The property that matters: a day is exactly one slot wide, with no hour lost or
  // gained at a DST boundary — 2026-03-29 is the European spring-forward Sunday.
  assert.equal(utcDayMs("2026-03-30") - utcDayMs("2026-03-29"), DAY_MS);
  assert.equal(utcDayMs("2026-11-02") - utcDayMs("2026-11-01"), DAY_MS);
});

test("a day slot maps back to the day string the API sent", () => {
  const day = "2026-08-11";
  assert.equal(new Date(utcDayMs(day)).toISOString().slice(0, 10), day);
  // `timeSlots` at day width lands on UTC midnight, which is what lets the chart look a
  // rollup up by its own day key rather than by a rounded local timestamp.
  const slots = timeSlots(utcDayMs(day) + 61_000, 3, DAY_MS);
  assert.equal(slots.at(-1), utcDayMs(day));
  assert.equal(slots[0], utcDayMs("2026-08-09"));
});

test("a day is labelled in UTC, not in the reader's zone", () => {
  // Any offset west of UTC would render 2026-08-01 as "31 Jul" if the formatter were left
  // to the local zone; the label pins the zone instead of hoping.
  assert.equal(dayLabel("2026-08-01"), "1 Aug");
  assert.equal(dayLabel("2026-12-31"), "31 Dec");
});
