import { useEffect, useRef, useState } from 'react';
import {
  X, Link2, Download, Send, Users, ExternalLink,
} from 'lucide-react';
import useStore from '../lib/store.js';
import { createSnapshot, publishDashboard } from '../lib/api.js';
import AudiencePicker from './AudiencePicker.jsx';

export default function SaveModal() {
  const open       = useStore((s) => s.saveModalOpen);
  const close      = useStore((s) => s.closeSaveModal);
  const userInfo   = useStore((s) => s.userInfo);
  const manifest   = useStore((s) => s.manifest);
  const openShare  = useStore((s) => s.openShareModal);

  const canDashboard = !!userInfo?.dashboard_maker;
  const [tab, setTab] = useState('snapshot');

  const [snapping, setSnapping] = useState(false);
  const [snapErr,  setSnapErr]  = useState(null);

  const initialName = (manifest?.meta?.title || '').trim();
  const [dashName, setDashName] = useState(initialName);
  const audienceRef = useRef(null);

  const [publishing, setPublishing]       = useState(false);
  const [publishErr, setPublishErr]       = useState(null);
  const [publishResult, setPublishResult] = useState(null);

  useEffect(() => {
    if (!open) return;
    setTab('snapshot');
    setSnapping(false); setSnapErr(null);
    setDashName(initialName);
    setPublishing(false); setPublishErr(null); setPublishResult(null);
  }, [open, initialName]);

  if (!open) return null;

  async function handleSnapshot() {
    if (snapping) return;
    setSnapping(true); setSnapErr(null);
    try {
      const result = await createSnapshot();
      const fullUrl = new URL(result.url, window.location.origin).href;
      close();
      openShare({ ...result, url: fullUrl });
    } catch (e) {
      setSnapErr(e.message || String(e));
    } finally {
      setSnapping(false);
    }
  }

  function handleDownloadOnePager() {
    const apiBase = window.location.pathname.replace(/\/$/, '');
    window.location.href = `${apiBase}/export.html`;
  }

  async function handlePublish() {
    if (publishing) return;
    setPublishing(true); setPublishErr(null); setPublishResult(null);
    try {
      const audience_sicils = audienceRef.current?.getResolvedSicils() || [];
      const result = await publishDashboard({
        name: dashName.trim() || initialName,
        audience_sicils,
      });
      setPublishResult(result);
    } catch (e) {
      setPublishErr(e.message || String(e));
    } finally {
      setPublishing(false);
    }
  }

  return (
    <div className="save-modal-backdrop" onClick={close}>
      <div className="save-modal" onClick={(e) => e.stopPropagation()}>
        <div className="save-modal-header">
          <h3>Kaydet</h3>
          <button className="save-modal-close" onClick={close} aria-label="Kapat">
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        <div className="save-modal-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            className={`save-modal-tab${tab === 'snapshot' ? ' is-active' : ''}`}
            onClick={() => setTab('snapshot')}
          >
            <Link2 size={13} strokeWidth={1.9} />
            <span>Snapshot</span>
          </button>
          {canDashboard && (
            <button
              type="button"
              role="tab"
              className={`save-modal-tab${tab === 'dashboard' ? ' is-active' : ''}`}
              onClick={() => setTab('dashboard')}
            >
              <Users size={13} strokeWidth={1.9} />
              <span>Ekip Raporları</span>
            </button>
          )}
        </div>

        <div className="save-modal-body">
          {tab === 'snapshot' && (
            <div className="save-tab">
              <p className="save-tab-desc">
                Sunum'un anlık halini paylaşılabilir bir bağlantı olarak dondur
                veya tek HTML dosyası olarak indir.
              </p>
              <div className="save-action-row">
                <button
                  type="button"
                  className="save-btn save-btn--ghost"
                  onClick={handleSnapshot}
                  disabled={snapping}
                >
                  <Link2 size={14} strokeWidth={2} />
                  <span>{snapping ? 'Oluşturuluyor…' : 'Bağlantı oluştur'}</span>
                </button>
                <button
                  type="button"
                  className="save-btn save-btn--ghost"
                  onClick={handleDownloadOnePager}
                  title="Sunum'u tek HTML dosyası olarak indir"
                >
                  <Download size={14} strokeWidth={2} />
                  <span>One-pager indir (HTML)</span>
                </button>
              </div>
              {snapErr && <div className="save-error">{snapErr}</div>}
            </div>
          )}

          {tab === 'dashboard' && canDashboard && (
            <div className="save-tab">
              {publishResult ? (
                <div className="save-success">
                  <div className="save-success-title">Rapor yayınlandı 🎉</div>
                  <p className="save-success-desc">
                    <strong>{publishResult.name}</strong> artık <em>R &rsaquo; Ekip Raporları</em>
                    {' '}menüsünde gözükecek (yetkili kullanıcılar için).
                  </p>
                  <a
                    href={publishResult.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="save-btn save-btn--ghost"
                  >
                    <ExternalLink size={13} strokeWidth={2} />
                    <span>Yayınlanan raporu aç</span>
                  </a>
                </div>
              ) : (
                <>
                  <label className="save-field">
                    <span className="save-field-label">Rapor adı (menüde gözükecek)</span>
                    <input
                      type="text"
                      className="save-field-input"
                      value={dashName}
                      onChange={(e) => setDashName(e.target.value)}
                      placeholder={initialName || 'Rapor adı'}
                    />
                  </label>

                  <div className="save-field">
                    <span className="save-field-label">Audience</span>
                    <AudiencePicker ref={audienceRef} userInfo={userInfo} resetKey={open} />
                  </div>

                  <div className="save-action-row">
                    <button
                      type="button"
                      className="save-btn save-btn--ghost"
                      onClick={handlePublish}
                      disabled={publishing || !dashName.trim()}
                    >
                      <Send size={14} strokeWidth={2} />
                      <span>{publishing ? 'Yayınlanıyor…' : 'Yayınla'}</span>
                    </button>
                  </div>

                  {publishErr && <div className="save-error">{publishErr}</div>}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
