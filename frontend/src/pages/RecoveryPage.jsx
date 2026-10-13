import { Bomb, ChevronRight, History, PauseCircle, PlayCircle, Power, Repeat, Send, Unplug, Zap } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import StudentPicker, { CourseSelect } from '../components/StudentPicker';
import {
  Alert, Avatar, Button, ConfirmButton, Empty, EventBadge, eventTone, Field, Mono, PageHeader, Panel, QueryView, StatusBadge, Tabs,
} from '../components/ui';
import { useStudents } from '../hooks/useStudents';
import { useConnection } from '../hooks/ConnectionContext';
import { usePolling } from '../hooks/usePolling';
import { api, newRequestId } from '../services/api';
import { fmtTime } from '../utils/format';

const EVENT_TYPES = [
  'REQUEST_RECEIVED', 'DUPLICATE_REQUEST', 'QUEUED', 'WORKER_STARTED', 'TRANSACTION_COMMITTED', 'TRANSACTION_ROLLED_BACK',
  'WORKER_FAILURE', 'RETRY_SCHEDULED', 'REQUEST_CONFIRMED', 'REQUEST_WAITLISTED', 'REQUEST_ALREADY_REGISTERED',
  'REQUEST_FAILED', 'RESPONSE_LOST', 'REGISTRATION_CANCELLED', 'WAITLIST_PROMOTED', 'RECOVERY_REQUEUED',
];

const FAILURE_KINDS = [
  { value: 'BEFORE_COMMIT', label: 'Before commit', hint: 'Fails after its writes, before COMMIT: MySQL rolls back.' },
  { value: 'AFTER_DEQUEUE', label: 'After dequeue', hint: 'Fails right after taking the request (course slot held), before BEGIN.' },
  { value: 'WORKER_CRASH', label: 'Worker thread crash', hint: 'The worker thread dies holding the row lock; the pool requeues and replaces it.' },
];

async function submitRegistration(payload) {
  try {
    const res = await api.register(payload, { wait: true, timeout: 30 });
    return { status: res.status, data: res.data };
  } catch (error) {
    return { status: error.status, data: error.body, error };
  }
}

const nextStudent = (id) => {
  const m = /^S(\d{4})$/.exec(id);
  return m ? `S${String(Number(m[1]) + 1).padStart(4, '0')}` : id;
};

