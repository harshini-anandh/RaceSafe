import {
  Activity, Armchair, BookOpen, CheckCircle2, Clock, Cpu, Gauge, History, Send, ShieldAlert, ShieldCheck, TriangleAlert, Users, XCircle,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import ChartTooltip from '../components/ChartTooltip';
import EventList from '../components/EventList';
import { CapacityBar, Empty, KpiCards, Notice, PageHeader, Panel, QueryView, Segmented } from '../components/ui';
import { usePolling } from '../hooks/usePolling';
import { api } from '../services/api';
import { fmtMs, fmtNum, MODE_LABEL } from '../utils/format';
import { CHART } from '../utils/theme';

/** Picks the single most useful thing to say, from real data only. */
function overviewNotice(m, courses, checks) {
  if (checks && !checks.all_passed) {
    const failed = checks.checks.filter((c) => !c.passed);
    return {
      tone: 'danger', icon: ShieldAlert, title: 'A correctness check is failing',
      text: <><mark>{failed[0].name}</mark> does not hold in MySQL right now.</>,
      action: <Link to="/architecture" className="btn btn-sm btn-ink">Open diagnostics</Link>,
    };
  }
  if (!m || !courses) return null;
  if (m.live.scheduler.paused) {
    return {
      icon: TriangleAlert, title: 'Dispatching is paused',
      text: <><mark>{m.live.scheduler.queue_size} requests</mark> are saved and waiting in memory until workers resume.</>,
      action: <Link to="/architecture" className="btn btn-sm btn-primary">Open controls</Link>,
    };
  }
  if (m.requests.pending > 0) {
    return {
      icon: Clock, title: 'Requests in progress',
      text: <><mark>{m.requests.pending} requests</mark> are persisted and still being processed by the worker pool.</>,
      action: <Link to="/recovery" className="btn btn-sm btn-primary">View timeline</Link>,
    };
  }
  const full = courses.filter((c) => c.available_seats === 0 && c.waitlisted > 0).sort((a, b) => b.waitlisted - a.waitlisted)[0];
  if (full) {
    return {
      icon: Users, title: `${full.course_id} is full`,
      text: <><mark>{full.waitlisted} {full.waitlisted === 1 ? 'student is' : 'students are'}</mark> waiting for {full.course_name} in FIFO order; the first is promoted automatically when a seat is cancelled.</>,
      action: <Link to={`/courses?course=${full.course_id}`} className="btn btn-sm btn-primary">View waitlist</Link>,
    };
  }
  return {
    icon: ShieldCheck, title: 'Every course has free seats',
    text: <><mark>{fmtNum(m.seats.remaining)} seats</mark> remain across {m.seats.courses} courses, and every correctness check passes.</>,
    action: <Link to="/register" className="btn btn-sm btn-primary">Register a student</Link>,
  };
}

export default function OverviewPage() {
  const [scope, setScope] = useState('catalog');
  const metrics = usePolling(() => api.metrics(scope), 2000, [scope]);
  const courses = usePolling(() => api.courses(scope === 'all'), 3000, [scope]);
  const events = usePolling(() => api.events({ limit: 12, include_experiment: scope === 'all' }), 2500, [scope]);
  const throughput = usePolling(() => api.throughput(120, scope), 3000, [scope]);
  const checks = usePolling(() => api.consistency(), 5000);
  const m = metrics.data;
  const notice = overviewNotice(m, courses.data?.courses, checks.data);

  return (
    <>
      <PageHeader
        description="Live registration state, read from MySQL and the running scheduler."
        actions={
          <Segmented label="Data scope" value={scope} onChange={setScope}
            options={[{ value: 'catalog', label: 'Catalog courses' }, { value: 'all', label: 'Include experiments' }]} />
        }
      />

      {notice && <Notice tone={notice.tone} icon={notice.icon} title={notice.title} action={notice.action}>{notice.text}</Notice>}

      <QueryView query={metrics} rows={2}>
        {() => (
          <KpiCards items={[
            { label: 'Registration requests', icon: Send, to: '/recovery', value: fmtNum(m.requests.total),
              detail: `${fmtNum(m.requests.pending)} pending · ${fmtNum(m.requests.failed)} failed · ${fmtNum(m.requests.retried)} retried` },
            { label: 'Seats filled', icon: Armchair, to: '/courses', value: fmtNum(m.seats.confirmed_count), unit: `/ ${fmtNum(m.seats.capacity)}`,
              detail: `${fmtNum(m.seats.remaining)} remaining across ${m.seats.courses} courses` },
            { label: 'Waitlisted', icon: Clock, to: '/courses', value: fmtNum(m.registrations.waitlisted), detail: 'Students in FIFO waitlists' },
            { label: 'Queue now', icon: Cpu, to: '/architecture', value: fmtNum(m.live.scheduler.queue_size), unit: 'queued',
              detail: `${m.live.workers.active}/${m.live.workers.size} workers busy · ${MODE_LABEL[m.live.scheduler.mode]}${m.live.scheduler.paused ? ' (paused)' : ''}` },
          ]} />
        )}
      </QueryView>

      <div className="grid split">
        <Panel icon={BookOpen} title="Course capacity" description="Confirmed seats per course" flush
          actions={<Link to="/courses" className="btn btn-sm btn-secondary">Manage waitlists</Link>}>
          <QueryView query={courses} isEmpty={(d) => d.courses.length === 0} empty={<Empty title="No courses" />}>
            {(d) => (
              <div className="table-scroll">
                <table className="table stackable">
                  <thead>
                    <tr><th>Course</th><th style={{ width: '40%' }}>Seats</th><th className="num">Free</th><th className="num">Waitlist</th></tr>
                  </thead>
                  <tbody>
                    {d.courses.map((c) => (
                      <tr key={c.course_id}>
                        <td data-label="Course">
                          <b>{c.course_id}</b>
                          {!c.consistent && <span className="badge danger" style={{ marginLeft: 8 }}><XCircle size={12} />count mismatch</span>}
                          <span className="sub">{c.course_name}</span>
                        </td>
                        <td data-label="Seats"><CapacityBar confirmed={c.confirmed_actual} capacity={c.capacity} /></td>
                        <td data-label="Free" className="num">{c.available_seats}</td>
                        <td data-label="Waitlist" className="num">{c.waitlisted || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </QueryView>
        </Panel>

        <div className="stack-lg">
          <Panel icon={ShieldCheck} iconTone="success" title="Correctness checks" description="Run directly against MySQL" flush>
            <QueryView query={checks}>
              {(d) => (
                <ul className="list-plain">
                  {d.checks.map((c) => (
                    <li key={c.name} className="check-row">
                      {c.passed
                        ? <CheckCircle2 size={16} className="text-success" aria-label="Pass" />
                        : <XCircle size={16} className="text-danger" aria-label="Fail" />}
                      <div>
                        {c.name}
                        {!c.passed && <div className="small text-danger">{JSON.stringify(c.details).slice(0, 140)}</div>}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </QueryView>
          </Panel>

          <Panel icon={Gauge} title="Timing" description="Per request, from MySQL timestamps">
            <QueryView query={metrics} rows={2}>
              {() => (
                <dl className="kv">
                  <dt>Average processing</dt><dd>{fmtMs(m.requests.avg_processing_ms)}</dd>
                  <dt>Average queue wait</dt><dd>{fmtMs(m.requests.avg_queue_wait_ms)}</dd>
                  <dt>Longest queue wait</dt><dd>{fmtMs(m.requests.max_queue_wait_ms)}</dd>
                </dl>
              )}
            </QueryView>
          </Panel>
        </div>
      </div>

      <div className="grid cols-2">
        <Panel icon={Activity} title="Completed requests per second" description="Last 2 minutes · busiest second highlighted">
          <QueryView query={throughput}
            isEmpty={(d) => d.series.every((p) => p.completed === 0)}
            empty={<Empty icon={Activity} title="No requests completed in the last 2 minutes" />}>
            {(d) => {
              const peak = d.series.reduce((best, p, i) => (p.completed > d.series[best].completed ? i : best), 0);
              return (
                <ResponsiveContainer width="100%" height={210}>
                  <BarChart data={d.series} margin={{ top: 8, right: 4, left: -16, bottom: 0 }} barCategoryGap={2}>
                    <CartesianGrid vertical={false} stroke={CHART.grid} />
                    <XAxis dataKey="offset_s" tick={{ fill: CHART.tick, fontSize: 11 }} axisLine={false}
                      tickLine={false} interval={29} tickFormatter={(v) => (v === 0 ? 'now' : `${v}s`)} />
                    <YAxis allowDecimals={false} tick={{ fill: CHART.tick, fontSize: 11 }} axisLine={false} tickLine={false} />
                    <Tooltip cursor={{ fill: CHART.cursor }}
                      content={<ChartTooltip labelFormatter={(v) => (v === 0 ? 'Now' : `${-v} s ago`)} valueFormatter={(v) => `${v} requests`} />} />
                    <Bar dataKey="completed" name="Completed" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                      {d.series.map((p, i) => <Cell key={p.offset_s} fill={i === peak ? CHART.series1 : CHART.soft} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              );
            }}
          </QueryView>
        </Panel>

        <Panel icon={History} title="Recent events" description="Latest rows in registration_events" flush
          actions={<Link to="/recovery" className="btn btn-sm btn-secondary">Open timeline</Link>}>
          <QueryView query={events}>
            {(d) => <div className="scroll-y" style={{ maxHeight: 270 }}><EventList events={d.events} /></div>}
          </QueryView>
        </Panel>
      </div>
    </>
  );
}
