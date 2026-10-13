import {
  BookOpen, FlaskConical, History, LayoutDashboard, Network, Scale, ShieldCheck, UserPlus,
} from 'lucide-react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useHealth } from '../hooks/HealthContext';
import { API_BASE } from '../services/api';
import { MODE_LABEL } from '../utils/format';
import { Alert } from './ui';

export const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, section: 'Monitor' },
  { to: '/register', label: 'Student Registration', icon: UserPlus, section: 'Monitor' },
  { to: '/courses', label: 'Courses & Waitlists', icon: BookOpen, section: 'Monitor' },
  { to: '/fairqueue', label: 'FairQueue Experiment', icon: Scale, section: 'Experiments' },
  { to: '/race-lab', label: 'Race Condition Lab', icon: FlaskConical, section: 'Experiments' },
  { to: '/recovery', label: 'Failure Recovery', icon: History, section: 'Experiments' },
  { to: '/architecture', label: 'Architecture & Demo', icon: Network, section: 'System' },
];

export default function Layout() {
  const { health, error } = useHealth();
  const location = useLocation();
  const current = NAV.find((n) => (n.to === '/' ? location.pathname === '/' : location.pathname.startsWith(n.to)));
  let lastSection = null;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><ShieldCheck size={20} /></div>
          <div className="brand-text">
            <div className="brand-name">RaceSafe</div>
            <div className="brand-sub">Fair & fault-tolerant registration</div>
          </div>
        </div>
        {NAV.map((item) => {
          const header = item.section !== lastSection ? item.section : null;
          lastSection = item.section;
          const Icon = item.icon;
          return (
            <div key={item.to}>
              {header && <div className="nav-section">{header}</div>}
              <NavLink to={item.to} end={item.to === '/'} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                title={item.label}>
                <Icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            </div>
          );
        })}
        <div className="sidebar-foot">
          Team Ctrl Alt Del · PS OSH001
          <br />
          Kernel Ops OS Hackathon
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-title">{current ? current.label : 'RaceSafe'}</div>
          <div className="topbar-pills">
            <span className="pill">
              <span className={`dot ${error ? 'critical' : 'good'}`} /> API <b>{error ? 'offline' : 'online'}</b>
            </span>
            <span className="pill">
              <span className={`dot ${health?.database?.ok ? 'good' : 'critical'}`} /> MySQL{' '}
              <b>{health ? (health.database.ok ? 'connected' : 'down') : '—'}</b>
            </span>
            {health && (
              <>
                <span className="pill">Scheduler <b>{MODE_LABEL[health.scheduler_mode]}</b>{health.scheduler_paused && <b> (paused)</b>}</span>
                <span className="pill">Workers <b>{health.workers}</b></span>
                <span className="pill">Env <b>{health.environment}</b></span>
              </>
            )}
          </div>
        </header>
        <main className="content">
          {error && (
            <Alert tone="critical" title="Backend not reachable">
              The dashboard could not reach <span className="mono">{API_BASE}</span>. Start the backend in its own
              terminal: <span className="mono">cd backend; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --port 8000</span>
            </Alert>
          )}
          {health && !health.database.ok && (
            <Alert tone="critical" title="Backend is running but cannot reach MySQL">
              {health.database.error} — check that the MySQL80 service is running and that backend/.env has the right
              password, then run <span className="mono">python scripts\init_db.py</span>.
            </Alert>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  );
}