export default function RecoveryPage() {
  const { demoMode, apiOnline } = useConnection();
  const courses = usePolling(() => api.courses(false), 5000);
  const [params] = useSearchParams();
  const [tab, setTab] = useState('worker');
  const [filters, setFilters] = useState({
    request_id: params.get('request_id') || '', course_id: '', event_type: '', include_experiment: false,
  });
  const timeline = usePolling(
    () => api.timeline({ ...filters, limit_requests: 30 }), 2000,
    [filters.request_id, filters.course_id, filters.event_type, filters.include_experiment],
  );
  const focus = (rid) => setFilters((f) => ({ ...f, request_id: rid }));
  useEffect(() => {
    const fromUrl = params.get('request_id');
    if (fromUrl !== null) setFilters((f) => (f.request_id === fromUrl ? f : { ...f, request_id: fromUrl }));
  }, [params]);
  const { students } = useStudents();
  const names = useMemo(() => new Map(students.map((s) => [s.student_id, s.full_name])), [students]);
  const courseList = courses.data?.courses || [];
  const setFilter = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value.trim() }));

  return (
    <>
      <PageHeader
        description="Inject controlled failures, then follow each request ID from receipt through commit or rollback, retry and final result." />

      <Panel icon={Zap} title="Inject a failure" description="Development demo controls · they affect only the requests you send here" flush>
        <Tabs value={tab} onChange={setTab} tabs={[
          { value: 'worker', label: 'Worker failure', icon: Zap },
          { value: 'lost', label: 'Lost response', icon: Unplug },
          { value: 'restart', label: 'Restart recovery', icon: Power },
        ]} />
        <div style={{ padding: 16 }}>
          {!demoMode && apiOnline && <Alert tone="info">Failure injection requires RACESAFE_ENV=development.</Alert>}
          {tab === 'worker' && <WorkerFailure courses={courseList} disabled={!demoMode} onFocus={focus} />}
          {tab === 'lost' && <LostResponse courses={courseList} disabled={!demoMode} onFocus={focus} />}
          {tab === 'restart' && <RestartRecovery courses={courseList} disabled={!demoMode} />}
        </div>
      </Panel>

      <Panel icon={History} title="Timeline" description="One row per request, newest first. Expand a row to see every event." flush
        actions={filters.request_id && <Button variant="ghost" size="sm" onClick={() => focus('')}>Show all requests</Button>}>
        <div className="grid cols-4" style={{ gap: 12, padding: 16, borderBottom: '1px solid var(--border)' }}>
          <Field label="Request ID or prefix" htmlFor="tl-rid">
            <input id="tl-rid" className="input mono" value={filters.request_id} placeholder="e.g. 3f1c2a9e" onChange={setFilter('request_id')} />
          </Field>
          <Field label="Course" htmlFor="tl-course">
            <select id="tl-course" className="select" value={filters.course_id} onChange={setFilter('course_id')}>
              <option value="">All courses</option>
              {courseList.map((c) => <option key={c.course_id} value={c.course_id}>{c.course_id}</option>)}
            </select>
          </Field>
          <Field label="Contains event" htmlFor="tl-type">
            <select id="tl-type" className="select" value={filters.event_type} onChange={setFilter('event_type')}>
              <option value="">Any event</option>
              {EVENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <div className="field" style={{ justifyContent: 'flex-end' }}>
            <label className="check" style={{ height: 36 }}>
              <input type="checkbox" checked={filters.include_experiment} onChange={setFilter('include_experiment')} />
              Include experiment traffic
            </label>
          </div>
        </div>
        <QueryView query={timeline} isEmpty={(d) => d.requests.length === 0}
          empty={<Empty title="No matching requests">Inject a failure above or register a student.</Empty>}>
          {(d) => (
            <div role="list">
              {d.requests.map((g) => (
                <RequestRow key={g.request_id} group={g} name={names.get(g.student_id)} defaultOpen={!!filters.request_id || d.requests.length === 1} onFocus={focus} />
              ))}
            </div>
          )}
        </QueryView>
      </Panel>
    </>
  );
}

