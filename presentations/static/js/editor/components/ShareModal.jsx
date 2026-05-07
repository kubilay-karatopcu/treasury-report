import { useState, useRef, useEffect } from 'react';
import useStore from '../lib/store.js';

export default function ShareModal() {
  const info = useStore((s) => s.shareModal);
  const close = useStore((s) => s.closeShareModal);
  const inputRef = useRef(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (info && inputRef.current) {
      inputRef.current.select();
    }
    setCopied(false);
  }, [info]);

  if (!info) return null;

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(info.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for browsers without clipboard API permission
      inputRef.current?.select();
      document.execCommand('copy');
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  return (
    <div className="share-modal-backdrop" onClick={close}>
      <div className="share-modal" onClick={(e) => e.stopPropagation()}>
        <div className="share-modal-header">
          <h3>Snapshot Hazır</h3>
          <button className="share-modal-close" onClick={close} aria-label="Kapat">✕</button>
        </div>
        <div className="share-modal-body">
          <p className="share-modal-desc">
            Sunum'un v{info.manifest_version} haline ait dondurulmuş bir kopya oluşturuldu.
            Bu bağlantıyı paylaşabilirsiniz — açan kişi mevcut görünümü değiştiremez.
          </p>
          <div className="share-modal-row">
            <input
              ref={inputRef}
              className="share-modal-url"
              type="text"
              readOnly
              value={info.url}
            />
            <button className="share-modal-copy-btn" onClick={copyLink}>
              {copied ? '✓ Kopyalandı' : 'Kopyala'}
            </button>
          </div>
          <div className="share-modal-meta">
            ID: <code>{info.snapshot_id}</code>
          </div>
        </div>
        <div className="share-modal-footer">
          <a href={info.url} target="_blank" rel="noopener noreferrer" className="btn btn-sm btn-outline-primary">
            Yeni sekmede aç
          </a>
          <button className="btn btn-sm btn-outline-secondary" onClick={close}>
            Tamam
          </button>
        </div>
      </div>
    </div>
  );
}
