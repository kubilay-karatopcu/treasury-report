import { useEffect, useState } from 'react';
import useStore, { getBlockById } from './lib/store.js';
import { fetchUserInfo } from './lib/api.js';
// Header.jsx is now imported by Sidebar.jsx (toolbar lives inside the
// left rail above the chat). Removed from the App.jsx tree.
import Sidebar         from './components/Sidebar.jsx';
import BlockCard       from './components/BlockCard.jsx';
import ShareModal      from './components/ShareModal.jsx';
import SaveModal       from './components/SaveModal.jsx';
import SaveBlockModal  from './components/SaveBlockModal.jsx';
import AddBlockPanel   from './components/AddBlockPanel.jsx';
import ReportTitle     from './components/ReportTitle.jsx';
import PropertiesPanel from './components/PropertiesPanel.jsx';
import TableDocsPanel  from './components/TableDocsPanel.jsx';
import ChatBox         from './components/ChatBox.jsx';
import { Sparkles, Plus, HelpCircle } from 'lucide-react';
import useResizable from './lib/useResizable.js';
import { draggableProps, dropBeforeProps, dropIntoProps, planDropRender } from './lib/dnd.js';
import HelpModal from './components/HelpModal.jsx';
import ManualSqlEditor from './components/ManualSqlEditor.jsx';
import FilterBar from './components/FilterBar.jsx';
import FixedDateFilter from './components/FixedDateFilter.jsx';

const WIDTH_SPAN = {
  'full': 12,
  '2/3':  8,
  '1/2':  6,
  '1/3':  4,
};

