import {
  Activity, AlertOctagon, Armchair, CheckCircle2, Clock, Cpu, Gauge, Hourglass, ListChecks, Send, XCircle,
} from 'lucide-react';
import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import ChartTooltip from '../components/ChartTooltip';
import EventList from '../components/EventList';
import {
  CapacityBar, Card, EmptyState, ErrorState, LoadingState, PageHeader, Segmented, StatCard,
} from '../components/ui';
import { usePolling } from '../hooks/usePolling';
import { api } from '../services/api';
import { fmtMs, fmtNum, MODE_LABEL } from '../utils/format';
import { CHART } from '../utils/theme';

export default function OverviewPage() {
  const [scope, setScope] = useState('catalog');
  const metrics = usePolling(() => api.metrics(scope), 2000, [scope]);
  const courses = usePolling(() => api.courses(scope === 'all'), 3000, [scope]);
  const events = usePolling(() => api.events({ limit: 14, include_experiment: scope === 'all' }), 2000, [scope]);
  const throughput = usePolling(() => api.throughput(120, scope), 3000, [scope]);
  const checks = usePolling(() => api.consistency(), 5000);

  const m = metrics.data;
  const live = m?.live;
  const loading = !m && metrics.loading;

  return (
    <>
      <PageHeader
        title="Live overview"
        description="Every number on this page is read from MySQL and the running scheduler, refreshed every few seconds."
        actions={
          <Segmented value={scope} onChange={setScope}
            options={[{ value: 'catalog', label: 'Catalog courses' }, { value: 'all', label: 'Include experiments' }]} />
        }
      />
      {metrics.error && !m && <ErrorState error={metrics.error} onRetry={metrics.refresh} />}

      <div className="grid grid-4">
        <StatCard icon={Send} label="Registration requests" loading={loading} value={fmtNum(m?.requests.total)}
          hint={m ? `${fmtNum(m.requests.pending)} pending · ${fmtNum(m.requests.retried)} retried` : null} />
        <StatCard icon={CheckCircle2} label="Confirmed registrations" loading={loading}
          value={fmtNum(m?.registrations.confirmed)} hint="Rows with status CONFIRMED" />
        <StatCard icon={Clock} label="Waitlisted registrations" loading={loading}
          value={fmtNum(m?.registrations.waitlisted)} hint="FIFO waitlist entries" />
        <StatCard icon={Armchair} label="Remaining seats" loading={loading} value={fmtNum(m?.seats.remaining)}
          hint={m ? `${fmtNum(m.seats.capacity)} seats in ${m.seats.courses} courses` : null} />
        <StatCard icon={XCircle} label="Failed requests" loading={loading} value={fmtNum(m?.requests.failed)}
          hint="Gave up after max attempts" />
        <StatCard icon={Gauge} label="Avg processing time" loading={loading}
          value={fmtMs(m?.requests.avg_processing_ms)} hint="Dispatch → commit, per request" />
        <StatCard icon={Hourglass} label="Avg queue wait" loading={loading} value={fmtMs(m?.requests.avg_queue_wait_ms)}
          hint={m ? `max ${fmtMs(m.requests.max_queue_wait_ms)}` : null} />
        <StatCard icon={Cpu} label="Live queue / workers" loading={loading}
          value={live ? `${live.scheduler.queue_size}` : '—'} unit="queued"
          hint={live ? `${live.workers.active}/${live.workers.size} workers busy · ${MODE_LABEL[live.scheduler.mode]}${live.scheduler.paused ? ' (paused)' : ''}` : null} />
      </div>

      <div className="grid grid-sidebar">
        <Card title="Course capacity" icon={Armchair} subtitle="Confirmed seats vs capacity; waitlist size per course">
          {courses.error && !courses.data && <ErrorState error={courses.error} />}
          {!courses.data && !courses.error && <LoadingState />}
          {courses.data && courses.data.courses.length === 0 && <EmptyState title="No courses" />}
          {courses.data && (
            <div className="grid grid-2" style={{ gap: '14px 28px' }}>
              {courses.data.courses.map((c) => (
                <div key={c.course_id}>
                  <div className="row-between" style={{ marginBottom: 6 }}>
                    <span><b>{c.course_id}</b> <span className="muted small">{c.course_name}</span></span>
                    {!c.consistent && <span className="badge critical"><AlertOctagon size={12} />inconsistent</span>}
                  </div>
                  <CapacityBar confirmed={c.confirmed_actual} capacity={c.capacity} waitlisted={c.waitlisted} />
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Correctness invariants" icon={ListChecks} subtitle="Checked directly in MySQL">
          {checks.error && !checks.data && <ErrorState error={checks.error} />}
          {!checks.data && !checks.error && <LoadingState />}
          {checks.data && (
            <div className="stack">
              {checks.data.checks.map((c) => (
                <div key={c.name} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}>
                  {c.passed
                    ? <CheckCircle2 size={17} color="#3fcf3f" style={{ flex: 'none', marginTop: 1 }} />
                    : <XCircle size={17} color="#f06a6a" style={{ flex: 'none', marginTop: 1 }} />}
                  <div>
                    <div>{c.name}</div>
                    <div className="small muted">{c.passed ? 'Pass' : `Fail: ${JSON.stringify(c.details).slice(0, 120)}`}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Completed requests per second" icon={Activity} subtitle="Last 2 minutes, from completed_at in MySQL">
          {throughput.error && !throughput.data && <ErrorState error={throughput.error} />}
          {!throughput.data && !throughput.error && <LoadingState />}
          {throughput.data && (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={throughput.data.series} margin={{ top: 8, right: 8, left: -12, bottom: 0 }} barCategoryGap={1}>
                <CartesianGrid vertical={false} stroke={CHART.grid} />
                <XAxis dataKey="offset_s" tick={{ fill: CHART.tick, fontSize: 11 }} axisLine={{ stroke: CHART.axis }}
                  tickLine={false} interval={19} tickFormatter={(v) => (v === 0 ? 'now' : `${v}s`)} />
                <YAxis allowDecimals={false} tick={{ fill: CHART.tick, fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip cursor={{ fill: CHART.cursor }}
                  content={<ChartTooltip labelFormatter={(v) => (v === 0 ? 'Now' : `${-v} s ago`)} valueFormatter={(v) => `${v} req`} />} />
                <Bar dataKey="completed" name="Completed" fill={CHART.series1} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Recent events" icon={Activity} subtitle="Latest rows in registration_events" flush>
          {events.error && !events.data && <div style={{ padding: '0 18px 14px' }}><ErrorState error={events.error} /></div>}
          {!events.data && !events.error && <LoadingState />}
          {events.data && <div className="list-scroll"><EventList events={events.data.events} /></div>}
        </Card>
      </div>
    </>
  );
}
