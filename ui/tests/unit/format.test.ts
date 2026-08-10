import assert from "node:assert/strict";
import { test } from "node:test";

import {
  count,
  money,
  moneyFromPicos,
  ms,
  percent,
  sumUsd,
  toPicos,
} from "../../lib/format.ts";

/**
 * **The one place this console could quietly undo six phases of exactness.**
 *
 * The gateway serialises money as a string precisely so a double never touches it
 * (H-024). A dashboard that did `parseFloat` on arrival and summed the results would be
 * the D-017 mistake reintroduced in the last mile — visible to nobody, because a float
 * sum of small numbers looks completely reasonable until somebody compares it against
 * `psql`. So the arithmetic runs on integer picodollars, and this file pins it.
 */

test("a ledger string parses to exact picodollars", () => {
  assert.equal(toPicos("0.000011500000"), 11_500_000n);
  assert.equal(toPicos("1"), 1_000_000_000_000n);
  assert.equal(toPicos("0"), 0n);
  assert.equal(toPicos(null), 0n);
  assert.equal(toPicos(undefined), 0n);
});

test("a negative amount keeps its sign", () => {
  // `remaining` really can go negative: a cap lowered under its own spend is a state the
  // budget store maintains exactly rather than clamping.
  assert.equal(toPicos("-0.000950000000"), -950_000_000n);
});

test("the smallest amount the ledger can hold survives", () => {
  assert.equal(toPicos("0.000000000001"), 1n);
});

test("a budget-sized amount stays exact past the point a double stops being one", () => {
  // **The assertion that catches a `parseFloat` implementation.** Below about $9,007 —
  // 2^53 picodollars — `Math.round(parseFloat(x) * 1e12)` happens to land on the right
  // integer, so every small-value test in this file passes against it. Above it the
  // float has fewer bits than the number needs and starts landing on a neighbour, and
  // every sum built on it drifts. $9,007 is an ordinary monthly cap.
  assert.equal(toPicos("9999.999999999999"), 9_999_999_999_999_999n);
  assert.notEqual(BigInt(Math.round(parseFloat("9999.999999999999") * 1e12)), 9_999_999_999_999_999n);

  assert.equal(sumUsd(["9999.999999999999", "0.000000000001"]), 10_000_000_000_000_000n);
  assert.equal(moneyFromPicos(10_000_000_000_000_000n), "$10000.00");
});

test("summing a column is exact where a float sum would not be", () => {
  const rows = Array.from({ length: 10 }, () => "0.000000000001");
  assert.equal(sumUsd(rows), 10n);

  // The float version of the same sum, for the record: 0.1 + 0.2 is the canonical
  // example, and a spend column is full of numbers that behave the same way.
  assert.notEqual(0.1 + 0.2, 0.3);
  assert.equal(sumUsd(["0.1", "0.2"]), toPicos("0.3"));
});

test("a total sums the way the ledger does", () => {
  assert.equal(sumUsd(["0.000011500000", "0.000011500000", "0.000011500000"]), 34_500_000n);
  // Rounded half-up on the integer. `Number(34_500_000n) / 1e12` is a double a hair below
  // 0.0000345, so `.toFixed(6)` on it gives "0.000034" — the same class of last-mile
  // rounding error the whole money pipeline exists to avoid.
  assert.equal(moneyFromPicos(34_500_000n), "$0.000035");
  assert.notEqual((Number(34_500_000n) / 1e12).toFixed(6), "0.000035");
});

test("a small cost renders as a number rather than as $0.00", () => {
  // The canonical mock request costs $0.0000115. Rounding it to cents would render the
  // whole keyless demo as free, which is the opposite of what a cost meter is for.
  assert.equal(money("0.000011500000"), "$0.000012");
  assert.equal(money("0.000000000000"), "$0.00");
  assert.equal(money(null), "—");
});

test("larger amounts round to the places a human reads", () => {
  assert.equal(money("12.500000000000"), "$12.50");
  assert.equal(money("0.045000000000"), "$0.0450");
  assert.equal(money("2500.000000000000", { compact: true }), "$2.5K");
});

test("counts are grouped, and compacted only when asked", () => {
  assert.equal(count(1284), "1,284");
  assert.equal(count(12900, { compact: true }), "12.9K");
  assert.equal(count(1284, { compact: true }), "1,284");
  assert.equal(count(null), "—");
});

test("durations carry precision where it means something", () => {
  assert.equal(ms(0.019), "0.019 ms");
  assert.equal(ms(4.457), "4.5 ms");
  assert.equal(ms(5639.662), "5640 ms");
  assert.equal(ms(12000), "12.0 s");
  assert.equal(ms(null), "—");
});

test("a percentage of nothing is not zero", () => {
  assert.equal(percent(0, 0), "—");
  assert.equal(percent(3, 4), "75.0%");
});
