import { AlertTriangle, CheckCircle2, FlaskConical, Play, ShieldAlert, ShieldCheck, Table2, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Alert, Button, Card, EmptyState, Field, PageHeader } from '../components/ui';
import { useHealth } from '../hooks/HealthContext';
import { api } from '../services/api';
import { fmtDateTime, fmtNum } from '../utils/format';

const DEFAULTS = {
  capacity: 10, students: 500, client_threads: 50, duplicate_ratio: 0, race_window_ms: 20, force_interleaving: false,
};

const ROWS = [
  ['Attempted registrations', 'attempts'],
  ['Distinct students', 'students'],
  ['Course capacity', 'capacity'],
  ['Confirmed seats (rows in DB)', 'confirmed'],
  ['Waitlisted students', 'waitlisted'],
  ['Rejected as already registered', 'already_registered'],
  ['Duplicate registrations', 'duplicate_registrations'],
  ['Overbooked seats', 'overbooked'],
  ['Stored confirmed_count', 'stored_confirmed_count'],
  ['Lost counter updates', 'lost_updates'],
  ['Failed / errors', 'failed'],
  ['Elapsed (ms)', 'elapsed_ms'],
];

export default function RaceLabPage() {
  const { health } = useHealth();
  const [cfg, setCfg] = useState(DEFAULTS);
  const [results, setResults] = useState({ UNSAFE: null, SAFE: null });
  const [running, setRunning] = useState(null);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    api.raceHistory().then((h) => {
      setHistory(h.history);
      const latest = (type) => h.history.find((x) => x.type === type)?.result || null;
      setResults({ UNSAFE: latest('RACE_UNSAFE'), SAFE: latest('RACE_SAFE') });
    }).catch(() => {});
  }, []);

  const set = (key, numeric = true) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : numeric ? Number(e.target.value) : e.target.value;
    setCfg((c) => ({ ...c, [key]: v }));
  };

  async function run(mode) {
    setRunning(mode);
    setError(null);
    try {
      const r = mode === 'UNSAFE' ? await api.raceUnsafe(cfg) : await api.raceSafe(cfg);
      setResults((prev) => ({ ...prev, [mode]: r }));
      api.raceHistory().then((h) => setHistory(h.history)).catch(() => {});
    } catch (e) {
      setError(e);
    } finally {
      setRunning(null);
    }
  }

  const disabled = !health?.demo_mode || running !== null;
  return (
    <>
      <PageHeader
        title="Race Condition Lab"
        description="The same burst of simultaneous registrations, run once through a deliberately broken check-then-act implementation and once through the real RaceSafe pipeline."
      />
      <Alert tone="warning" title="Unsafe mode is an isolated demonstration">
        The unsafe path writes only to the separate tables <span className="mono">lab_courses</span> and{' '}
        <span className="mono">lab_registrations</span> (no constraints, no locks). It is never used by the real API,
        and it is disabled unless RACESAFE_ENV=development. The safe run uses its own experiment course
        <span className="mono"> LAB-SAFE</span>, never a catalog course.
      </Alert>

      <Card title="Burst settings" icon={FlaskConical}>
        <div className="grid grid-4" style={{ gap: 14 }}>
          <Field label="Course capacity"><input className="input" type="number" min={1} max={500} value={cfg.capacity} onChange={set('capacity')} /></Field>
          <Field label="Distinct students"><input className="input" type="number" min={2} max={600} value={cfg.students} onChange={set('students')} /></Field>
          <Field label="Concurrent clients" hint="threads released together by a barrier"><input className="input" type="number" min={2} max={64} value={cfg.client_threads} onChange={set('client_threads')} /></Field>
          <Field label="Double-click share" value={`${Math.round(cfg.duplicate_ratio * 100)}%`} hint="students who submit twice">
            <input type="range" min={0} max={0.5} step={0.05} value={cfg.duplicate_ratio} onChange={set('duplicate_ratio')} />
          </Field>
          <Field label="Unsafe race window (ms)" hint="delay between the seat check and the write">
            <input className="input" type="number" min={0} max={200} value={cfg.race_window_ms} onChange={set('race_window_ms')} />
          </Field>
          <div className="field" style={{ gridColumn: 'span 3', justifyContent: 'center' }}>
            <label className="check">
              <input type="checkbox" checked={cfg.force_interleaving} onChange={set('force_interleaving', false)} />
              Force worst-case interleaving (unsafe only): every client finishes its first read before anyone writes.
              Without this, the unsafe outcome depends on the real thread schedule and can vary between runs.
            </label>
          </div>
        </div>
      </Card>

      {error && <Alert tone="critical" title="Run failed">{error.message}</Alert>}

      <div className="grid grid-2">
        <ResultCard mode="UNSAFE" result={results.UNSAFE} running={running === 'UNSAFE'} disabled={disabled} onRun={() => run('UNSAFE')} />
        <ResultCard mode="SAFE" result={results.SAFE} running={running === 'SAFE'} disabled={disabled} onRun={() => run('SAFE')} />
      </div>

      <Card title="Side-by-side comparison" icon={Table2} subtitle="Latest run of each mode (settings may differ — check the first rows)" flush>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Measure</th><th className="num">Unsafe check-then-act</th><th className="num">RaceSafe</th></tr></thead>
            <tbody>
              {ROWS.map(([label, key]) => (
                <tr key={key}>
                  <td>{label}</td>
                  {['UNSAFE', 'SAFE'].map((m) => {
                    const v = results[m]?.[key];
                    const bad = ['overbooked', 'duplicate_registrations', 'lost_updates'].includes(key) && v > 0;
                    return <td key={m} className="num" style={bad ? { color: '#ff9b9b', fontWeight: 700 } : undefined}>{v === undefined ? '—' : fmtNum(v, key === 'elapsed_ms' ? 0 : 0)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid-2">
        <Card title="Why the unsafe version breaks" icon={ShieldAlert}>
          <div className="code">{`# every statement auto-commits, nothing is locked
`}<span className="bad">{`row = SELECT confirmed_count, capacity FROM lab_courses`}</span>{`
if row.confirmed_count < row.capacity:     `}<span className="cm"># check</span>{`
    `}<span className="cm"># ← other clients read the SAME old count here</span>{`
    INSERT INTO lab_registrations (...)    `}<span className="cm"># act</span>{`
    `}<span className="bad">{`UPDATE lab_courses SET confirmed_count = row.confirmed_count + 1`}</span>{`
`}</div>
          <p className="text-2 small" style={{ marginTop: 10 }}>
            The check and the update are separate steps with no mutual exclusion. Many clients read “9 of 10 taken”, all
            decide a seat is free, all insert — overbooking. Each then writes its own stale <i>count + 1</i>, so updates are
            lost and the counter disagrees with the rows. No UNIQUE constraint means double-clicks become duplicate rows.
          </p>
        </Card>
        <Card title="What RaceSafe does instead" icon={ShieldCheck}>
          <div className="code">{`BEGIN
  SELECT ... FROM registration_requests WHERE request_id = ? FOR UPDATE
  `}<span className="ok">{`SELECT capacity, confirmed_count FROM courses WHERE course_id = ? FOR UPDATE`}</span>{`
  SELECT ... FROM registrations WHERE student_id = ? AND course_id = ? FOR UPDATE
  INSERT INTO registrations (...)   `}<span className="cm">-- UNIQUE (student_id, course_id)</span>{`
  `}<span className="ok">{`UPDATE courses SET confirmed_count = confirmed_count + 1`}</span>{`  `}<span className="cm">-- CHECK ≤ capacity</span>{`
COMMIT`}</div>
          <p className="text-2 small" style={{ marginTop: 10 }}>
            The course row lock makes check-and-take one critical section per course: the second worker waits until the
            first commits, then sees the real count. Other courses use other rows, so they are not blocked. The UNIQUE and
            CHECK constraints are a backstop, and the request-row lock makes retries idempotent.
          </p>
        </Card>
      </div>

      {history.length > 0 && (
        <Card title="Run history" icon={Table2} flush>
          <div className="table-wrap list-scroll" style={{ maxHeight: 280 }}>
            <table className="table">
              <thead><tr><th>Finished</th><th>Mode</th><th className="num">Capacity</th><th className="num">Attempts</th><th className="num">Confirmed</th><th className="num">Overbooked</th><th className="num">Duplicates</th></tr></thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.experiment_id}>
                    <td className="small">{fmtDateTime(h.finished_at)}</td>
                    <td>{h.type === 'RACE_UNSAFE' ? 'Unsafe' : 'RaceSafe'}</td>
                    <td className="num">{h.result?.capacity}</td>
                    <td className="num">{h.result?.attempts}</td>
                    <td className="num">{h.result?.confirmed}</td>
                    <td className="num">{h.result?.overbooked}</td>
                    <td className="num">{h.result?.duplicate_registrations}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}

function ResultCard({ mode, result, running, disabled, onRun }) {
  const unsafe = mode === 'UNSAFE';
  return (
    <Card
      title={unsafe ? 'Unsafe check-then-act' : 'RaceSafe (locked transaction)'}
      icon={unsafe ? ShieldAlert : ShieldCheck}
      className={unsafe ? 'danger' : 'accent'}
      subtitle={unsafe ? 'Isolated lab tables · demo only' : 'Real pipeline: queue → worker → MySQL'}
      actions={<Button variant={unsafe ? 'danger' : 'primary'} icon={Play} loading={running} disabled={disabled} onClick={onRun}>
        {unsafe ? 'Run unsafe' : 'Run RaceSafe'}</Button>}
    >
      {!result ? (
        <EmptyState icon={unsafe ? AlertTriangle : ShieldCheck} title="Not run yet">Press the button to fire the burst.</EmptyState>
      ) : (
        <div className="stack">
          <div className="row" style={{ alignItems: 'flex-end', gap: 18 }}>
            <div>
              <div className="small muted">Confirmed seats</div>
              <div className="hero-number">{result.confirmed}<span className="muted" style={{ fontSize: 20 }}> / {result.capacity}</span></div>
            </div>
            <div>
              {result.correct ? (
                <span className="badge good"><CheckCircle2 size={13} />No overbooking, no duplicates</span>
              ) : (
                <span className="badge critical"><XCircle size={13} />
                  {result.overbooked > 0 ? `Overbooked by ${result.overbooked}` : 'Inconsistent'}
                  {result.duplicate_registrations > 0 ? ` · ${result.duplicate_registrations} duplicates` : ''}
                </span>
              )}
            </div>
          </div>
          <dl className="kv">
            <dt>Waitlisted</dt><dd>{fmtNum(result.waitlisted)}</dd>
            <dt>Already registered (rejected)</dt><dd>{fmtNum(result.already_registered)}</dd>
            <dt>Duplicate registrations</dt><dd>{fmtNum(result.duplicate_registrations)}</dd>
            <dt>Stored counter vs real rows</dt><dd>{result.stored_confirmed_count} vs {result.confirmed}</dd>
            <dt>{result.attempts} attempts from {result.client_threads} clients</dt><dd>{fmtNum(result.elapsed_ms)} ms</dd>
          </dl>
          {result.config?.force_interleaving && unsafe && <div className="small muted">Ran with forced worst-case interleaving.</div>}
        </div>
      )}
    </Card>
  );
}
