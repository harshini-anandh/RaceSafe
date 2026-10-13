import {
  Activity, BookOpen, ChevronRight, DatabaseZap, FlaskConical, History, LayoutDashboard, Menu, Network, RefreshCw, Scale,
  ShieldCheck, Unplug, UserPlus, X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useConnection } from '../hooks/ConnectionContext';
import { API_BASE } from '../services/api';
import { MODE_LABEL } from '../utils/format';
import QuickSearch from './QuickSearch';
import { Button, Dot } from './ui';

const PAGES = {
  '/': { title: 'Overview', icon: LayoutDashboard },
  '/register': { title: 'Student Registration', icon: UserPlus },
  '/courses': { title: 'Courses & Waitlists', icon: BookOpen },
  '/fairqueue': { title: 'FairQueue Experiment', icon: Scale },
  '/race-lab': { title: 'Race Condition Lab', icon: FlaskConical },
  '/recovery': { title: 'Failure Recovery', icon: History },
  '/architecture': { title: 'Architecture & Diagnostics', icon: Network },
};

const MAIN = ['/', '/register', '/courses'];
const EXPERIMENTS = [
  { to: '/fairqueue', label: 'FairQueue' },
  { to: '/race-lab', label: 'Race Condition Lab' },
  { to: '/recovery', label: 'Failure Recovery' },
];

const today = () => new Intl.DateTimeFormat(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).format(new Date());

