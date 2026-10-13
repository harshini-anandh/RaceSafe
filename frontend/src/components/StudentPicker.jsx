import { useId } from 'react';
import { useStudents } from '../hooks/useStudents';

/** Type-ahead student selector (input + datalist of the sample students). */
export default function StudentPicker({ value, onChange, placeholder = 'e.g. S0001' }) {
  const { students } = useStudents();
  const listId = useId();
  return (
    <>
      <input
        className="input"
        list={listId}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value.trim().toUpperCase())}
      />
      <datalist id={listId}>
        {students.map((s) => (
          <option key={s.student_id} value={s.student_id}>{s.full_name}</option>
        ))}
      </datalist>
    </>
  );
}

export function CourseSelect({ courses, value, onChange }) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {(courses || []).map((c) => (
        <option key={c.course_id} value={c.course_id}>
          {c.course_id} — {c.course_name} ({c.confirmed_count}/{c.capacity})
        </option>
      ))}
    </select>
  );
}
