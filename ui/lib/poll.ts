"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AdminRequestError } from "@/lib/api";

/**
 * How this console stays current: **it polls.** No websocket, no SSE, no shared
 * subscription layer (H-056).
 *
 * The argument is short. Every number here is a `GET` against an admin API that already
 * exists and is already tested; a push channel would need a second transport on the
 * gateway, a fan-out story for the several Fargate tasks Phase 9 runs, and a reconnect
 * dance — all to shave a second off a figure a human is *watching*. A two-second poll
 * makes the kill demo legible on screen (a request appears, the provider it names
 * changes) and costs one indexed query. The plan's own §P7 text asks for live tiles
 * "where it's cheap"; this is where it is cheap.
 *
 * Four properties that are not optional in a thing left open on a second monitor:
 *
 * - **A hidden tab does not poll.** A console open behind an editor for a day would
 *   otherwise be a slow, permanent load against the same ledger the experiments read.
 * - **Refetch never flashes.** The previous render is held while the next is in flight
 *   (the caller styles that with `.refreshing`), so nothing jumps and no number blinks
 *   out and back — including across a filter change, where a skeleton would be a layout
 *   jump for data that is about to look almost the same.
 * - **In-flight results from an old parameter set are dropped.** Changing a filter while
 *   a poll is running must not let the old slice land on top of the new one.
 * - **`fetchedAt` is the clock.** Every view that draws a time window uses the moment the
 *   data was *read* rather than `Date.now()` at render time. That keeps rendering pure —
 *   two renders of the same data draw the same chart — and it is also more honest: a
 *   window ending "now" when the rows are four seconds old is a window with a gap at the
 *   end that means "no traffic" and does not.
 */

export const LIVE_INTERVAL_MS = 2000;
export const PAGE_INTERVAL_MS = 5000;
export const SLOW_INTERVAL_MS = 15000;

export type Poll<T> = {
  data: T | null;
  error: AdminRequestError | Error | null;
  /** When the data on screen was read. `0` before the first result. */
  fetchedAt: number;
  /** True while a fetch is in flight *and* something is already on screen. */
  refreshing: boolean;
  /** True until the first result (or first failure) — the only time a skeleton is right. */
  loading: boolean;
  refresh: () => void;
};

export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number = PAGE_INTERVAL_MS,
  deps: readonly unknown[] = [],
): Poll<T> {
  const [state, setState] = useState<{
    data: T | null;
    error: AdminRequestError | Error | null;
    fetchedAt: number;
  }>({ data: null, error: null, fetchedAt: 0 });
  const [inFlight, setInFlight] = useState(false);
  const generation = useRef(0);

  // Every caller passes an inline closure, so depending on the fetcher itself would
  // restart the interval on every render. The declared `deps` are the contract instead,
  // and the ref is synced in its own effect (declared first, so it commits before the
  // polling effect below re-reads it) rather than during render.
  const latest = useRef(fetcher);
  useEffect(() => {
    latest.current = fetcher;
  });

  const run = useCallback(async () => {
    const mine = ++generation.current;
    setInFlight(true);
    try {
      const result = await latest.current();
      if (mine !== generation.current) return; // a newer request has already been sent
      setState({ data: result, error: null, fetchedAt: Date.now() });
    } catch (caught) {
      if (mine !== generation.current) return;
      setState((previous) => ({
        ...previous,
        error: caught instanceof Error ? caught : new Error(String(caught)),
        fetchedAt: Date.now(),
      }));
    } finally {
      if (mine === generation.current) setInFlight(false);
    }
  }, []);

  useEffect(() => {
    void run();
    if (intervalMs <= 0) return;

    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(() => void run(), intervalMs);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        void run(); // catch up immediately rather than after a full interval
        start();
      }
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, intervalMs, ...deps]);

  return {
    data: state.data,
    error: state.error,
    fetchedAt: state.fetchedAt,
    refreshing: inFlight && state.fetchedAt > 0,
    loading: state.fetchedAt === 0,
    refresh: () => void run(),
  };
}
