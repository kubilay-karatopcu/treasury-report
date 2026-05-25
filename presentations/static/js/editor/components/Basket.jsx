import { useEffect, useState } from 'react';
import {
  ChevronRight, Building2, Percent, Calendar, Network, Database, Tag, Eye,
  Upload, Trash2,
} from 'lucide-react';
import useStore from '../lib/store.js';
import { fetchSources, updateBasket, uploadDelete } from '../lib/api.js';

const DOMAIN_ICONS = {
  building:  Building2,
  percent:   Percent,
  network:   Network,
  calendar:  Calendar,
  database:  Database,
  upload:    Upload,
};

function pickDomainIcon(domain) {
  if (domain.icon && DOMAIN_ICONS[domain.icon]) return DOMAIN_ICONS[domain.icon];

  const haystack = `${domain.id || ''} ${domain.label || ''}`.toLowerCase();
  if (/(mevduat|deposit|bilanco)/.test(haystack)) return Building2;
  if (/(nii|faiz|interest|rate)/.test(haystack))   return Percent;
  if (/(rakip|sektor|sector|competitor|market)/.test(haystack)) return Network;
  if (/(takvim|calendar|event)/.test(haystack)) return Calendar;
  if (/(yuklenen|upload)/.test(haystack)) return Upload;
  return Database;
}

// Re-group catalog domains by SCHEMA (prefix before "." in each table
// id). Sunum + Hazırlık both use this so the sidebar reads like Keşif's
// schema tree. The curated domain labels (Mevduat / NII & Faiz / ...)
// only fit a small hand-picked catalog; the real fixture has 30+
// tables across many schemas, so we group by schema instead.
// `dom_uploads` is preserved verbatim because uploads are grouped per
// file, not by schema.
function regroupBySchema(domains) {
  const result = [];
  const bySchema = new Map();
  for (const d of (domains || [])) {
    if (d.id === 'dom_uploads') {
      result.push(d);
      continue;
    }
    for (const t of (d.tables || [])) {
      const tid = t.id || '';
      const schema = tid.includes('.') ? tid.split('.')[0] : 'Diğer';
      if (!bySchema.has(schema)) {
        const group = { id: `schema_${schema}`, label: schema, tables: [] };
        bySchema.set(schema, group);
        result.push(group);
      }
      bySchema.get(schema).tables.push(t);
    }
  }
  return result;
}

function basketHas(basket, tableId) {
  return basket.some((b) => b.table === tableId);
}

