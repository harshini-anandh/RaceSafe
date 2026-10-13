import { BarChart3, ListOrdered, Play, Scale, SlidersHorizontal, Table2, Timer } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import ChartTooltip from '../components/ChartTooltip';
import {
  Alert, Button, Disclosure, Empty, Field, Loading, PageHeader, Panel, Progress, QueryView, Segmented, StatusBadge,
} from '../components/ui';
import { useConnection } from '../hooks/ConnectionContext';
import { useToast } from '../hooks/Toasts';
import { usePolling } from '../hooks/usePolling';
import { api } from '../services/api';
import { fmtDateTime, fmtMs, fmtNum, MODE_LABEL, shortId } from '../utils/format';
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

/** Relative change, worded from the measured numbers only. */
function changeText(before, after, lowerIsBetter = true) {
  if (before == null || after == null || before === 0) return '—';
  const p = ((after - before) / before) * 100;
  if (Math.abs(p) < 2) return `${p > 0 ? '+' : ''}${p.toFixed(1)}% (about the same)`;
  const better = lowerIsBetter ? p < 0 : p > 0;
  return `${p > 0 ? '+' : ''}${p.toFixed(1)}% (${better ? 'better' : 'worse'})`;
}

export default function FairQueuePage() {
  const { demoMode } = useConnection();
  const toast = useToast();
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [groupId, setGroupId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [pollFast, setPollFast] = useState(false);

  const history = usePolling(() => api.experiments(), 0, [historyKey]);
  const group = usePolling(() => (groupId ? api.experiment(groupId) : Promise.resolve(null)), pollFast ? 700 : 0, [groupId, pollFast]);
  const g = group.data && group.data.group_id === groupId ? group.data : null;
  const running = g?.status === 'RUNNING';
  const single = Number(config.course_count) === 1;

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
    setConfig((c) => ({ ...c, course_count: count, weights: Array.from({ length: Math.max(count, 0) }, (_, i) => c.weights[i] ?? 1) }));
  };

  async function run() {
    if (starting || running) return;
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
      if (window.innerWidth < 1180) {
        setTimeout(() => document.getElementById('fq-run')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
      }
    } catch (e) {
      setStartError(e);
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      <PageHeader
        description="Run the same burst of requests through Global FIFO and FairQueue, then compare what MySQL recorded." />

      <div className="grid split-left">
        <div className="sticky-col">
          <Panel icon={SlidersHorizontal} title="Configuration" description="Workload, scheduler and execution">
            <div className="stack-lg">
              <fieldset>
                <legend className="fieldset-title">Workload</legend>
                <div className="stack">
                  <div className="grid cols-3" style={{ gap: 8 }}>
                    <Field label="Requests" htmlFor="fq-req"><input id="fq-req" className="input" type="number" min={20} max={2000} value={config.total_requests} onChange={set('total_requests')} /></Field>
                    <Field label="Courses" htmlFor="fq-courses"><input id="fq-courses" className="input" type="number" min={1} max={8} value={config.course_count} onChange={(e) => setCourseCount(e.target.value)} /></Field>
                    <Field label="Seats each" htmlFor="fq-cap"><input id="fq-cap" className="input" type="number" min={1} max={600} value={config.capacity} onChange={set('capacity')} /></Field>
                  </div>
                  {!single && (
                    <Field label="Demand across courses">
                      <Segmented block label="Demand" value={config.distribution} onChange={set('distribution')} options={[
                        { value: 'skewed', label: 'Hot course' }, { value: 'uniform', label: 'Uniform' }, { value: 'custom', label: 'Custom' }]} />
                    </Field>
                  )}
                  {single && (
                    <div className="small muted">One course: every request goes to XP-1 (up to 600, one per student). There is no cross-course comparison and Jain's index is not defined.</div>
                  )}
                  {!single && config.distribution === 'skewed' && (
                    <Field label="Hot course share" value={`${Math.round(config.hot_share * 100)}%`} htmlFor="fq-hot">
                      <input id="fq-hot" type="range" min={0.2} max={0.95} step={0.05} value={config.hot_share} onChange={set('hot_share')} />
                    </Field>
                  )}
                  {!single && config.distribution === 'custom' && (
                    <Field label="Relative weight per course" hint="XP-1 … XP-n">
                      <div className="row" style={{ gap: 6 }}>
                        {config.weights.map((w, i) => (
                          <input key={i} aria-label={`Weight XP-${i + 1}`} className="input" type="number" min={0.1} step={0.5} value={w} style={{ width: 64 }}
                            onChange={(e) => setConfig((c) => ({ ...c, weights: c.weights.map((x, j) => (j === i ? e.target.value : x)) }))} />
                        ))}
                      </div>
                    </Field>
                  )}
                </div>
              </fieldset>

              <fieldset>
                <legend className="fieldset-title">Scheduler</legend>
                <Segmented block label="Scheduler" value={config.mode} onChange={set('mode')} options={[
                  { value: 'BOTH', label: 'Both' }, { value: 'GLOBAL_FIFO', label: 'Global FIFO' },
                  { value: 'FAIR_QUEUE', label: 'FairQueue' }]} />
                <div className="field-hint" style={{ marginTop: 6 }}>“Both” runs the identical workload once per scheduler and compares them.</div>
              </fieldset>

              <fieldset>
                <legend className="fieldset-title">Execution</legend>
                <div className="grid cols-3" style={{ gap: 8 }}>
                  <Field label="Work (ms)" htmlFor="fq-work"><input id="fq-work" className="input" type="number" min={0} max={100} value={config.work_ms} onChange={set('work_ms')} /></Field>
                  <Field label="Repeats" htmlFor="fq-rep"><input id="fq-rep" className="input" type="number" min={1} max={5} value={config.repeats} onChange={set('repeats')} /></Field>
                  <Field label="Seed" htmlFor="fq-seed"><input id="fq-seed" className="input" type="number" min={0} value={config.seed} onChange={set('seed')} /></Field>
                </div>
                <div className="field-hint" style={{ marginTop: 6 }}>
                  Work is a sleep inside each transaction (holding the course lock). Same seed = same students and arrival order.
                </div>
              </fieldset>

              <Button variant="primary" icon={Play} loading={starting || running} disabled={!demoMode} onClick={run}>
                {running ? 'Running…' : 'Run experiment'}
              </Button>
              {!demoMode && <div className="small muted">Experiments require RACESAFE_ENV=development.</div>}
              {startError && <Alert tone="danger">{startError.message}</Alert>}
            </div>
          </Panel>
        </div>

        <div className="stack-lg">
          {!groupId && <Panel><Empty icon={Scale} title="No experiment yet">Choose a workload on the left and press Run experiment.</Empty></Panel>}
          {groupId && !g && (group.error ? <QueryView query={group}>{() => null}</QueryView> : <Panel><Loading rows={4} /></Panel>)}
          {g && (
            <Results g={g} history={history.data?.groups || []} onSelect={(id) => { setGroupId(id); setPollFast(false); }}
              onUseSettings={(c) => {
                setConfig((cur) => ({
                  ...cur, mode: c.mode ?? cur.mode, total_requests: c.total_requests ?? cur.total_requests,
                  course_count: c.course_count ?? cur.course_count, distribution: c.distribution ?? cur.distribution,
                  hot_share: c.hot_share ?? cur.hot_share, capacity: c.capacity ?? cur.capacity, work_ms: c.work_ms ?? cur.work_ms,
                  repeats: c.repeats ?? cur.repeats, seed: c.seed ?? cur.seed,
                  weights: c.weights || Array.from({ length: c.course_count || cur.course_count }, (_, k) => cur.weights[k] ?? 1),
                }));
                toast({ tone: 'info', title: 'Settings loaded', text: 'The configuration now matches this run. Press Run experiment to repeat it.' });
              }} />
          )}
        </div>
      </div>
    </>
  );
}

function Results({ g, history, onSelect, onUseSettings }) {
  const cmp = g.comparison?.by_mode || {};
  const modes = MODES.filter((m) => cmp[m]);
  const courses = g.courses || g.config?.courses || [];
  const both = modes.length === 2;

  const rows = useMemo(() => {
    if (!modes.length) return [];
    return cmp[modes[0]].per_course.map((c) => {
      const row = { course: c.course_id, label: c.label, requests: c.requests };
      modes.forEach((m) => {
        const pc = cmp[m].per_course.find((x) => x.course_id === c.course_id) || {};
        Object.assign(row, {
          [`${m}_avg`]: pc.avg_wait_ms, [`${m}_max`]: pc.max_wait_ms, [`${m}_turn`]: pc.avg_turnaround_ms,
          [`${m}_conf`]: pc.confirmed, [`${m}_wait`]: pc.waitlisted, [`${m}_fail`]: pc.failed,
        });
      });
      return row;
    });
  }, [g]); // eslint-disable-line react-hooks/exhaustive-deps

  const quietAvg = (m) => mean(rows.filter((r) => r.label !== 'hot').map((r) => r[`${m}_avg`]));
  const hotAvg = (m) => mean(rows.filter((r) => r.label === 'hot').map((r) => r[`${m}_avg`]));
  const hasHot = rows.some((r) => r.label === 'hot');
  const started = g.started_at || g.runs?.[0]?.created_at;

  return (
    <>
      <Panel
        id="fq-run"
        icon={Timer}
        title={<span className="row" style={{ gap: 10 }}>Run {shortId(g.group_id)}
          <StatusBadge status={g.status === 'RUNNING' ? 'RUNNING' : g.status === 'COMPLETED' ? 'COMPLETED' : 'FAILED'}
            label={g.status === 'RUNNING' ? 'Running' : g.status === 'COMPLETED' ? 'Completed' : g.status} /></span>}
        description={`Started ${fmtDateTime(started)}`}
        actions={
          <>
            <label htmlFor="fq-history" className="sr-only">Show a previous run</label>
            <select id="fq-history" className="select" style={{ width: 'auto', maxWidth: 280, height: 32 }} value={g.group_id}
              onChange={(e) => onSelect(e.target.value)}>
              {!history.some((h) => h.group_id === g.group_id) && <option value={g.group_id}>Current run</option>}
              {history.map((h) => (
                <option key={h.group_id} value={h.group_id}>
                  {fmtDateTime(h.created_at)} · {h.modes.map((m) => MODE_LABEL[m] || m).join(' vs ')}{h.failed ? ' · failed' : ''}
                </option>
              ))}
            </select>
            {g.status !== 'RUNNING' && g.config && (
              <Button size="sm" onClick={() => onUseSettings(g.config)} title="Copy this run's settings into the configuration">Use these settings</Button>
            )}
          </>
        }>
        <div className="facts" style={{ marginBottom: g.status === 'RUNNING' || g.error ? 12 : 0 }}>
          <span className="fact"><b>{g.config?.total_requests}</b> requests</span>
          <span className="fact">
            <b>{courses.length}</b> course{courses.length === 1 ? '' : 's'}
            {courses.length > 0 && <>&nbsp;· {courses.map((c) => `${c.course_id} ${c.requests}`).join(' / ')}</>}
          </span>
          <span className="fact"><b>{g.config?.capacity}</b> seats each</span>
          <span className="fact"><b>{g.config?.work_ms}</b> ms work</span>
          <span className="fact">{g.config?.repeats > 1 ? <><b>{g.config.repeats}</b> repeats · </> : null}seed <b>{g.config?.seed}</b></span>
        </div>
        {g.status === 'RUNNING' && (
          <div className="stack" style={{ gap: 6 }}>
            <div className="row-between small">
              <span>Run {Math.min((g.runs_done || 0) + 1, g.runs_total)} of {g.runs_total}: <b>{MODE_LABEL[g.current_mode] || '…'}</b></span>
              <span className="mono">{g.current_done}/{g.current_total} requests</span>
            </div>
            <Progress value={(g.runs_done || 0) * (g.current_total || 1) + (g.current_done || 0)} max={(g.runs_total || 1) * (g.current_total || 1)} />
          </div>
        )}
        {g.error && <Alert tone="danger" title="Experiment failed">{g.error}</Alert>}
      </Panel>

      {modes.length > 0 && (
        <>
          {rows.length === 1 && both && (
            <Alert tone="info" title="Single course: nothing to share">
              FairQueue keeps one request of the course in progress, so other workers stay idle; Global FIFO lets every
              worker do the work outside the row lock in parallel. FIFO can therefore finish sooner here. Seat results are
              identical; Jain's index needs at least two courses.
            </Alert>
          )}
          {both && hasHot && (
            <Alert tone="info" title="What was measured">
              Non-hot courses waited <b>{fmtMs(quietAvg('GLOBAL_FIFO'))}</b> on average with Global FIFO and{' '}
              <b>{fmtMs(quietAvg('FAIR_QUEUE'))}</b> with FairQueue: {changeText(quietAvg('GLOBAL_FIFO'), quietAvg('FAIR_QUEUE'))}.
              Hot course: {fmtMs(hotAvg('GLOBAL_FIFO'))} vs {fmtMs(hotAvg('FAIR_QUEUE'))}. Total time{' '}
              {fmtMs(cmp.GLOBAL_FIFO.completion_time_ms)} vs {fmtMs(cmp.FAIR_QUEUE.completion_time_ms)}. The hot course
              shares one row lock, so FairQueue cannot make it faster; it stops it from occupying every worker.
            </Alert>
          )}

          <Panel icon={Table2} title="Comparison" description={`Mean of ${cmp[modes[0]].runs} run(s) per mode`} flush>
            <div className="table-scroll">
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
                    ['Throughput (requests/s)', 'throughput_rps', (v) => fmtNum(v, 1), false],
                    ['Average wait', 'avg_wait_ms', fmtMs, true],
                    ['Maximum wait', 'max_wait_ms', fmtMs, true],
                    ["Jain's fairness index", 'jain_index', (v) => fmtNum(v, 3), false],
                    ['Peak courses in flight', 'peak_courses_in_flight', (v) => fmtNum(v, 1), null],
                    ['Confirmed / waitlisted / failed', null, null, null],
                  ].map(([label, key, fmt, lower]) => (
                    <tr key={label}>
                      <td>{label}</td>
                      {modes.map((m) => (
                        <td key={m} className="num">
                          {key ? fmt(cmp[m][key]) : `${fmtNum(cmp[m].confirmed)} / ${fmtNum(cmp[m].waitlisted)} / ${fmtNum(cmp[m].failed)}`}
                        </td>
                      ))}
                      {both && <td className="num small text-2">{key && lower !== null ? changeText(cmp.GLOBAL_FIFO[key], cmp.FAIR_QUEUE[key], lower) : ''}</td>}
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
          </Panel>

          <Panel icon={BarChart3} title="Waiting time per course" description="Wait = dispatch time − burst release time. Lower is better."
            actions={<div className="legend">{modes.map((m) => <span key={m}><i style={{ background: MODE_COLOR[m] }} />{MODE_LABEL[m]}</span>)}</div>}>
            <div className="grid cols-2">
              <WaitChart title="Average" rows={rows} modes={modes} suffix="_avg" />
              <WaitChart title="Maximum" rows={rows} modes={modes} suffix="_max" />
            </div>
          </Panel>

          <Panel icon={ListOrdered} title="Per-course results" flush>
            <div className="table-scroll">
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
                      <td><b>{r.course}</b>{r.label === 'hot' && <span className="sub">hot course</span>}</td>
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
            <Disclosure summary="How waits and fairness are measured">
              <p className="small text-2" style={{ maxWidth: 760 }}>
                All requests are persisted while dispatch is paused, then released at one instant. Wait is dispatch time minus
                that release time; turnaround is commit time minus release time. Jain's index J = (Σx)² / (n·Σx²) uses the number of
                requests each course had dispatched while every course still had requests waiting: 1 means equal service, 1/n
                means one course took every worker. Timings come from MySQL timestamps of this run only; repeat runs or change the
                seed to see the run-to-run noise.
              </p>
            </Disclosure>
          </Panel>
        </>
      )}
    </>
  );
}

function WaitChart({ title, rows, modes, suffix }) {
  return (
    <figure style={{ margin: 0 }}>
      <figcaption className="small text-2" style={{ marginBottom: 4 }}>{title}</figcaption>
      <ResponsiveContainer width="100%" height={230}>
        <BarChart data={rows} margin={{ top: 8, right: 4, left: 0, bottom: 0 }} barGap={3} barCategoryGap="30%">
          <CartesianGrid vertical={false} stroke={CHART.grid} />
          <XAxis dataKey="course" tick={{ fill: CHART.tick, fontSize: 12 }} axisLine={false} tickLine={false}
            tickFormatter={(v) => (rows.find((x) => x.course === v)?.label === 'hot' ? `${v} (hot)` : v)} />
          <YAxis tick={{ fill: CHART.tick, fontSize: 11 }} axisLine={false} tickLine={false} width={52}
            tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`)} />
          <Tooltip cursor={{ fill: CHART.cursor }} content={<ChartTooltip valueFormatter={(v) => fmtMs(v)} />} />
          {modes.map((m) => (
            <Bar key={m} dataKey={`${m}${suffix}`} name={MODE_LABEL[m]} fill={MODE_COLOR[m]} radius={[5, 5, 0, 0]}
              maxBarSize={28} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </figure>
  );
}
