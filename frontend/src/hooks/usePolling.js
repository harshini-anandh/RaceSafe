import { useCallback, useEffect, useRef, useState } from 'react';
import { useConnection } from './ConnectionContext';

/**
 * Calls `fetcher` now and then every `intervalMs` (0 = only once).
 *
 * - Keeps the last good data while a refresh fails, so layouts stay stable.
 * - Ignores responses from an older `deps` generation.
 * - `stale` is true while the data on screen belongs to a previous selection
 *   (e.g. the previous course), so the UI can dim it instead of showing it as current.
 * - Refetches immediately when the backend/database reconnects.
 */
export function usePolling(fetcher, intervalMs, deps = []) {
  const { epoch } = useConnection();
  const [state, setState] = useState({ data: null, error: null, loading: true, gen: 0 });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const generation = useRef(0);
  const selection = useRef(0); // increments only when `deps` change (not on reconnect)
  const depsKey = JSON.stringify(deps);
  const lastDeps = useRef(depsKey);
  if (lastDeps.current !== depsKey) {
    lastDeps.current = depsKey;
    selection.current += 1;
  }
  const currentSelection = selection.current;

  const load = useCallback(async (gen, sel) => {
    try {
      const data = await fetcherRef.current();
      if (gen === generation.current) setState({ data, error: null, loading: false, gen: sel });
    } catch (error) {
      if (gen === generation.current) setState((s) => ({ ...s, error, loading: false }));
    }
  }, []);

  useEffect(() => {
    const gen = ++generation.current;
    const sel = currentSelection;
    let timer;
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    const tick = async () => {
      await load(gen, sel);
      if (alive && intervalMs) timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, load, epoch, depsKey]);

  const refresh = useCallback(() => load(generation.current, selection.current), [load]);
  return {
    data: state.data,
    error: state.error,
    loading: state.loading,
    stale: state.data !== null && state.gen !== currentSelection,
    refresh,
  };
}
