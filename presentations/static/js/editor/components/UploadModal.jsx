import { useState, useRef, useEffect } from 'react';
import {
  Upload, ClipboardPaste, FileText, Database, Hash, AlertTriangle,
  Loader2, CheckCircle, FileSpreadsheet,
} from 'lucide-react';
import Modal from './Modal.jsx';
import {
  uploadParseFile,
  uploadParsePaste,
  uploadCommitFile,
  uploadCommitPaste,
} from '../lib/api.js';
import useStore from '../lib/store.js';

const MAX_BYTES = 10 * 1024 * 1024;   // 10 MB hard cap on the client side too

const TYPE_LABEL = {
  NUMBER:  'sayı',
  DATE:    'tarih',
  VARCHAR: 'metin',
};

/**
 * Modal for adding a new data source via:
 *   A. Picking an .xlsx / .xls file from disk
 *   B. Pasting tab-separated data copied from Excel
 *
 * Workflow:
 *   1. User chooses input → component calls /uploads/parse for preview
 *   2. Server returns sheets + columns + first 10 rows; we render a table per sheet
 *   3. On "Kaydet", call /uploads to commit (S3 write + manifest patch)
 *   4. On success, signal parent so Basket can refresh
 */
export default function UploadModal({ open, onClose, onCommit }) {
  const fileInputRef = useRef(null);

  // Tab state
  const [tab, setTab] = useState('file');    // "file" | "paste"

  // Common
  const [parsing, setParsing]       = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError]           = useState(null);
  const [preview, setPreview]       = useState(null);
  const [activeSheet, setActiveSheet] = useState(0);

  // File-tab state
  const [pickedFile, setPickedFile] = useState(null);

  // Paste-tab state
  const [pasteText, setPasteText]   = useState('');
  const [tableName, setTableName]   = useState('yapistirilan');
  const [hasHeaderOverride, setHasHeaderOverride] = useState(null); // null | true | false

  // Reset when modal closes
  useEffect(() => {
    if (open) return;
    setTab('file');
    setParsing(false);
    setCommitting(false);
    setError(null);
    setPreview(null);
    setActiveSheet(0);
    setPickedFile(null);
    setPasteText('');
    setTableName('yapistirilan');
    setHasHeaderOverride(null);
  }, [open]);

  // ── File picker ──────────────────────────────────────────────────────────
  function onFileSelected(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > MAX_BYTES) {
      setError(`Dosya çok büyük: ${(f.size / 1024 / 1024).toFixed(1)} MB (maks 10 MB).`);
      setPickedFile(null);
      return;
    }
    setError(null);
    setPickedFile(f);
    setPreview(null);
    parseFile(f);
  }

  async function parseFile(f) {
    setParsing(true);
    setError(null);
    try {
      const res = await uploadParseFile(f);
      setPreview(res);
      setActiveSheet(0);
    } catch (err) {
      setError(err.message);
    } finally {
      setParsing(false);
    }
  }

  // ── Paste handling ───────────────────────────────────────────────────────
  async function parsePaste() {
    setError(null);
    if (!pasteText.trim()) {
      setError('Yapıştırılan içerik boş.');
      return;
    }
    setParsing(true);
    try {
      const res = await uploadParsePaste({
        paste: pasteText,
        table_name: tableName,
        has_header: hasHeaderOverride,
      });
      setPreview(res);
      setActiveSheet(0);
    } catch (err) {
      setError(err.message);
    } finally {
      setParsing(false);
    }
  }

  // ── Commit (save to S3 + manifest) ───────────────────────────────────────
  async function commit() {
    if (!preview) return;
    setCommitting(true);
    setError(null);
    try {
      const res = tab === 'file'
        ? await uploadCommitFile(pickedFile)
        : await uploadCommitPaste({
            paste: pasteText,
            table_name: tableName,
            has_header: hasHeaderOverride,
          });
      onCommit?.(res);
      onClose?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setCommitting(false);
    }
  }

  const sheets = preview?.sheets || [];
  const sheet = sheets[activeSheet];
  const canCommit = preview && !committing && !parsing && !error;

  const footer = (
    <>
      <button type="button" className="btn-ghost" onClick={onClose}>İptal</button>
      <button
        type="button"
        className="btn-primary"
        onClick={commit}
        disabled={!canCommit}
      >
        {committing
          ? <><Loader2 size={12} className="ts-spin" /><span>Yükleniyor…</span></>
          : <><CheckCircle size={12} /><span>Kaydet</span></>}
      </button>
    </>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Veri Yükle"
      size="lg"
      footer={footer}
    >
      <div className="upload-tabs">
        <button
          type="button"
          className={`upload-tab${tab === 'file' ? ' is-active' : ''}`}
          onClick={() => { setTab('file'); setPreview(null); setError(null); }}
        >
          <Upload size={12} strokeWidth={1.8} />
          <span>Dosya Seç</span>
        </button>
        <button
          type="button"
          className={`upload-tab${tab === 'paste' ? ' is-active' : ''}`}
          onClick={() => { setTab('paste'); setPreview(null); setError(null); }}
        >
          <ClipboardPaste size={12} strokeWidth={1.8} />
          <span>Excel'den Yapıştır</span>
        </button>
      </div>

      {error && (
        <div className="upload-error">
          <AlertTriangle size={12} strokeWidth={2} />
          <span>{error}</span>
        </div>
      )}

      {tab === 'file' && (
        <div className="upload-section">
          {!pickedFile && (
            <button
              type="button"
              className="upload-dropzone"
              onClick={() => fileInputRef.current?.click()}
            >
              <FileSpreadsheet size={28} strokeWidth={1.4} />
              <div className="upload-dropzone-title">Excel dosyası seç</div>
              <div className="upload-dropzone-hint">
                .xlsx veya .xls · en fazla 10 MB · ilk 5 sheet okunur
              </div>
            </button>
          )}
          {pickedFile && (
            <div className="upload-file-info">
              <FileSpreadsheet size={16} strokeWidth={1.6} />
              <span className="upload-file-name">{pickedFile.name}</span>
              <span className="upload-file-size">
                {(pickedFile.size / 1024).toFixed(1)} KB
              </span>
              <button
                type="button"
                className="btn-ghost btn-ghost--sm"
                onClick={() => {
                  setPickedFile(null);
                  setPreview(null);
                  if (fileInputRef.current) fileInputRef.current.value = '';
                }}
              >
                Değiştir
              </button>
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
            onChange={onFileSelected}
            style={{ display: 'none' }}
          />
        </div>
      )}

      {tab === 'paste' && (
        <div className="upload-section">
          <div className="upload-paste-fields">
            <label className="upload-field">
              <span className="upload-field-label">Tablo adı</span>
              <input
                type="text"
                className="upload-input"
                value={tableName}
                onChange={(e) => setTableName(e.target.value)}
                placeholder="örn. q4_hedefler"
              />
            </label>

            <label className="upload-field upload-field--inline">
              <input
                type="checkbox"
                checked={hasHeaderOverride !== false}
                onChange={(e) => setHasHeaderOverride(e.target.checked ? null : false)}
              />
              <span>İlk satır başlık satırı</span>
            </label>
          </div>

          <textarea
            className="upload-paste-area"
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            placeholder={
              "Excel'den bir aralık kopyalayıp buraya yapıştırın.\n\n" +
              "Örnek:\n" +
              "Branch\tBalance\tDate\n" +
              "LEVENT\t42300000\t2026-01-15\n" +
              "MASLAK\t38100000\t2026-01-15"
            }
            rows={8}
          />

          <button
            type="button"
            className="btn-secondary"
            onClick={parsePaste}
            disabled={!pasteText.trim() || parsing}
          >
            {parsing
              ? <><Loader2 size={12} className="ts-spin" /><span>İşleniyor…</span></>
              : <><FileText size={12} /><span>Önizle</span></>}
          </button>
        </div>
      )}

      {parsing && tab === 'file' && (
        <div className="upload-loading">
          <Loader2 size={14} className="ts-spin" /> Dosya işleniyor…
        </div>
      )}

      {preview && sheet && (
        <div className="upload-preview">
          {preview.filename && (
            <div className="upload-preview-source">
              {preview.kind === 'paste' ? <ClipboardPaste size={11} /> : <FileSpreadsheet size={11} />}
              <span>{preview.filename}</span>
              {preview.sheets.length > 1 && (
                <span className="upload-preview-source-meta">
                  · {preview.sheets.length} sheet
                </span>
              )}
            </div>
          )}

          {sheets.length > 1 && (
            <div className="upload-sheet-tabs">
              {sheets.map((s, i) => (
                <button
                  key={i}
                  type="button"
                  className={`upload-sheet-tab${i === activeSheet ? ' is-active' : ''}`}
                  onClick={() => setActiveSheet(i)}
                >
                  {s.display_name}
                </button>
              ))}
            </div>
          )}

          <div className="upload-sheet-meta">
            <span><Database size={11} /> {sheet.columns.length} kolon</span>
            <span><Hash size={11} /> {sheet.row_count.toLocaleString('tr-TR')} satır</span>
          </div>

          <div className="upload-preview-table-wrap ts-scroll">
            <table className="upload-preview-table">
              <thead>
                <tr>
                  {sheet.columns.map((c, i) => (
                    <th key={i}>
                      <div className="upload-th-name">{c.name}</div>
                      <div className="upload-th-meta">
                        <span className="upload-th-type">{TYPE_LABEL[c.type] || c.type.toLowerCase()}</span>
                        {c.display_name !== c.name && (
                          <span className="upload-th-orig" title={`Orijinal: ${c.display_name}`}>
                            ← {c.display_name}
                          </span>
                        )}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sheet.preview_rows.map((row, ri) => (
                  <tr key={ri}>
                    {row.map((v, ci) => (
                      <td key={ci}>{formatCell(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {sheet.row_count > sheet.preview_rows.length && (
            <div className="upload-preview-rest">
              + {(sheet.row_count - sheet.preview_rows.length).toLocaleString('tr-TR')} satır daha
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}


function formatCell(v) {
  if (v === null || v === undefined) return <span className="upload-cell-null">∅</span>;
  if (typeof v === 'number') return v.toLocaleString('tr-TR', { maximumFractionDigits: 4 });
  if (typeof v === 'string' && v.length > 40) return v.slice(0, 40) + '…';
  return String(v);
}