function RequestRow({ group, name, defaultOpen, onFocus }) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => { if (defaultOpen) setOpen(true); }, [defaultOpen]);
  const outcome = group.status === 'COMPLETED' ? group.result : group.status === 'RECEIVED' ? 'PENDING' : group.status;
  const count = (t) => group.events.filter((e) => e.event_type === t).length;
  return (
    <div className={`req-row ${open ? 'open' : ''}`} role="listitem">
      <button type="button" className="req-summary" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <ChevronRight size={16} className="chev" aria-hidden="true" />
        <span className="person">
          <Avatar id={group.student_id || ''} name={name || group.student_id || '?'} size="sm" />
          <span style={{ minWidth: 0 }}>
            <span className="person-name">{name || group.student_id} → {group.course_id}</span>
            <span className="person-id">{group.request_id}</span>
          </span>
        </span>
        <span className="small text-2 hide-md">
          {group.attempts ?? 0} attempt{group.attempts === 1 ? '' : 's'} · {count('TRANSACTION_COMMITTED')} commit · {count('TRANSACTION_ROLLED_BACK')} rollback
        </span>
        <span className="req-steps hide-md" aria-label="Event sequence">
          {group.events.map((e) => <span key={e.event_id} className={`step-dot ${eventTone(e.event_type)}`} title={e.event_type} />)}
        </span>
        <StatusBadge status={outcome} />
      </button>
      {open && (
        <div className="req-events">
          {group.events.map((e) => (
            <div key={e.event_id} className="req-event">
              <span className="mono muted">{fmtTime(e.created_at)}</span>
              <span><EventBadge type={e.event_type} /></span>
              <span className="small text-2">{e.attempt ? `attempt ${e.attempt}` : ''}{e.worker_name ? ` · ${e.worker_name}` : ''}</span>
              <span className="small text-2">{e.message}</span>
            </div>
          ))}
          <div style={{ paddingTop: 6 }}>
            <Button variant="ghost" size="sm" onClick={() => onFocus(group.request_id)}>Filter to this request</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function useCourse(courses) {
  const [courseId, setCourseId] = useState('');
  return [courseId || courses[0]?.course_id || '', setCourseId];
}

function WorkerFailure({ courses, disabled, onFocus }) {
  const [studentId, setStudentId] = useState('S0101');
  const [courseId, setCourseId] = useCourse(courses);
  const [kind, setKind] = useState('BEFORE_COMMIT');
  const [failures, setFailures] = useState(1);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const lock = useRef(false);

  async function run() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setResult(null);
    const requestId = newRequestId();
    try {
      await api.injectFailure({ kind, request_id: requestId, count: Number(failures) });
      const r = await submitRegistration({ student_id: studentId, course_id: courseId, request_id: requestId });
      setResult({ ...r, requestId });
      onFocus(requestId);
      setStudentId(nextStudent);
    } catch (error) {
      setResult({ error, requestId });
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="grid cols-4" style={{ gap: 12, alignItems: 'end' }}>
        <Field label="Student"><StudentPicker value={studentId} onChange={setStudentId} /></Field>
        <Field label="Course"><CourseSelect courses={courses} value={courseId} onChange={setCourseId} /></Field>
        <Field label="Failure point" htmlFor="wf-kind">
          <select id="wf-kind" className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
            {FAILURE_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
        </Field>
        <Field label="Failures in a row" htmlFor="wf-count">
          <select id="wf-count" className="select" value={failures} onChange={(e) => setFailures(e.target.value)}>
            <option value={1}>1 · succeeds on attempt 2</option>
            <option value={2}>2 · succeeds on attempt 3</option>
            <option value={3}>3 · gives up (FAILED)</option>
          </select>
        </Field>
      </div>
      <div className="row-between">
        <span className="small muted">{FAILURE_KINDS.find((k) => k.value === kind).hint} Maximum 3 attempts.</span>
        <Button variant="primary" icon={Zap} loading={busy} disabled={disabled || !courseId} onClick={run}>Inject and submit</Button>
      </div>
      {result && <ScenarioResult result={result} />}
    </div>
  );
}

function LostResponse({ courses, disabled, onFocus }) {
  const [studentId, setStudentId] = useState('S0201');
  const [courseId, setCourseId] = useCourse(courses);
  const [busy, setBusy] = useState(null);
  const [steps, setSteps] = useState([]);
  const [scenario, setScenario] = useState(null);
  const lock = useRef(false);

  async function send(kind) {
    if (lock.current) return;
    lock.current = true;
    setBusy(kind);
    let payload = scenario;
    try {
      if (kind === 'first') {
        payload = { student_id: studentId, course_id: courseId, request_id: newRequestId() };
        setScenario(payload);
        setSteps([]);
        await api.injectFailure({ kind: 'LOST_RESPONSE', request_id: payload.request_id, count: 1 });
        setStudentId(nextStudent);
      }
      if (kind === 'new') payload = { ...payload, request_id: newRequestId() };
      const r = await submitRegistration(payload);
      const label = { first: 'Original request', same: 'Retry · same request ID', new: 'Retry · new request ID' }[kind];
      setSteps((prev) => [...prev, { label, rid: payload.request_id, ...r }]);
      onFocus(kind === 'new' ? '' : payload.request_id);
    } catch (error) {
      setSteps((prev) => [...prev, { label: kind, rid: payload?.request_id, error }]);
    } finally {
      lock.current = false;
      setBusy(null);
    }
  }

  return (
    <div className="stack">
      <div className="grid cols-4" style={{ gap: 12, alignItems: 'end' }}>
        <Field label="Student"><StudentPicker value={studentId} onChange={setStudentId} /></Field>
        <Field label="Course"><CourseSelect courses={courses} value={courseId} onChange={setCourseId} /></Field>
        <div style={{ gridColumn: 'span 2' }} className="row">
          <Button variant="primary" icon={Send} loading={busy === 'first'} disabled={disabled || !courseId || !!busy} onClick={() => send('first')}>
            1. Submit with lost reply
          </Button>
          <Button icon={Repeat} loading={busy === 'same'} disabled={!scenario || !!busy} onClick={() => send('same')}>2. Retry same ID</Button>
          <Button icon={Repeat} loading={busy === 'new'} disabled={!scenario || !!busy} onClick={() => send('new')}>3. Retry new ID</Button>
        </div>
      </div>
      <span className="small muted">The seat is committed but the HTTP reply is dropped. A retry with the same request ID returns the stored result; a new ID is rejected as already registered.</span>
      {steps.map((st, i) => (
        <div key={i} className="stack" style={{ gap: 4 }}>
          <div className="small muted">{st.label} · <Mono>{st.rid?.slice(0, 8)}</Mono></div>
          <ScenarioResult result={st} />
        </div>
      ))}
    </div>
  );
}

function RestartRecovery({ courses, disabled }) {
  const { health, retry } = useConnection();
  const [courseId, setCourseId] = useCourse(courses);
  const [count, setCount] = useState(5);
  const [busy, setBusy] = useState(null);
  const [message, setMessage] = useState(null);
  const paused = health?.scheduler_paused;
  const lock = useRef(false);

  async function act(kind) {
    if (lock.current) return;
    lock.current = true;
    setBusy(kind);
    try {
      if (kind === 'pause') {
        await api.setPaused(!paused);
        setMessage({ tone: 'info', text: paused ? 'Workers resumed.' : 'Workers paused: new requests are saved as RECEIVED and wait in memory.' });
      } else if (kind === 'queue') {
        const r = await api.simulateRegistrations(courseId, Number(count));
        setMessage({ tone: 'info', text: `${r.submitted} requests saved in MySQL and queued in memory${paused ? ' (waiting while paused)' : ''}.` });
      } else {
        const r = await api.simulateRestart();
        setMessage({ tone: 'success', text: `Crash: ${r.dropped_from_memory} queued requests lost from memory. Restart: ${r.requeued_from_database} pending requests rebuilt from MySQL and processed.` });
      }
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    } finally {
      lock.current = false;
      setBusy(null);
      retry();
    }
  }

  return (
    <div className="stack">
      <div className="row" style={{ alignItems: 'end', gap: 12 }}>
        <Button icon={paused ? PlayCircle : PauseCircle} loading={busy === 'pause'} disabled={disabled} onClick={() => act('pause')}>
          {paused ? '1. Resume workers' : '1. Pause workers'}
        </Button>
        <Field label="Requests" htmlFor="rr-count">
          <input id="rr-count" className="input" type="number" min={1} max={50} value={count} onChange={(e) => setCount(e.target.value)} style={{ width: 80 }} />
        </Field>
        <Field label="Course"><CourseSelect courses={courses} value={courseId} onChange={setCourseId} /></Field>
        <Button icon={Send} loading={busy === 'queue'} disabled={disabled || !courseId} onClick={() => act('queue')}>2. Submit requests</Button>
        <ConfirmButton icon={Bomb} loading={busy === 'restart'} disabled={disabled} onConfirm={() => act('restart')}
          confirmLabel="Confirm: drop queues and recover">3. Simulate crash + restart</ConfirmButton>
      </div>
      <span className="small muted">
        The crash discards the in-memory queues; recovery rebuilds them from requests still marked RECEIVED in MySQL. For a real
        restart, pause, submit, then stop and start uvicorn: the log reports how many pending requests were requeued.
      </span>
      {message && <Alert tone={message.tone}>{message.text}</Alert>}
    </div>
  );
}

function ScenarioResult({ result }) {
  const { data, error, status } = result;
  if (error && data?.code === 'RESPONSE_LOST') {
    return <Alert tone="warning" title={`HTTP ${status}: reply lost after commit`}>{data.detail}</Alert>;
  }
  if (error) return <Alert tone="danger" title={status ? `HTTP ${status}` : 'Error'}>{error.message}</Alert>;
  return (
    <div className="alert">
      <div className="stack" style={{ gap: 2 }}>
        <div className="row">
          <StatusBadge status={data.outcome} />
          {data.replayed && <span className="badge info">Stored result (same request ID)</span>}
          <span className="small muted">HTTP {status} · attempts {data.attempts}</span>
        </div>
        <div className="small text-2">{data.message}</div>
      </div>
    </div>
  );
}
