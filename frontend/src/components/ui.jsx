import {
  AlertTriangle, ArrowUpRight, CheckCircle2, CircleDashed, Clock, Copy, Info, Loader2, RotateCcw, WifiOff, XCircle,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { isConnectivityError } from '../services/api';

/* ------------------------------------------------------------------ layout */
/** Intro row under the top bar. The page title itself is the <h1> in the top bar. */
export function PageHeader({ description, actions }) {
  if (!description && !actions) return null;
  return (
    <div className="page-header">
      {description ? <p>{description}</p> : <span />}
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

/** The one grouping container. Use sparingly: one per meaningful section. */
export function Panel({ title, description, actions, children, flush = false, tone, className = '', id, icon: Icon, iconTone }) {
  return (
    <section className={`panel ${tone ? `tone-${tone}` : ''} ${className}`} id={id} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || actions) && (
        <div className="panel-head">
          <div className="panel-heading">
            {Icon && <span className={`icon-chip ${iconTone || ''}`}><Icon size={16} aria-hidden="true" /></span>}
            <div>
              {title && <h2 className="panel-title">{title}</h2>}
              {description && <div className="panel-desc">{description}</div>}
            </div>
          </div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      <div className={`panel-body ${flush ? 'flush' : ''}`}>{children}</div>
    </section>
  );
}

/** Several numbers inside a single panel, separated by thin rules. */
export function Metrics({ items }) {
  return (
    <div className="panel metrics" style={{ '--n': items.length }}>
      {items.map((m) => (
        <div className="metric" key={m.label}>
          <div className="metric-label">{m.label}</div>
          <div className="metric-value">
            {m.value ?? '—'}
            {m.unit && <small>{m.unit}</small>}
          </div>
          {m.detail && <div className="metric-detail">{m.detail}</div>}
        </div>
      ))}
    </div>
  );
}

/** Small summary cards: label (+ link to the page that owns the number), icon, value, detail. */
export function KpiCards({ items }) {
  return (
    <div className="kpis" style={{ '--n': items.length }}>
      {items.map((k) => {
        const Icon = k.icon;
        return (
          <div className="kpi" key={k.label}>
            <div className="kpi-head">
              <span>{k.label}</span>
              {k.to && <Link to={k.to} aria-label={`Open ${k.label}`} title="Open"><ArrowUpRight size={15} /></Link>}
            </div>
            <div className="kpi-body">
              {Icon && <span className="kpi-icon"><Icon size={16} aria-hidden="true" /></span>}
              <span className="kpi-value">{k.value ?? '—'}{k.unit && <small>{k.unit}</small>}</span>
            </div>
            {k.detail && <div className="kpi-detail">{k.detail}</div>}
          </div>
        );
      })}
    </div>
  );
}

/** One real, computed observation with a single follow-up action. */
export function Notice({ icon: Icon = Info, title, children, action, tone }) {
  return (
    <div className={`notice ${tone || ''}`} role="status">
      <span className={`icon-chip ${tone === 'danger' ? 'danger' : 'ink'}`} style={{ width: 40, height: 40 }}><Icon size={17} aria-hidden="true" /></span>
      <div className="grow">
        <div className="notice-title">{title}</div>
        <div className="notice-text">{children}</div>
      </div>
      {action}
    </div>
  );
}

const AVATAR_TINTS = [
  ['#e8eefe', '#2456d6'], ['#fde8ef', '#b4235a'], ['#e3f6ea', '#167a43'],
  ['#fdf1dc', '#9a5b0b'], ['#e6f3fa', '#1c6a8f'], ['#efe9fd', '#5b3bb8'],
];

export function initials(name = '') {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '?';
}

/** Initials avatar; the tint is derived from the student ID so it is stable everywhere. */
export function Avatar({ id = '', name = '', size }) {
  let h = 0;
  for (const ch of id || name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const [bg, fg] = AVATAR_TINTS[h % AVATAR_TINTS.length];
  return <span className={`avatar ${size === 'sm' ? 'sm' : ''}`} style={{ background: bg, color: fg }} aria-hidden="true">{initials(name || id)}</span>;
}

export function Person({ id, name, size, secondary }) {
  return (
    <span className="person">
      <Avatar id={id} name={name} size={size} />
      <span style={{ minWidth: 0 }}>
        <span className="person-name">{name || id}</span>
        {secondary !== false && <span className="person-id">{secondary || id}</span>}
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------ status */
const STATUS = {
  CONFIRMED: { tone: 'success', icon: CheckCircle2, label: 'Confirmed' },
  COMPLETED: { tone: 'success', icon: CheckCircle2, label: 'Completed' },
  WAITLISTED: { tone: 'warning', icon: Clock, label: 'Waitlisted' },
  ALREADY_REGISTERED: { tone: 'info', icon: Copy, label: 'Already registered' },
  FAILED: { tone: 'danger', icon: XCircle, label: 'Failed' },
  PENDING: { tone: 'accent', icon: Loader2, label: 'Pending' },
  RECEIVED: { tone: 'accent', icon: Loader2, label: 'Pending' },
  RUNNING: { tone: 'accent', icon: Loader2, label: 'Running' },
  RESPONSE_LOST: { tone: 'warning', icon: AlertTriangle, label: 'Response lost' },
};

export function StatusBadge({ status, label }) {
  const s = STATUS[status] || { tone: '', icon: CircleDashed, label: status || '—' };
  const Icon = s.icon;
  return (
    <span className={`badge ${s.tone}`}>
      <Icon size={12} aria-hidden="true" />
      {label || s.label}
    </span>
  );
}

const EVENT_TONE = {
  TRANSACTION_COMMITTED: 'success', REQUEST_CONFIRMED: 'success', WAITLIST_PROMOTED: 'success',
  REQUEST_WAITLISTED: 'warning', RETRY_SCHEDULED: 'warning', RECOVERY_REQUEUED: 'warning',
  DUPLICATE_REQUEST: 'warning', RESPONSE_LOST: 'warning', REGISTRATION_CANCELLED: 'warning',
  TRANSACTION_ROLLED_BACK: 'danger', WORKER_FAILURE: 'danger', REQUEST_FAILED: 'danger', SIMULATED_CRASH: 'danger',
};
const TONE_ICON = { success: CheckCircle2, warning: RotateCcw, danger: XCircle, info: Info };
export const eventTone = (type) => EVENT_TONE[type] || 'info';

export function EventBadge({ type }) {
  const tone = eventTone(type);
  const Icon = TONE_ICON[tone];
  return (
    <span className={`badge event ${tone === 'info' ? '' : tone}`}>
      <Icon size={12} aria-hidden="true" />
      {type}
    </span>
  );
}

export function Dot({ tone }) {
  return <span className={`dot ${tone || ''}`} aria-hidden="true" />;
}

/* ------------------------------------------------------------------ controls */
export function Button({ variant = 'secondary', size, loading, disabled, icon: Icon, children, type = 'button', className = '', ...props }) {
  return (
    <button type={type} className={`btn btn-${variant} ${size === 'sm' ? 'btn-sm' : ''} ${className}`}
      disabled={loading || disabled} aria-busy={loading || undefined} {...props}>
      {loading ? <span className="spinner" aria-hidden="true" /> : Icon ? <Icon size={size === 'sm' ? 14 : 16} aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

/** Two-step button for disruptive actions: first click arms, second confirms. */
export function ConfirmButton({ children, confirmLabel = 'Click again to confirm', onConfirm, ...props }) {
  const [armed, setArmed] = useState(false);
  const timer = useRef();
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <Button {...props} variant={armed ? 'danger-solid' : props.variant || 'danger'}
      aria-label={armed ? `${confirmLabel}${props['aria-label'] ? `: ${props['aria-label']}` : ''}` : props['aria-label']}
      onClick={() => {
        if (!armed) {
          setArmed(true);
          timer.current = setTimeout(() => setArmed(false), 4000);
          return;
        }
        clearTimeout(timer.current);
        setArmed(false);
        onConfirm();
      }}>
      {armed ? confirmLabel : children}
    </Button>
  );
}

export function Field({ label, hint, value, children, htmlFor }) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={htmlFor}>
        <span>{label}</span>
        {value !== undefined && <span className="mono">{value}</span>}
      </label>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

export function Segmented({ options, value, onChange, label, block }) {
  return (
    <div className={`segmented ${block ? 'block' : ''}`} role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => {
        const Icon = t.icon;
        return (
          <button key={t.value} type="button" role="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)}>
            {Icon && <Icon size={15} aria-hidden="true" />}
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

export function Progress({ value, max, indeterminate, label }) {
  if (indeterminate) {
    return <div className="progress indeterminate" role="progressbar" aria-label={label || 'Working'}><div /></div>;
  }
  const p = max ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="progress" role="progressbar" aria-label={label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={max}>
      <div style={{ width: `${p}%` }} />
    </div>
  );
}

export function CapacityBar({ confirmed, capacity }) {
  const p = capacity ? Math.min(100, (confirmed / capacity) * 100) : 0;
  const cls = confirmed > capacity ? 'over' : confirmed === capacity ? 'full' : '';
  return (
    <div className="cap" title={`${confirmed} of ${capacity} seats taken`}>
      <div className="cap-track"><div className={`cap-fill ${cls}`} style={{ width: `${p}%` }} /></div>
      <span className="cap-text">{confirmed}/{capacity}</span>
    </div>
  );
}

export function Disclosure({ summary, children, defaultOpen = false, first = false }) {
  return (
    <details className={`disclosure ${first ? 'first' : ''}`} open={defaultOpen || undefined}>
      <summary>{summary}</summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}

export function Mono({ children, title }) {
  return <span className="mono" title={title}>{children}</span>;
}

/* ------------------------------------------------------------------ feedback */
const ALERT_ICON = { danger: XCircle, warning: AlertTriangle, success: CheckCircle2, info: Info };

export function Alert({ tone = 'info', title, children, action }) {
  const Icon = ALERT_ICON[tone] || Info;
  return (
    <div className={`alert ${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <Icon size={16} aria-hidden="true" />
      <div className="grow">
        {title && <div className="alert-title">{title}</div>}
        {children && <div className="alert-body">{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function Loading({ rows = 3 }) {
  return (
    <div className="stack" style={{ padding: 16 }} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

export function Empty({ icon: Icon = CircleDashed, title, children, compact }) {
  return (
    <div className={`state ${compact ? 'compact' : ''}`}>
      <Icon size={compact ? 16 : 24} aria-hidden="true" />
      <div>
        {title && <div className="state-title">{title}</div>}
        {children && <div>{children}</div>}
      </div>
    </div>
  );
}

export function InlineError({ error, onRetry }) {
  return (
    <div style={{ padding: 16 }}>
      <Alert tone="danger" title="Could not load this section"
        action={onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}>
        {error?.message || String(error)}
      </Alert>
    </div>
  );
}

/**
 * Renders a usePolling query consistently:
 *   data -> children(data)   (stale data stays visible during an outage)
 *   connectivity error, no data -> quiet "unavailable" line (the global banner explains why)
 *   other error -> contextual error with Retry
 *   loading -> skeleton
 *   empty -> `empty` element
 */
export function QueryView({ query, isEmpty, empty, rows, children }) {
  const { data, error, refresh, stale } = query;
  if (data !== null && data !== undefined) {
    const body = isEmpty && isEmpty(data) ? (empty || <Empty title="Nothing here yet" />) : children(data);
    // Data from the previous selection stays visible but dimmed until the new data arrives.
    return stale ? <div className="is-stale" aria-busy="true">{body}</div> : body;
  }
  if (error) {
    if (isConnectivityError(error)) {
      return <Empty compact icon={WifiOff}>Live data unavailable while the backend is unreachable.</Empty>;
    }
    return <InlineError error={error} onRetry={refresh} />;
  }
  return <Loading rows={rows} />;
}
