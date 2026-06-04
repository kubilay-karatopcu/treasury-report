import { useCallback, useEffect, useRef, useState } from 'react';
import { X, Save } from 'lucide-react';
import useStore, { findBlockPath } from '../lib/store.js';
import { saveBlockAsTemplate } from '../lib/api.js';


/**
 * Build the absolute URL to the BlockStore library page that lists the saved
 * block. Survives the SCRIPT_NAME proxy prefix (/proxy/8080/...) by deriving
 * the base from window.location.pathname instead of url_for.
 */
function blocksLibraryUrl() {
  const path = window.location.pathname;
  const i = path.indexOf('/presentations/');
  const base = i >= 0 ? path.slice(0, i) : '';
  return `${base}/presentations/?tab=blocks`;
}


/**
 * Normalise a free-form string into a snake_case identifier slug.
 */
function slugify(s) {
  return String(s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')   // strip diacritics
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_');
}


/**
 * Bir container'ı (carousel/canvas) şablon olarak kaydederken alt blokları
 * sadeleştir: çalışma anındaki ağır/eski veriyi (data_source.rows /
 * preview_rows) atıp yalnız yeniden çalıştırılabilir SQL'i (original_sql)
 * sakla. config (stil) korunur — insert sonrası SQL yeniden koşunca veri dolar.
 */
export function stripForTemplate(node) {
  if (!node || typeof node !== 'object') return node;
  const out = { ...node };
  if (out.data_source && typeof out.data_source === 'object') {
    const sql = out.data_source.original_sql || out.data_source.sql || '';
    out.data_source = sql ? { original_sql: sql } : undefined;
  }
  if (Array.isArray(out.children)) out.children = out.children.map(stripForTemplate);
  return out;
}


/**
 * Tek kütüphane = BLOCK_STORE (Phase 6.5). Yalnızca SQL (query) taşıyan bloklar
 * kütüphaneye kaydedilebilir; dataset'e bağlı / config-only bloklar scope'a
 * özgü olduğundan taşınabilir değildir ve kaydedilemez. (Eski LIBRARY_STORE
 * yolu kaldırıldı.)
 */
export default function SaveBlockModal() {
  const modal     = useStore((s) => s.saveBlockModal);
  const close     = useStore((s) => s.closeSaveBlockModal);
  const userInfo  = useStore((s) => s.userInfo);
  const manifest  = useStore((s) => s.manifest);

  // Stripped-down form: name + id. Other metadata (description, tags,
  // documentation) is edited later from /blocks/edit/<team>/<id>.
  const [name, setName]               = useState('');
  const [blockSlug, setBlockSlug]     = useState('');
  const [busy, setBusy]               = useState(false);
  const [err, setErr]                 = useState(null);
  const [result, setResult]           = useState(null);

  const blockId = modal?.blockId;
  const block = blockId && manifest ? (findBlockPath(manifest, blockId)?.block ?? null) : null;

  useEffect(() => {
    if (!modal) return;
    setName((block?.title || '').trim());
    setBlockSlug(slugify((block?.id || '').replace(/^[bt]_/, '')));
    setBusy(false); setErr(null); setResult(null);
  }, [modal, block?.title, block?.id]);

  // SQL taşıyan leaf bloklar VEYA içi dolu container'lar (carousel/canvas)
  // kütüphaneye kaydedilebilir.
  const isContainer = block?.type === 'carousel' || block?.type === 'canvas';
  const saveable = isContainer
    ? (Array.isArray(block?.children) && block.children.length > 0)
    : (typeof block?.query === 'string' && (block.query || '').trim().length > 0);

  // Auto-derive team from userInfo.department; fall back to sicil.
  const team = userInfo?.department
    ? slugify(userInfo.department) || 'default'
    : (userInfo?.sicil ? slugify(userInfo.sicil) : 'default');

  const handleSave = useCallback(async () => {
    if (busy) return;
    setBusy(true); setErr(null); setResult(null);
    try {
      if (!saveable) {
        throw new Error(
          isContainer
            ? 'Boş container kaydedilemez — önce içine en az bir blok ekleyin.'
            : 'Bu blok kütüphaneye kaydedilemez — yalnız SQL (sorgu) taşıyan '
              + 'bloklar şablon olarak kaydedilebilir. Properties panelinde SQL '
              + "yazıp Çalıştır'a bastıktan sonra deneyin.",
        );
      }
      if (!blockSlug.trim()) throw new Error('Blok kimliği zorunlu.');
      if (!name.trim()) throw new Error('Blok adı zorunlu.');

      const blockBody = isContainer
        ? {
            id: blockSlug.trim(),
            version: 1,
            title: name.trim(),
            team,
            owner: userInfo?.sicil || undefined,
            tags: [],
            kind: 'composite',
            query: '',
            children: (block.children || []).map(stripForTemplate),
            variables: [],
            visualization: { type: block.type, config: {} },
          }
        : {
            id: blockSlug.trim(),
            version: 1,
            title: name.trim(),
            team,
            owner: userInfo?.sicil || undefined,
            tags: [],
            query: block.query || '',
            variables: block.variables || [],
            visualization: { type: block.type, config: {} },
            // description / documentation deliberately omitted — filled later
            // from /blocks/edit/<team>/<id>.
          };
      const meta = await saveBlockAsTemplate({ block: blockBody });
      setResult({ name: name.trim(), team: meta.team, id: meta.id, version: meta.version });
    } catch (e) {
      const detail = (e.errors && e.errors.length) ? e.errors.join('; ') : (e.message || String(e));
      setErr(detail);
    } finally {
      setBusy(false);
    }
  }, [busy, block, blockSlug, name, team, userInfo]);

  // Ctrl+S / Cmd+S save; Esc close (with dirty guard).
  useEffect(() => {
    if (!modal) return;
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
        if (!result) handleSave();
      } else if (e.key === 'Escape') {
        const dirty = name.trim() || blockSlug.trim();
        if (!dirty || window.confirm('Kapat? Yazdıkların kaybolabilir.')) close();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [modal, handleSave, name, blockSlug, result, close]);

  if (!modal) return null;
  if (!block) return null;

  return (
    <div className="save-modal-backdrop">
      <div className="save-modal" onClick={(e) => e.stopPropagation()}>
        <div className="save-modal-header">
          <h3>Kütüphaneye Kaydet</h3>
          <button className="save-modal-close" onClick={close} aria-label="Kapat">
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        <div className="save-modal-body">
          {result ? (
            <div className="save-success">
              <div className="save-success-title">Blok kaydedildi 🎉</div>
              <p className="save-success-desc">
                <strong>{result.team}/{result.id}</strong> v{result.version} olarak
                kütüphaneye yazıldı. Açıklama, etiketler ve dokümantasyonu
                Bloklar sayfasından eklersin.
              </p>
              <div className="save-action-row">
                <a className="save-btn save-btn--primary" href={blocksLibraryUrl()}>
                  Bloklar sayfasına git →
                </a>
                <button type="button" className="save-btn save-btn--ghost" onClick={close}>
                  Tamam
                </button>
              </div>
            </div>
          ) : (
            <>
              <p className="save-tab-desc">
                <code>{block.type}</code> · <code>{block.id}</code> · ekip: <strong>{team}</strong>
              </p>

              {!saveable && (
                <div className="save-error">
                  {isContainer
                    ? 'Bu container boş — önce içine en az bir blok/slide ekleyin, '
                      + 'sonra kaydedin. (İçi dolu carousel/tuval kütüphaneye '
                      + 'kaydedilebilir.)'
                    : 'Bu blok kütüphaneye kaydedilemez — yalnız SQL (sorgu) taşıyan '
                      + 'bloklar kaydedilebilir. Dataset\'e bağlı / metin blokları '
                      + 'taşınabilir değildir.'}
                </div>
              )}

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

              <div className="save-action-row">
                <button
                  type="button"
                  className="save-btn save-btn--primary"
                  onClick={handleSave}
                  disabled={busy || !saveable}
                  title="Ctrl + S"
                >
                  <Save size={14} strokeWidth={2} />
                  <span>{busy ? 'Kaydediliyor…' : 'Kaydet'}</span>
                </button>
              </div>

              {err && <div className="save-error">{err}</div>}

              <p className="save-tab-desc" style={{ marginTop: 12, opacity: 0.7 }}>
                Açıklama, etiket ve dokümantasyon Bloklar &rsaquo; Düzenle ekranında.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
