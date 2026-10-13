import { BookOpen, ClipboardList, RefreshCw, Repeat, Search, Send, UserRound, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import {
  Alert, Button, CapacityBar, Card, EmptyState, ErrorState, LoadingState, Mono, PageHeader, StatusBadge,
} from '../components/ui';
import { usePolling } from '../hooks/usePolling';
import { useStudents } from '../hooks/useStudents';
import { api, newRequestId } from '../services/api';
import { fmtMs, fmtTime } from '../utils/format';

export default function RegisterPage() {
  const { students, loading: studentsLoading, error: studentsError } = useStudents();
  const [search, setSearch] = useState('');
  const [studentId, setStudentId] = useState('S0001');
  const [busyCourse, setBusyCourse] = useState(null);
  const [last, setLast] = useState(null); // { payload, status, data, error }
  const [cancelling, setCancelling] = useState(null);
  const [notice, setNotice] = useState(null);

  const courses = usePolling(() => api.courses(false), 3000);
  const mine = usePolling(() => (studentId ? api.studentRegistrations(studentId) : Promise.resolve(null)), 3000, [studentId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = q ? students.filter((s) => s.student_id.toLowerCase().includes(q) || s.full_name.toLowerCase().includes(q)) : students;
    return list.slice(0, 60);
  }, [students, search]);
  const student = students.find((s) => s.student_id === studentId);

  async function submit(payload) {
    setBusyCourse(payload.course_id);
    setNotice(null);
    try {
      const res = await api.register(payload, { wait: true, timeout: 30 });
      setLast({ payload, status: res.status, data: res.data, error: null });
    } catch (error) {
      setLast({ payload, status: error.status, data: error.body, error });
    } finally {
      setBusyCourse(null);
      mine.refresh();
      courses.refresh();
    }
  }

  const register = (courseId) => submit({ student_id: studentId, course_id: courseId, request_id: newRequestId() });

  async function cancel(courseId) {
    setCancelling(courseId);
    setNotice(null);
    try {
      const r = await api.cancel(studentId, courseId);
      setNotice({
        tone: 'good',
        text: `Cancelled ${r.cancelled_status.toLowerCase()} registration in ${courseId}.` +
          (r.promoted ? ` ${r.promoted.student_id} was promoted from the waitlist in the same transaction.` : ''),
      });
    } catch (error) {
      setNotice({ tone: 'critical', text: error.message });
    } finally {
      setCancelling(null);
      mine.refresh();
      courses.refresh();
    }
  }

  const registered = new Map((mine.data?.registrations || []).map((r) => [r.course_id, r]));

  return (
    <>
      <PageHeader
        title="Student registration"
        description="Pick a student, register for courses and watch the result come back from the worker pool. Each click creates a new request ID (UUID); retrying reuses it."
      />
      <div className="grid grid-sidebar-left">
        <Card title="Student" icon={UserRound} subtitle={`${students.length} sample students`}>
          <div className="stack">
            <div style={{ position: 'relative' }}>
              <Search size={15} style={{ position: 'absolute', left: 11, top: 11, color: 'var(--muted)' }} />
              <input className="input" style={{ paddingLeft: 32 }} placeholder="Search by ID or name"
                value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            {studentsError && <ErrorState error={studentsError} />}
            {studentsLoading && <LoadingState />}
            <div className="list-scroll" style={{ maxHeight: 300 }}>
              <table className="table">
                <tbody>
                  {filtered.map((s) => (
                    <tr key={s.student_id} className={`clickable ${s.student_id === studentId ? 'selected' : ''}`}
                      onClick={() => setStudentId(s.student_id)}>
                      <td className="mono">{s.student_id}</td>
                      <td>{s.full_name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!studentsLoading && filtered.length === 0 && <EmptyState title="No matching students" />}
            </div>
            {student && (
              <div className="card" style={{ padding: 12, background: 'var(--surface-2)' }}>
                <div className="small muted">Selected</div>
                <div style={{ fontWeight: 600 }}>{student.full_name}</div>
                <div className="small text-2"><Mono>{student.student_id}</Mono> · {student.email}</div>
              </div>
            )}
          </div>
        </Card>

        <div className="stack" style={{ gap: 16 }}>
          {last && <LastResult last={last} onRetry={() => submit(last.payload)} />}
          {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}

          <Card title="Courses" icon={BookOpen} subtitle="Seats update live from MySQL" flush>
            {courses.error && !courses.data && <div style={{ padding: 16 }}><ErrorState error={courses.error} /></div>}
            {!courses.data && !courses.error && <LoadingState />}
            {courses.data && (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>Course</th><th style={{ width: '34%' }}>Seats</th><th>Your status</th><th /></tr>
                  </thead>
                  <tbody>
                    {courses.data.courses.map((c) => {
                      const reg = registered.get(c.course_id);
                      return (
                        <tr key={c.course_id}>
                          <td><b>{c.course_id}</b><div className="small muted">{c.course_name}</div></td>
                          <td><CapacityBar confirmed={c.confirmed_actual} capacity={c.capacity} waitlisted={c.waitlisted} /></td>
                          <td>
                            {reg ? (
                              <StatusBadge status={reg.status}
                                label={reg.status === 'WAITLISTED' ? `Waitlist #${reg.waitlist_position}` : undefined} />
                            ) : <span className="muted small">—</span>}
                          </td>
                          <td className="num">
                            <Button size="sm" icon={Send} loading={busyCourse === c.course_id}
                              disabled={!studentId || busyCourse !== null} onClick={() => register(c.course_id)}>
                              Register
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title={`Registrations of ${studentId || '—'}`} icon={ClipboardList} flush
            actions={<Button variant="ghost" size="sm" icon={RefreshCw} onClick={mine.refresh}>Refresh</Button>}>
            {mine.error && <div style={{ padding: 16 }}><ErrorState error={mine.error} /></div>}
            {!mine.data && !mine.error && <LoadingState />}
            {mine.data && mine.data.registrations.length === 0 && (
              <EmptyState title="No registrations yet">Register for a course above.</EmptyState>
            )}
            {mine.data && mine.data.registrations.length > 0 && (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>Course</th><th>Status</th><th>Waitlist position</th><th>Since</th><th>Request ID</th><th /></tr>
                  </thead>
                  <tbody>
                    {mine.data.registrations.map((r) => (
                      <tr key={r.course_id}>
                        <td><b>{r.course_id}</b><div className="small muted">{r.course_name}</div></td>
                        <td><StatusBadge status={r.status} />{r.promoted_at && <div className="small muted">promoted {fmtTime(r.promoted_at)}</div>}</td>
                        <td>{r.waitlist_position ? `#${r.waitlist_position}` : '—'}</td>
                        <td className="mono small">{fmtTime(r.created_at)}</td>
                        <td><Mono title={r.request_id}>{r.request_id ? r.request_id.slice(0, 8) : '—'}</Mono></td>
                        <td className="num">
                          <Button variant="danger" size="sm" icon={X} loading={cancelling === r.course_id}
                            onClick={() => cancel(r.course_id)}>Cancel</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function LastResult({ last, onRetry }) {
  const { data, error, status, payload } = last;
  const lost = error && data?.code === 'RESPONSE_LOST';
  const outcome = lost ? 'RESPONSE_LOST' : error ? 'FAILED' : data?.outcome;
  return (
    <Card title="Last request" icon={Send} className={error && !lost ? 'danger' : 'accent'}
      actions={<Button variant="secondary" size="sm" icon={Repeat} onClick={onRetry}>Retry with same request ID</Button>}>
      <div className="row-between" style={{ alignItems: 'flex-start' }}>
        <div className="stack" style={{ gap: 6 }}>
          <div className="row">
            <StatusBadge status={outcome} />
            {data?.replayed && <span className="badge info">idempotent replay</span>}
            <span className="muted small">HTTP {status || 'network error'}</span>
          </div>
          <div>{error ? error.message : data?.message}</div>
          <div className="small muted">
            {payload.student_id} → {payload.course_id} · request <Mono>{payload.request_id}</Mono>
          </div>
        </div>
        {data && !error && (
          <dl className="kv" style={{ minWidth: 220 }}>
            <dt>Attempts</dt><dd>{data.attempts}</dd>
            <dt>Worker</dt><dd>{data.worker_name || '—'}</dd>
            <dt>Queue wait</dt><dd>{fmtMs(data.queue_wait_ms)}</dd>
            <dt>Processing</dt><dd>{fmtMs(data.processing_ms)}</dd>
          </dl>
        )}
      </div>
    </Card>
  );
}
