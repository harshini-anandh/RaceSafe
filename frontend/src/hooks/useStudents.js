import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { useConnection } from './ConnectionContext';

let cache = null;

/** Loads the sample students once, shares them between pages, and retries after reconnecting. */
export function useStudents() {
  const { epoch } = useConnection();
  const [state, setState] = useState({ students: cache || [], loading: !cache, error: null });
  useEffect(() => {
    if (cache) return undefined;
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    api
      .students(undefined, 1000)
      .then((d) => {
        cache = d.students;
        if (alive) setState({ students: d.students, loading: false, error: null });
      })
      .catch((error) => alive && setState({ students: [], loading: false, error }));
    return () => {
      alive = false;
    };
  }, [epoch]);
  return state;
}