export default function App({ initialManifest, mode = 'editor' }) {
  const setManifest = useStore((s) => s.setManifest);
  const setMode     = useStore((s) => s.setMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const manifest    = useStore((s) => s.manifest);
  const viewMode    = useStore((s) => s.viewMode);
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const layoutEditMode   = useStore((s) => s.layoutEditMode);
  const docsTable        = useStore((s) => s.docsTable);
  const addBlockPanel    = useStore((s) => s.addBlockPanel);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const closeDocsTable   = useStore((s) => s.closeDocsTable);

  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Resizable widths (persist to localStorage) — sol Sidebar sabit,
  // sadece DocsPanel ve PropertiesPanel genişletilebilir.
  const [docsW,  dragDocs]  = useResizable('docs',  460, 'right', { min: 280, max: 800 });
  const [propsW, dragProps] = useResizable('props', 340, 'left',  { min: 280, max: 600 });

  useEffect(() => {
    setManifest(initialManifest);
    setMode(mode);
    if (mode === 'snapshot') setViewMode('presentation');
    if (mode !== 'snapshot') {
      fetchUserInfo()
        .then((info) => useStore.getState().setUserInfo(info))
        .catch((e) => console.warn('fetchUserInfo failed:', e));
    }
    // ?focus_block=<bid> from Bloklar > Yeni Blok handler — auto-select the
    // freshly-created empty block so Properties opens to it immediately.
    const focusBid = new URLSearchParams(window.location.search).get('focus_block');
    if (focusBid) {
      // Also flip into layout-edit so Properties + ManualSqlEditor are visible.
      const s = useStore.getState();
      s.setSelectedBlock(focusBid);
      if (typeof s.toggleLayoutEditMode === 'function' && !s.layoutEditMode) {
        s.toggleLayoutEditMode();
      } else if (typeof s.setLayoutEditMode === 'function') {
        s.setLayoutEditMode(true);
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!manifest) return <div className="editor-loading">Yükleniyor…</div>;

  const isSnapshot = mode === 'snapshot';
  const isBlockPreview = mode === 'block-preview';
  const isTemplateEdit = mode === 'template-edit';
  const sections = manifest.blocks || [];
  const isEdit = viewMode === 'edit' && !isSnapshot && !isBlockPreview && !isTemplateEdit;

  // Block preview mode — sadece tek bloğu render et, hiçbir chrome yok
  if (isBlockPreview) {
    const firstSection = sections[0];
    const block = firstSection?.children?.[0];
    if (!block) return <div className="editor-loading">Blok bulunamadı.</div>;
    return (
      <div className="block-preview-root">
        <BlockCard block={block} />
      </div>
    );
  }

  // Template-edit mode — mini-canvas at top (real chart render),
  // Properties-style form below. Single block manifest, no chrome.
  if (isTemplateEdit) {
    const firstSection = sections[0];
    const block = firstSection?.children?.[0];
    if (!block) return <div className="editor-loading">Şablon yüklenemedi.</div>;
    return (
      <TemplateEditView
        block={block}
        templateRef={initialManifest.template_ref}
        templateNew={!!initialManifest.template_new}
      />
    );
  }

  const rootClass = [
    'editor-root',
    `mode-${viewMode}`,
    isSnapshot ? 'is-snapshot' : '',
    !sidebarOpen && !isSnapshot ? 'sidebar-collapsed' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={rootClass}>
      {/* Phase 12.sunum-toolbar-2 — the floating toolbar moved inside
          Sidebar.jsx so it sits above the chat box. Header is rendered
          there now; we keep the import path for HelpModal portal. */}
      <div className="editor-body">
        {/* Sidebar her zaman görünür — snapshot'ta TOC için (sadece İçindekiler).
            Edit modda EditSidebar (data sources + chat), presentation/snapshot
            modda PresentationSidebar (TOC). */}
        <Sidebar />
        {!isSnapshot && docsTable && <TableDocsPanel width={docsW} onResizeStart={dragDocs} />}
        <main
          className="blocks-canvas ts-scroll"
          onClick={(e) => {
            // Canvas boş alanına tıklama → seçimi kaldır + docs panelini kapat.
            // Blok ya da interactive element içine tıklanmışsa hiçbir şey yapma.
            if (e.target.closest('[data-block-id]')) return;
            if (e.target.closest('button, input, textarea, a, select, [role="button"]')) return;
            if (selectedBlockId) setSelectedBlock(null);
            if (docsTable) closeDocsTable();
          }}
        >
          <div className={`canvas-content${sections.length === 0 && !isSnapshot ? ' canvas-content--empty' : ''}`}>
            {sections.length > 0 && <ReportTitle meta={manifest.meta || {}} />}
            {!isSnapshot && <FilterBar />}
            {isEdit && sections.length > 0 && <Hint hasSelection={!!selectedBlockId} />}
            <div className="sections-list">
              {sections.map((section) => (
                <SectionContainer
                  key={section.id}
                  section={section}
                  layoutEditMode={isEdit && layoutEditMode}
                />
              ))}
              {sections.length === 0 && (
                isSnapshot
                  ? <div className="editor-loading">Bu snapshot boş.</div>
                  : <EmptyStart />
              )}
              {isEdit && layoutEditMode && sections.length > 0 && (
                <AddSectionRow />
              )}
            </div>
          </div>
        </main>
        {isEdit && layoutEditMode && selectedBlockId && (
          <PropertiesPanel width={propsW} onResizeStart={dragProps} />
        )}
        {isEdit && layoutEditMode && addBlockPanel && (
          <AddBlockPanel width={propsW} onResizeStart={dragProps} />
        )}
      </div>
      {!isSnapshot && <FixedDateFilter />}
      {!isSnapshot && <ShareModal />}
      {!isSnapshot && <SaveModal />}
      {!isSnapshot && <SaveBlockModal />}
    </div>
  );
}


function SectionContainer({ section, layoutEditMode }) {
  const children = section.children || [];
  const dropPreview = useStore((s) => s.dropPreview);
  const draggingBlockId = useStore((s) => s.draggingBlockId);
  const manifest = useStore((s) => s.manifest);
  const draggedBlock = draggingBlockId ? getBlockById(manifest, draggingBlockId) : null;
  // DnD aktif (layout düzenleme modunda + bir blok sürükleniyorken). Section'ın
  // kendisi "sona ekle" drop bölgesi; slot'lar "öncesine ekle". Render listesi
  // sürüklerken hayalet kutuyu içerir (canlı yeni-layout önizlemesi).
  const dndOn = layoutEditMode && !!draggingBlockId;
  const items = planDropRender(children, section.id, dropPreview, draggingBlockId, draggedBlock);
  return (
    <section
      className={`section-container${section.locked ? ' is-locked' : ''}`
        + (dndOn ? ' is-dnd-zone' : '')}
      data-block-id={section.id}
      {...dropIntoProps(section.id, layoutEditMode)}
    >
      <BlockCard block={section} />
      {items.length > 0 && (
        <div className="section-children-grid">
          {items.map((it, i) => {
            if (it.kind === 'ghost') {
              const span = WIDTH_SPAN[it.width] ?? 12;
              return (
                <div key="__dnd_ghost__" className="dnd-ghost" style={{ gridColumn: `span ${span}` }}>
                  <span className="dnd-ghost-label">{it.title}</span>
                </div>
              );
            }
            const child = it.block;
            const width = child.width || 'full';
            const span = WIDTH_SPAN[width] ?? 12;
            return (
              <div
                key={child.id}
                data-block-id={child.id}
                className={`block-slot block-slot--${width.replace('/', '-')}`
                  + (layoutEditMode ? ' is-draggable' : '')
                  + (it.dim ? ' is-dnd-dragging' : '')
                  + (it.hidden ? ' is-dnd-hidden' : '')}
                style={{ gridColumn: `span ${span}` }}
                {...draggableProps(child.id, layoutEditMode)}
                {...dropBeforeProps(section.id, child.id, layoutEditMode)}
              >
                <BlockCard block={child} />
              </div>
            );
          })}
        </div>
      )}
      {layoutEditMode && <AddChildRow sectionId={section.id} />}
    </section>
  );
}


function AddSectionRow() {
  const addSection = useStore((s) => s.addSection);
  return (
    <div className="layout-add-row layout-add-row--section">
      <button
        type="button"
        className="layout-add-btn"
        onClick={() => addSection()}
      >
        <Plus size={14} strokeWidth={2.5} />
        <span>Yeni Bölüm Ekle</span>
      </button>
    </div>
  );
}


const CHILD_BLOCK_TYPES = [
  { type: 'kpi',        label: 'KPI' },
  { type: 'bar_chart',  label: 'Çubuk' },
  { type: 'line_chart', label: 'Çizgi' },
  { type: 'combo_chart', label: 'Combo (Çubuk+Çizgi)' },
  { type: 'area_chart', label: 'Alan' },
  { type: 'pie_chart',  label: 'Pasta' },
  { type: 'heatmap',    label: 'Isı Haritası' },
  { type: 'radial_bar', label: 'Gösterge' },
  { type: 'data_table', label: 'Tablo' },
  { type: 'narrative',  label: 'Metin' },
  { type: 'carousel',   label: '⟳ Carousel' },
];

function AddChildRow({ sectionId }) {
  const openAddBlockPanel = useStore((s) => s.openAddBlockPanel);
  return (
    <div className="layout-add-row layout-add-row--child">
      <button
        type="button"
        className="layout-add-btn layout-add-btn--ghost"
        onClick={() => openAddBlockPanel(sectionId)}
      >
        <Plus size={13} strokeWidth={2.5} />
        <span>Bu bölüme blok ekle</span>
      </button>
    </div>
  );
}


function EmptyStart() {
  const addSection        = useStore((s) => s.addSection);
  const layoutEditMode    = useStore((s) => s.layoutEditMode);
  const toggleLayoutEdit  = useStore((s) => s.toggleLayoutEdit);
  const [helpOpen, setHelpOpen] = useState(false);

  function handleManual() {
    addSection();
    if (!layoutEditMode) toggleLayoutEdit();
  }

  return (
    <>
    <div className="empty-start">
      <div className="empty-start__intro">
        <div className="empty-start__sparkle">
          <Sparkles size={28} strokeWidth={1.5} />
        </div>
        <h2 className="empty-start__title">Yeni sunuma başla</h2>
        <p className="empty-start__sub">
          Aşağıya rapor için ne istediğini yaz; sistem ilk bölümleri ve
          bloklarını senin için oluştursun.
          <button
            type="button"
            className="empty-start__help"
            onClick={() => setHelpOpen(true)}
            title="Komut yardımı — blok tipleri ve örnekler"
          >
            <HelpCircle size={15} strokeWidth={1.8} />
          </button>
        </p>
      </div>

      <div className="empty-start__chat-wrap">
        <div className="empty-start__chat">
          <ChatBox compact />
        </div>
      </div>

      <div className="empty-start__bottom">
        <div className="empty-start__divider">
          <span>veya</span>
        </div>
        <button
          type="button"
          className="empty-start__manual"
          onClick={handleManual}
        >
          <Plus size={14} strokeWidth={2.5} />
          <span>Manuel başla — boş bölümle aç</span>
        </button>
      </div>
    </div>
    <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </>
  );
}


function Hint({ hasSelection }) {
  return (
    <div className="editor-hint">
      <Sparkles size={13} strokeWidth={2} className="editor-hint-icon" />
      <div>
        {hasSelection ? (
          <span><strong>Bir blok seçili.</strong> Soldaki sohbet kutusu artık yalnızca bu bloğa etki ediyor.</span>
        ) : (
          <span><strong>Genel mod.</strong> Sohbet kutusuna yazılan komut tüm rapora etki eder. Bir bloğa tıklayarak yalnızca onu hedefleyin.</span>
        )}
      </div>
    </div>
  );
}


/* ──────────────────────────────────────────────────────────────────────────
   TemplateEditView — Phase 6.5 mini-canvas for /blocks/edit/<team>/<id>.

   Top: live BlockCard render of the template under edit. Updates after every
        successful Çalıştır via /blocks/api/preview.
   Bottom: PropertiesPanel-shaped form (ManualSqlEditor + title/type).
   Toolbar: "Şablonu güncelle (yeni sürüm)" → POST /blocks/api/save_new_version.
   ──────────────────────────────────────────────────────────────────────── */
function TemplateEditView({ block, templateRef, templateNew }) {
  const setBlockField = useStore((s) => s.setBlockField);

  // Auto-preview on mount: trigger the same /blocks/api/preview the user
  // would get by clicking Çalıştır. Saves them one click per page open.
  // Only runs once per block.id; no-op if the block has no query yet.
  useEffect(() => {
    if (!block?.query) return;
    const baseUrl = window.location.pathname.replace(/\/blocks\/(edit|new).*/, '/blocks/api');
    fetch(`${baseUrl}/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        block: {
          id: (block.id || '').replace(/^preview_/, '') || 'preview_block',
          version: 1,
          title: block.title || 'preview',
          team: 'preview',
          owner: 'preview',
          created_at: new Date().toISOString(),
          query: block.query,
          variables: block.variables || [],
          visualization: { type: block.type, config: {} },
        },
        render_type: block.type,
      }),
    })
      .then((r) => r.json())
      .then((body) => {
        if (body?.ok && body.block) {
          setBlockField(block.id, 'config', body.block.config);
          setBlockField(block.id, 'data_source', body.block.data_source);
        }
      })
      .catch((e) => console.warn('auto-preview failed:', e));
  }, [block.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="template-edit-root template-edit-root--side-by-side">
      <TemplateEditToolbar templateRef={templateRef} templateNew={templateNew} blockId={block.id} />
      <div className="template-edit-canvas">
        <BlockCard block={block} />
      </div>
      <div className="template-edit-properties">
        <TemplateEditProperties block={block} />
      </div>
      <SaveBlockModal />
    </div>
  );
}


function TemplateEditToolbar({ templateRef, templateNew, blockId }) {
  const [busy, setBusy]     = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr]       = useState(null);
  const manifest = useStore((s) => s.manifest);
  const openSaveBlockModal = useStore((s) => s.openSaveBlockModal);

  async function handleSaveNewVersion() {
    setBusy(true); setErr(null); setResult(null);
    try {
      const block = manifest?.blocks?.[0]?.children?.[0];
      if (!block) throw new Error('Blok bulunamadı.');
      const baseUrl = window.location.pathname.replace(/\/blocks\/edit\/.*/, '/blocks/api');
      const resp = await fetch(`${baseUrl}/save_new_version`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          block: {
            id: templateRef?.id || block.id,
            version: (templateRef?.version || 1),
            title: block.title,
            description: block.description || undefined,
            team: templateRef?.team || 'unknown',
            owner: templateRef?.owner || undefined,
            tags: block.tags || [],
            documentation: block.documentation || undefined,
            query: block.query || '',
            variables: block.variables || [],
            visualization: { type: block.type, config: {} },
          },
        }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok || !body.ok) {
        throw new Error((body.errors || [body.error]).filter(Boolean).join('; ') || 'Kaydedilemedi');
      }
      setResult(body);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const libraryUrl = window.location.pathname.replace(/\/blocks\/(edit\/.*|new).*/, '/blocks/');

  return (
    <div className="template-edit-toolbar">
      <div className="template-edit-toolbar__left">
        <a className="template-edit-back" href={libraryUrl}>← Kütüphane</a>
        {templateNew ? (
          <span className="template-edit-ref"><strong>Yeni blok</strong></span>
        ) : templateRef && (
          <span className="template-edit-ref">
            <strong>{templateRef.team}/{templateRef.id}</strong>
            <span className="template-edit-version">v{templateRef.version}</span>
          </span>
        )}
      </div>
      <div className="template-edit-toolbar__right">
        {!templateNew && err && <span className="template-edit-err">{err}</span>}
        {!templateNew && result && (
          <span className="template-edit-ok">
            v{result.version} olarak kaydedildi
          </span>
        )}
        {templateNew ? (
          <button
            type="button"
            className="template-edit-save-btn"
            onClick={() => openSaveBlockModal(blockId)}
          >
            Şablon olarak kaydet
          </button>
        ) : (
          <button
            type="button"
            className="template-edit-save-btn"
            onClick={handleSaveNewVersion}
            disabled={busy}
          >
            {busy ? 'Kaydediliyor…' : 'Yeni sürüm olarak kaydet'}
          </button>
        )}
      </div>
    </div>
  );
}


function TemplateEditProperties({ block }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const [title, setTitle]             = useState(block.title || '');
  const [description, setDescription] = useState(block.description || '');
  const [tagsText, setTagsText]       = useState((block.tags || []).join(', '));
  const [docPurpose, setDocPurpose]   = useState(block.documentation?.purpose || '');
  const [docContext, setDocContext]   = useState(block.documentation?.business_context || '');
  const [docDecision, setDocDecision] = useState(block.documentation?.decision_support || '');
  const [docLimits, setDocLimits]     = useState(block.documentation?.known_limitations || '');

  useEffect(() => {
    setTitle(block.title || '');
    setDescription(block.description || '');
    setTagsText((block.tags || []).join(', '));
    setDocPurpose(block.documentation?.purpose || '');
    setDocContext(block.documentation?.business_context || '');
    setDocDecision(block.documentation?.decision_support || '');
    setDocLimits(block.documentation?.known_limitations || '');
  }, [block.id]);

  // Commit any edited field on blur. Each field maps to a path on the
  // synthetic manifest block; the "Yeni sürüm olarak kaydet" toolbar
  // captures the current block dict on click, so commits on blur are
  // sufficient (no debounce needed).
  function commit(fieldPath, newValue) {
    setBlockField(block.id, fieldPath, newValue);
  }
  function commitTags() {
    const tags = tagsText.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean);
    setBlockField(block.id, 'tags', tags);
  }
  function commitDocField(key, value) {
    const doc = { ...(block.documentation || {}) };
    if (value.trim()) doc[key] = value.trim();
    else delete doc[key];
    setBlockField(block.id, 'documentation', doc);
  }

  return (
    <div className="template-edit-form">
      <section className="props-section">
        <h4 className="props-section__title">Şablon Bilgileri</h4>
        <div className="props-section__body">
          <div className="props-form-row">
            <label className="props-form-label">Başlık</label>
            <input
              type="text"
              className="props-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => { if (title !== (block.title || '')) commit('title', title); }}
            />
          </div>
          <div className="props-form-row">
            <label className="props-form-label">Açıklama</label>
            <textarea
              className="props-textarea"
              rows={2}
              value={description}
              placeholder="Bu blok hangi soruyu yanıtlar?"
              onChange={(e) => setDescription(e.target.value)}
              onBlur={() => { if (description !== (block.description || '')) commit('description', description); }}
            />
          </div>
          <div className="props-form-row">
            <label className="props-form-label">Etiketler</label>
            <input
              type="text"
              className="props-input"
              value={tagsText}
              placeholder="mevduat, şube, top10"
              onChange={(e) => setTagsText(e.target.value)}
              onBlur={commitTags}
            />
            <div className="props-form-hint">Virgülle ayır</div>
          </div>
        </div>
      </section>

      <section className="props-section">
        <h4 className="props-section__title">Dokümantasyon</h4>
        <div className="props-section__body">
          <div className="props-form-row">
            <label className="props-form-label">Amaç</label>
            <textarea
              className="props-textarea"
              rows={2}
              value={docPurpose}
              placeholder="Bu blok hangi soruyu yanıtlar?"
              onChange={(e) => setDocPurpose(e.target.value)}
              onBlur={() => commitDocField('purpose', docPurpose)}
            />
          </div>
          <div className="props-form-row">
            <label className="props-form-label">İş bağlamı</label>
            <textarea
              className="props-textarea"
              rows={2}
              value={docContext}
              placeholder="Hangi sürece / toplantıya hizmet eder?"
              onChange={(e) => setDocContext(e.target.value)}
              onBlur={() => commitDocField('business_context', docContext)}
            />
          </div>
          <div className="props-form-row">
            <label className="props-form-label">Karar desteği</label>
            <textarea
              className="props-textarea"
              rows={2}
              value={docDecision}
              placeholder="Hangi kararı/aksiyonu tetikler?"
              onChange={(e) => setDocDecision(e.target.value)}
              onBlur={() => commitDocField('decision_support', docDecision)}
            />
          </div>
          <div className="props-form-row">
            <label className="props-form-label">Bilinen kısıtlar</label>
            <textarea
              className="props-textarea"
              rows={2}
              value={docLimits}
              placeholder="Hangi durumlarda anlamlı değil?"
              onChange={(e) => setDocLimits(e.target.value)}
              onBlur={() => commitDocField('known_limitations', docLimits)}
            />
          </div>
        </div>
      </section>

      <TemplateManualEditor block={block} />
      {/* "Veri Yenileme Politikası" kaldırıldı — yenileme blok bazlı değil,
          tablo bazlı yönetiliyor (Hazırlık'taki dataset cron'u). */}
    </div>
  );
}


/**
 * Variant of ManualSqlEditor that calls /blocks/api/preview (stateless)
 * instead of /<pid>/block/<bid>/run-manual (presentation-scoped). The
 * /api/preview endpoint returns a block-shaped result that we splice into
 * the synthetic local manifest so the top-of-page BlockCard re-renders
 * with the fresh data.
 */
function TemplateManualEditor({ block }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const _emit = (patch) => {
    // Lift the block dict into a single setBlockField call by writing each
    // field individually. We could use a "replace whole block" setter but
    // setBlockField+'' for each field keeps it incremental and consistent.
    if (patch.query !== undefined) setBlockField(block.id, 'query', patch.query);
    if (patch.variables !== undefined) setBlockField(block.id, 'variables', patch.variables);
    if (patch.config !== undefined) setBlockField(block.id, 'config', patch.config);
    if (patch.data_source !== undefined) setBlockField(block.id, 'data_source', patch.data_source);
  };

  return <ManualSqlEditor block={block} previewMode={true} onPreviewResult={_emit} />;
}
