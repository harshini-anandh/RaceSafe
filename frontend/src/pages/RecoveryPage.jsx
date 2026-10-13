import { Bomb, Filter, History, PauseCircle, PlayCircle, Power, Repeat, Send, Unplug, Zap } from 'lucide-react';
import { useState } from 'react';
import StudentPicker, { CourseSelect } from '../components/StudentPicker';
import {
  Alert, Button, Card, EmptyState, ErrorState, EventBadge, eventTone, Field, LoadingState, Mono, PageHeader, StatusBadge,
} from '../components/ui';
import { useHealth } from '../hooks/HealthContext';
import { usePolling } from '../hooks/usePolling';
import { api, newRequestId } from '../services/api';
import { fmtTime } from '../utils/format';

const EVENT_TYPES = [
  'REQUEST_RECEIVED', 'DUPLICATE_REQUEST', 'QUEUED', 'WORKER_STARTED', 'TRANSACTION_COMMITTED', 'TRANSACTION_ROLLED_BACK',
  'WORKER_FAILURE', 'RETRY_SCHEDULED', 'REQUEST_CONFIRMED', 'REQUEST_WAITLISTED', 'REQUEST_ALREADY_REGISTERED',
  'REQUEST_FAILED', 'RESPONSE_LOST', 'REGISTRATION_CANCELLED', 'WAITLIST_PROMOTED', 'RECOVERY_REQUEUED',
];

async function submitRegistration(payload) {
  try {
    const res = await api.register(payload, { wait: true, timeout: 30 });
    return { status: res.status, data: res.data };
  } catch (error) {
    return { status: error.status, data: error.body, error };
  }
}

