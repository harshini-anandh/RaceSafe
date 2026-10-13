import { BarChart3, FlaskConical, History, Play, Scale, Table2, Timer } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import ChartTooltip from '../components/ChartTooltip';
import {
  Alert, Button, Card, EmptyState, ErrorState, Field, LoadingState, PageHeader, ProgressBar, Segmented, StatusBadge,
} from '../components/ui';
import { useHealth } from '../hooks/HealthContext';
import { usePolling } from '../hooks/usePolling';
import { api } from '../services/api';
import { fmtDateTime, fmtMs, fmtNum, MODE_LABEL } from '../utils/format';
import { CHART, MODE_COLOR } from '../utils/theme';

const MODES = ['GLOBAL_FIFO', 'FAIR_QUEUE'];
const DEFAULT_CONFIG = {
  mode: 'BOTH', total_requests: 500, course_count: 5, distribution: 'skewed', hot_share: 0.7,
  weights: [5, 1, 1, 1, 1], capacity: 50, work_ms: 5, repeats: 1, seed: 42,
};

const mean = (xs) => {
  const v = xs.filter((x) => x !== null && x !== undefined);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

function changeText(before, after, lowerIsBetter = true) {
  if (before == null || after == null || before === 0) return '—';
  const p = ((after - before) / before) * 100;
  const better = lowerIsBetter ? p < 0 : p > 0;
  return `${p > 0 ? '+' : ''}${p.toFixed(1)}% ${Math.abs(p) < 2 ? '(about the same)' : better ? '(better)' : '(worse)'}`;
}

export default function FairQueuePage() {
  const { health } = useHealth();
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [groupId, setGroupId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const [historyKey, setHistoryKey] = useState(0);

  const history = usePolling(() => api.experiments(), 0, [historyKey]);
  const [pollFast, setPollFast] = useState(false);
  const group = usePolling(() => (groupId ? api.experiment(groupId) : Promise.resolve(null)), pollFast ? 700 : 0, [groupId, pollFast]);
  const g = group.data && group.data.group_id === groupId ? group.data : null;
  const running = g?.status === 'RUNNING';

  useEffect(() => {
    if (g && g.status !== 'RUNNING' && pollFast) {
      setPollFast(false);
      setHistoryKey((k) => k + 1);
    }
  }, [g, pollFast]);

  useEffect(() => {
    if (!groupId && history.data?.groups?.length) setGroupId(history.data.groups[0].group_id);
  }, [history.data, groupId]);

  const set = (key) => (e) => {
    const v = e?.target ? e.target.value : e;
    setConfig((c) => ({ ...c, [key]: v }));
  };
  const setCourseCount = (n) => {
    const count = Number(n);
    setConfig((c) => ({ ...c, course_count: count, weights: Array.from({ length: count }, (_, i) => c.weights[i] ?? 1) }));
  };

  async function run() {
    setStarting(true);
    setStartError(null);
    try {
      const body = {
        ...config,
        total_requests: Number(config.total_requests), course_count: Number(config.course_count),
        hot_share: Number(config.hot_share), capacity: Number(config.capacity), work_ms: Number(config.work_ms),
        repeats: Number(config.repeats), seed: Number(config.seed),
        weights: config.distribution === 'custom' ? config.weights.map(Number) : null,
      };
      const started = await api.startExperiment(body);
      setGroupId(started.group_id);
      setPollFast(true);
    } catch (e) {
      setStartError(e);
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      <PageHeader
        title="FairQueue vs Global FIFO"
        description="Runs the same burst of requests (same students, same shuffled arrival order) through both schedulers and measures waiting time, completion time, throughput and fairness from the committed rows in MySQL."
      />
      <div className="grid grid-sidebar-left">
        <div className="stack" style={{ gap: 16 }}>
          <Card title="Experiment setup" icon={FlaskConical}>
            <div className="stack">
              <Field label="Scheduler">
                <Segmented value={config.mode} onChange={set('mode')} options={[
                  { value: 'BOTH', label: 'Compare both' }, { value: 'GLOBAL_FIFO', label: 'Global FIFO' },
                  { value: 'FAIR_QUEUE', label: 'FairQueue' }]} />
              </Field>
              <div className="grid grid-2" style={{ gap: 12 }}>
                <Field label="Requests"><input className="input" type="number" min={20} max={2000} value={config.total_requests} onChange={set('total_requests')} /></Field>
                <Field label="Courses"><input className="input" type="number" min={2} max={8} value={config.course_count} onChange={(e) => setCourseCount(e.target.value)} /></Field>
                <Field label="Seats per course"><input className="input" type="number" min={1} max={600} value={config.capacity} onChange={set('capacity')} /></Field>
                <Field label="Repeats per mode"><input className="input" type="number" min={1} max={5} value={config.repeats} onChange={set('repeats')} /></Field>
              </div>
              <Field label="Course demand">
                <Segmented value={config.distribution} onChange={set('distribution')} options={[
                  { value: 'skewed', label: 'Skewed (one hot)' }, { value: 'uniform', label: 'Uniform' }, { value: 'custom', label: 'Custom' }]} />
              </Field>
              {config.distribution === 'skewed' && (
                <Field label="Share of requests for the hot course" value={`${Math.round(config.hot_share * 100)}%`}>
                  <input type="range" min={0.2} max={0.95} step={0.05} value={config.hot_share} onChange={set('hot_share')} />
                </Field>
              )}
              {config.distribution === 'custom' && (
                <Field label="Relative weight per course" hint="XP-1 … XP-n, e.g. 5,1,1,1,1">
                  <div className="row" style={{ gap: 6 }}>
                    {config.weights.map((w, i) => (
                      <input key={i} className="input" type="number" min={0.1} step={0.5} value={w} style={{ width: 62 }}
                        onChange={(e) => setConfig((c) => ({ ...c, weights: c.weights.map((x, j) => (j === i ? e.target.value : x)) }))} />
                    ))}
                  </div>
                </Field>
              )}
              <div className="grid grid-2" style={{ gap: 12 }}>
                <Field label="Simulated work (ms)" hint="sleep inside each transaction">
                  <input className="input" type="number" min={0} max={100} value={config.work_ms} onChange={set('work_ms')} />
                </Field>
                <Field label="Random seed" hint="same seed = same workload">
                  <input className="input" type="number" min={0} value={config.seed} onChange={set('seed')} />
                </Field>
              </div>
              <Button icon={Play} loading={starting || running} disabled={!health?.demo_mode} onClick={run}>
                {running ? 'Running…' : 'Run experiment'}
              </Button>
              {!health?.demo_mode && <div className="small muted">Experiments need RACESAFE_ENV=development.</div>}
              {startError && <Alert tone="critical">{startError.message}</Alert>}
            </div>
          </Card>

          <Card title="Previous runs" icon={History} flush>
            {history.error && !history.data && <div style={{ padding: '0 18px 14px' }}><ErrorState error={history.error} /></div>}
            {!history.data && !history.error && <LoadingState />}
            {history.data && history.data.groups.length === 0 && <EmptyState title="No experiments yet" />}
            {history.data && history.data.groups.length > 0 && (
              <div className="list-scroll" style={{ maxHeight: 260 }}>
                <table className="table">
                  <tbody>
                    {history.data.groups.map((h) => (
                      <tr key={h.group_id} className={`clickable ${h.group_id === groupId ? 'selected' : ''}`}
                        onClick={() => { setGroupId(h.group_id); setPollFast(false); }}>
                        <td className="small">{fmtDateTime(h.created_at)}</td>
                        <td className="small">{h.modes.map((m) => MODE_LABEL[m] || m).join(' vs ')}</td>
                        <td className="small num">{h.runs} run{h.runs > 1 ? 's' : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        <div className="stack" style={{ gap: 16 }}>
          {!groupId && (
            <Card><EmptyState icon={Scale} title="No experiment selected">Configure a workload and press “Run experiment”.</EmptyState></Card>
          )}
          {groupId && group.error && !g && <ErrorState error={group.error} onRetry={group.refresh} />}
          {groupId && !g && !group.error && <Card><LoadingState label="Loading experiment…" /></Card>}
          {g && <ExperimentResults g={g} />}
        </div>
      </div>
    </>
  );
}

function ExperimentResults({ g }) {
  const cmp = g.comparison?.by_mode || {};
  const modes = MODES.filter((m) => cmp[m]);
  const courses = g.courses || g.config?.courses || [];

  const rows = useMemo(() => {
    if (!modes.length) return [];
    return cmp[modes[0]].per_course.map((c) => {
      const row = { course: c.course_id, label: c.label, requests: c.requests };
      modes.forEach((m) => {
        const pc = cmp[m].per_course.find((x) => x.course_id === c.course_id) || {};
        row[`${m}_avg`] = pc.avg_wait_ms;
        row[`${m}_max`] = pc.max_wait_ms;
        row[`${m}_turn`] = pc.avg_turnaround_ms;
        row[`${m}_conf`] = pc.confirmed;
        row[`${m}_wait`] = pc.waitlisted;
        row[`${m}_fail`] = pc.failed;
      });
      return row;
    });
  }, [g]); // eslint-disable-line react-hooks/exhaustive-deps

  const quietAvg = (m) => mean(rows.filter((r) => r.label !== 'hot').map((r) => r[`${m}_avg`]));
  const hotAvg = (m) => mean(rows.filter((r) => r.label === 'hot').map((r) => r[`${m}_avg`]));
  const hasHot = rows.some((r) => r.label === 'hot');
  const both = modes.length === 2;

  return (
    <>
      <Card title="Run status" icon={Timer}
        actions={<StatusBadge status={g.status === 'RUNNING' ? 'PENDING' : g.status === 'COMPLETED' ? 'COMPLETED' : 'FAILED'}
          label={g.status === 'RUNNING' ? 'Running' : g.status === 'COMPLETED' ? 'Completed' : g.status} />}>
        <div className="stack">
          <div className="small text-2">
            {g.config?.total_requests} requests · {courses.length} courses ({courses.map((c) => `${c.course_id}: ${c.requests}`).join(', ')}) ·
            {' '}{g.config?.capacity} seats each · {g.config?.work_ms} ms simulated work · seed {g.config?.seed}
          </div>
          {g.status === 'RUNNING' && (
            <>
              <div className="row-between small">
                <span>Run {Math.min((g.runs_done || 0) + 1, g.runs_total)} of {g.runs_total}: <b>{MODE_LABEL[g.current_mode] || '…'}</b></span>
                <span className="mono">{g.current_done}/{g.current_total}</span>
              </div>
              <ProgressBar value={(g.runs_done || 0) * (g.current_total || 1) + (g.current_done || 0)}
                max={(g.runs_total || 1) * (g.current_total || 1)} />
            </>
          )}
          {g.error && <Alert tone="critical" title="Experiment failed">{g.error}</Alert>}
        </div>
      </Card>

      {modes.length > 0 && (
        <>
          {both && hasHot && (
            <Alert tone="info" title="What the measurements say">
              Average wait of the non-hot courses: <b>{fmtMs(quietAvg('GLOBAL_FIFO'))}</b> with Global FIFO vs{' '}
              <b>{fmtMs(quietAvg('FAIR_QUEUE'))}</b> with FairQueue ({changeText(quietAvg('GLOBAL_FIFO'), quietAvg('FAIR_QUEUE'))}).
              Hot course: {fmtMs(hotAvg('GLOBAL_FIFO'))} vs {fmtMs(hotAvg('FAIR_QUEUE'))}. Total completion time:{' '}
              {fmtMs(cmp.GLOBAL_FIFO.completion_time_ms)} vs {fmtMs(cmp.FAIR_QUEUE.completion_time_ms)}. The hot course
              shares one row lock, so FairQueue cannot make it faster — it stops it from blocking everyone else.
            </Alert>
          )}

          <Card title="Summary" icon={Table2} subtitle={`Averaged over ${cmp[modes[0]].runs} run(s) per mode`} flush>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    {modes.map((m) => <th key={m} className="num">{MODE_LABEL[m]}</th>)}
                    {both && <th className="num">FairQueue vs FIFO</th>}
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['Total completion time', 'completion_time_ms', fmtMs, true],
                    ['Throughput (req/s)', 'throughput_rps', (v) => fmtNum(v, 1), false],
                    ['Average wait (all requests)', 'avg_wait_ms', fmtMs, true],
                    ['Maximum wait', 'max_wait_ms', fmtMs, true],
                    ["Jain's fairness index", 'jain_index', (v) => fmtNum(v, 3), false],
                    ['Peak courses in flight', 'peak_courses_in_flight', (v) => fmtNum(v, 1), null],
                    ['Confirmed', 'confirmed', (v) => fmtNum(v), null],
                    ['Waitlisted', 'waitlisted', (v) => fmtNum(v), null],
                    ['Failed requests', 'failed', (v) => fmtNum(v), null],
                  ].map(([label, key, fmt, lower]) => (
                    <tr key={key}>
                      <td>{label}</td>
                      {modes.map((m) => <td key={m} className="num">{fmt(cmp[m][key])}</td>)}
                      {both && <td className="num small text-2">{lower === null ? '' : changeText(cmp.GLOBAL_FIFO[key], cmp.FAIR_QUEUE[key], lower)}</td>}
                    </tr>
                  ))}
                  {hasHot && (
                    <tr>
                      <td>Average wait, non-hot courses</td>
                      {modes.map((m) => <td key={m} className="num">{fmtMs(quietAvg(m))}</td>)}
                      {both && <td className="num small text-2">{changeText(quietAvg('GLOBAL_FIFO'), quietAvg('FAIR_QUEUE'))}</td>}
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid grid-2">
            <WaitChart title="Average wait per course" rows={rows} modes={modes} suffix="_avg" />
            <WaitChart title="Maximum wait per course" rows={rows} modes={modes} suffix="_max" />
          </div>

          <Card title="Per-course results" icon={BarChart3} subtitle="Wait = dispatch time − release time; turnaround = commit time − release time" flush>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Course</th><th className="num">Requests</th>
                    {modes.map((m) => <th key={`a${m}`} className="num">Avg wait · {MODE_LABEL[m]}</th>)}
                    {modes.map((m) => <th key={`x${m}`} className="num">Max wait · {MODE_LABEL[m]}</th>)}
                    {modes.map((m) => <th key={`t${m}`} className="num">Avg turnaround · {MODE_LABEL[m]}</th>)}
                    <th className="num">Confirmed / waitlisted / failed</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.course}>
                      <td><b>{r.course}</b> {r.label === 'hot' && <span className="badge serious">hot</span>}</td>
                      <td className="num">{r.requests}</td>
                      {modes.map((m) => <td key={`a${m}`} className="num">{fmtMs(r[`${m}_avg`])}</td>)}
                      {modes.map((m) => <td key={`x${m}`} className="num">{fmtMs(r[`${m}_max`])}</td>)}
                      {modes.map((m) => <td key={`t${m}`} className="num">{fmtMs(r[`${m}_turn`])}</td>)}
                      <td className="num small">{modes.map((m) => `${fmtNum(r[`${m}_conf`])}/${fmtNum(r[`${m}_wait`])}/${fmtNum(r[`${m}_fail`])}`).join('  ·  ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Alert tone="info" title="How fairness is measured">
            Jain's index J = (Σx)² / (n·Σx²) over x = the number of requests each course got dispatched while
            <i> every</i> course still had requests waiting (the contention window). J = 1 means every backlogged course got
            an equal share of the workers; J = 1/n means one course took them all. Timings come from MySQL timestamps of
            this run only; repeat runs (or a different seed) to see the noise on a single laptop.
          </Alert>
        </>
      )}
    </>
  );
}

function WaitChart({ title, rows, modes, suffix }) {
  return (
    <Card title={title} icon={BarChart3} subtitle="Lower is better · hover a bar for values">
      <div className="legend" style={{ marginBottom: 8 }}>
        {modes.map((m) => <span key={m}><i style={{ background: MODE_COLOR[m] }} />{MODE_LABEL[m]}</span>)}
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={rows} margin={{ top: 4, right: 8, left: 4, bottom: 0 }} barGap={2} barCategoryGap="28%">
          <CartesianGrid vertical={false} stroke={CHART.grid} />
          <XAxis dataKey="course" tick={{ fill: CHART.tick, fontSize: 12 }} axisLine={{ stroke: CHART.axis }} tickLine={false}
            tickFormatter={(v) => { const r = rows.find((x) => x.course === v); return r?.label === 'hot' ? `${v} (hot)` : v; }} />
          <YAxis tick={{ fill: CHART.tick, fontSize: 11 }} axisLine={false} tickLine={false} width={58}
            tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`)} />
          <Tooltip cursor={{ fill: CHART.cursor }} content={<ChartTooltip valueFormatter={(v) => fmtMs(v)} />} />
          <Legend wrapperStyle={{ display: 'none' }} />
          {modes.map((m) => (
            <Bar key={m} dataKey={`${m}${suffix}`} name={MODE_LABEL[m]} fill={MODE_COLOR[m]} radius={[4, 4, 0, 0]}
              maxBarSize={30} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </Card>
  );
}
