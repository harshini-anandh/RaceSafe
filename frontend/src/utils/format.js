export const fmtNum = (v, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });

export function fmtMs(v) {
  if (v === null || v === undefined) return '—';
  if (v >= 10000) return `${(v / 1000).toFixed(1)} s`;
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  if (v >= 100) return `${v.toFixed(0)} ms`;
  return `${v.toFixed(1)} ms`;
}

export function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

export function fmtDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

export const shortId = (id) => (id ? `${id.slice(0, 8)}…` : '—');

export const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);

export const MODE_LABEL = { GLOBAL_FIFO: 'Global FIFO', FAIR_QUEUE: 'FairQueue' };
