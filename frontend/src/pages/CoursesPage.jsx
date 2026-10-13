import { BookOpen, CheckCircle2, ListOrdered, UserCheck, X, XCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import EventList from '../components/EventList';
import {
  Button, CapacityBar, ConfirmButton, Disclosure, Empty, PageHeader, Panel, Person, QueryView, Segmented,
} from '../components/ui';
import { useConnection } from '../hooks/ConnectionContext';
import { useToast } from '../hooks/Toasts';
import { usePolling } from '../hooks/usePolling';
import { api } from '../services/api';
import { fmtTime } from '../utils/format';

export default function CoursesPage() {
  const { demoMode } = useConnection();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [showExperiment, setShowExperiment] = useState(false);
  const selected = params.get('course') || 'CS301';
  const [fillCount, setFillCount] = useState(10);
  const [busy, setBusy] = useState(null);
  const lock = useRef(false);
  const triedExperiment = useRef(false);

  const courses = usePolling(() => api.courses(showExperiment), 2500, [showExperiment]);
  const detail = usePolling(() => api.courseRegistrations(selected), 1500, [selected]);
  const activity = usePolling(
    () => api.events({ course_id: selected, event_type: ['REGISTRATION_CANCELLED', 'WAITLIST_PROMOTED'], include_experiment: true, limit: 30 }),
    2000, [selected],
  );

  const select = (id) => setParams((p) => { p.set('course', id); return p; }, { replace: true });

  useEffect(() => {
    const list = courses.data?.courses;
    if (!list || !list.length || list.some((c) => c.course_id === selected)) return;
    // A linked course may be an experiment course: on first load, show those before
    // falling back to the first course. Later (user chose "Catalog") just fall back.
    if (!showExperiment && !triedExperiment.current) {
      triedExperiment.current = true;
      setShowExperiment(true);
    } else {
      select(list[0].course_id);
    }
  }, [courses.data]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(key, fn) {
    if (lock.current) return;
    lock.current = true;
    setBusy(key);
    try {
      toast(await fn());
    } catch (e) {
      toast({ tone: 'danger', title: 'Action failed', text: e.message });
    } finally {
      lock.current = false;
      setBusy(null);
      detail.refresh();
      activity.refresh();
      courses.refresh();
    }
  }

  const cancel = (studentId) => run(studentId, async () => {
    const r = await api.cancel(studentId, selected);
    return r.promoted
      ? { title: `${studentId} cancelled · ${r.promoted.student_id} promoted`, text: `${r.promoted.student_id} (waitlist seq ${r.promoted.waitlist_seq}) took the seat in the same transaction. Seats ${r.confirmed_count}/${r.capacity}.` }
      : { title: `${studentId} removed`, text: `The ${r.cancelled_status.toLowerCase()} registration was cancelled; no promotion was needed.` };
  });

  const fill = () => run('fill', async () => {
    const r = await api.simulateRegistrations(selected, Number(fillCount));
    return { tone: 'info', title: `${r.submitted} requests submitted`, text: `They go through the normal queue for ${selected}; the lists update as they commit.` };
  });

  return (
    <>
      <PageHeader
        description="Pick a course to see who holds a seat and who is waiting. Cancelling a seat promotes the first waitlisted student in the same transaction."
        actions={<Segmented label="Courses shown" value={showExperiment ? 'all' : 'catalog'} onChange={(v) => setShowExperiment(v === 'all')}
          options={[{ value: 'catalog', label: 'Catalog' }, { value: 'all', label: 'Include experiment courses' }]} />}
      />

      <div className="grid split-left">
        <Panel icon={BookOpen} title="Courses" description={courses.data ? `${courses.data.courses.length} courses` : 'Loading'} flush className="sticky-col">
          <QueryView query={courses} isEmpty={(d) => d.courses.length === 0} empty={<Empty title="No courses" />}>
            {(d) => (
              <ul className="pick-list" aria-label="Courses">
                {d.courses.map((c) => (
                  <li key={c.course_id}>
                    <button type="button" className="pick-item" aria-current={c.course_id === selected} onClick={() => select(c.course_id)}>
                      <span style={{ minWidth: 0 }}>
                        <span className="pick-title">{c.course_id}</span>
                        <span className="sub" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.course_name}</span>
                      </span>
                      <span className="pick-meta">
                        {!c.consistent
                          ? <span className="badge danger"><XCircle size={12} />mismatch</span>
                          : c.available_seats > 0 ? `${c.available_seats} free` : c.waitlisted ? `Full · ${c.waitlisted} waiting` : 'Full'}
                      </span>
                      <CapacityBar confirmed={c.confirmed_actual} capacity={c.capacity} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        </Panel>

        <QueryView query={detail} rows={6}>
          {(d) => (
            <Panel
              title={`${d.course.course_id} · ${d.course.course_name}`}
              description={d.course.kind === 'EXPERIMENT' ? 'Experiment course' : 'Catalog course'}
              actions={
                <span className="small text-2 row" title="courses.confirmed_count compared with the CONFIRMED rows">
                  {d.course.consistent
                    ? <><CheckCircle2 size={14} className="text-success" aria-hidden="true" />Counter matches rows</>
                    : <><XCircle size={14} className="text-danger" aria-hidden="true" />confirmed_count {d.course.confirmed_count} ≠ rows</>}
                </span>}
              flush>
              <div className="stat-strip">
                <div><b>{d.course.capacity}</b>Capacity</div>
                <div><b>{d.confirmed.length}</b>Confirmed</div>
                <div><b>{d.course.available_seats}</b>Free seats</div>
                <div><b>{d.waitlist.length}</b>Waiting</div>
              </div>

              <div className="grid cols-2" style={{ gap: 0, borderTop: '1px solid var(--border)' }}>
                <section style={{ borderRight: '1px solid var(--border)' }} aria-label="Confirmed students">
                  <div className="panel-head" style={{ paddingBottom: 8 }}>
                    <div className="panel-heading"><span className="icon-chip success" style={{ width: 28, height: 28 }}><UserCheck size={14} /></span><h3 className="panel-title">Confirmed</h3></div>
                  </div>
                  {d.confirmed.length === 0 ? <Empty compact title="No confirmed students yet" /> : (
                    <ul className="list-plain">
                      {d.confirmed.map((r) => (
                        <li key={r.registration_id} className="row-between" style={{ padding: '8px 20px', flexWrap: 'nowrap' }}>
                          <Person id={r.student_id} name={r.full_name}
                            secondary={r.promoted_at ? `${r.student_id} · promoted ${fmtTime(r.promoted_at)}` : undefined} />
                          <ConfirmButton size="sm" icon={X} confirmLabel="Confirm cancel" loading={busy === r.student_id}
                            disabled={!!busy && busy !== r.student_id} onConfirm={() => cancel(r.student_id)}
                            aria-label={`Cancel ${r.student_id}`}>Cancel</ConfirmButton>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
                <section aria-label="FIFO waitlist">
                  <div className="panel-head" style={{ paddingBottom: 8 }}>
                    <div className="panel-heading"><span className="icon-chip warning" style={{ width: 28, height: 28 }}><ListOrdered size={14} /></span><h3 className="panel-title">FIFO waitlist</h3></div>
                    <span className="small muted">#1 is promoted first</span>
                  </div>
                  {d.waitlist.length === 0 ? <Empty compact title="Nobody is waiting" /> : (
                    <ul className="list-plain">
                      {d.waitlist.map((r) => (
                        <li key={r.registration_id} className="row-between" style={{ padding: '8px 20px', flexWrap: 'nowrap', gap: 12 }}>
                          <span className="row" style={{ flexWrap: 'nowrap', gap: 12, minWidth: 0 }}>
                            <span className="fact" title={`waitlist_seq ${r.waitlist_seq}`}><b>#{r.position}</b></span>
                            <Person id={r.student_id} name={r.full_name} size="sm" secondary={`${r.student_id} · seq ${r.waitlist_seq} · ${fmtTime(r.created_at)}`} />
                          </span>
                          <ConfirmButton size="sm" variant="secondary" confirmLabel="Confirm" loading={busy === r.student_id}
                            disabled={!!busy && busy !== r.student_id} onConfirm={() => cancel(r.student_id)}
                            aria-label={`Remove ${r.student_id} from waitlist`}>Leave</ConfirmButton>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>

              <Disclosure summary="Cancellation & promotion activity" defaultOpen>
                <div className="inset" style={{ overflow: 'hidden' }}>
                  <QueryView query={activity}>
                    {(a) => <EventList events={a.events} emptyText="No cancellations yet" />}
                  </QueryView>
                </div>
              </Disclosure>

              {demoMode && (
                <Disclosure summary="Demo tools">
                  <div className="row">
                    <label htmlFor="fill-count" className="text-2">Let</label>
                    <input id="fill-count" className="input" type="number" min={1} max={200} value={fillCount}
                      onChange={(e) => setFillCount(e.target.value)} style={{ width: 84 }} />
                    <span className="text-2">unregistered students register for {d.course.course_id} through the normal queue.</span>
                    <Button size="sm" loading={busy === 'fill'} disabled={!!busy} onClick={fill}>Submit requests</Button>
                  </div>
                </Disclosure>
              )}
            </Panel>
          )}
        </QueryView>
      </div>
    </>
  );
}
