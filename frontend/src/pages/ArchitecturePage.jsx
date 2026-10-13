import { Activity, BookText, SlidersHorizontal, SquareTerminal, Trash2, Workflow } from 'lucide-react';
import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Button, Disclosure, Dot, EventBadge, PageHeader, Panel, QueryView, Segmented,
} from '../components/ui';
import { useConnection } from '../hooks/ConnectionContext';
import { useToast } from '../hooks/Toasts';
import { usePolling } from '../hooks/usePolling';
import { API_BASE, api } from '../services/api';
import { MODE_LABEL } from '../utils/format';

const CONCEPTS = [
  ['Threads, bounded pool', 'Worker pool (ThreadPoolExecutor, 8 threads)', 'Overlaps I/O-bound MySQL work while capping threads and connections. Python threads do not give CPU parallelism (GIL); the GIL is released while waiting on the database.'],
  ['Race condition, critical section', 'Check seat, then take seat', 'Where overbooking happens; RaceSafe runs it from the course row lock to COMMIT.'],
  ['Mutual exclusion, synchronization', 'SELECT … FOR UPDATE; condition variable on queues', 'One transaction per course at a time; idle workers sleep in Condition.wait() instead of spinning.'],
  ['Round-robin scheduling', 'FairQueue', 'Stops one hot course from starving the others. Application-level policy; the OS still schedules threads.'],
  ['Producer–consumer', 'API → queues → workers', 'Absorbs bursts; HTTP 503 when the queue is full (back-pressure).'],
  ['Deadlock avoidance', 'Fixed lock order: request → course → registration', 'No circular waits; MySQL deadlocks and lock timeouts roll back and retry.'],
  ['Fault tolerance, recovery', 'Rollback, 3 attempts, request IDs, restart scan', 'No half-written state; every request gets exactly one result.'],
];

const DEMO_STEPS = [
  ['Architecture: request flow and live workers', '/architecture'],
  ['Race Condition Lab: unsafe run overbooks', '/race-lab'],
  ['Race Condition Lab: RaceSafe, 500 requests → exactly 10 confirmed', '/race-lab'],
  ['Overview: every correctness check passes', '/'],
  ['FairQueue: compare both under skewed demand', '/fairqueue'],
  ['FairQueue: per-course waiting-time chart', '/fairqueue'],
  ['Failure Recovery: worker failure → rollback → retry', '/recovery'],
  ['Failure Recovery: lost reply → retry same ID', '/recovery'],
  ['Courses & Waitlists: cancel → first waitlisted promoted', '/courses'],
  ['Overview: final metrics; show pytest and load test in a terminal', '/'],
];

const COMMANDS = [
  ['Automated tests (unit + MySQL integration)', 'pytest'],
  ['Unit tests only, no database', 'pytest -m "not mysql"'],
  ['500-request concurrent HTTP load test', 'python scripts\\load_test.py'],
  ['KO-06 challenges C1–C5 with every check', 'python scripts\\run_challenges.py -v'],
  ['Create / reset the database', 'python scripts\\init_db.py'],
];

