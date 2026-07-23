import { useEffect, useRef, useState } from 'react';
import {
  X, Link2, Download, Send, Users, ExternalLink, Sparkles, Star,
} from 'lucide-react';
import useStore from '../lib/store.js';
import {
  createSnapshot, publishDashboard, listExperts, suggestExperts,
} from '../lib/api.js';
import AudiencePicker from './AudiencePicker.jsx';

const CONFIDENCE_AUTOCHECK = 0.7;  // matches spec §5.5 / Phase 10D

export default function SaveModal() {
  const open       = useStore((s) => s.saveModalOpen);
  const close      = useStore((s) => s.closeSaveModal);
  const userInfo   = useStore((s) => s.userInfo);
  const manifest   = useStore((s) => s.manifest);
  const openShare  = useStore((s) => s.openShareModal);

  const canDashboard = !!userInfo?.dashboard_maker;
  const [tab, setTab] = useState('snapshot');

  if (!open) return null;

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
            <span>Süreç Yayınla</span>
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
            <SnapshotForm
              manifest={manifest}
              onClose={close}
              onSaved={(result) => {
                close();
                const fullUrl = new URL(result.url, window.location.origin).href;
                openShare({ ...result, url: fullUrl });
              }}
            />
          )}
          {tab === 'dashboard' && canDashboard && (
            <DashboardForm manifest={manifest} userInfo={userInfo} open={open} />
          )}
        </div>
      </div>
    </div>
  );
}


/**
 * Snapshot tab — Phase 10D save form.
 *
 * Loads the expert catalog + LLM suggestions when first mounted.
 * Auto-checks any suggestion with confidence >= 0.7 and stars the top one.
 * Submit POSTs {title, description, bound_experts} and hands the resulting
 * snapshot meta to onSaved (which opens the share modal).
 */
