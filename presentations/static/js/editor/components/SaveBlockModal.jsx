import { useEffect, useRef, useState } from 'react';
import { X, Save, ExternalLink, Tag } from 'lucide-react';
import useStore, { findBlockPath } from '../lib/store.js';
import { saveBlockToLibrary, saveBlockAsTemplate } from '../lib/api.js';
import AudiencePicker from './AudiencePicker.jsx';

export default function SaveBlockModal() {
  const modal     = useStore((s) => s.saveBlockModal);
  const close     = useStore((s) => s.closeSaveBlockModal);
  const userInfo  = useStore((s) => s.userInfo);
  const manifest  = useStore((s) => s.manifest);

  const [name, setName]               = useState('');
  const [description, setDescription] = useState('');
  const [tagsText, setTagsText]       = useState('');
  const [busy, setBusy]               = useState(false);
  const [err, setErr]                 = useState(null);
  const [result, setResult]           = useState(null);
  const audienceRef = useRef(null);

  // Phase 6.5: manual_sql blocks save to BlockStore (template), not LibraryStore.
  const [team, setTeam]               = useState('');
  const [blockSlug, setBlockSlug]     = useState('');
  const [docPurpose, setDocPurpose]   = useState('');
  const [docContext, setDocContext]   = useState('');
  const [docDecision, setDocDecision] = useState('');
  const [docLimits, setDocLimits]     = useState('');

  // Modal açılınca form'u block bilgisinden doldur
  const blockId = modal?.blockId;
  const block = blockId && manifest ? findBlockPath(manifest, blockId)?.slide
                                    || findBlockPath(manifest, blockId)?.child
                                    || findBlockPath(manifest, blockId)?.section
                                    : null;

  useEffect(() => {
    if (!modal) return;
    setName((block?.title || '').trim());
    setDescription('');
    setTagsText('');
    setTeam('');
    // Derive a sensible default slug from the in-presentation block id.
    setBlockSlug((block?.id || '').replace(/^[bt]_/, '').replace(/[^a-z0-9_]/gi, '_').toLowerCase());
    setDocPurpose('');
    setDocContext('');
    setDocDecision('');
    setDocLimits('');
    setBusy(false); setErr(null); setResult(null);
  }, [modal, block?.title, block?.id]);

  if (!modal) return null;
  if (!block) {
    // Block manifest'te bulunamadı → otomatik kapan
    return null;
  }

  async function handleSave() {
    if (busy) return;
    setBusy(true); setErr(null); setResult(null);
    try {
      const tags = tagsText.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean);

      if (block.manual_sql) {
        // Phase 6.5 path — save to BlockStore as a versioned template.
        const documentation = {};
        if (docPurpose.trim())  documentation.purpose          = docPurpose.trim();
        if (docContext.trim())  documentation.business_context = docContext.trim();
        if (docDecision.trim()) documentation.decision_support = docDecision.trim();
        if (docLimits.trim())   documentation.known_limitations = docLimits.trim();

        const meta = await saveBlockAsTemplate({
          block: {
            id: blockSlug.trim(),
            version: 1,
            title: name.trim() || (block.title || 'Adsız blok'),
            description: description.trim() || undefined,
            team: team.trim(),
            owner: userInfo?.sicil || undefined,
            tags,
            documentation: Object.keys(documentation).length ? documentation : undefined,
            query: block.query || '',
            variables: block.variables || [],
            // Map the Phase 6 chart type to a Phase 6.5 visualization spec.
            visualization: { type: block.type, config: {} },
          },
        });
        setResult({
          name: name.trim(),
          phase_65: true,
          team: meta.team, id: meta.id, version: meta.version,
        });
      } else {
        // Legacy path — Phase 6 LibraryStore (audience-scoped, opaque block dict).
        const audience_sicils = audienceRef.current?.getResolvedSicils() || [];
        const meta = await saveBlockToLibrary({
          block_id: block.id,
          name: name.trim() || (block.title || 'Adsız blok'),
          description: description.trim(),
          tags,
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
  }

  const isManual = !!block.manual_sql;
  const modalTitle = isManual ? 'Bloğu Şablon Olarak Kaydet (v6.5)' : 'Bloğu Kütüphaneye Kaydet';

  return (
    <div className="save-modal-backdrop" onClick={close}>
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
                    şablon kütüphanesine yazıldı. <em>Bloklar</em> sayfasından düzenleyebilirsiniz.
                  </>
                ) : (
                  <>
                    <strong>{result.name}</strong> artık herhangi bir sunumda <em>+ Blok Ekle &rsaquo;
                    Library</em> sekmesinden eklenebilir.
                  </>
                )}
              </p>
              <button
                type="button"
                className="save-btn save-btn--ghost"
                onClick={close}
              >Tamam</button>
            </div>
          ) : (
            <>
              <p className="save-tab-desc">
                Bu bloğu (<code>{block.type}</code> · <code>{block.id}</code>) yeniden
                kullanılabilir bir şablon olarak kaydet.
                {isManual && (
                  <> Manuel SQL bloğu — Phase 6.5 değişken bağlamalı şablon olarak yazılacak.</>
                )}
              </p>

              {isManual && (
                <>
                  <label className="save-field">
                    <span className="save-field-label">Ekip <span style={{color:'#dc2626'}}>*</span></span>
                    <input
                      type="text"
                      className="save-field-input"
                      value={team}
                      onChange={(e) => setTeam(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g,'_'))}
                      placeholder="ör. retail_banking"
                    />
                    <span className="save-field-hint">küçük harf + alt çizgi. Blok bu ekip altında saklanır.</span>
                  </label>

                  <label className="save-field">
                    <span className="save-field-label">Blok kimliği <span style={{color:'#dc2626'}}>*</span></span>
                    <input
                      type="text"
                      className="save-field-input"
                      value={blockSlug}
                      onChange={(e) => setBlockSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g,'_'))}
                      placeholder="ör. branch_position_kpi"
                    />
                    <span className="save-field-hint">3-60 karakter, [a-z0-9_]. Aynı ekip altında benzersiz olmalı.</span>
                  </label>
                </>
              )}

              <label className="save-field">
                <span className="save-field-label">Blok adı</span>
                <input
                  type="text"
                  className="save-field-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Örn: Şube Mevduat Top 10"
                />
              </label>

              <label className="save-field">
                <span className="save-field-label">Açıklama</span>
                <textarea
                  className="save-field-input save-field-textarea"
                  rows={3}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Bu blok hangi veriyi gösterir, hangi karar/analiz için kullanılır?"
                />
              </label>

              <label className="save-field">
                <span className="save-field-label">
                  <Tag size={11} strokeWidth={1.8} style={{ verticalAlign: 'middle' }} />
                  {' '}Tag'ler (virgülle ayır)
                </span>
                <input
                  type="text"
                  className="save-field-input"
                  value={tagsText}
                  onChange={(e) => setTagsText(e.target.value)}
                  placeholder="mevduat, şube, top10"
                />
              </label>

              {isManual && (
                <>
                  <label className="save-field">
                    <span className="save-field-label">Amaç (purpose)</span>
                    <textarea
                      className="save-field-input save-field-textarea"
                      rows={2}
                      value={docPurpose}
                      onChange={(e) => setDocPurpose(e.target.value)}
                      placeholder="Bu blok hangi soruyu yanıtlar?"
                    />
                  </label>
                  <label className="save-field">
                    <span className="save-field-label">İş bağlamı</span>
                    <textarea
                      className="save-field-input save-field-textarea"
                      rows={2}
                      value={docContext}
                      onChange={(e) => setDocContext(e.target.value)}
                      placeholder="Hangi sürece / toplantıya hizmet eder?"
                    />
                  </label>
                  <label className="save-field">
                    <span className="save-field-label">Karar desteği</span>
                    <textarea
                      className="save-field-input save-field-textarea"
                      rows={2}
                      value={docDecision}
                      onChange={(e) => setDocDecision(e.target.value)}
                      placeholder="Hangi kararı/aksiyonu tetikler?"
                    />
                  </label>
                  <label className="save-field">
                    <span className="save-field-label">Bilinen kısıtlar</span>
                    <textarea
                      className="save-field-input save-field-textarea"
                      rows={2}
                      value={docLimits}
                      onChange={(e) => setDocLimits(e.target.value)}
                      placeholder="Hangi durumlarda anlamlı değil?"
                    />
                  </label>
                </>
              )}

              {!isManual && (
                <div className="save-field">
                  <span className="save-field-label">Kim görsün?</span>
                  <AudiencePicker ref={audienceRef} userInfo={userInfo} resetKey={modal} />
                </div>
              )}

              <div className="save-action-row">
                <button
                  type="button"
                  className="save-btn save-btn--ghost"
                  onClick={handleSave}
                  disabled={busy || !name.trim() || (isManual && (!team.trim() || !blockSlug.trim()))}
                >
                  <Save size={14} strokeWidth={2} />
                  <span>{busy ? 'Kaydediliyor…' : 'Kaydet'}</span>
                </button>
              </div>

              {err && <div className="save-error">{err}</div>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
