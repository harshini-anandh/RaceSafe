import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api } from '../services/api';

/**
 * Single source of truth for backend connectivity.
 *
 * Polls GET /api/health every 5 s. Pages never show their own "backend not
 * reachable" errors; the Layout shows ONE banner from this state. When the API
 * (or the database) comes back, `epoch` increments, and every usePolling hook
 * refetches immediately.
 */
const ConnectionContext = createContext(null);
const INTERVAL_MS = 5000;

export function ConnectionProvider({ children }) {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [checking, setChecking] = useState(true);
  const [lastOk, setLastOk] = useState(null);
  const [epoch, setEpoch] = useState(0);
  const wasDown = useRef(false);
  const inFlight = useRef(false);

  const check = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setChecking(true);
    try {
      const h = await api.health();
      setHealth(h);
      setError(null);
      setLastOk(new Date());
      const down = !h.database?.ok;
      if (wasDown.current && !down) setEpoch((e) => e + 1); // recovered -> refetch everything
      wasDown.current = down;
    } catch (e) {
      setError(e);
      wasDown.current = true;
    } finally {
      inFlight.current = false;
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    check();
    const id = setInterval(check, INTERVAL_MS);
    return () => clearInterval(id);
  }, [check]);

  const apiOnline = !error && health !== null;
  const value = {
    health: error ? null : health,
    lastHealth: health,
    error,
    checking,
    lastOk,
    epoch,
    apiOnline,
    dbOnline: apiOnline && !!health?.database?.ok,
    demoMode: !!health?.demo_mode,
    retry: check,
  };
  return <ConnectionContext.Provider value={value}>{children}</ConnectionContext.Provider>;
}

export function useConnection() {
  const ctx = useContext(ConnectionContext);
  if (!ctx) throw new Error('useConnection must be used inside <ConnectionProvider>');
  return ctx;
}
