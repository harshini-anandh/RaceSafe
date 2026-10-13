import { CheckCircle2, Play, ShieldAlert, ShieldCheck, SlidersHorizontal, Table2, XCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Disclosure, Empty, Field, PageHeader, Panel, Progress } from '../components/ui';
import { useConnection } from '../hooks/ConnectionContext';
import { api } from '../services/api';
import { fmtDateTime, fmtNum } from '../utils/format';

const DEFAULTS = {
  capacity: 10, students: 500, client_threads: 50, duplicate_ratio: 0, race_window_ms: 20, force_interleaving: false,
};

const ROWS = [
  ['Attempted registrations', 'attempts'],
  ['Distinct students', 'students'],
  ['Concurrent clients', 'client_threads'],
  ['Course capacity', 'capacity'],
  ['Confirmed seats (rows)', 'confirmed'],
  ['Waitlisted', 'waitlisted'],
  ['Rejected as already registered', 'already_registered'],
  ['Duplicate registrations', 'duplicate_registrations', true],
  ['Overbooked seats', 'overbooked', true],
  ['Stored confirmed_count', 'stored_confirmed_count'],
  ['Lost counter updates', 'lost_updates', true],
  ['Failed / errors', 'failed'],
  ['Elapsed (ms)', 'elapsed_ms'],
];

