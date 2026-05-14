import { useEffect, useRef, useState } from 'react';
import {
  Upload, FileSpreadsheet, ClipboardPaste, Loader2, CheckCircle2,
  AlertTriangle, X,
} from 'lucide-react';
import Modal from './Modal.jsx';
import {
  uploadParseFile, uploadParsePaste,
  uploadCommitFile, uploadCommitPaste,
} from '../lib/api.js';

/**
 * Excel upload / paste modal.
 *
 * Flow:
 *   1. User chooses "Dosya" or "Yapıştır" tab
 *   2. Provides input → onPreview triggers a parse call (no save)
 *   3. Preview renders: sheet tabs + column types + first 10 rows
 *   4. User clicks "Kaydet" → commit call saves to S3 + manifest
 *   5. onCommit callback fires → parent reloads sources
 *
 * Props:
 *   - open        : boolean
 *   - onClose()   : called on dismiss / cancel / after success
 *   - onCommit()  : called after a successful commit; parent should refresh sources
 */
export default function UploadModal({ open, onClose, onCommit }) {
  const [tab, setTab] = useState('file');     // 'file' | 'paste'
  const [file, setFile] = useState(null);
  const [paste, setPaste] = useState('');
  const [tableName, setTableName] = useState('');
  const [hasHeader, setHasHeader] = useState(true);
  const [autoDetectHeader, setAutoDetectHeader] = useState(true);
  const [preview, setPreview] = useState(null);
  const [activeSheetIdx, setActiveSheetIdx] = useState(0);
  const [error, setError] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [committing, setCommitting] = useState(false);

  // Reset on close
  useEffect(() => {
    if (!open) {
      setFile(null);
      setPaste('');
      setTableName('');
      setHasHeader(true);
      setAutoDetectHeader(true);
      setPreview(null);
      setActiveSheetIdx(0);
      setError(null);
      setLoadingPreview(false);
      setCommitting(false);
    }
  }, [open]);

  async function refreshPreview(opts = {}) {
    setError(null);
    setLoadingPreview(true);
    try {
      let data;
      if (tab === 'file') {
        if (!file) { setPreview(null); return; }
        if (file.size > 10 * 1024 * 1024) {
          throw new Error(`Dosya çok büyük: ${(file.size / 1024 / 1024).toFixed(1)} MB (max 10 MB).`);
        }
        data = await uploadParseFile(file);
      } else {
        if (!paste.trim()) { setPreview(null); return; }
        data = await uploadParsePaste({
          paste,
          tableName: tableName || undefined,
          hasHeader: opts.autoHeader ? null : hasHeader,
        });
      }
      setPreview(data);
      setActiveSheetIdx(0);
    } catch (e) {
      setError(e.message);
      setPreview(null);
    } finally {
      setLoadingPreview(false);
    }
  }

  // File: re-parse whenever file changes
  useEffect(() => {
    if (open && tab === 'file' && file) refreshPreview();
  }, [file, open]);

  // Paste: re-parse on demand (button click), header changes, name changes,
  // or auto-detect toggle. We DON'T re-parse on every keystroke (too noisy).
  function onParsePasteClick() {
    refreshPreview({ autoHeader: autoDetectHeader });
  }

  async function doCommit() {
    setError(null);
    setCommitting(true);
    try {
      let result;
      if (tab === 'file') {
        if (!file) throw new Error('Dosya seçili değil.');
        result = await uploadCommitFile(file);
      } else {
        if (!paste.trim()) throw new Error('Yapıştırma içeriği boş.');
        result = await uploadCommitPaste({
          paste,
          tableName: tableName || undefined,
          hasHeader: autoDetectHeader ? null : hasHeader,
        });
      }
      onCommit?.(result);
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setCommitting(false);
    }
  }

  const canCommit = !!preview && !loadingPreview && !committing;

  return (
    <Modal open={open} onClose={onClose} title="Veri Yükle" size="lg">
      <div className="upload-modal">
        <div className="upload-tabs">
          <button
            type="button"
            className={`upload-tab${tab === 'file' ? ' is-active' : ''}`}
            onClick={() => { setTab('file'); setPreview(null); setError(null); }}
          >
            <FileSpreadsheet size={13} strokeWidth={1.8} />
            <span>Dosya Yükle</span>
          </button>
          <button
            type="button"
            className={`upload-tab${tab === 'paste' ? ' is-active' : ''}`}
            onClick={() => { setTab('paste'); setPreview(null); setError(null); }}
          >
            <ClipboardPaste size={13} strokeWidth={1.8} />
            <span>Excel'den Yapıştır</span>
          </button>
        </div>

        {tab === 'file'
          ? <FilePane file={file} onFile={setFile} />
          : <PastePane
              paste={paste} onPaste={setPaste}
              tableName={tableName} onTableName={setTableName}
              autoDetectHeader={autoDetectHeader}
              onAutoDetectHeader={setAutoDetectHeader}
              hasHeader={hasHeader} onHasHeader={setHasHeader}
              onParse={onParsePasteClick}
              loading={loadingPreview}
            />
        }

        {error && (
          <div className="upload-error">
            <AlertTriangle size={12} strokeWidth={2} />
            <span>{error}</span>
          </div>
        )}

        {loadingPreview && !preview && (
          <div className="upload-loading">
            <Loader2 size={14} className="ts-spin" />
            <span>Ayrıştırılıyor…</span>
          </div>
        )}

        {preview && (
          <PreviewPanel
            preview={preview}
            activeSheetIdx={activeSheetIdx}
            onActiveSheet={setActiveSheetIdx}
            overrideName={tab === 'paste' && tableName.trim() ? tableName.trim() : null}
          />
        )}

        <div className="upload-modal-footer">
          <button type="button" className="btn-ghost" onClick={onClose}>
            İptal
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={doCommit}
            disabled={!canCommit}
            title={canCommit ? 'S3\'e kaydet' : 'Önce bir dosya yükle veya tablo yapıştır'}
          >
            {committing
              ? <><Loader2 size={12} className="ts-spin" /><span>Kaydediliyor…</span></>
              : <><CheckCircle2 size={12} strokeWidth={2} /><span>Kaydet</span></>}
          </button>
        </div>
      </div>
    </Modal>
  );
}


