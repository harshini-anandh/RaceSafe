import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Calls `fetcher` now and then every `intervalMs` (0 = only once).
 * Responses from an older `deps` generation are ignored, so switching
 * selections never shows stale data.
 */
export function usePolling(fetcher, intervalMs, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const generation = useRef(0);

  const load = useCallback(async (gen) => {
    try {
      const data = await fetcherRef.current();
      if (gen === generation.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (gen === generation.current) setState((s) => ({ ...s, error, loading: false }));
    }
  }, []);

  useEffect(() => {
    const gen = ++generation.current;
    let timer;
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    const tick = async () => {
      await load(gen);
      if (alive && intervalMs) timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, load, ...deps]);

  const refresh = useCallback(() => load(generation.current), [load]);
  return { ...state, refresh };
}