export default function RaceLabPage() {
  const { demoMode, apiOnline, epoch } = useConnection();
  const [cfg, setCfg] = useState(DEFAULTS);
  const [results, setResults] = useState({ UNSAFE: null, SAFE: null });
  const [running, setRunning] = useState(null);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState(null);
  const lock = useRef(false);

  const loadHistory = () => api.raceHistory().then((h) => {
    setHistory(h.history);
    return h.history;
  });

  useEffect(() => {
    if (!apiOnline) return;
    loadHistory().then((h) => {
      const latest = (type) => h.find((x) => x.type === type)?.result || null;
      setResults((prev) => ({ UNSAFE: prev.UNSAFE || latest('RACE_UNSAFE'), SAFE: prev.SAFE || latest('RACE_SAFE') }));
    }).catch(() => {});
  }, [apiOnline, epoch]);

  const set = (key, numeric = true) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : numeric ? Number(e.target.value) : e.target.value;
    setCfg((c) => ({ ...c, [key]: v }));
  };

  async function run(mode) {
    if (lock.current) return;
    lock.current = true;
    setRunning(mode);
    setError(null);
    try {
      const r = mode === 'UNSAFE' ? await api.raceUnsafe(cfg) : await api.raceSafe(cfg);
      setResults((prev) => ({ ...prev, [mode]: r }));
      loadHistory().catch(() => {});
    } catch (e) {
      setError(e);
    } finally {
      lock.current = false;
      setRunning(null);
    }
  }

  const disabled = !demoMode || running !== null;
  return (
    <>
      <PageHeader
        description="Fire the same burst of simultaneous registrations at a deliberately broken check-then-act implementation and at the real RaceSafe pipeline." />

      <Alert tone="warning" title="Development demo only">
        The unsafe run writes only to separate lab tables (no constraints, no locks) and never touches real registrations.
        The safe run uses its own experiment course, LAB-SAFE.
      </Alert>

      <Panel icon={SlidersHorizontal} title="Burst settings" description="Applied to both runs">
        <div className="grid cols-4" style={{ gap: 12 }}>
          <Field label="Course capacity" htmlFor="rl-cap"><input id="rl-cap" className="input" type="number" min={1} max={500} value={cfg.capacity} onChange={set('capacity')} /></Field>
          <Field label="Distinct students" htmlFor="rl-stu"><input id="rl-stu" className="input" type="number" min={2} max={600} value={cfg.students} onChange={set('students')} /></Field>
          <Field label="Concurrent clients" htmlFor="rl-cli" hint="Released together by a barrier"><input id="rl-cli" className="input" type="number" min={2} max={64} value={cfg.client_threads} onChange={set('client_threads')} /></Field>
          <Field label="Double-click share" value={`${Math.round(cfg.duplicate_ratio * 100)}%`} htmlFor="rl-dup" hint="Students who submit twice">
            <input id="rl-dup" type="range" min={0} max={0.5} step={0.05} value={cfg.duplicate_ratio} onChange={set('duplicate_ratio')} />
          </Field>
        </div>
        <Disclosure summary="Unsafe-run timing options">
          <div className="grid cols-2" style={{ gap: 12, alignItems: 'end' }}>
            <Field label="Race window (ms)" htmlFor="rl-win" hint="Delay between the seat check and the write">
              <input id="rl-win" className="input" type="number" min={0} max={200} value={cfg.race_window_ms} onChange={set('race_window_ms')} />
            </Field>
            <label className="check">
              <input type="checkbox" checked={cfg.force_interleaving} onChange={set('force_interleaving', false)} />
              Force worst-case interleaving: every client reads before anyone writes (otherwise the outcome depends on the real thread schedule).
            </label>
          </div>
        </Disclosure>
      </Panel>

      {error && <Alert tone="danger" title="Run failed">{error.message}</Alert>}
      {!demoMode && apiOnline && <Alert tone="info">Race lab runs require RACESAFE_ENV=development.</Alert>}

      <div className="grid cols-2">
        <ResultPanel mode="UNSAFE" result={results.UNSAFE} running={running === 'UNSAFE'} disabled={disabled} onRun={() => run('UNSAFE')} />
        <ResultPanel mode="SAFE" result={results.SAFE} running={running === 'SAFE'} disabled={disabled} onRun={() => run('SAFE')} />
      </div>

      <Panel icon={Table2} title="Side by side" description="Latest result of each run" flush>
        {settingsDiffer(results) && (
          <div style={{ padding: '0 20px 12px' }}>
            <Alert tone="warning" title="These two results used different settings">
              Run both again with the same burst settings to compare like for like. Each card above lists the settings it ran with.
            </Alert>
          </div>
        )}
        <div className="table-scroll">
          <table className="table">
            <thead><tr><th>Measure</th><th className="num">Unsafe</th><th className="num">RaceSafe</th></tr></thead>
            <tbody>
              {ROWS.map(([label, key, badWhenPositive]) => (
                <tr key={key}>
                  <td>{label}</td>
                  {['UNSAFE', 'SAFE'].map((m) => {
                    const v = results[m]?.[key];
                    const bad = badWhenPositive && v > 0;
                    return <td key={m} className={`num ${bad ? 'text-danger' : ''}`} style={bad ? { fontWeight: 600 } : undefined}>{v === undefined ? '—' : fmtNum(v)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <Disclosure summary="Why the unsafe version breaks, and what RaceSafe does instead">
          <div className="grid cols-2">
            <div className="stack">
              <pre className="code">{`# every statement auto-commits; nothing is locked
`}<span className="bad">{`row = SELECT confirmed_count, capacity FROM lab_courses`}</span>{`
if row.confirmed_count < row.capacity:      `}<span className="cm"># check</span>{`
    `}<span className="cm"># other clients read the SAME old count here</span>{`
    INSERT INTO lab_registrations (...)     `}<span className="cm"># act</span>{`
    `}<span className="bad">{`UPDATE lab_courses SET confirmed_count = row.confirmed_count + 1`}</span></pre>
              <p className="small text-2">Check and update are separate steps with no mutual exclusion: many clients see a free seat
                and all insert (overbooking), each writes its own stale count (lost updates), and without a UNIQUE constraint a
                double-click becomes a duplicate row.</p>
            </div>
            <div className="stack">
              <pre className="code">{`BEGIN
  SELECT ... FROM registration_requests WHERE request_id = ? FOR UPDATE
  `}<span className="ok">{`SELECT capacity, confirmed_count FROM courses WHERE course_id = ? FOR UPDATE`}</span>{`
  SELECT ... FROM registrations WHERE student_id = ? AND course_id = ? FOR UPDATE
  INSERT INTO registrations (...)    `}<span className="cm">-- UNIQUE (student_id, course_id)</span>{`
  `}<span className="ok">{`UPDATE courses SET confirmed_count = confirmed_count + 1`}</span>{`   `}<span className="cm">-- CHECK ≤ capacity</span>{`
COMMIT`}</pre>
              <p className="small text-2">The course row lock turns check-and-take into one critical section per course: the next
                worker waits for the commit, then sees the real count. Other courses lock other rows and are not blocked. UNIQUE and
                CHECK constraints are the backstop; the request-row lock makes retries idempotent.</p>
            </div>
          </div>
        </Disclosure>

        <Disclosure summary={`Run history${history ? ` (${history.length})` : ''}`}>
          {!history || history.length === 0 ? <Empty compact title="No runs recorded yet" /> : (
            <div className="table-scroll scroll-y" style={{ maxHeight: 280 }}>
              <table className="table">
                <thead><tr><th>Finished</th><th>Run</th><th className="num">Capacity</th><th className="num">Attempts</th><th className="num">Confirmed</th><th className="num">Overbooked</th><th className="num">Duplicates</th></tr></thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.experiment_id}>
                      <td className="small">{fmtDateTime(h.finished_at)}</td>
                      <td>{h.type === 'RACE_UNSAFE' ? 'Unsafe' : 'RaceSafe'}</td>
                      <td className="num">{h.result?.capacity}</td>
                      <td className="num">{h.result?.attempts}</td>
                      <td className="num">{h.result?.confirmed}</td>
                      <td className={`num ${h.result?.overbooked > 0 ? 'text-danger' : ''}`}>{h.result?.overbooked}</td>
                      <td className={`num ${h.result?.duplicate_registrations > 0 ? 'text-danger' : ''}`}>{h.result?.duplicate_registrations}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Disclosure>
      </Panel>
    </>
  );
}

function ResultPanel({ mode, result, running, disabled, onRun }) {
  const unsafe = mode === 'UNSAFE';
  return (
    <Panel
      icon={unsafe ? ShieldAlert : ShieldCheck}
      iconTone={unsafe ? 'danger' : 'success'}
      title={unsafe ? 'Unsafe check-then-act' : 'RaceSafe transaction'}
      description={unsafe ? 'Isolated lab tables, no locks' : 'Queue → worker → locked MySQL transaction'}
      tone={unsafe ? 'danger' : undefined}
      actions={<Button variant={unsafe ? 'danger' : 'primary'} icon={Play} loading={running} disabled={disabled} onClick={onRun}>
        {unsafe ? 'Run unsafe' : 'Run RaceSafe'}</Button>}>
      {running && (
        <div className="stack" style={{ gap: 8 }}>
          <Progress indeterminate label="Running the burst" />
          <div className="small muted">Firing the burst and counting the rows in MySQL… this takes a few seconds.</div>
        </div>
      )}
      {!running && !result && <Empty compact title="Not run yet" />}
      {!running && result && (
        <div className="stack">
          <div className="row" style={{ alignItems: 'flex-end', gap: 16 }}>
            <div>
              <div className="small muted">Confirmed seats</div>
              <div className="hero-number">{result.confirmed}<span className="muted" style={{ fontSize: 18 }}> / {result.capacity}</span></div>
            </div>
            {result.correct ? (
              <span className="badge success"><CheckCircle2 size={12} />No overbooking or duplicates</span>
            ) : (
              <span className="badge danger"><XCircle size={12} />
                {result.overbooked > 0 ? `Overbooked by ${result.overbooked}` : 'Inconsistent'}
                {result.duplicate_registrations > 0 ? ` · ${result.duplicate_registrations} duplicates` : ''}
              </span>
            )}
          </div>
          <RunSettings cfg={result.config} unsafe={unsafe} />
          <dl className="kv">
            <dt>Waitlisted</dt><dd>{fmtNum(result.waitlisted)}</dd>
            <dt>Stored counter vs actual rows</dt><dd>{result.stored_confirmed_count} vs {result.confirmed}</dd>
            <dt>{result.attempts} attempts from {result.client_threads} clients</dt><dd>{fmtNum(result.elapsed_ms)} ms</dd>
          </dl>
        </div>
      )}
    </Panel>
  );
}

const SAME_KEYS = ['capacity', 'students', 'client_threads', 'duplicate_ratio'];

function settingsDiffer(results) {
  const a = results.UNSAFE?.config;
  const b = results.SAFE?.config;
  return !!(a && b) && SAME_KEYS.some((k) => a[k] !== b[k]);
}

/** The settings a result was produced with (not the form's current values). */
function RunSettings({ cfg, unsafe }) {
  if (!cfg) return null;
  return (
    <div className="facts">
      <span className="fact">capacity <b>{cfg.capacity}</b></span>
      <span className="fact"><b>{cfg.students}</b> students</span>
      <span className="fact"><b>{cfg.client_threads}</b> clients</span>
      {cfg.duplicate_ratio > 0 && <span className="fact"><b>{Math.round(cfg.duplicate_ratio * 100)}%</b> double-click</span>}
      {unsafe && <span className="fact">window <b>{cfg.race_window_ms} ms</b></span>}
      {unsafe && cfg.force_interleaving && <span className="fact">forced interleaving</span>}
    </div>
  );
}
