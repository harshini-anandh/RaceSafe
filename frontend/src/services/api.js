// All calls to the RaceSafe FastAPI backend. Base URL is configurable via
// VITE_API_BASE_URL (see frontend/.env.example).
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
    this.code = body && body.code;
  }
}

function formatDetail(data, status) {
  if (!data) return `HTTP ${status}`;
  const d = data.detail;
  if (Array.isArray(d)) {
    return d.map((e) => `${(e.loc || []).filter((x) => x !== 'body').join('.')}: ${e.msg}`).join('; ');
  }
  return d || data.message || `HTTP ${status}`;
}

async function send(path, { method = 'GET', body, params } = {}) {
  const url = new URL(`${API_BASE}/api${path}`);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    if (Array.isArray(value)) value.forEach((v) => url.searchParams.append(key, v));
    else url.searchParams.set(key, String(value));
  });
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${API_BASE}. Is uvicorn running?`, null);
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) throw new ApiError(res.status, formatDetail(data, res.status), data);
  return { status: res.status, data };
}

const get = async (path, params) => (await send(path, { params })).data;
const post = async (path, body, params) => (await send(path, { method: 'POST', body, params })).data;
const put = async (path, body) => (await send(path, { method: 'PUT', body })).data;
const del = async (path) => (await send(path, { method: 'DELETE' })).data;

/** True when the problem is reaching the backend or its database (shown once, globally). */
export function isConnectivityError(error) {
  return !!error && (error.status === 0 || error.code === 'DATABASE_UNAVAILABLE');
}

export function newRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export const api = {
  health: () => get('/health'),
  courses: (includeExperiment = false) => get('/courses', { include_experiment: includeExperiment }),
  course: (id) => get(`/courses/${encodeURIComponent(id)}`),
  courseRegistrations: (id) => get(`/courses/${encodeURIComponent(id)}/registrations`),
  students: (search, limit = 1000) => get('/students', { search, limit }),
  studentRegistrations: (id) => get(`/students/${encodeURIComponent(id)}/registrations`),

  /** Returns { status, data }: 200 = final result, 202 = still processing. Throws ApiError (504 = lost response demo). */
  register: (payload, { wait = true, timeout = 30 } = {}) =>
    send('/registrations', { method: 'POST', body: payload, params: { wait, timeout } }),
  requestStatus: (id) => get(`/requests/${encodeURIComponent(id)}`),
  requestEvents: (id) => get(`/requests/${encodeURIComponent(id)}/events`),
  cancel: (studentId, courseId) => post('/registrations/cancel', { student_id: studentId, course_id: courseId }),

  events: (params) => get('/events', params),
  timeline: (params) => get('/events/timeline', params),
  eventTypes: () => get('/events/types'),
  metrics: (scope = 'catalog') => get('/metrics', { scope }),
  live: () => get('/metrics/live'),
  throughput: (seconds = 120, scope = 'all') => get('/metrics/throughput', { seconds, scope }),
  consistency: () => get('/consistency'),

  scheduler: () => get('/scheduler'),
  setMode: (mode) => put('/scheduler/mode', { mode }),
  setPaused: (paused) => put('/scheduler/paused', { paused }),

  startExperiment: (config) => post('/experiments', config),
  experiments: () => get('/experiments'),
  experiment: (groupId) => get(`/experiments/${encodeURIComponent(groupId)}`),

  raceUnsafe: (config) => post('/race-lab/unsafe', config),
  raceSafe: (config) => post('/race-lab/safe', config),
  raceHistory: () => get('/race-lab/history'),

  injectFailure: (body) => post('/failures/inject', body),
  failures: () => get('/failures'),
  clearFailures: () => del('/failures'),
  simulateRestart: () => post('/failures/simulate-restart'),

  reset: () => post('/admin/reset', { confirm: 'RESET' }),
  simulateRegistrations: (courseId, count) => post('/admin/simulate-registrations', { course_id: courseId, count }),
};
