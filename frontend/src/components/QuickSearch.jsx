import { BookOpen, History, Search } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePolling } from '../hooks/usePolling';
import { useStudents } from '../hooks/useStudents';
import { api } from '../services/api';
import { Avatar } from './ui';

const HEXISH = /^[0-9a-f][0-9a-f-]{3,35}$/i;

/**
 * Jump to a student (registration page), a course (waitlist view) or a request ID
 * (recovery timeline). Press "/" anywhere to focus it.
 */
export default function QuickSearch() {
  const navigate = useNavigate();
  const { students } = useStudents();
  const courses = usePolling(() => api.courses(true), 0);
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef(null);
  const listId = useId();

  useEffect(() => {
    const onKey = (e) => {
      const tag = e.target.tagName;
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const results = useMemo(() => {
    const t = q.trim();
    if (!t) return [];
    const lower = t.toLowerCase();
    const courseHits = (courses.data?.courses || [])
      .filter((c) => c.course_id.toLowerCase().includes(lower) || c.course_name.toLowerCase().includes(lower))
      .slice(0, 4)
      .map((c) => ({ group: 'Courses', key: `c-${c.course_id}`, label: c.course_id, sub: `${c.course_name} · ${c.confirmed_actual}/${c.capacity} seats`, to: `/courses?course=${encodeURIComponent(c.course_id)}`, kind: 'course' }));
    const studentHits = students
      .filter((s) => s.student_id.toLowerCase().includes(lower) || s.full_name.toLowerCase().includes(lower))
      .slice(0, 5)
      .map((s) => ({ group: 'Students', key: `s-${s.student_id}`, label: s.full_name, sub: s.student_id, to: `/register?student=${s.student_id}`, kind: 'student', student: s }));
    const requestHit = HEXISH.test(t)
      ? [{ group: 'Requests', key: 'r', label: `Request ID starting “${t.toLowerCase()}”`, sub: 'Open in the recovery timeline', to: `/recovery?request_id=${encodeURIComponent(t.toLowerCase())}`, kind: 'request' }]
      : [];
    return [...courseHits, ...studentHits, ...requestHit];
  }, [q, students, courses.data]);

  useEffect(() => setActive(0), [q]);

  const go = (item) => {
    if (!item) return;
    navigate(item.to);
    setQ('');
    setOpen(false);
    input.current?.blur();
  };

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); go(results[active]); }
    else if (e.key === 'Escape') { setOpen(false); input.current?.blur(); }
  };

  let lastGroup = null;
  return (
    <div className="qs">
      <Search size={16} aria-hidden="true" />
      <input ref={input} value={q} placeholder="Search students, courses, requests" aria-label="Quick search"
        role="combobox" aria-expanded={open && !!q} aria-controls={listId} aria-autocomplete="list"
        onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)} onKeyDown={onKeyDown} />
      {!q && <kbd aria-hidden="true">/</kbd>}
      {open && q.trim() && (
        <div className="qs-menu" id={listId} role="listbox">
          {results.length === 0 && <div className="qs-empty">No student, course or request matches “{q.trim()}”.</div>}
          {results.map((r, i) => {
            const header = r.group !== lastGroup ? <div className="qs-group" key={`g-${r.group}`}>{r.group}</div> : null;
            lastGroup = r.group;
            return [
              header,
              <button key={r.key} type="button" className="qs-item" role="option" aria-selected={i === active}
                onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => go(r)}>
                {r.kind === 'student' && <Avatar id={r.student.student_id} name={r.student.full_name} size="sm" />}
                {r.kind === 'course' && <span className="icon-chip neutral" style={{ width: 26, height: 26 }}><BookOpen size={13} /></span>}
                {r.kind === 'request' && <span className="icon-chip neutral" style={{ width: 26, height: 26 }}><History size={13} /></span>}
                <span style={{ minWidth: 0 }}>
                  <span style={{ fontWeight: 500 }}>{r.label}</span>
                  <span className="sub">{r.sub}</span>
                </span>
              </button>,
            ];
          })}
        </div>
      )}
    </div>
  );
}