/* ── File-input pane ──────────────────────────────────────────────────── */

function FilePane({ file, onFile }) {
  const inputRef = useRef(null);

  function onDrop(e) {
    e.preventDefault();
    const f = e.dataTransfer.files?.[0];
    if (f) onFile(f);
  }

  return (
    <div className="upload-pane">
      <label
        className="upload-dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
          onChange={(e) => onFile(e.target.files?.[0] || null)}
          style={{ display: 'none' }}
        />
        <Upload size={20} strokeWidth={1.5} />
        {file ? (
          <>
            <div className="upload-dropzone-name">{file.name}</div>
            <div className="upload-dropzone-size">
              {(file.size / 1024 / 1024).toFixed(2)} MB
            </div>
            <button
              type="button"
              className="upload-dropzone-clear"
              onClick={(e) => { e.preventDefault(); onFile(null); }}
            >
              <X size={11} /> Kaldır
            </button>
          </>
        ) : (
          <>
            <div className="upload-dropzone-prompt">Dosyayı sürükle veya tıkla</div>
            <div className="upload-dropzone-hint">.xlsx, .xls · maksimum 10 MB · 5 sheet</div>
          </>
        )}
      </label>
    </div>
  );
}


/* ── Paste pane ────────────────────────────────────────────────────────── */

