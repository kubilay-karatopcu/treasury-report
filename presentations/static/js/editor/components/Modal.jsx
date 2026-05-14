import { useEffect } from 'react';
import { X } from 'lucide-react';

/**
 * Generic modal shell with backdrop, ESC-to-close, body-scroll-lock,
 * and a header strip with a close button.
 *
 * Children render inside `.ts-modal-body`. Use `size` for width:
 *   "sm"  → 420px   (confirmations)
 *   "md"  → 560px   (table docs)
 *   "lg"  → 780px   (source modal w/ preview rows)
 */
export default function Modal({ open, onClose, title, size = 'md', children, footer }) {
  // ESC handler + scroll lock — bound only while open.
  useEffect(() => {
    if (!open) return;

    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', onKey);

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="ts-modal-backdrop" onMouseDown={onClose}>
      <div
        className={`ts-modal ts-modal--${size}`}
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <header className="ts-modal-header">
          <h3 className="ts-modal-title">{title}</h3>
          <button
            type="button"
            className="ts-modal-close"
            onClick={onClose}
            aria-label="Kapat"
          >
            <X size={16} strokeWidth={2} />
          </button>
        </header>

        <div className="ts-modal-body ts-scroll">{children}</div>

        {footer && <footer className="ts-modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}