function SnapshotForm({ manifest, onClose, onSaved }) {
  const manifestTitle = (manifest?.meta?.title || '').trim();

  const [title, setTitle]             = useState(manifestTitle);
  const [description, setDescription] = useState('');

  const [experts, setExperts]           = useState([]);          // [{id, code, name, ui, ...}]
  const [suggestions, setSuggestions]   = useState([]);          // [{id, confidence, reason}]
  const [selectedIds, setSelectedIds]   = useState(() => new Set());
  const [loading, setLoading]           = useState(true);
  const [suggestLoading, setSuggestLoading] = useState(true);

  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(null);

  // Load experts on mount; resolve suggestions in parallel.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSuggestLoading(true);
    Promise.all([
      listExperts().catch(() => []),
      suggestExperts({ manifest, title: manifestTitle, description: '' }).catch(() => []),
    ]).then(([exps, sugs]) => {
      if (cancelled) return;
      setExperts(exps);
      setSuggestions(sugs);
      // Auto-check suggestions above the confidence threshold.
      const autoSelect = new Set();
      sugs.forEach((s) => {
        if (typeof s.confidence === 'number' && s.confidence >= CONFIDENCE_AUTOCHECK) {
          autoSelect.add(s.id);
        }
      });
      // Always make sure at least the top suggestion is checked if any exist.
      if (autoSelect.size === 0 && sugs.length > 0) {
        autoSelect.add(sugs[0].id);
      }
      setSelectedIds(autoSelect);
      setLoading(false);
      setSuggestLoading(false);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleExpert(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const suggestionById = Object.fromEntries(suggestions.map((s) => [s.id, s]));
  const topSuggestionId = suggestions[0]?.id;

  async function handleSubmit() {
    if (saving) return;
    setSaving(true);
    setSaveErr(null);
    try {
      const body = {
        title:         (title || '').trim(),
        description:   (description || '').trim(),
        bound_experts: Array.from(selectedIds),
      };
      const result = await createSnapshot(body);
      onSaved(result);
    } catch (e) {
      setSaveErr(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  function handleDownloadOnePager() {
    const apiBase = window.location.pathname.replace(/\/$/, '');
    window.location.href = `${apiBase}/export.html`;
  }

  return (
    <div className="save-tab">
      <p className="save-tab-desc">
        Sunum'un anlık halini paylaşılabilir bir bağlantı olarak dondur.
        Hangi uzman(lar)ın kaynakçası altında görüneceğini seç — sistem ön öneri sundu.
      </p>

      <label className="save-field">
        <span className="save-field-label">Başlık</span>
        <input
          type="text"
          className="save-field-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={manifestTitle || 'Yayın başlığı'}
        />
      </label>

      <label className="save-field">
        <span className="save-field-label">Açıklama (opsiyonel)</span>
        <textarea
          className="save-field-input"
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Bu yayın ne için? (örn. sabah toplantısı, çeyrek kapanış)"
          style={{ resize: 'vertical', minHeight: 50, fontFamily: 'inherit' }}
        />
      </label>

      <div className="save-field">
        <span className="save-field-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span>Uzman Bağlantısı</span>
          {!suggestLoading && suggestions.length > 0 && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: 10, fontWeight: 500, color: '#a16207',
              background: '#fef3c7', padding: '2px 7px', borderRadius: 3,
              textTransform: 'none', letterSpacing: 0.2,
            }}>
              <Sparkles size={10} strokeWidth={2} /> sistem önerisi
            </span>
          )}
        </span>
        <p style={{
          fontSize: 12, color: '#64748b', margin: '0 0 10px',
          fontStyle: 'italic', lineHeight: 1.5,
        }}>
          Bu süreç hangi uzman(lar)a bağlanmalı? Seçtiğin uzmanlar
          ana ekranda kullanıcılara bu içerikten yararlanarak brifing verebilir.
        </p>

        {loading ? (
          <div style={{ padding: '12px 0', fontSize: 12, color: '#94a3b8' }}>
            Uzmanlar yükleniyor…
          </div>
        ) : experts.length === 0 ? (
          <div style={{ padding: '12px 0', fontSize: 12, color: '#94a3b8' }}>
            Mevcut uzman bulunamadı.
          </div>
        ) : (
          <div className="save-expert-chips">
            {experts.map((expert) => (
              <ExpertChip
                key={expert.id}
                expert={expert}
                selected={selectedIds.has(expert.id)}
                suggestion={suggestionById[expert.id]}
                isTopSuggestion={expert.id === topSuggestionId}
                onClick={() => toggleExpert(expert.id)}
              />
            ))}
          </div>
        )}
      </div>

      {saveErr && <div className="save-error">{saveErr}</div>}

      <div className="save-action-row">
        <button
          type="button"
          className="save-btn save-btn--ghost"
          onClick={handleDownloadOnePager}
          title="Sunum'u tek HTML dosyası olarak indir"
        >
          <Download size={14} strokeWidth={2} />
          <span>One-pager indir (HTML)</span>
        </button>
        <button
          type="button"
          className="save-btn save-btn--primary"
          onClick={handleSubmit}
          disabled={saving}
        >
          <Link2 size={14} strokeWidth={2} />
          <span>{saving ? 'Yayınlanıyor…' : 'Yayınla & Paylaş'}</span>
        </button>
      </div>
    </div>
  );
}


function ExpertChip({ expert, selected, suggestion, isTopSuggestion, onClick }) {
  const accent = expert.ui?.accent_color || '#6B8AFD';
  const confidence = suggestion?.confidence;
  const isSuggested = !!suggestion;

  const baseStyle = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
    padding: '7px 12px',
    background: selected ? '#ecfdf5' : '#fff',
    border: `1.5px solid ${selected ? accent : (isSuggested ? '#cbd5e1' : '#e2e8f0')}`,
    borderStyle: isSuggested && !selected ? 'dashed' : 'solid',
    borderRadius: 4,
    fontSize: 12.5,
    color: selected ? '#0f172a' : '#475569',
    cursor: 'pointer',
    transition: 'all 0.15s',
    fontFamily: 'inherit',
    userSelect: 'none',
  };

  return (
    <button
      type="button"
      onClick={onClick}
      style={baseStyle}
      title={suggestion?.reason || ''}
    >
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        background: accent, display: 'inline-block',
      }} />
      <span style={{
        fontFamily: 'ui-monospace, SF Mono, monospace',
        fontSize: 10, color: selected ? accent : '#94a3b8',
        letterSpacing: 1,
      }}>
        {expert.code}
      </span>
      <span>{expert.domain_label || expert.name}</span>
      {isTopSuggestion && (
        <Star size={10} strokeWidth={2} fill="#f59e0b" stroke="#f59e0b"
              style={{ marginLeft: 2 }} />
      )}
      {isSuggested && !isTopSuggestion && (
        <span style={{
          fontSize: 9, color: '#a16207', textTransform: 'uppercase',
          letterSpacing: 0.5, fontWeight: 500, marginLeft: 2,
        }}>
          öneri{confidence ? ` ${Math.round(confidence * 100)}%` : ''}
        </span>
      )}
    </button>
  );
}


/**
 * Dashboard tab — unchanged from pre-10D logic. Kept inline so this file
 * remains the single source for both tabs.
 */
function DashboardForm({ manifest, userInfo, open }) {
  const initialName = (manifest?.meta?.title || '').trim();
  const [dashName, setDashName]           = useState(initialName);
  const audienceRef                       = useRef(null);
  const [publishing, setPublishing]       = useState(false);
  const [publishErr, setPublishErr]       = useState(null);
  const [publishResult, setPublishResult] = useState(null);

  useEffect(() => {
    setDashName(initialName);
    setPublishing(false); setPublishErr(null); setPublishResult(null);
  }, [open, initialName]);

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
  );
}
