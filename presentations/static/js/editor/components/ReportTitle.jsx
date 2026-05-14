import { useEffect, useRef, useState } from 'react';
import { Pencil } from 'lucide-react';
import useStore from '../lib/store.js';

export default function ReportTitle({ meta }) {
  const setMetaTitle = useStore((s) => s.setMetaTitle);
  const viewMode     = useStore((s) => s.viewMode);
  const mode         = useStore((s) => s.mode);
  const canEdit      = viewMode === 'edit' && mode !== 'snapshot';

  const title  = meta.title || 'Başlıksız Sunum';
  const date   = meta.date;
  const author = meta.author_label || meta.author;
  const authorLine = [date, author].filter(Boolean).join(' · ');

  const [editing, setEditing] = useState(false);
  const [local, setLocal]     = useState(title);
  const inputRef = useRef(null);

  useEffect(() => { setLocal(title); }, [title]);
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  function commit() {
    setEditing(false);
    setMetaTitle(local);
  }

  function cancel() {
    setEditing(false);
    setLocal(title);
  }

  return (
    <div className="report-title">
      {editing ? (
        <input
          ref={inputRef}
          className="report-title-input"
          value={local}
          onChange={(e) => setLocal(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit();
            else if (e.key === 'Escape') cancel();
          }}
        />
      ) : (
        <h1 className="report-title-h1">
          <span>{title}</span>
          {canEdit && (
            <button
              type="button"
              className="report-title-edit"
              onClick={() => setEditing(true)}
              title="Başlığı düzenle"
            >
              <Pencil size={18} strokeWidth={2} />
            </button>
          )}
        </h1>
      )}
      {authorLine && <div className="report-title-author">{authorLine}</div>}
    </div>
  );
}