function PastePane({
  paste, onPaste, tableName, onTableName,
  autoDetectHeader, onAutoDetectHeader,
  hasHeader, onHasHeader,
  onParse, loading,
}) {
  return (
    <div className="upload-pane">
      <div className="upload-paste-meta">
        <label className="upload-paste-field">
          <span>Tablo adı</span>
          <input
            type="text"
            value={tableName}
            onChange={(e) => onTableName(e.target.value)}
            placeholder="yapistirilan (otomatik artar)"
            className="upload-paste-name-input"
          />
        </label>
        <label className="upload-paste-check">
          <input
            type="checkbox"
            checked={autoDetectHeader}
            onChange={(e) => onAutoDetectHeader(e.target.checked)}
          />
          <span>Başlığı otomatik algıla</span>
        </label>
        {!autoDetectHeader && (
          <label className="upload-paste-check">
            <input
              type="checkbox"
              checked={hasHeader}
              onChange={(e) => onHasHeader(e.target.checked)}
            />
            <span>İlk satır başlık</span>
          </label>
        )}
      </div>

      <textarea
        className="upload-paste-area"
        rows={8}
        placeholder="Excel'den bir tablo aralığı seç ve Ctrl+V ile buraya yapıştır..."
        value={paste}
        onChange={(e) => onPaste(e.target.value)}
      />

      <div className="upload-paste-row">
        <span className="upload-paste-hint">
          Tab-ayrılmış değer (TSV) bekleniyor. Excel "kopyala" zaten bu formatta yapıştırır.
        </span>
        <button
          type="button"
          className="btn-secondary"
          onClick={onParse}
          disabled={loading || !paste.trim()}
        >
          {loading
            ? <><Loader2 size={11} className="ts-spin" /><span>İşleniyor…</span></>
            : <span>Önizle</span>}
        </button>
      </div>
    </div>
  );
}


/* ── Preview panel (sheets, columns, rows) ─────────────────────────────── */

function PreviewPanel({ preview, activeSheetIdx, onActiveSheet, overrideName }) {
  const sheets = preview.sheets || [];
  if (!sheets.length) {
    return <div className="upload-empty">Sheet bulunamadı.</div>;
  }
  const sheet = sheets[activeSheetIdx] || sheets[0];
  const displayFilename = overrideName || preview.filename;
  const displaySheetName = overrideName || sheet.display_name || sheet.name;

  return (
    <div className="upload-preview">
      <div className="upload-preview-header">
        <span className="upload-preview-filename">{displayFilename}</span>
        <span className="upload-preview-sep">·</span>
        <span className="upload-preview-summary">
          {sheets.length} sheet
          {preview.size ? ` · ${(preview.size / 1024).toFixed(1)} KB` : ''}
        </span>
      </div>

      {sheets.length > 1 && (
        <div className="upload-sheet-tabs">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              type="button"
              className={`upload-sheet-tab${i === activeSheetIdx ? ' is-active' : ''}`}
              onClick={() => onActiveSheet(i)}
              title={`SQL adı: ${s.name}`}
            >
              {s.display_name || s.name}
              <span className="upload-sheet-rows">({s.row_count})</span>
            </button>
          ))}
        </div>
      )}

      <div className="upload-sheet-meta">
        <span><strong>{displaySheetName}</strong></span>
        <span className="upload-preview-sep">·</span>
        <span>{sheet.row_count.toLocaleString('tr-TR')} satır</span>
        <span className="upload-preview-sep">·</span>
        <span>{sheet.columns.length} kolon</span>
        <span className="upload-preview-sep">·</span>
        <span className="upload-sheet-id">SQL: <code>{sheet.name}</code></span>
      </div>

      <div className="upload-preview-table-wrap ts-scroll">
        <table className="upload-preview-table">
          <thead>
            <tr>
              {sheet.columns.map((c, i) => (
                <th key={i}>
                  <div className="upload-col-name">{c.name}</div>
                  <div className="upload-col-meta">
                    <span className={`upload-col-type upload-col-type--${c.type.toLowerCase()}`}>
                      {c.type}
                    </span>
                    {c.display_name && c.display_name !== c.name && (
                      <span className="upload-col-orig" title="Orijinal kolon adı">
                        {c.display_name}
                      </span>
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(sheet.preview_rows || []).map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci}>{cell === null ? <span className="upload-null">∅</span> : String(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sheet.row_count > (sheet.preview_rows?.length || 0) && (
        <div className="upload-preview-more">
          + {(sheet.row_count - (sheet.preview_rows?.length || 0)).toLocaleString('tr-TR')} satır daha
        </div>
      )}
    </div>
  );
}