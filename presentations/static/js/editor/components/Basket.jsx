import { useEffect, useState } from 'react';
import {
  ChevronRight, Building2, Percent, Calendar, Network, Database, Tag, Eye,
  Upload, Trash2,
} from 'lucide-react';
import useStore from '../lib/store.js';
import { fetchSources, updateBasket, uploadDelete } from '../lib/api.js';
import TableDocsModal from './TableDocsModal.jsx';
import UploadModal from './UploadModal.jsx';

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
  const [docsTable, setDocsTable]  = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  async function reloadCatalog() {
    try {
      const data = await fetchSources();
      setCatalog(data);
      // Keep "Yüklenenler" open by default if it exists; otherwise the first domain.
      const uploadsDomain = data?.domains?.find((d) => d.id === 'dom_uploads');
      if (uploadsDomain) {
        setOpen((cur) => ({ ...cur, dom_uploads: true }));
      } else if (data?.domains?.[0]?.id && Object.keys(openDomains).length === 0) {
        setOpen({ [data.domains[0].id]: true });
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
    setDocsTable({ table, domain });
  }

  async function handleUploadCommit() {
    // After a successful commit, server updated the manifest's `uploads` list
    // AND its version. Refresh the catalog (which now includes the new sheets
    // as dom_uploads tables) and the manifest version.
    await reloadCatalog();
    // The latest version is in the response — but easier to just fetch the
    // manifest fresh. For now, bump locally; the next chat turn will sync.
    // (If you want a hard refresh, expose a refreshManifest() action.)
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
      <button
        type="button"
        className="sources-upload-cta"
        onClick={() => setUploadOpen(true)}
        title="Excel dosyası yükle veya bir tablo yapıştır"
      >
        <Upload size={12} strokeWidth={2} />
        <span>Veri Yükle</span>
      </button>

      <div className="sources-list">
        {catalog.domains.map((domain) => (
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

      <TableDocsModal
        open={!!docsTable}
        onClose={() => setDocsTable(null)}
        table={docsTable?.table}
        domain={docsTable?.domain}
      />

      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onCommit={handleUploadCommit}
      />
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
              onToggle={() => onToggleTable(t)}
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
          onToggle={() => onToggleTable(t)}
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


function TableRow({ table, isActive, basketEntry, onToggle, onOpenDocs, labelOverride }) {
  const shortName = labelOverride || (table.id || '').split('.').pop();
  const filter = basketEntry?.row_filter;

  return (
    <div className={`sources-table-wrap${isActive ? ' is-active' : ''}`}>
      <button
        type="button"
        className="sources-table"
        onClick={onToggle}
        title={isActive ? 'Sepetten çıkar' : 'Sepete ekle'}
      >
        <span className="sources-checkbox">
          {isActive && (
            <svg width="9" height="9" viewBox="0 0 12 12" fill="none">
              <path d="M2 6L5 9L10 3" stroke="white" strokeWidth="2"
                    strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </span>

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
      </button>

      <button
        type="button"
        className="sources-table-eye"
        onClick={(e) => { e.stopPropagation(); onOpenDocs(); }}
        title="Tablo dokümantasyonu"
      >
        <Eye size={12} strokeWidth={1.8} />
      </button>
    </div>
  );
}
