import { useState, useRef, useEffect } from 'react';
import { X, Copy, ExternalLink, Check } from 'lucide-react';
import useStore from '../lib/store.js';
import { copyToClipboard } from '../lib/clipboard.js';

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
      await copyToClipboard(info.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      console.error('clipboard copy failed:', e);
    }
  }

  return (
    <div className="save-modal-backdrop" onClick={close}>
      <div className="save-modal" onClick={(e) => e.stopPropagation()}>
        <div className="save-modal-header">
          <h3>Süreç Yayınlandı</h3>
          <button className="save-modal-close" onClick={close} aria-label="Kapat">
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        <div className="save-modal-body">
          <p className="save-tab-desc">
            Sunum'un v{info.manifest_version} hali süreç olarak yayınlandı —
            Kütüphane &gt; Süreçler'de görünür. Aşağıdaki görünüm salt-okunurdur;
            dışarı vermek için tek dosyalık HTML indir.
          </p>

          <div className="share-url-row">
            <input
              ref={inputRef}
              className="share-url-input"
              type="text"
              readOnly
              value={info.url}
            />
            <button
              type="button"
              className="save-btn save-btn--ghost share-copy-btn"
              onClick={copyLink}
            >
              {copied
                ? <><Check size={13} strokeWidth={2} /><span>Kopyalandı</span></>
                : <><Copy  size={13} strokeWidth={2} /><span>Kopyala</span></>}
            </button>
          </div>

          <div className="share-meta">
            ID: <code>{info.snapshot_id}</code>
          </div>

          <div className="save-action-row" style={{ marginTop: 16 }}>
            <a
              href={`${info.url}/export`}
              className="save-btn save-btn--ghost"
            >
              <Copy size={13} strokeWidth={2} />
              <span>HTML indir (tek dosya)</span>
            </a>
            <a
              href={info.url}
              target="_blank"
              rel="noopener noreferrer"
              className="save-btn save-btn--ghost"
            >
              <ExternalLink size={13} strokeWidth={2} />
              <span>Yeni sekmede aç</span>
            </a>
            <button type="button" className="save-btn save-btn--ghost" onClick={close}>
              Tamam
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
