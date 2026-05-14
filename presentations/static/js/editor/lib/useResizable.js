import { useCallback, useRef, useState } from 'react';

/**
 * Panel width hook — drag-to-resize, persists to localStorage.
 *
 * @param {string} key      — storage key suffix (e.g. 'sidebar', 'docs', 'props')
 * @param {number} initial  — default width in px
 * @param {'left'|'right'} side — which edge the resize handle is on.
 *        'right' for left-docked panels (drag right → wider)
 *        'left'  for right-docked panels (drag left → wider)
 * @param {object} [opts]   — { min, max }
 *
 * Returns [width, startDrag].
 */
export default function useResizable(key, initial, side, opts = {}) {
  const min = opts.min ?? 200;
  const max = opts.max ?? 900;
  const storageKey = 'prisma.w.' + key;

  const [width, setWidth] = useState(() => {
    try {
      const v = localStorage.getItem(storageKey);
      const n = v ? parseInt(v, 10) : NaN;
      return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : initial;
    } catch { return initial; }
  });

  const widthRef = useRef(width);
  widthRef.current = width;

  const startDrag = useCallback((e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = widthRef.current;

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const newW = side === 'left' ? startW - dx : startW + dx;
      setWidth(Math.max(min, Math.min(max, newW)));
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      try { localStorage.setItem(storageKey, String(widthRef.current)); } catch {}
    }
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [side, min, max, storageKey]);

  return [width, startDrag];
}
