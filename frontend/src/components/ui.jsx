import {
  AlertTriangle, CheckCircle2, CircleDashed, Clock, Copy, Inbox, Info, Loader2, RotateCcw, XCircle,
} from 'lucide-react';

export function PageHeader({ title, description, actions }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Card({ title, subtitle, icon: Icon, actions, children, className = '', flush = false }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          <div>
            {title && (
              <h3 className="card-title">
                {Icon && <Icon size={16} />}
                {title}
              </h3>
            )}
            {subtitle && <div className="card-sub">{subtitle}</div>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      <div className={`card-body ${flush ? 'flush' : ''}`}>{children}</div>
    </section>
  );
}

export function StatCard({ label, value, unit, hint, icon: Icon, loading }) {
  return (
    <div className="card stat">
      <div className="stat-label">
        {Icon && <Icon size={15} />}
        {label}
      </div>
      {loading ? (
        <div className="skeleton" style={{ height: 34, width: '60%' }} />
      ) : (
        <div className="stat-value">
          {value}
          {unit && <small>{unit}</small>}
        </div>
      )}
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

const STATUS = {
  CONFIRMED: { tone: 'good', icon: CheckCircle2, label: 'Confirmed' },
  COMPLETED: { tone: 'good', icon: CheckCircle2, label: 'Completed' },
  WAITLISTED: { tone: 'warning', icon: Clock, label: 'Waitlisted' },
  ALREADY_REGISTERED: { tone: 'info', icon: Copy, label: 'Already registered' },
  FAILED: { tone: 'critical', icon: XCircle, label: 'Failed' },
  PENDING: { tone: 'accent', icon: Loader2, label: 'Pending' },
  RECEIVED: { tone: 'accent', icon: Loader2, label: 'Pending' },
  RESPONSE_LOST: { tone: 'serious', icon: AlertTriangle, label: 'Response lost' },
};

export function StatusBadge({ status, label }) {
  const s = STATUS[status] || { tone: '', icon: CircleDashed, label: status || '—' };
  const Icon = s.icon;
  return (
    <span className={`badge ${s.tone}`}>
      <Icon size={13} />
      {label || s.label}
    </span>
  );
}

const EVENT_TONE = {
  REQUEST_RECEIVED: 'info',
  QUEUED: 'info',
  WORKER_STARTED: 'info',
  DUPLICATE_REQUEST: 'warning',
  TRANSACTION_COMMITTED: 'good',
  REQUEST_CONFIRMED: 'good',
  WAITLIST_PROMOTED: 'good',
  REQUEST_WAITLISTED: 'warning',
  REQUEST_ALREADY_REGISTERED: 'info',
  RETRY_SCHEDULED: 'warning',
  RECOVERY_REQUEUED: 'warning',
  REGISTRATION_CANCELLED: 'serious',
  TRANSACTION_ROLLED_BACK: 'critical',
  WORKER_FAILURE: 'critical',
  REQUEST_FAILED: 'critical',
  RESPONSE_LOST: 'serious',
  SIMULATED_CRASH: 'critical',
};
const TONE_ICON = { good: CheckCircle2, warning: RotateCcw, critical: XCircle, serious: AlertTriangle, info: Info };

export const eventTone = (type) => EVENT_TONE[type] || 'info';

export function EventBadge({ type }) {
  const tone = eventTone(type);
  const Icon = TONE_ICON[tone] || Info;
  return (
    <span className={`badge ${tone}`}>
      <Icon size={12} />
      {type}
    </span>
  );
}

export function Button({ variant = 'primary', size, loading, disabled, icon: Icon, children, type = 'button', ...props }) {
  return (
    <button type={type} className={`btn btn-${variant} ${size === 'sm' ? 'btn-sm' : ''}`} disabled={loading || disabled} {...props}>
      {loading ? <span className="spinner" /> : Icon ? <Icon size={size === 'sm' ? 14 : 16} /> : null}
      {children}
    </button>
  );
}

export function Spinner() {
  return <span className="spinner" />;
}

export function LoadingState({ label = 'Loading…' }) {
  return (
    <div className="state">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, children }) {
  return (
    <div className="state">
      <Icon size={30} />
      {title && <div className="state-title">{title}</div>}
      {children && <div className="small">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  return (
    <Alert tone="critical" title="Could not load data">
      {error?.message || String(error)}
      {onRetry && (
        <div style={{ marginTop: 8 }}>
          <Button variant="secondary" size="sm" onClick={onRetry}>Retry</Button>
        </div>
      )}
    </Alert>
  );
}

const ALERT_ICON = { critical: XCircle, warning: AlertTriangle, good: CheckCircle2, info: Info };

export function Alert({ tone = 'info', title, children }) {
  const Icon = ALERT_ICON[tone] || Info;
  return (
    <div className={`alert ${tone}`} role={tone === 'critical' ? 'alert' : undefined}>
      <Icon size={18} />
      <div>
        {title && <div className="alert-title">{title}</div>}
        <div className="alert-body">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, hint, value, children }) {
  return (
    <label className="field">
      <span className="field-label">
        <span>{label}</span>
        {value !== undefined && <span className="mono">{value}</span>}
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Segmented({ options, value, onChange }) {
  return (
    <div className="segmented" role="tablist">
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" aria-selected={value === o.value}
          className={value === o.value ? 'active' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ProgressBar({ value, max }) {
  const p = max ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="progress" role="progressbar" aria-valuenow={value} aria-valuemax={max}>
      <div style={{ width: `${p}%` }} />
    </div>
  );
}

export function CapacityBar({ confirmed, capacity, waitlisted }) {
  const p = capacity ? Math.min(100, (confirmed / capacity) * 100) : 0;
  const cls = confirmed > capacity ? 'over' : confirmed === capacity ? 'full' : '';
  return (
    <div className="cap">
      <div className="cap-track" title={`${confirmed} of ${capacity} seats taken`}>
        <div className={`cap-fill ${cls}`} style={{ width: `${p}%` }} />
      </div>
      <div className="cap-meta">
        <span><b>{confirmed}</b> / {capacity} seats</span>
        <span>{waitlisted ? `${waitlisted} waitlisted` : confirmed >= capacity ? 'full' : `${capacity - confirmed} free`}</span>
      </div>
    </div>
  );
}

export function Mono({ children, title }) {
  return <span className="mono" title={title}>{children}</span>;
}
