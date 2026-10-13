import { BookOpen, ClipboardList, Repeat, Search, Users, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert, Avatar, Button, CapacityBar, ConfirmButton, Empty, Loading, Mono, PageHeader, Panel, QueryView, StatusBadge,
} from '../components/ui';
import { useToast } from '../hooks/Toasts';
import { usePolling } from '../hooks/usePolling';
import { useStudents } from '../hooks/useStudents';
import { api, isConnectivityError, newRequestId } from '../services/api';
import { fmtMs, fmtTime } from '../utils/format';

export default function RegisterPage() {
  const toast = useToast();
  const { students, loading: studentsLoading, error: studentsError } = useStudents();
  const [search, setSearch] = useState('');
  const [params, setParams] = useSearchParams();
  const [studentId, setStudentId] = useState(params.get('student') || 'S0001');
  const [pending, setPending] = useState(null);     // course_id of the request in flight
  const [last, setLast] = useState(null);           // { payload, status, data, error }
  const [cancelling, setCancelling] = useState(null);
  const busy = useRef(false); // synchronous guard against double clicks within one render

  const courses = usePolling(() => api.courses(false), 3000);
  const mine = usePolling(() => api.studentRegistrations(studentId), 3000, [studentId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = q ? students.filter((s) => s.student_id.toLowerCase().includes(q) || s.full_name.toLowerCase().includes(q)) : students;
    const top = list.slice(0, 50);
    // Keep the selected student visible even when they are outside the first 50.
    const selectedStudent = !q && students.find((s) => s.student_id === studentId);
    return selectedStudent && !top.includes(selectedStudent) ? [selectedStudent, ...top.slice(0, 49)] : top;
  }, [students, search, studentId]);
  const student = students.find((s) => s.student_id === studentId);
  const registered = new Map((mine.stale ? [] : mine.data?.registrations || []).map((r) => [r.course_id, r]));
  const catalogCount = (courses.data?.courses || []).filter((c) => registered.has(c.course_id)).length;

  const selectStudent = (id) => {
    setStudentId(id);
    setLast(null);
    setParams((p) => { p.set('student', id); return p; }, { replace: true });
  };

  // Follow ?student= when it changes (e.g. picked from the quick search).
  useEffect(() => {
    const fromUrl = params.get('student');
    if (fromUrl && fromUrl !== studentId) { setStudentId(fromUrl); setLast(null); }
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(payload) {
    if (busy.current) return; // one request at a time: no accidental double submissions
    busy.current = true;
    setPending(payload.course_id);
    try {
      const res = await api.register(payload, { wait: true, timeout: 30 });
      setLast({ payload, status: res.status, data: res.data, error: null });
    } catch (error) {
      setLast({ payload, status: error.status, data: error.body, error });
    } finally {
      busy.current = false;
      setPending(null);
      mine.refresh();
      courses.refresh();
    }
  }

  async function cancel(courseId) {
    if (busy.current) return;
    busy.current = true;
    setCancelling(courseId);
    try {
      const r = await api.cancel(studentId, courseId);
      toast({
        title: `${courseId} cancelled`,
        text: `${studentId}'s ${r.cancelled_status.toLowerCase()} registration was removed.` +
          (r.promoted ? ` ${r.promoted.student_id} was promoted from the waitlist in the same transaction.` : ''),
      });
    } catch (error) {
      toast({ tone: 'danger', title: 'Could not cancel', text: error.message });
    } finally {
      busy.current = false;
      setCancelling(null);
      mine.refresh();
      courses.refresh();
    }
  }

  const register = (courseId) => submit({ student_id: studentId, course_id: courseId, request_id: newRequestId() });

  return (
    <>
      <PageHeader description="Pick a student on the left, then register for a course. Each click sends a new request ID; Retry re-sends the same one." />

      <div className="grid split-left">
        {/* ---------------------------------------------------------- who */}
        <div className="stack-lg">
          <Panel icon={Users} title="Students" description={`${students.length || 600} sample students`} flush>
            <div style={{ padding: '0 12px 8px' }}>
              <div className="search-wrap">
                <Search size={15} aria-hidden="true" />
                <label htmlFor="student-search" className="sr-only">Search students</label>
                <input id="student-search" className="input search" placeholder="Search by ID or name" value={search}
                  onChange={(e) => setSearch(e.target.value)} />
              </div>
            </div>
            {studentsLoading && <Loading />}
            {studentsError && !studentsLoading && (
              isConnectivityError(studentsError)
                ? <Empty compact>Student list unavailable while offline.</Empty>
                : <div style={{ padding: '0 12px 12px' }}><Alert tone="danger">{studentsError.message}</Alert></div>
            )}
            {!studentsLoading && students.length > 0 && (
              <>
                <ul className="pick-list scroll-y" style={{ maxHeight: 300 }} aria-label="Students">
                  {filtered.map((s) => (
                    <li key={s.student_id}>
                      <button type="button" className="pick-item" style={{ gridTemplateColumns: '1fr' }}
                        aria-current={s.student_id === studentId} onClick={() => selectStudent(s.student_id)}>
                        <span className="person">
                          <Avatar id={s.student_id} name={s.full_name} size="sm" />
                          <span><span className="person-name">{s.full_name}</span><span className="person-id">{s.student_id}</span></span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                {filtered.length === 0 && <Empty compact title="No matching students" />}
                <div className="small muted" style={{ padding: '8px 20px 6px', borderTop: '1px solid var(--border)' }}>
                  {search ? `${filtered.length}${filtered.length === 50 ? '+' : ''} matches` : `Showing 50 of ${students.length} · search to find others`}
                </div>
              </>
            )}
          </Panel>

          <Panel icon={ClipboardList} title="Their registrations" description="Includes experiment courses" flush>
            <QueryView query={mine} isEmpty={(d) => d.registrations.length === 0}
              empty={<Empty compact title="No registrations yet">Register for a course on the right.</Empty>}>
              {(d) => (
                <ul className="list-plain">
                  {d.registrations.map((r) => (
                    <li key={r.course_id} style={{ padding: '10px 20px', borderTop: '1px solid #f0f2f6' }}>
                      <div className="row-between" style={{ flexWrap: 'nowrap', alignItems: 'flex-start' }}>
                        <div style={{ minWidth: 0 }}>
                          <div className="row" style={{ gap: 8 }}>
                            <b>{r.course_id}</b>
                            <StatusBadge status={r.status} label={r.status === 'WAITLISTED' ? `Waitlist #${r.waitlist_position}` : undefined} />
                          </div>
                          <span className="sub">
                            {r.course_name} · since {fmtTime(r.created_at).slice(0, 8)}
                            {r.promoted_at ? ' · promoted' : ''} · <Mono title={r.request_id}>{r.request_id ? r.request_id.slice(0, 8) : '—'}</Mono>
                          </span>
                        </div>
                        <ConfirmButton size="sm" icon={X} confirmLabel="Confirm" loading={cancelling === r.course_id}
                          disabled={!!cancelling && cancelling !== r.course_id} onConfirm={() => cancel(r.course_id)}
                          aria-label={`Cancel ${r.course_id}`}>Cancel</ConfirmButton>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </QueryView>
          </Panel>
        </div>

        {/* ---------------------------------------------------------- what */}
        <Panel
          title={<span className="row" style={{ gap: 10 }}><Avatar id={studentId} name={student?.full_name || studentId} />{student ? student.full_name : studentId}</span>}
          description={student ? `${student.student_id} · ${student.email}` : 'Selected student'}
          actions={courses.data && <span className="fact"><BookOpen size={12} aria-hidden="true" /><b>{catalogCount}</b> of {courses.data.courses.length} courses</span>}
          flush>
          {last && <LastResult last={last} retrying={pending === last.payload.course_id} disabled={!!pending}
            onRetry={() => submit(last.payload)} onDismiss={() => setLast(null)} />}
          <QueryView query={courses}>
            {(d) => (
              <div className="table-scroll">
                <table className="table stackable">
                  <thead>
                    <tr><th>Course</th><th style={{ width: '30%' }}>Seats</th><th className="num">Waitlist</th><th>Status</th><th className="num">Actions</th></tr>
                  </thead>
                  <tbody>
                    {d.courses.map((c) => {
                      const reg = registered.get(c.course_id);
                      return (
                        <tr key={c.course_id}>
                          <td data-label="Course"><b>{c.course_id}</b><span className="sub">{c.course_name}</span></td>
                          <td data-label="Seats"><CapacityBar confirmed={c.confirmed_actual} capacity={c.capacity} /></td>
                          <td data-label="Waitlist" className="num">{c.waitlisted || '—'}</td>
                          <td data-label="Status">
                            {reg
                              ? <StatusBadge status={reg.status} label={reg.status === 'WAITLISTED' ? `Waitlist #${reg.waitlist_position}` : undefined} />
                              : <span className="muted small">{c.available_seats ? `${c.available_seats} free` : 'Full · joins waitlist'}</span>}
                          </td>
                          <td className="num">
                            <span className="row" style={{ justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
                              <Button size="sm" variant={reg ? 'secondary' : 'primary'} loading={pending === c.course_id}
                                disabled={!!pending || !studentId}
                                title={reg ? 'Sends another request; the backend answers ALREADY_REGISTERED' : undefined}
                                onClick={() => register(c.course_id)}>
                                Register
                              </Button>
                              {reg && (
                                <ConfirmButton size="sm" confirmLabel="Confirm" loading={cancelling === c.course_id}
                                  disabled={!!cancelling && cancelling !== c.course_id} onConfirm={() => cancel(c.course_id)}
                                  aria-label={`Cancel ${c.course_id}`}>Cancel</ConfirmButton>
                              )}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </QueryView>
        </Panel>
      </div>
    </>
  );
}

function LastResult({ last, onRetry, onDismiss, retrying, disabled }) {
  const { data, error, status, payload } = last;
  const lost = error && data?.code === 'RESPONSE_LOST';
  const outcome = lost ? 'RESPONSE_LOST' : error ? 'FAILED' : data?.outcome;
  return (
    <div className="inset" style={{ margin: '0 20px 12px', padding: 14 }} aria-live="polite">
      <div className="row-between" style={{ alignItems: 'flex-start' }}>
        <div className="stack" style={{ gap: 4, minWidth: 0 }}>
          <div className="row">
            <b>Last request · {payload.course_id}</b>
            <StatusBadge status={outcome} />
            {data?.replayed && <span className="badge info">Stored result (same request ID)</span>}
            <span className="small muted">HTTP {status || 'network error'}</span>
          </div>
          <div className="small">{error ? error.message : data?.message}</div>
          <div className="small muted">
            Request <Mono>{payload.request_id}</Mono>
            {data && !error && <> · attempt {data.attempts} · {data.worker_name || '—'} · queue wait {fmtMs(data.queue_wait_ms)} · processing {fmtMs(data.processing_ms)}</>}
          </div>
        </div>
        <span className="row" style={{ flexWrap: 'nowrap' }}>
          <Button size="sm" icon={Repeat} loading={retrying} disabled={disabled} onClick={onRetry}>Retry same ID</Button>
          <button type="button" className="toast-close" onClick={onDismiss} aria-label="Dismiss last result"><X size={14} /></button>
        </span>
      </div>
    </div>
  );
}
