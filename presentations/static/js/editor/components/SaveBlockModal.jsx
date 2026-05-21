import { useCallback, useEffect, useRef, useState } from 'react';
import { X, Save } from 'lucide-react';
import useStore, { findBlockPath } from '../lib/store.js';
import { saveBlockToLibrary, saveBlockAsTemplate } from '../lib/api.js';
import AudiencePicker from './AudiencePicker.jsx';


/**
 * Build the absolute URL to the BlockStore library page that lists the saved
 * template. Survives the SCRIPT_NAME proxy prefix (/proxy/8080/...) by
 * deriving the base from window.location.pathname instead of url_for.
 */
function blocksLibraryUrl() {
  const path = window.location.pathname;
  const i = path.indexOf('/presentations/');
  const base = i >= 0 ? path.slice(0, i) : '';
  return `${base}/presentations/blocks/`;
}


/**
 * Normalise a free-form string into a kebab/snake-case identifier slug:
 * lowercase, ASCII letters/digits/underscore only, collapse repeats.
 * Used for both team (auto-derived from userInfo.department) and block id.
 */
function slugify(s) {
  return String(s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')   // strip diacritics
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_');
}


export default function SaveBlockModal() {
  const modal     = useStore((s) => s.saveBlockModal);
  const close     = useStore((s) => s.closeSaveBlockModal);
  const userInfo  = useStore((s) => s.userInfo);
  const manifest  = useStore((s) => s.manifest);

  // Stripped-down form: just name + id. Other metadata (description, tags,
  // documentation) is edited later from the /blocks/edit/<team>/<id> page
  // — the user explicitly asked for a "save first, fill metadata in
  // library view later" flow. Team is auto-derived from the user's
  // department so they don't have to type it every time.
  const [name, setName]               = useState('');
  const [blockSlug, setBlockSlug]     = useState('');
  const [busy, setBusy]               = useState(false);
  const [err, setErr]                 = useState(null);
  const [result, setResult]           = useState(null);
  const audienceRef = useRef(null);

  // Modal açılınca form'u block bilgisinden doldur.
  const blockId = modal?.blockId;
  const block = blockId && manifest ? findBlockPath(manifest, blockId)?.slide
                                    || findBlockPath(manifest, blockId)?.child
                                    || findBlockPath(manifest, blockId)?.section
                                    : null;

  useEffect(() => {
    if (!modal) return;
    setName((block?.title || '').trim());
    // Block id (b_xxx etc.) → strip leading b_/t_, slugify for a sensible default.
    setBlockSlug(slugify((block?.id || '').replace(/^[bt]_/, '')));
    setBusy(false); setErr(null); setResult(null);
  }, [modal, block?.title, block?.id]);

  // Phase 6.5 shape detection: block has the variable-aware fields → save to
  // BlockStore (templates). Legacy blocks fall through to LibraryStore.
  const isPhase65Shape = typeof block?.query === 'string';

  // Auto-derive team from userInfo.department; fall back to sicil. The user
  // can change team later from the /blocks/edit page.
  const team = userInfo?.department
    ? slugify(userInfo.department) || 'default'
    : (userInfo?.sicil ? slugify(userInfo.sicil) : 'default');

  const handleSave = useCallback(async () => {
    if (busy) return;
    setBusy(true); setErr(null); setResult(null);
    try {
      if (isPhase65Shape && !(block.query || '').trim()) {
        throw new Error(
          'SQL boş. Properties panelinde SQL yazıp Çalıştır\'a bastıktan sonra kaydedin.',
        );
      }
      if (isPhase65Shape && !blockSlug.trim()) {
        throw new Error('Blok kimliği zorunlu.');
      }
      if (!name.trim()) {
        throw new Error('Blok adı zorunlu.');
      }

      if (isPhase65Shape) {
        const meta = await saveBlockAsTemplate({
          block: {
            id: blockSlug.trim(),
            version: 1,
            title: name.trim(),
            team,
            owner: userInfo?.sicil || undefined,
            tags: [],
            query: block.query || '',
            variables: block.variables || [],
            visualization: { type: block.type, config: {} },
            // description / documentation deliberately omitted — user fills
            // these later from /blocks/edit/<team>/<id>.
          },
        });
        setResult({
          name: name.trim(), phase_65: true,
          team: meta.team, id: meta.id, version: meta.version,
        });
      } else {
        // Legacy LibraryStore path.
        const audience_sicils = audienceRef.current?.getResolvedSicils() || [];
        const meta = await saveBlockToLibrary({
          block_id: block.id,
          name: name.trim(),
          description: '',
          tags: [],
          audience_sicils,
        });
        setResult(meta);
      }
    } catch (e) {
      const detail = (e.errors && e.errors.length) ? e.errors.join('; ') : (e.message || String(e));
      setErr(detail);
    } finally {
      setBusy(false);
    }
  }, [busy, isPhase65Shape, block, blockSlug, name, team, userInfo]);

  // Ctrl+S / Cmd+S triggers save (only when modal is open). Esc still
  // closes the modal — but user input is preserved across renders.
  useEffect(() => {
    if (!modal) return;
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
        if (!result) handleSave();
      } else if (e.key === 'Escape') {
        // Esc closes only if no unsaved input — guard against accidental
        // loss after the user typed a long name.
        const dirty = name.trim() || blockSlug.trim();
        if (!dirty || window.confirm('Kapat? Yazdıkların kaybolabilir.')) {
          close();
        }
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [modal, handleSave, name, blockSlug, result, close]);

  if (!modal) return null;
  if (!block) return null;   // block was deleted while modal mounted

  const modalTitle = isPhase65Shape ? 'Şablon Olarak Kaydet' : 'Bloğu Kütüphaneye Kaydet';

  return (
    // Backdrop click NO LONGER closes the modal — the user has typed in
    // these fields and accidental dismissal loses work. Only the X icon
    // or Esc (with confirm) closes.
    <div className="save-modal-backdrop">
      <div className="save-modal" onClick={(e) => e.stopPropagation()}>
        <div className="save-modal-header">
          <h3>{modalTitle}</h3>
          <button className="save-modal-close" onClick={close} aria-label="Kapat">
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        <div className="save-modal-body">
          {result ? (
            <div className="save-success">
              <div className="save-success-title">Blok kaydedildi 🎉</div>
              <p className="save-success-desc">
                {result.phase_65 ? (
                  <>
                    <strong>{result.team}/{result.id}</strong> v{result.version} olarak
                    şablon kütüphanesine yazıldı. Açıklama, etiketler ve dokümantasyonu
                    Bloklar sayfasından eklersin.
                  </>
                ) : (
                  <>
                    <strong>{result.name}</strong> kütüphaneye kaydedildi.
                  </>
                )}
              </p>
              <div className="save-action-row">
                {result.phase_65 && (
                  <a className="save-btn save-btn--primary" href={blocksLibraryUrl()}>
                    Bloklar sayfasına git →
                  </a>
                )}
                <button
                  type="button"
                  className="save-btn save-btn--ghost"
                  onClick={close}
                >Tamam</button>
              </div>
            </div>
          ) : (
            <>
              <p className="save-tab-desc">
                <code>{block.type}</code> · <code>{block.id}</code>
                {isPhase65Shape && (
                  <> · ekip: <strong>{team}</strong></>
                )}
              </p>

              <label className="save-field">
                <span className="save-field-label">Blok adı</span>
                <input
                  type="text"
                  className="save-field-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Örn: Şube Mevduat Top 10"
                  autoFocus
                />
              </label>

              {isPhase65Shape && (
                <label className="save-field">
                  <span className="save-field-label">Blok kimliği</span>
                  <input
                    type="text"
                    className="save-field-input"
                    value={blockSlug}
                    onChange={(e) => setBlockSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_'))}
                    placeholder="ör. branch_position_kpi"
                  />
                  <span className="save-field-hint">3-60 karakter, [a-z0-9_]. Aynı ekip altında benzersiz.</span>
                </label>
              )}

              {!isPhase65Shape && (
                <div className="save-field">
                  <span className="save-field-label">Kim görsün?</span>
                  <AudiencePicker ref={audienceRef} userInfo={userInfo} resetKey={modal} />
                </div>
              )}

              <div className="save-action-row">
                <button
                  type="button"
                  className="save-btn save-btn--primary"
                  onClick={handleSave}
                  disabled={busy}
                  title="Ctrl + S"
                >
                  <Save size={14} strokeWidth={2} />
                  <span>{busy ? 'Kaydediliyor…' : 'Kaydet'}</span>
                </button>
              </div>

              {err && <div className="save-error">{err}</div>}

              {isPhase65Shape && (
                <p className="save-tab-desc" style={{ marginTop: 12, opacity: 0.7 }}>
                  Açıklama, etiket ve dokümantasyon Bloklar &rsaquo; Düzenle ekranında.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
