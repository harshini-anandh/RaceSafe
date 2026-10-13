import { Activity } from 'lucide-react';
import { fmtTime, shortId } from '../utils/format';
import { EmptyState, EventBadge, Mono } from './ui';

/** Compact event feed (newest first). */
export default function EventList({ events, emptyText = 'No events yet', showRequest = true }) {
  if (!events || events.length === 0) {
    return <EmptyState icon={Activity} title={emptyText} />;
  }
  return (
    <div className="events">
      {events.map((e) => (
        <div className="event" key={e.event_id}>
          <span className="event-time">{fmtTime(e.created_at)}</span>
          <span><EventBadge type={e.event_type} /></span>
          <span className="event-msg" title={e.message}>
            {[e.student_id, e.course_id].filter(Boolean).join(' → ')}
            {showRequest && e.request_id && <> · <Mono title={e.request_id}>{shortId(e.request_id)}</Mono></>}
            {e.attempt ? ` · attempt ${e.attempt}` : ''}
            {e.worker_name ? ` · ${e.worker_name}` : ''}
            {e.message ? ` — ${e.message}` : ''}
          </span>
        </div>
      ))}
    </div>
  );
}
