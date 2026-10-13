import { ArrowUpCircle, BookOpen, Clock, ListOrdered, UserCheck, Users, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import EventList from '../components/EventList';
import {
  Alert, Button, CapacityBar, Card, EmptyState, ErrorState, LoadingState, Mono, PageHeader, Segmented, StatCard,
} from '../components/ui';
import { useHealth } from '../hooks/HealthContext';
import { usePolling } from '../hooks/usePolling';
import { api } from '../services/api';
import { fmtTime } from '../utils/format';

export default function CoursesPage() {
  const { health } = useHealth();
  const [showExperiment, setShowExperiment] = useState(false);
  const [selected, setSelected] = useState('CS301');
  const [fillCount, setFillCount] = useState(10);
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);

  const courses = usePolling(() => api.courses(showExperiment), 2500, [showExperiment]);
  const detail = usePolling(() => (selected ? api.courseRegistrations(selected) : Promise.resolve(null)), 1500, [selected]);
  const activity = usePolling(
    () => (selected ? api.events({ course_id: selected, event_type: ['REGISTRATION_CANCELLED', 'WAITLIST_PROMOTED'], include_experiment: true, limit: 30 }) : Promise.resolve(null)),
    2000, [selected],
  );

  useEffect(() => {
    const list = courses.data?.courses;
    if (list && list.length && !list.some((c) => c.course_id === selected)) setSelected(list[0].course_id);
  }, [courses.data, selected]);

  async function cancel(studentId) {
    setBusy(studentId);
    setNotice(null);
    try {
      const r = await api.cancel(studentId, selected);
      setNotice({
        tone: 'good',
        text: r.promoted
          ? `Cancelled ${studentId}. ${r.promoted.student_id} (waitlist seq ${r.promoted.waitlist_seq}) was promoted in the same transaction — confirmed count is still ${r.confirmed_count}/${r.capacity}.`
          : `Cancelled ${studentId} (${r.cancelled_status.toLowerCase()}). No promotion needed.`,
      });
    } catch (e) {
      setNotice({ tone: 'critical', text: e.message });
    } finally {
      setBusy(null);
      detail.refresh();
      activity.refresh();
      courses.refresh();
    }
  }

  async function fill() {
    setBusy('fill');
    setNotice(null);
    try {
      const r = await api.simulateRegistrations(selected, Number(fillCount));
      setNotice({ tone: 'info', text: `Submitted ${r.submitted} registration requests for ${selected}. Watch the lists update.` });
    } catch (e) {
      setNotice({ tone: 'critical', text: e.message });
    } finally {
      setBusy(null);
    }
  }

  const d = detail.data;
  return (
    <>
      <PageHeader
        title="Courses & FIFO waitlists"
        description="Cancelling a confirmed seat deletes it and promotes the earliest waitlisted student inside one transaction, so a new request can never take the freed seat first."
        actions={<Segmented value={showExperiment ? 'all' : 'catalog'} onChange={(v) => setShowExperiment(v === 'all')}
          options={[{ value: 'catalog', label: 'Catalog' }, { value: 'all', label: 'Include experiment courses' }]} />}
      />
      <div className="grid grid-sidebar-left">
        <Card title="Courses" icon={BookOpen} flush>
          {courses.error && !courses.data && <div style={{ padding: 16 }}><ErrorState error={courses.error} /></div>}
          {!courses.data && !courses.error && <LoadingState />}
          {courses.data && (
            <div className="list-scroll" style={{ maxHeight: 640 }}>
              <table className="table">
                <tbody>
                  {courses.data.courses.map((c) => (
                    <tr key={c.course_id} className={`clickable ${c.course_id === selected ? 'selected' : ''}`}
                      onClick={() => setSelected(c.course_id)}>
                      <td style={{ width: '45%' }}><b>{c.course_id}</b><div className="small muted">{c.course_name}</div></td>
                      <td><CapacityBar confirmed={c.confirmed_actual} capacity={c.capacity} waitlisted={c.waitlisted} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="stack" style={{ gap: 16 }}>
          {detail.error && !d && <ErrorState error={detail.error} />}
          {!d && !detail.error && <LoadingState />}
          {d && (
            <>
              <div className="grid grid-4">
                <StatCard icon={Users} label={`${d.course.course_id} capacity`} value={d.course.capacity} hint={d.course.course_name} />
                <StatCard icon={UserCheck} label="Confirmed" value={d.confirmed.length}
                  hint={`confirmed_count = ${d.course.confirmed_count}${d.course.consistent ? ' ✓ matches' : ' ✗ mismatch'}`} />
                <StatCard icon={ArrowUpCircle} label="Available seats" value={d.course.available_seats} />
                <StatCard icon={Clock} label="Waitlist" value={d.waitlist.length} hint="FIFO by waitlist_seq" />
              </div>

              {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}

              {health?.demo_mode && (
                <Card>
                  <div className="row">
                    <span className="text-2">Demo helper: let</span>
                    <input className="input" type="number" min={1} max={200} value={fillCount}
                      onChange={(e) => setFillCount(e.target.value)} style={{ width: 90 }} />
                    <span className="text-2">unregistered students register for <b>{selected}</b> (goes through the real queue).</span>
                    <Button variant="secondary" size="sm" loading={busy === 'fill'} onClick={fill}>Submit requests</Button>
                  </div>
                </Card>
              )}

              <div className="grid grid-2">
                <Card title="Confirmed students" icon={UserCheck} subtitle={`${d.confirmed.length} of ${d.course.capacity} seats`} flush>
                  {d.confirmed.length === 0 ? <EmptyState title="No confirmed students" /> : (
                    <div className="list-scroll">
                      <table className="table">
                        <thead><tr><th>Student</th><th>Since</th><th /></tr></thead>
                        <tbody>
                          {d.confirmed.map((r) => (
                            <tr key={r.registration_id}>
                              <td><Mono>{r.student_id}</Mono> <span className="text-2">{r.full_name}</span>
                                {r.promoted_at && <div className="small" style={{ color: '#3fcf3f' }}>promoted from waitlist {fmtTime(r.promoted_at)}</div>}
                              </td>
                              <td className="mono small">{fmtTime(r.created_at)}</td>
                              <td className="num"><Button variant="danger" size="sm" icon={X} loading={busy === r.student_id}
                                onClick={() => cancel(r.student_id)}>Cancel</Button></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Card>

                <Card title="FIFO waitlist" icon={ListOrdered} subtitle="Position 1 is promoted first" flush>
                  {d.waitlist.length === 0 ? <EmptyState title="Waitlist is empty" /> : (
                    <div className="list-scroll">
                      <table className="table">
                        <thead><tr><th>#</th><th>Student</th><th>Seq</th><th>Joined</th><th /></tr></thead>
                        <tbody>
                          {d.waitlist.map((r) => (
                            <tr key={r.registration_id}>
                              <td><b>{r.position}</b></td>
                              <td><Mono>{r.student_id}</Mono> <span className="text-2">{r.full_name}</span></td>
                              <td className="mono small">{r.waitlist_seq}</td>
                              <td className="mono small">{fmtTime(r.created_at)}</td>
                              <td className="num"><Button variant="ghost" size="sm" icon={X} loading={busy === r.student_id}
                                onClick={() => cancel(r.student_id)}>Leave</Button></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Card>
              </div>

              <Card title="Cancellation & promotion activity" icon={ArrowUpCircle} subtitle={`Events for ${selected}`} flush>
                {activity.error && !activity.data ? <div style={{ padding: '0 18px 14px' }}><ErrorState error={activity.error} /></div> : !activity.data ? <LoadingState /> : (
                  <EventList events={activity.data.events} emptyText="No cancellations yet — cancel a confirmed student to see a promotion" />
                )}
              </Card>
            </>
          )}
        </div>
      </div>
    </>
  );
}