export default function Layout() {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const page = PAGES[pathname] || PAGES['/'];
  const PageIcon = page.icon;
  const date = useMemo(today, []);

  useEffect(() => {
    setOpen(false);
    window.scrollTo(0, 0); // a new page starts at the top
  }, [pathname]);
  useEffect(() => { document.title = `${page.title} · RaceSafe`; }, [page.title]);

  return (
    <div className="app">
      <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Main navigation">
        <div className="brand">
          <div className="brand-mark"><ShieldCheck size={17} aria-hidden="true" /></div>
          <div>
            <div className="brand-name">RaceSafe</div>
            <div className="brand-sub">Course registration engine</div>
          </div>
        </div>
        <Navigation pathname={pathname} />
        <SystemStatus />
        <div className="side-foot">Ctrl Alt Del · PS OSH001</div>
      </aside>
      <div className={`drawer-backdrop ${open ? 'open' : ''}`} onClick={() => setOpen(false)} aria-hidden="true" />

      <div className="main">
        <header className="topbar">
          <button type="button" className="round-btn menu-button" onClick={() => setOpen((v) => !v)}
            aria-label={open ? 'Close navigation' : 'Open navigation'} aria-expanded={open}>
            {open ? <X size={18} /> : <Menu size={18} />}
          </button>
          <div className="topbar-page">
            <span className="page-chip"><PageIcon size={18} aria-hidden="true" /></span>
            <div style={{ minWidth: 0 }}>
              <h1>{page.title}</h1>
              <div className="topbar-date">{date}</div>
            </div>
          </div>
          <div className="topbar-actions">
            <QuickSearch />
            <StatusButton />
          </div>
        </header>
        <main className="content">
          <ConnectionBanner />
          <div className="page" key={pathname}>
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

function Navigation({ pathname }) {
  const inExperiments = EXPERIMENTS.some((e) => e.to === pathname);
  const [expOpen, setExpOpen] = useState(true);
  useEffect(() => { if (inExperiments) setExpOpen(true); }, [inExperiments]);

  const item = (to) => {
    const Icon = PAGES[to].icon;
    return (
      <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
        <span className="nav-icon"><Icon size={16} aria-hidden="true" /></span>
        <span>{PAGES[to].title}</span>
      </NavLink>
    );
  };

  return (
    <nav>
      <div className="nav-group">
        <div className="nav-heading">Main</div>
        {MAIN.map(item)}
      </div>
      <div className="nav-group">
        <div className="nav-heading">Experiments</div>
        <button type="button" className={`nav-link nav-parent ${inExperiments ? 'within' : ''}`} aria-expanded={expOpen}
          onClick={() => setExpOpen((v) => !v)}>
          <span className="nav-icon"><FlaskConical size={16} aria-hidden="true" /></span>
          <span>OS experiments</span>
          <ChevronRight size={15} className="nav-chevron" aria-hidden="true" />
        </button>
        {expOpen && (
          <div className="nav-sub">
            {EXPERIMENTS.map((e) => (
              <NavLink key={e.to} to={e.to} className={({ isActive }) => (isActive ? 'active' : '')}>
                <span className="nav-dot" style={{ background: pathname === e.to ? 'var(--accent)' : 'var(--border-strong)' }} aria-hidden="true" />
                {e.label}
              </NavLink>
            ))}
          </div>
        )}
      </div>
      <div className="nav-group">
        <div className="nav-heading">System</div>
        {item('/architecture')}
      </div>
    </nav>
  );
}

function SystemStatus() {
  const { apiOnline, dbOnline, health, error } = useConnection();
  return (
    <div className="side-status" aria-label="System status">
      <div className="side-status-title">
        <span>System status</span>
        <Link to="/architecture" className="small">Details</Link>
      </div>
      <dl>
        <dt>API</dt>
        <dd><Dot tone={apiOnline ? 'success' : error ? 'danger' : ''} />{apiOnline ? 'Online' : error ? 'Offline' : '…'}</dd>
        <dt>MySQL</dt>
        <dd><Dot tone={dbOnline ? 'success' : apiOnline ? 'danger' : ''} />{dbOnline ? 'Connected' : apiOnline ? 'Down' : '—'}</dd>
        <dt>Scheduler</dt>
        <dd>{health ? `${MODE_LABEL[health.scheduler_mode]}${health.scheduler_paused ? ' · paused' : ''}` : '—'}</dd>
        <dt>Workers</dt>
        <dd>{health ? `${health.workers}${health.demo_mode ? ' · dev mode' : ''}` : '—'}</dd>
      </dl>
    </div>
  );
}

function StatusButton() {
  const { apiOnline, dbOnline, error } = useConnection();
  const tone = apiOnline && dbOnline ? 'var(--success-dot)' : error || apiOnline ? 'var(--danger-dot)' : 'var(--border-strong)';
  const label = apiOnline ? (dbOnline ? 'API online, MySQL connected' : 'API online, MySQL down') : error ? 'API offline' : 'Connecting';
  return (
    <Link to="/architecture" className="round-btn" title={label} aria-label={`System status: ${label}`}>
      <Activity size={17} aria-hidden="true" />
      <span className="badge-dot" style={{ background: tone }} />
    </Link>
  );
}

/** The only place a connectivity problem is reported. */
function ConnectionBanner() {
  const { apiOnline, dbOnline, health, error, checking, lastOk, retry } = useConnection();
  if (apiOnline && dbOnline) return null;
  if (!apiOnline && !error) return null; // first check still running

  const retryButton = <Button size="sm" variant="ink" icon={RefreshCw} loading={checking} onClick={retry}>Retry now</Button>;

  if (!apiOnline) {
    return (
      <div className="conn-banner" role="alert">
        <span className="icon-chip danger" style={{ width: 40, height: 40 }}><Unplug size={17} aria-hidden="true" /></span>
        <div className="grow">
          <b>Can’t reach the RaceSafe API.</b> Live data is paused; figures on screen may be out of date.
          {lastOk && <> Last contact {lastOk.toLocaleTimeString()}.</>}
          <details>
            <summary>Details</summary>
            Tried <span className="mono">{API_BASE}/api/health</span>: {error.message} Start the backend in its own terminal:{' '}
            <span className="mono">cd backend; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --port 8000</span>. This page
            retries automatically every 5 s.
          </details>
        </div>
        {retryButton}
      </div>
    );
  }
  return (
    <div className="conn-banner warning" role="alert">
      <span className="icon-chip warning" style={{ width: 40, height: 40 }}><DatabaseZap size={17} aria-hidden="true" /></span>
      <div className="grow">
        <b>The API is running but MySQL is unavailable.</b> Registrations and metrics need the database.
        <details>
          <summary>Details</summary>
          {health?.database?.error || 'No error detail returned.'} Check that the MySQL80 service is running and that
          backend\.env has the right password, then run <span className="mono">python scripts\init_db.py</span>.
        </details>
      </div>
      {retryButton}
    </div>
  );
}
