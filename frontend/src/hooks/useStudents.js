import { useEffect, useState } from 'react';
import { api } from '../services/api';

let cache = null;

/** Loads the sample students once and shares them between pages. */
export function useStudents() {
  const [state, setState] = useState({ students: cache || [], loading: !cache, error: null });
  useEffect(() => {
    if (cache) return;
    let alive = true;
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
  }, []);
  return state;
}
