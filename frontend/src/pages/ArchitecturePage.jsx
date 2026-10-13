import { BookText, Cpu, ListChecks, Network, Settings2, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Card, ErrorState, EventBadge, LoadingState, PageHeader, Segmented } from '../components/ui';
import { useHealth } from '../hooks/HealthContext';
import { usePolling } from '../hooks/usePolling';
import { api } from '../services/api';
import { MODE_LABEL } from '../utils/format';

const CONCEPTS = [
  ['Threads, bounded pool', 'Worker pool (8 threads, ThreadPoolExecutor)', 'Overlaps I/O-bound MySQL work while capping threads and connections. Python threads do not give CPU parallelism (GIL); the work here is mostly waiting on the database, which releases the GIL.'],
  ['Race condition / critical section', 'Check-seat-then-take-seat', 'This is where overbooking happens; RaceSafe runs it from the course row lock to COMMIT.'],
  ['Mutual exclusion & synchronization', 'SELECT … FOR UPDATE; condition variable on queues', 'One transaction per course at a time; idle workers sleep in Condition.wait() instead of spinning.'],
  ['Round-robin scheduling', 'FairQueue', 'Stops one hot course from starving the others. Application-level policy; the OS still schedules the threads.'],
  ['Producer–consumer', 'API → queues → workers', 'Absorbs bursts; returns HTTP 503 when the queue is full (back-pressure).'],
  ['Deadlock avoidance', 'Fixed lock order: request → course → registration', 'No circular waits; MySQL deadlock/lock-timeout errors roll back and retry.'],
  ['Fault tolerance & recovery', 'Rollback, 3 attempts, request IDs, restart scan', 'No half-written state; every request gets exactly one result.'],
];

const DEMO_STEPS = [
  ['Show the architecture (this page) and the live overview', '/'],
  ['Race Condition Lab: run the unsafe burst → overbooking', '/race-lab'],
  ['Race Condition Lab: run RaceSafe with 500 requests → exactly 10 confirmed, 490 waitlisted', '/race-lab'],
  ['Overview: every invariant check passes (capacity never exceeded)', '/'],
  ['FairQueue Experiment: Compare both, skewed demand', '/fairqueue'],
  ['FairQueue Experiment: per-course waiting-time chart from measured data', '/fairqueue'],
  ['Failure Recovery: inject a worker failure → rollback → retry → confirmed', '/recovery'],
  ['Failure Recovery: lost response → retry same request ID → no duplicate', '/recovery'],
  ['Courses: cancel a confirmed student → first waitlisted student promoted', '/courses'],
  ['Overview: final metrics; show pytest + load-test output in the terminal', '/'],
];

