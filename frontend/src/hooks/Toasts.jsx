import { CheckCircle2, Info, TriangleAlert, X, XCircle } from 'lucide-react';
import { createContext, useCallback, useContext, useRef, useState } from 'react';

/**
 * Short-lived confirmations for quick actions (cancel, switch mode, reset…).
 * They appear in a corner and never push the page content around.
 * Results that ARE the content (registration results, experiment results) stay inline.
 */
const ToastContext = createContext(() => {});
const ICONS = { success: CheckCircle2, danger: XCircle, warning: TriangleAlert, info: Info };

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), []);
  const toast = useCallback(({ tone = 'success', title, text, duration = 5000 }) => {
    const id = nextId.current++;
    setToasts((list) => [...list.slice(-3), { id, tone, title, text }]);
    if (duration) setTimeout(() => dismiss(id), duration);
    return id;
  }, [dismiss]);

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => {
          const Icon = ICONS[t.tone] || Info;
          return (
            <div key={t.id} className={`toast ${t.tone}`}>
              <Icon size={17} className="toast-icon" aria-hidden="true" />
              <div className="grow">
                {t.title && <div className="toast-title">{t.title}</div>}
                {t.text && <div className="toast-text">{t.text}</div>}
              </div>
              <button type="button" className="toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss"><X size={14} /></button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
