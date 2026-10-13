import { createContext, useContext } from 'react';
import { api } from '../services/api';
import { usePolling } from './usePolling';

const HealthContext = createContext({ health: null, error: null, refresh: () => {} });

/** Polls GET /api/health every 4 s and shares the result with every page. */
export function HealthProvider({ children }) {
  const { data, error, refresh } = usePolling(() => api.health(), 4000);
  return <HealthContext.Provider value={{ health: data, error, refresh }}>{children}</HealthContext.Provider>;
}

export const useHealth = () => useContext(HealthContext);