export default function ArchitecturePage() {
  const { health, refresh } = useHealth();
  const live = usePolling(() => api.live(), 1000);
  const glossary = usePolling(() => api.eventTypes(), 0);
  const [busy, setBusy] = useState(null);
  const [message, setMessage] = useState(null);
  const [confirmText, setConfirmText] = useState('');

  const sched = live.data?.scheduler;
  const workers = live.data?.workers;
  const queued = Object.entries(sched?.queued_by_course || {}).sort((a, b) => b[1] - a[1]).slice(0, 6);

  async function setMode(mode) {
    setBusy('mode');
    try {
      await api.setMode(mode);
      setMessage({ tone: 'good', text: `Scheduler switched to ${MODE_LABEL[mode]}. Queued requests were migrated in arrival order.` });
    } catch (e) {
      setMessage({ tone: 'critical', text: e.message });
    } finally {
      setBusy(null);
      refresh();
      live.refresh();
    }
  }

  async function reset() {
    setBusy('reset');
    try {
      const r = await api.reset();
      setMessage({ tone: 'good', text: `Demo data reset: ${Object.entries(r.deleted).map(([k, v]) => `${v} ${k}`).join(', ')}.` });
      setConfirmText('');
    } catch (e) {
      setMessage({ tone: 'critical', text: e.message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader title="Architecture & demo controls"
        description="One FastAPI process, one MySQL server, eight worker threads. The boxes below show live state from the running backend." />
      {message && <Alert tone={message.tone}>{message.text}</Alert>}

      <Card title="Request flow" icon={Network} subtitle="UI / load script → API → idempotency → per-course queues → FairQueue → worker pool → MySQL → events & metrics → dashboard">
        {live.error && !live.data && <ErrorState error={live.error} />}
        {!live.data && !live.error && <LoadingState />}
        {live.data && (
          <div className="flow">
            <Node step="1" title="Registration API" text="FastAPI validates student, course and request UUID. 503 when the queue is full." />
            <Node step="2" title="Validation & idempotency" text="INSERT request row (status RECEIVED). Same request_id again → stored result, never re-run." />
            <Node step="3" title="Per-course queues" highlight={false}>
              <div className="flow-text">{sched.queue_size} queued · max {sched.max_queue_size}</div>
              {queued.length === 0 && <div className="flow-text muted">all queues empty</div>}
              {queued.map(([course, n]) => (
                <div className="queue-row" key={course}>
                  <span className="mono small">{course}</span>
                  <span className="queue-blocks">{Array.from({ length: Math.min(n, 24) }).map((_, i) => <i key={i} />)}</span>
                  <span className="mono small">{n}</span>
                </div>
              ))}
            </Node>
            <Node step="4" title={`Scheduler: ${MODE_LABEL[sched.mode]}`} highlight>
              <div className="flow-text">
                {sched.mode === 'FAIR_QUEUE'
                  ? `Round-robin over non-empty course queues, FIFO inside a course, ≤ ${sched.per_course_limit} in flight per course.`
                  : 'One shared FIFO queue in arrival order (baseline).'}
              </div>
              <div className="flow-text" style={{ marginTop: 6 }}>{sched.paused ? 'Paused' : 'Dispatching'} · {sched.dispatched_total} dispatched</div>
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
            <Node step="6" title="Transaction & seat allocation" className="db" text="BEGIN · lock request row · lock course row (FOR UPDATE) · lock registration · confirm or waitlist · COMMIT" />
            <Node step="7" title="Waitlist & cancellation" className="db" text="FIFO by waitlist_seq. Cancel + promote first waitlisted in one transaction." />
            <Node step="8–9" title="Events & metrics" className="db" text="registration_events feeds the recovery timeline; metrics are queried from MySQL." />
          </div>
        )}
      </Card>

      <div className="grid grid-2">
        <Card title="Scheduler controls" icon={Settings2} subtitle="Switching migrates already-queued requests, keeping arrival order">
          <div className="stack">
            <Segmented value={health?.scheduler_mode} onChange={(m) => setMode(m)}
              options={[{ value: 'GLOBAL_FIFO', label: 'Global FIFO (baseline)' }, { value: 'FAIR_QUEUE', label: 'FairQueue' }]} />
            <div className="small muted">
              Correctness never depends on this switch: the MySQL row lock and constraints decide seats in both modes.
              Experiments set the mode themselves and restore it afterwards.
            </div>
            <div className="divider" />
            <div className="small text-2">
              Database <b>{health?.database?.name}</b> · MySQL {health?.database?.version || '—'} · max attempts {health?.max_attempts} ·
              recovered on startup: {health?.recovered_on_startup ?? '—'}
            </div>
          </div>
        </Card>

        <Card title="Judge demo flow" icon={ListChecks} subtitle="Suggested order (about 8 minutes)">
          <ol style={{ margin: 0, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {DEMO_STEPS.map(([text, to]) => <li key={text}><Link to={to}>{text}</Link></li>)}
          </ol>
        </Card>
      </div>

      <Card title="OS concepts and where they are used" icon={Cpu} flush>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Concept</th><th>Where</th><th>Why</th></tr></thead>
            <tbody>
              {CONCEPTS.map(([c, w, y]) => <tr key={c}><td><b>{c}</b></td><td>{w}</td><td className="text-2">{y}</td></tr>)}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Event glossary" icon={BookText} flush>
        {glossary.error && !glossary.data ? <div style={{ padding: '0 18px 14px' }}><ErrorState error={glossary.error} /></div> : !glossary.data ? <LoadingState /> : (
          <div className="table-wrap">
            <table className="table">
              <tbody>
                {glossary.data.event_types.map((e) => (
                  <tr key={e.event_type}><td style={{ width: 260 }}><EventBadge type={e.event_type} /></td><td className="text-2">{e.description}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {health?.demo_mode && (
        <Card title="Reset demo data" icon={Trash2} className="danger"
          subtitle="Deletes all registrations, requests, events, experiments and lab rows. Students and catalog courses stay. Works only in development mode, from this computer.">
          <div className="row">
            <input className="input" style={{ maxWidth: 220 }} placeholder='Type RESET to confirm' value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)} />
            <Button variant="danger" icon={Trash2} loading={busy === 'reset'} disabled={confirmText !== 'RESET'} onClick={reset}>
              Reset demo data
            </Button>
          </div>
        </Card>
      )}
    </>
  );
}

function Node({ step, title, text, children, highlight, className = '' }) {
  return (
    <div className={`flow-node ${highlight ? 'highlight' : ''} ${className}`}>
      <div className="flow-step">{step}</div>
      <div className="flow-title">{title}</div>
      {text && <div className="flow-text">{text}</div>}
      {children}
    </div>
  );
}