export default function ArchitecturePage() {
  const { health, apiOnline, dbOnline, lastOk, error, demoMode, retry } = useConnection();
  const live = usePolling(() => api.live(), 1000);
  const glossary = usePolling(() => api.eventTypes(), 0);
  const [busy, setBusy] = useState(null);
  const toast = useToast();
  const [confirmText, setConfirmText] = useState('');
  const lock = useRef(false);

  async function act(key, fn) {
    if (lock.current) return;
    lock.current = true;
    setBusy(key);
    try {
      const { tone = 'success', text } = await fn();
      toast({ tone, title: text });
    } catch (e) {
      toast({ tone: 'danger', title: 'Action failed', text: e.message });
    } finally {
      lock.current = false;
      setBusy(null);
      retry();
      live.refresh();
    }
  }

  const setMode = (mode) => act('mode', async () => {
    await api.setMode(mode);
    return { tone: 'success', text: `Scheduler switched to ${MODE_LABEL[mode]}. Queued requests were migrated in arrival order.` };
  });
  const togglePause = () => act('pause', async () => {
    await api.setPaused(!health?.scheduler_paused);
    return { tone: 'info', text: health?.scheduler_paused ? 'Dispatching resumed.' : 'Dispatching paused; requests keep queueing.' };
  });
  const reset = () => act('reset', async () => {
    const r = await api.reset();
    setConfirmText('');
    return { tone: 'success', text: `Demo data reset: ${Object.entries(r.deleted).map(([k, v]) => `${v} ${k}`).join(', ')}.` };
  });

  const sched = live.data?.scheduler;
  const workers = live.data?.workers;

  return (
    <>
      <PageHeader
        description="One FastAPI process, one MySQL server, a bounded pool of worker threads. Everything here is read live from the backend." />

      <Panel icon={Activity} title="Diagnostics" description="Live from /api/health and /api/metrics/live" flush>
        <div className="grid cols-3" style={{ gap: 0 }}>
          <DiagBlock title="API" ok={apiOnline}>
            <dt>Base URL</dt><dd className="mono">{API_BASE}</dd>
            <dt>Health check</dt><dd>{apiOnline ? 'Responding' : error ? `Failing: ${error.message}` : 'Checking…'}</dd>
            <dt>Last successful contact</dt><dd>{lastOk ? lastOk.toLocaleTimeString() : '—'}</dd>
            <dt>Environment</dt><dd>{health ? `${health.environment}${demoMode ? ' (demo tools on)' : ''}` : '—'}</dd>
          </DiagBlock>
          <DiagBlock title="MySQL" ok={dbOnline}>
            <dt>Database</dt><dd>{health?.database?.name || '—'}</dd>
            <dt>Server version</dt><dd>{health?.database?.version || '—'}</dd>
            <dt>Status</dt><dd>{!health ? '—' : health.database.ok ? 'Connected' : health.database.error}</dd>
            <dt>Recovered on startup</dt><dd>{health ? `${health.recovered_on_startup} pending requests` : '—'}</dd>
          </DiagBlock>
          <DiagBlock title="Scheduler & workers" ok={apiOnline && !!sched}>
            <dt>Mode</dt><dd>{sched ? `${MODE_LABEL[sched.mode]}${sched.paused ? ' (paused)' : ''}` : '—'}</dd>
            <dt>Queue</dt><dd>{sched ? `${sched.queue_size} of ${sched.max_queue_size} · ${sched.in_flight_total} in flight` : '—'}</dd>
            <dt>Workers</dt><dd>{workers ? `${workers.live}/${workers.size} live · ${workers.active} busy · ${workers.restarts} restarts` : '—'}</dd>
            <dt>Limits</dt><dd>{health ? `${health.per_course_in_flight} per course in flight · ${health.max_attempts} attempts` : '—'}</dd>
            <dt>Armed failures</dt><dd>{live.data ? armedSummary(live.data.failures_armed) : '—'}</dd>
          </DiagBlock>
        </div>
      </Panel>

      <Panel icon={Workflow} title="Request flow" description="UI or load script → API → idempotency → per-course queues → scheduler → worker pool → MySQL → events and metrics">
        <QueryView query={live} rows={4}>
          {() => (
            <div className="flow">
              <Node step="1" title="Registration API" text="Validates student, course and request UUID. Returns 503 when the queue is full." />
              <Node step="2" title="Idempotency" text="Saves the request row (RECEIVED). A repeated request ID returns the stored result and is never re-run." />
              <Node step="3" title={`Per-course queues · ${sched.queue_size} queued`}>
                {Object.keys(sched.queued_by_course).length === 0 && <div className="flow-text">All queues empty</div>}
                {Object.entries(sched.queued_by_course).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([course, n]) => (
                  <div className="queue-row" key={course}>
                    <span className="mono">{course}</span>
                    <div className="cap-track"><div className="queue-bar" style={{ width: `${Math.min(100, (n / Math.max(1, sched.queue_size)) * 100)}%` }} /></div>
                    <span className="mono num">{n}</span>
                  </div>
                ))}
              </Node>
              <Node step="4" title={`Scheduler · ${MODE_LABEL[sched.mode]}`} emph>
                <div className="flow-text">
                  {sched.mode === 'FAIR_QUEUE'
                    ? `Round-robin over non-empty course queues, FIFO within a course, at most ${sched.per_course_limit} in flight per course.`
                    : 'One shared FIFO queue in arrival order (baseline).'}
                </div>
                <div className="flow-text" style={{ marginTop: 4 }}>{sched.paused ? 'Paused' : 'Dispatching'} · {sched.dispatched_total} dispatched</div>
              </Node>
              <Node step="5" title={`Worker pool · ${workers.active}/${workers.size} busy`}>
                <div className="workers">
                  {workers.workers.map((w) => (
                    <div key={w.worker} className={`worker ${w.state === 'busy' ? 'busy' : ''}`} title={w.course_id ? `${w.course_id} · ${w.request_id}` : w.state}>
                      {w.worker}
                    </div>
                  ))}
                </div>
                <div className="flow-text" style={{ marginTop: 6 }}>
                  In flight: {Object.entries(sched.in_flight_by_course).map(([c, n]) => `${c}×${n}`).join(', ') || 'none'}
                </div>
              </Node>
              <Node step="6" title="Seat transaction" text="Lock request row → lock course row (FOR UPDATE) → lock registration → confirm or waitlist → COMMIT." />
              <Node step="7" title="Waitlist & cancellation" text="FIFO by waitlist_seq; cancel and promote the first waitlisted student in one transaction." />
              <Node step="8" title="Events & metrics" text="registration_events feeds the timeline; metrics are queried from MySQL." />
            </div>
          )}
        </QueryView>
      </Panel>

      <div className="grid cols-2">
        <Panel icon={SlidersHorizontal} title="Scheduler controls" description="Seat safety never depends on this switch; the row lock and constraints decide seats in both modes.">
          <div className="stack">
            <div className="row">
              <Segmented label="Scheduler mode" value={health?.scheduler_mode} onChange={setMode}
                options={[{ value: 'GLOBAL_FIFO', label: 'Global FIFO' }, { value: 'FAIR_QUEUE', label: 'FairQueue' }]} />
              <Button size="sm" loading={busy === 'pause'} disabled={!apiOnline} onClick={togglePause}>
                {health?.scheduler_paused ? 'Resume dispatching' : 'Pause dispatching'}
              </Button>
            </div>
            <span className="small muted">Switching migrates queued requests in arrival order. Experiments set the mode themselves and restore it afterwards.</span>
          </div>
        </Panel>

        <Panel icon={SquareTerminal} title="Verification from a terminal" description="Run in backend\ with the virtual environment active" flush>
          <div className="table-scroll"><table className="table">
            <tbody>
              {COMMANDS.map(([what, cmd]) => (
                <tr key={cmd}><td className="small text-2">{what}</td><td className="mono nowrap">{cmd}</td></tr>
              ))}
            </tbody>
          </table></div>
        </Panel>
      </div>

      <Panel icon={BookText} title="Reference" description="Concepts, event names and the demo order" flush>
        <Disclosure first summary="OS concepts and where they are used">
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th>Concept</th><th>Where</th><th>Why</th></tr></thead>
              <tbody>{CONCEPTS.map(([c, w, y]) => <tr key={c}><td><b>{c}</b></td><td>{w}</td><td className="text-2">{y}</td></tr>)}</tbody>
            </table>
          </div>
        </Disclosure>
        <Disclosure summary="Event glossary">
          <QueryView query={glossary}>
            {(d) => (
              <table className="table">
                <tbody>
                  {d.event_types.map((e) => (
                    <tr key={e.event_type}><td style={{ width: 250 }}><EventBadge type={e.event_type} /></td><td className="text-2">{e.description}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryView>
        </Disclosure>
        <Disclosure summary="Judge demo flow">
          <ol style={{ margin: 0, paddingLeft: 20, display: 'grid', gap: 4 }}>
            {DEMO_STEPS.map(([text, to]) => <li key={text}><Link to={to}>{text}</Link></li>)}
          </ol>
        </Disclosure>
      </Panel>

      {demoMode && (
        <Panel icon={Trash2} iconTone="danger" title="Reset demo data" tone="danger"
          description="Deletes all registrations, requests, events, experiments and lab rows. Students and catalog courses stay. Development mode, this computer only.">
          <div className="row">
            <label htmlFor="reset-confirm" className="sr-only">Type RESET to confirm</label>
            <input id="reset-confirm" className="input" style={{ maxWidth: 200 }} placeholder="Type RESET to confirm" value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)} />
            <Button variant="danger-solid" icon={Trash2} loading={busy === 'reset'} disabled={confirmText !== 'RESET'} onClick={reset}>
              Reset demo data
            </Button>
          </div>
        </Panel>
      )}
    </>
  );
}

function armedSummary(f) {
  const n = (f.armed_for_requests || []).length + Object.keys(f.armed_next_any || {}).length;
  if (!f.enabled) return 'Injection disabled';
  return n ? `${n} armed` : 'None';
}

function DiagBlock({ title, ok, children }) {
  return (
    <div style={{ padding: 16, borderRight: '1px solid var(--border)' }}>
      <div className="row" style={{ marginBottom: 10 }}><Dot tone={ok ? 'success' : 'danger'} /><b>{title}</b></div>
      <dl className="kv">{children}</dl>
    </div>
  );
}

function Node({ step, title, text, children, emph }) {
  return (
    <div className={`flow-node ${emph ? 'emph' : ''}`}>
      <div className="flow-step">{step}</div>
      <div className="flow-title">{title}</div>
      {text && <div className="flow-text">{text}</div>}
      {children}
    </div>
  );
}