export default function Basket() {
  const manifest    = useStore((s) => s.manifest);
  const setManifest = useStore((s) => s.setManifest);

  const [catalog, setCatalog]      = useState(null);
  const [error, setError]          = useState(null);
  const [openDomains, setOpen]     = useState({});
  const [busy, setBusy]            = useState(false);
  // Veri Yükle was moved to Hazırlık (Polish-4) — no upload modal state in
  // Sunum anymore. Already-uploaded sheets are still rendered + deletable
  // below.
  const setDocsTable = useStore((s) => s.setDocsTable);

  async function reloadCatalog() {
    try {
      const data = await fetchSources();
      setCatalog(data);
      // Tüm domainler default olarak kapalı — kullanıcı istediğini açar.
      // Sadece "Yüklenenler" varsa onu otomatik aç (kullanıcı yüklediğini
      // hemen görsün).
      const uploadsDomain = data?.domains?.find((d) => d.id === 'dom_uploads');
      if (uploadsDomain) {
        setOpen((cur) => ({ ...cur, dom_uploads: true }));
      }
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    reloadCatalog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error)    return <div className="sidebar-error">{error}</div>;
  if (!catalog) return <div className="sidebar-loading">Kaynaklar yükleniyor…</div>;

  const basket = manifest?.basket || [];

  async function toggleTable(table) {
    if (busy) return;
    const newBasket = basketHas(basket, table.id)
      ? basket.filter((b) => b.table !== table.id)
      : [...basket, {
          table: table.id,
          columns: (table.columns || []).map((c) => c.name || c),
          row_filter: null,
        }];

    setBusy(true);
    try {
      const result = await updateBasket(newBasket);
      setManifest({ ...manifest, basket: result.basket, version: result.version });
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function toggleDomain(id) {
    setOpen((o) => ({ ...o, [id]: !o[id] }));
  }

  function openDocs(table, domain) {
    // Toggle: aynı tablonun gözüne tekrar basılırsa panel kapansın.
    const cur = useStore.getState().docsTable;
    if (cur && cur.table?.id === table.id) {
      useStore.getState().closeDocsTable();
    } else {
      setDocsTable({ table, domain });
    }
  }

  async function handleDeleteUpload(uploadId) {
    if (!confirm('Bu yüklü dosyayı silmek istediğinden emin misin?')) return;
    setBusy(true);
    try {
      await uploadDelete(uploadId);
      // Also drop any basket entries that referenced this upload's sheets.
      const remainingBasket = basket.filter(
        (b) => !b.table.startsWith(`upload__${uploadId}__`)
      );
      if (remainingBasket.length !== basket.length) {
        const result = await updateBasket(remainingBasket);
        setManifest({ ...manifest, basket: result.basket, version: result.version });
      }
      await reloadCatalog();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {/* Veri Yükle CTA moved to Hazırlık (Polish-4): users prepare data
          there. The Yüklenenler domain is still rendered below so already-
          uploaded sheets remain discoverable in Sunum. */}

      <div className="sources-list">
        {regroupBySchema(catalog.domains).map((domain) => (
          <DomainAccordion
            key={domain.id}
            domain={domain}
            isOpen={!!openDomains[domain.id]}
            onToggle={() => toggleDomain(domain.id)}
            basket={basket}
            onToggleTable={toggleTable}
            onOpenDocs={openDocs}
            onDeleteUpload={handleDeleteUpload}
          />
        ))}
      </div>
    </>
  );
}


function DomainAccordion({ domain, isOpen, onToggle, basket, onToggleTable, onOpenDocs, onDeleteUpload }) {
  const Icon = pickDomainIcon(domain);
  const inBasketCount = (domain.tables || []).filter((t) => basketHas(basket, t.id)).length;
  const isUploadsDomain = domain.id === 'dom_uploads';

  // Group upload tables by upload_id so we can render one row per file,
  // with sheets as sub-rows (and one delete button per file).
  const groupedUploads = isUploadsDomain ? groupByUploadId(domain.tables || []) : null;

  return (
    <div className={`sources-domain${isOpen ? ' is-open' : ''}`}>
      <button type="button" className="sources-domain-header" onClick={onToggle}>
        <ChevronRight size={12} strokeWidth={2} className="sources-domain-chevron" />
        <Icon size={14} strokeWidth={1.8} className="sources-domain-icon" />
        <span className="sources-domain-label">{domain.label}</span>
        {inBasketCount > 0 && (
          <span className="sources-domain-count">{inBasketCount}</span>
        )}
      </button>

      {isOpen && !isUploadsDomain && (
        <div className="sources-tables">
          {(domain.tables || []).map((t) => (
            <TableRow
              key={t.id}
              table={t}
              domain={domain}
              isActive={basketHas(basket, t.id)}
              basketEntry={basket.find((b) => b.table === t.id)}
              onOpenDocs={() => onOpenDocs(t, domain)}
            />
          ))}
        </div>
      )}

      {isOpen && isUploadsDomain && groupedUploads && (
        <div className="sources-tables">
          {groupedUploads.length === 0 && (
            <div className="sources-uploads-empty">Henüz yüklenen dosya yok.</div>
          )}
          {groupedUploads.map((group) => (
            <UploadFileGroup
              key={group.uploadId}
              group={group}
              domain={domain}
              basket={basket}
              onToggleTable={onToggleTable}
              onOpenDocs={onOpenDocs}
              onDeleteUpload={onDeleteUpload}
            />
          ))}
        </div>
      )}
    </div>
  );
}


function groupByUploadId(tables) {
  const map = new Map();
  for (const t of tables) {
    const uploadId = t._upload_id;
    if (!uploadId) continue;
    if (!map.has(uploadId)) {
      // The desc shape is "<filename> — <sheet>", so we recover the filename
      // from the first sheet by splitting on the em-dash.
      const filename = (t.desc || '').split(' — ')[0] || t.id;
      map.set(uploadId, { uploadId, filename, sheets: [] });
    }
    map.get(uploadId).sheets.push(t);
  }
  return Array.from(map.values());
}


function UploadFileGroup({ group, domain, basket, onToggleTable, onOpenDocs, onDeleteUpload }) {
  return (
    <div className="sources-upload-group">
      <div className="sources-upload-file">
        <FileSpreadsheetIcon />
        <span className="sources-upload-filename" title={group.filename}>
          {group.filename}
        </span>
        <span className="sources-upload-sheet-count">
          {group.sheets.length} sheet
        </span>
        <button
          type="button"
          className="sources-upload-delete"
          onClick={() => onDeleteUpload(group.uploadId)}
          title="Dosyayı sil"
        >
          <Trash2 size={11} strokeWidth={1.8} />
        </button>
      </div>
      {group.sheets.map((t) => (
        <TableRow
          key={t.id}
          table={t}
          domain={domain}
          isActive={basketHas(basket, t.id)}
          basketEntry={basket.find((b) => b.table === t.id)}
          onOpenDocs={() => onOpenDocs(t, domain)}
          labelOverride={t._sheet_name || (t.desc || '').split(' — ')[1]}
        />
      ))}
    </div>
  );
}


function FileSpreadsheetIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
         strokeLinejoin="round" style={{ flexShrink: 0 }}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="8" y1="13" x2="16" y2="13" />
      <line x1="8" y1="17" x2="16" y2="17" />
    </svg>
  );
}


function TableRow({ table, isActive, basketEntry, onOpenDocs, labelOverride }) {
  const shortName = labelOverride || (table.id || '').split('.').pop();
  const filter = basketEntry?.row_filter;

  // Tiklenir checkbox kaldırıldı — basket'a ekleme akışı bu view dışından
  // (gelecekteki katalog page'inden) gelir. Burada satır tıklanırsa
  // dokümantasyon paneli açılır.
  return (
    <div className={`sources-table-wrap${isActive ? ' is-active' : ''}`}>
      <button
        type="button"
        className="sources-table sources-table--readonly"
        onClick={onOpenDocs}
        title="Tabloyu önizle"
      >
        <div className="sources-table-info">
          <div className="sources-table-name">{shortName}</div>
          <div className="sources-table-desc">
            {labelOverride ? `${table.rows || ''} satır` : (
              <>
                {table.desc}
                {table.rows ? ` · ${table.rows}` : ''}
              </>
            )}
          </div>
          {isActive && filter && (
            <div className="sources-table-filter">
              <Tag size={9} strokeWidth={2} />
              <span className="sources-table-filter-text">{filter}</span>
            </div>
          )}
        </div>

        <span className="sources-table-eye-hint" title="Önizle">
          <Eye size={12} strokeWidth={1.8} />
        </span>
      </button>
    </div>
  );
}