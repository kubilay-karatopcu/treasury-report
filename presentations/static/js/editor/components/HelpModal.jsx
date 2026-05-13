import { useEffect, useState } from 'react';
import {
  TrendingUp, BarChart3, Activity, PieChart as PieIcon, Grid3x3,
  Table as TableIcon, FileText, Hash, Lightbulb, AlertTriangle, Copy, Check,
} from 'lucide-react';
import Modal from './Modal.jsx';
import { fetchHelp } from '../lib/api.js';

/**
 * Plot help / command reference modal. Content is JSON-driven so it can be
 * updated without code changes (presentations/help.json).
 *
 * Layout:
 *   - Intro (modes + tips)
 *   - Grid of plot-type cards (icon + description + data shape + style fields + examples)
 *   - Global commands section
 *   - Selected-block commands section
 *   - Limitations
 */
export default function HelpModal({ open, onClose }) {
  const [doc, setDoc] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!open || doc || err) return;
    fetchHelp().then(setDoc).catch((e) => setErr(e.message));
  }, [open, doc, err]);

  return (
    <Modal open={open} onClose={onClose} title="Komut Yardımı" size="lg">
      {err && <div className="help-error">{err}</div>}
      {!err && !doc && <div className="help-loading">Yükleniyor…</div>}
      {doc && <HelpBody doc={doc} />}
    </Modal>
  );
}


function HelpBody({ doc }) {
  return (
    <>
      {doc.intro && <Intro intro={doc.intro} />}

      {Array.isArray(doc.plot_types) && doc.plot_types.length > 0 && (
        <section className="help-section">
          <h4 className="help-section-title">Blok Tipleri</h4>
          <div className="help-grid">
            {doc.plot_types.map((p) => (
              <PlotCard key={p.type} plot={p} />
            ))}
          </div>
        </section>
      )}

      {doc.global_commands && <CommandList block={doc.global_commands} />}
      {doc.selected_commands && <CommandList block={doc.selected_commands} />}
      {doc.limitations && <Limitations block={doc.limitations} />}
    </>
  );
}


function Intro({ intro }) {
  return (
    <section className="help-section help-intro">
      {intro.lead && <p className="help-lead">{intro.lead}</p>}
      {Array.isArray(intro.modes) && (
        <div className="help-modes">
          {intro.modes.map((m, i) => (
            <div key={i} className="help-mode">
              <div className="help-mode-name">{m.name}</div>
              <div className="help-mode-when">{m.when}</div>
              <div className="help-mode-scope">{m.scope}</div>
            </div>
          ))}
        </div>
      )}
      {Array.isArray(intro.tips) && intro.tips.length > 0 && (
        <ul className="help-tips">
          {intro.tips.map((t, i) => (
            <li key={i}>
              <Lightbulb size={11} strokeWidth={1.8} />
              <span>{t}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}


const ICON_MAP = {
  "trending-up": TrendingUp,
  "bar-chart-3": BarChart3,
  "activity":    Activity,
  "pie-chart":   PieIcon,
  "grid-3x3":    Grid3x3,
  "table":       TableIcon,
  "file-text":   FileText,
  "hash":        Hash,
};

function PlotCard({ plot }) {
  const Icon = ICON_MAP[plot.icon] || FileText;
  return (
    <div className="help-card">
      <header className="help-card-header">
        <span className="help-card-icon"><Icon size={14} strokeWidth={1.8} /></span>
        <span className="help-card-label">{plot.label}</span>
        <code className="help-card-type">{plot.type}</code>
      </header>
      <p className="help-card-desc">{plot.description}</p>
      {plot.data_shape && (
        <div className="help-card-row">
          <span className="help-card-row-label">Veri:</span>
          <span className="help-card-row-text">{plot.data_shape}</span>
        </div>
      )}
      {Array.isArray(plot.style_fields) && plot.style_fields.length > 0 && (
        <details className="help-card-fields">
          <summary>Stil alanları ({plot.style_fields.length})</summary>
          <ul>
            {plot.style_fields.map((f, i) => (
              <li key={i}>
                <code className="help-field-key">{f.key}</code>
                <span className="help-field-type">{f.type}</span>
                <span className="help-field-desc">{f.desc}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {Array.isArray(plot.examples) && plot.examples.length > 0 && (
        <div className="help-card-examples">
          <div className="help-examples-label">Örnek komutlar:</div>
          <ul>
            {plot.examples.map((ex, i) => (
              <CommandRow key={i} text={ex} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}


function CommandList({ block }) {
  return (
    <section className="help-section">
      <h4 className="help-section-title">{block.title}</h4>
      <ul className="help-cmd-list">
        {(block.examples || []).map((ex, i) => (
          <CommandRow key={i} text={ex} />
        ))}
      </ul>
    </section>
  );
}


function Limitations({ block }) {
  return (
    <section className="help-section help-limitations">
      <h4 className="help-section-title">
        <AlertTriangle size={12} strokeWidth={2} />
        {block.title}
      </h4>
      <ul>
        {(block.items || []).map((t, i) => <li key={i}>{t}</li>)}
      </ul>
    </section>
  );
}


function CommandRow({ text }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    });
  }
  return (
    <li className="help-cmd">
      <span className="help-cmd-text">{text}</span>
      <button
        type="button"
        className="help-cmd-copy"
        onClick={copy}
        title="Komutu panoya kopyala"
      >
        {copied
          ? <Check size={11} strokeWidth={2.2} />
          : <Copy size={11} strokeWidth={1.8} />}
      </button>
    </li>
  );
}