export default function RecoveryPage() {
  const { health, refresh: refreshHealth } = useHealth();
  const courses = usePolling(() => api.courses(false), 5000);
  const [filters, setFilters] = useState({ request_id: '', course_id: '', event_type: '', include_experiment: false });
  const timeline = usePolling(
    () => api.timeline({ ...filters, limit_requests: 30 }), 2000,
    [filters.request_id, filters.course_id, filters.event_type, filters.include_experiment],
  );
  const focus = (rid) => setFilters((f) => ({ ...f, request_id: rid }));
  const demo = health?.demo_mode;
  const courseList = courses.data?.courses || [];

  return (
    <>
      <PageHeader
        title="Failure Recovery Timeline"
        description="Inject controlled failures and watch each request ID's events: received → queued → worker attempt → commit or rollback → retry → final result."
      />
      {!demo && health && <Alert tone="warning">Failure injection is disabled because RACESAFE_ENV is not “development”.</Alert>}

      <div className="grid grid-3">
        <WorkerFailureCard courses={courseList} disabled={!demo} onFocus={focus} />
        <LostResponseCard courses={courseList} disabled={!demo} onFocus={focus} />
        <RestartCard courses={courseList} disabled={!demo} health={health} refreshHealth={refreshHealth} />
      </div>

      <Card title="Timeline" icon={History} subtitle="Grouped by request ID, newest first · refreshes every 2 s"
        actions={filters.request_id && <Button variant="ghost" size="sm" onClick={() => focus('')}>Show all requests</Button>}>
        <div className="grid grid-4" style={{ gap: 12, marginBottom: 14 }}>
          <Field label="Request ID (or prefix)">
            <input className="input mono" value={filters.request_id} placeholder="e.g. 3f1c2a9e"
              onChange={(e) => setFilters((f) => ({ ...f, request_id: e.target.value.trim() }))} />
          </Field>
          <Field label="Course">
            <select className="select" value={filters.course_id} onChange={(e) => setFilters((f) => ({ ...f, course_id: e.target.value }))}>
              <option value="">All courses</option>
              {courseList.map((c) => <option key={c.course_id} value={c.course_id}>{c.course_id}</option>)}
            </select>
          </Field>
          <Field label="Contains event type">
            <select className="select" value={filters.event_type} onChange={(e) => setFilters((f) => ({ ...f, event_type: e.target.value }))}>
              <option value="">Any event</option>
              {EVENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <div className="field" style={{ justifyContent: 'flex-end' }}>
            <label className="check" style={{ height: 36 }}>
              <input type="checkbox" checked={filters.include_experiment}
                onChange={(e) => setFilters((f) => ({ ...f, include_experiment: e.target.checked }))} />
              <Filter size={14} /> Include experiment traffic
            </label>
          </div>
        </div>
        {timeline.error && !timeline.data && <ErrorState error={timeline.error} onRetry={timeline.refresh} />}
        {!timeline.data && !timeline.error && <LoadingState />}
        {timeline.data && timeline.data.requests.length === 0 && (
          <EmptyState icon={History} title="No matching requests">Run one of the scenarios above or register a student.</EmptyState>
        )}
        {timeline.data && (
          <div className="stack">
            {timeline.data.requests.map((g) => <TimelineGroup key={g.request_id} group={g} onFocus={focus} />)}
          </div>
        )}
      </Card>
    </>
  );
}

function TimelineGroup({ group, onFocus }) {
  const outcome = group.status === 'COMPLETED' ? group.result : group.status === 'RECEIVED' ? 'PENDING' : group.status;
  const committed = group.events.filter((e) => e.event_type === 'TRANSACTION_COMMITTED').length;
  const rolledBack = group.events.filter((e) => e.event_type === 'TRANSACTION_ROLLED_BACK').length;
  return (
    <div className="timeline-group">
      <div className="timeline-head">
        <button className="btn btn-ghost btn-sm mono" onClick={() => onFocus(group.request_id)} title="Filter to this request">
          {group.request_id}
        </button>
        <span><b>{group.student_id}</b> → <b>{group.course_id}</b></span>
        <span className="spacer" />
        <span className="small text-2">attempts {group.attempts ?? '—'} · {committed} commit · {rolledBack} rollback</span>
        <StatusBadge status={outcome} />
      </div>
      <ol className="timeline-list">
        {group.events.map((e) => (
          <li key={e.event_id} className={`timeline-item ${eventTone(e.event_type)}`}>
            <span className="mono small muted">{fmtTime(e.created_at)}</span>
            <span><EventBadge type={e.event_type} /></span>
            <span className="small text-2">{e.attempt ? `attempt ${e.attempt}` : ''}{e.worker_name ? ` · ${e.worker_name}` : ''}</span>
            <span className="small text-2">{e.message}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function useScenarioState(courses, firstStudent) {
  const [studentId, setStudentId] = useState(firstStudent);
  const [courseId, setCourseId] = useState('');
  const effectiveCourse = courseId || courses[0]?.course_id || '';
  // After a run, move to the next student so the next demo starts fresh.
  const advance = () => setStudentId((id) => {
    const m = /^S(\d{4})$/.exec(id);
    return m ? `S${String(Number(m[1]) + 1).padStart(4, '0')}` : id;
  });
  return { studentId, setStudentId, courseId: effectiveCourse, setCourseId, advance };
}

function WorkerFailureCard({ courses, disabled, onFocus }) {
  const s = useScenarioState(courses, 'S0101');
  const [failures, setFailures] = useState(1);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function run() {
    setBusy(true);
    setResult(null);
    const requestId = newRequestId();
    try {
      await api.injectFailure({ kind: 'BEFORE_COMMIT', request_id: requestId, count: Number(failures) });
      const r = await submitRegistration({ student_id: s.studentId, course_id: s.courseId, request_id: requestId });
      setResult({ ...r, requestId });
      onFocus(requestId);
      s.advance();
    } catch (error) {
      setResult({ error, requestId });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Worker failure before commit" icon={Bomb}
      subtitle="Worker crashes after its writes, before COMMIT → MySQL rolls back; the request is retried (max 3 attempts).">
      <div className="stack">
        <div className="grid grid-2" style={{ gap: 10 }}>
          <Field label="Student"><StudentPicker value={s.studentId} onChange={s.setStudentId} /></Field>
          <Field label="Failures to inject">
            <select className="select" value={failures} onChange={(e) => setFailures(e.target.value)}>
              <option value={1}>1 (succeeds on attempt 2)</option>
              <option value={2}>2 (succeeds on attempt 3)</option>
              <option value={3}>3 (gives up → FAILED)</option>
            </select>
          </Field>
        </div>
        <Field label="Course"><CourseSelect courses={courses} value={s.courseId} onChange={s.setCourseId} /></Field>
        <Button icon={Zap} loading={busy} disabled={disabled || !s.courseId} onClick={run}>Inject & submit</Button>
        {result && <ScenarioResult result={result} />}
      </div>
    </Card>
  );
}

function LostResponseCard({ courses, disabled, onFocus }) {
  const s = useScenarioState(courses, 'S0201');
  const [busy, setBusy] = useState(null);
  const [steps, setSteps] = useState([]);
  const [scenario, setScenario] = useState(null); // { request_id, student_id, course_id } of the original request

  async function send(kind) {
    setBusy(kind);
    let payload = scenario;
    try {
      if (kind === 'first') {
        payload = { student_id: s.studentId, course_id: s.courseId, request_id: newRequestId() };
        setScenario(payload);
        setSteps([]);
        await api.injectFailure({ kind: 'LOST_RESPONSE', request_id: payload.request_id, count: 1 });
        s.advance();
      }
      if (kind === 'new') payload = { ...payload, request_id: newRequestId() };
      const r = await submitRegistration(payload);
      const label = { first: 'Original request', same: 'Retry with the same request ID', new: 'Retry with a NEW request ID' }[kind];
      setSteps((prev) => [...prev, { label, rid: payload.request_id, ...r }]);
      onFocus(kind === 'new' ? '' : payload.request_id);
    } catch (error) {
      setSteps((prev) => [...prev, { label: kind, rid: payload?.request_id, error }]);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Lost response after commit" icon={Unplug}
      subtitle="The seat is committed, but the HTTP reply is dropped. Retrying with the same request ID returns the stored result.">
      <div className="stack">
        <div className="grid grid-2" style={{ gap: 10 }}>
          <Field label="Student"><StudentPicker value={s.studentId} onChange={s.setStudentId} /></Field>
          <Field label="Course"><CourseSelect courses={courses} value={s.courseId} onChange={s.setCourseId} /></Field>
        </div>
        <Button icon={Send} loading={busy === 'first'} disabled={disabled || !s.courseId || !!busy} onClick={() => send('first')}>
          Inject lost response & submit
        </Button>
        <div className="row">
          <Button variant="secondary" size="sm" icon={Repeat} loading={busy === 'same'} disabled={!scenario || !!busy} onClick={() => send('same')}>
            Retry same ID
          </Button>
          <Button variant="secondary" size="sm" icon={Repeat} loading={busy === 'new'} disabled={!scenario || !!busy} onClick={() => send('new')}>
            Retry with new ID
          </Button>
        </div>
        {steps.map((st, i) => (
          <div key={i} className="stack" style={{ gap: 4 }}>
            <div className="small muted">{st.label} · <Mono>{st.rid?.slice(0, 8)}</Mono></div>
            <ScenarioResult result={st} compact />
          </div>
        ))}
      </div>
    </Card>
  );
}

function RestartCard({ courses, disabled, health, refreshHealth }) {
  const [courseId, setCourseId] = useState('');
  const [count, setCount] = useState(5);
  const [busy, setBusy] = useState(null);
  const [message, setMessage] = useState(null);
  const effectiveCourse = courseId || courses[0]?.course_id || '';
  const paused = health?.scheduler_paused;

  async function act(kind) {
    setBusy(kind);
    try {
      if (kind === 'pause') {
        await api.setPaused(!paused);
        setMessage({ tone: 'info', text: paused ? 'Workers resumed.' : 'Workers paused — new requests are saved as RECEIVED and wait in memory.' });
      } else if (kind === 'queue') {
        const r = await api.simulateRegistrations(effectiveCourse, Number(count));
        setMessage({ tone: 'info', text: `${r.submitted} requests persisted in MySQL and queued in memory${paused ? ' (not processed while paused)' : ''}.` });
      } else {
        const r = await api.simulateRestart();
        setMessage({ tone: 'good', text: `Crash: ${r.dropped_from_memory} queued requests lost from memory. Restart: ${r.requeued_from_database} pending requests rebuilt from MySQL and processed.` });
      }
    } catch (e) {
      setMessage({ tone: 'critical', text: e.message });
    } finally {
      setBusy(null);
      refreshHealth();
    }
  }

  return (
    <Card title="Backend restart recovery" icon={Power}
      subtitle="In-memory queues are rebuilt from requests still marked RECEIVED in MySQL.">
      <div className="stack">
        <Button variant="secondary" icon={paused ? PlayCircle : PauseCircle} loading={busy === 'pause'} disabled={disabled}
          onClick={() => act('pause')}>{paused ? '1. Resume workers' : '1. Pause workers'}</Button>
        <div className="row" style={{ flexWrap: 'nowrap' }}>
          <input className="input" type="number" min={1} max={50} value={count} onChange={(e) => setCount(e.target.value)} style={{ width: 70 }} />
          <CourseSelect courses={courses} value={effectiveCourse} onChange={setCourseId} />
        </div>
        <Button variant="secondary" icon={Send} loading={busy === 'queue'} disabled={disabled || !effectiveCourse} onClick={() => act('queue')}>
          2. Submit requests
        </Button>
        <Button variant="danger" icon={Bomb} loading={busy === 'restart'} disabled={disabled} onClick={() => act('restart')}>
          3. Simulate crash + restart
        </Button>
        {message && <Alert tone={message.tone}>{message.text}</Alert>}
        <div className="small muted">
          Real restart: pause, submit, then press Ctrl+C in the backend terminal and start uvicorn again — the startup
          scan logs “Restart recovery requeued N pending requests”.
        </div>
      </div>
    </Card>
  );
}

function ScenarioResult({ result, compact }) {
  const { data, error, status } = result;
  if (error && data?.code === 'RESPONSE_LOST') {
    return <Alert tone="warning" title={`HTTP ${status} — response lost`}>{data.detail}</Alert>;
  }
  if (error) return <Alert tone="critical" title={status ? `HTTP ${status}` : 'Error'}>{error.message}</Alert>;
  return (
    <div className="card" style={{ padding: compact ? 10 : 12, background: 'var(--surface-2)' }}>
      <div className="row" style={{ marginBottom: 4 }}>
        <StatusBadge status={data.outcome} />
        {data.replayed && <span className="badge info">idempotent replay</span>}
        <span className="small muted">HTTP {status} · attempts {data.attempts}</span>
      </div>
      <div className="small text-2">{data.message}</div>
    </div>
  );
}
