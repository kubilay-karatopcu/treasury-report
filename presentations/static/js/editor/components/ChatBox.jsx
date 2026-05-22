import { useEffect, useRef, useState } from 'react';
import {
  MessageSquare, X, Lock, Send, Loader2, HelpCircle,
  Library, Plus, XCircle,
} from 'lucide-react';
import useStore from '../lib/store.js';
import { postChatMessage, openChatStream, fetchLibraryBlock } from '../lib/api.js';
import HelpModal from './HelpModal.jsx';

export default function ChatBox({ compact = false }) {
  const [input, setInput] = useState('');
  const [helpOpen, setHelpOpen] = useState(false);
  const messagesRef = useRef(null);

  const manifest         = useStore((s) => s.manifest);
  const chatHistory      = useStore((s) => s.chatHistory);
  const loading          = useStore((s) => s.loading);
  const setLoading       = useStore((s) => s.setLoading);
  const addChatMessage   = useStore((s) => s.addChatMessage);
  const applyPatches     = useStore((s) => s.applyPatches);
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const hydrateFilterDefaults = useStore((s) => s.hydrateFilterDefaults);
  const applyFilters     = useStore((s) => s.applyFilters);

  const selectedBlock = findBlock(manifest?.blocks, selectedBlockId);
  const isLocked = !!selectedBlock?.locked;

  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  }, [chatHistory.length, loading]);

  async function send() {
    const msg = input.trim();
    if (!msg || loading || isLocked) return;

    setInput('');
    addChatMessage({ role: 'user', text: msg });
    setLoading(true);

    // Phase 7: if this turn seeds a dashboard filter (e.g. a concept-mapped
    // value from the prompt), auto-apply it after the turn so the block
    // renders filtered without a manual Güncelle.
    let seededFilter = false;

    try {
      const { token } = await postChatMessage(msg, selectedBlockId);
      openChatStream(token, {
        onStatus: (data) => {
          if (data.phase === 'noop' && data.explanation) {
            addChatMessage({ role: 'assistant', text: data.explanation, status: 'noop' });
          }
        },
        onPatch: (data) => {
          if (Array.isArray(data.patches) && data.patches.length > 0) {
            applyPatches(data.patches);
            if (data.patches.some((p) => typeof p.path === 'string' && p.path.startsWith('/filters'))) {
              seededFilter = true;
            }
          }
          if (data.explanation) {
            addChatMessage({ role: 'assistant', text: data.explanation });
          }
        },
        onSuggestion: (data) => {
          // F.5: LLM library bloğu önerdi — chat'e kart olarak ekle
          if (data.explanation) {
            addChatMessage({ role: 'assistant', text: data.explanation });
          }
          for (const s of (data.suggestions || [])) {
            addChatMessage({
              role: 'assistant',
              kind: 'library_suggestion',
              suggestion: s,
            });
          }
        },
        onError: (data) => {
          addChatMessage({
            role: 'assistant',
            text: data.message || 'Bilinmeyen hata.',
            status: 'error',
          });
          setLoading(false);
        },
        onDone: () => {
          setLoading(false);
          // Seed the new filter's default into filterState + apply once, so
          // the freshly-authored block shows filtered data immediately.
          if (seededFilter) {
            hydrateFilterDefaults();
            applyFilters().catch((e) => console.warn('auto-apply after chat failed:', e));
          }
        },
      });
    } catch (err) {
      addChatMessage({ role: 'assistant', text: err.message, status: 'error' });
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      send();
    }
  }

  const isBlockMode = !!selectedBlockId;
  const placeholder = isBlockMode
    ? blockPlaceholder(selectedBlock)
    : 'Tüm sunuya yönelik bir komut yazın… (örn. "NII bölümüne forecast aralığı ekle")';

  return (
    <>
      <div className={`chat-box${compact ? ' chat-box--compact' : ''}`}>
        {!compact && (
          <div className="chat-box-header">
            <MessageSquare size={11} strokeWidth={2} />
            {isBlockMode ? (
              <>
                <span>Düzenleniyor:</span>
                <span className="chat-box-target" title={selectedBlock?.title}>
                  {selectedBlock?.title || selectedBlockId}
                </span>
                <button
                  type="button"
                  className="chat-box-target-clear"
                  onClick={() => setSelectedBlock(null)}
                  title="Genel moda dön"
                >
                  <X size={12} />
                </button>
              </>
            ) : (
              <>
                <span>Genel Komut</span>
                <button
                  type="button"
                  className="chat-box-help"
                  onClick={() => setHelpOpen(true)}
                  title="Kullanılabilir blok tipleri ve örnek komutlar"
                >
                  <HelpCircle size={12} strokeWidth={1.8} />
                </button>
              </>
            )}
          </div>
        )}

        {isLocked && (
          <div className="chat-box-locked-warn">
            <Lock size={11} />
            <span>Bu blok kilitli. Düzenlemek için kilidi kaldırın.</span>
          </div>
        )}

        <div className="chat-messages ts-scroll" ref={messagesRef}>
          {!compact && chatHistory.length === 0 && !loading && (
            <div className="chat-empty">
              Bir komut yaz, ya da bir bloğa tıklayıp onu hedefle.
            </div>
          )}
          {chatHistory.map((m, i) => {
            if (m.kind === 'library_suggestion') {
              return (
                <LibrarySuggestionCard
                  key={`${m.ts || i}_${i}`}
                  suggestion={m.suggestion}
                  manifest={manifest}
                  onConsumed={(updates) => {
                    // updates.dismissed/added → mesajı işaretle (idempotent)
                    addChatMessage({
                      role: 'assistant',
                      text: updates.added
                        ? `'${m.suggestion.name}' eklendi.`
                        : 'Öneri reddedildi. İstersen yeni baştan üretmeyi söyleyebilirsin.',
                      status: updates.added ? undefined : 'noop',
                    });
                  }}
                />
              );
            }
            return (
              <div
                key={`${m.ts || i}_${i}`}
                className={
                  `chat-msg chat-msg--${m.role}`
                  + (m.status ? ` chat-msg--${m.status}` : '')
                }
              >
                {m.text}
              </div>
            );
          })}
          {loading && <div className="chat-msg chat-msg--loading">Düşünüyor…</div>}
        </div>

        {compact ? (
          <div className="chat-input-wrap chat-input-wrap--compact">
            <textarea
              className="chat-input"
              placeholder={placeholder}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={4}
              disabled={loading || isLocked}
            />
            <button
              type="button"
              className="chat-input-send"
              onClick={send}
              disabled={loading || isLocked || !input.trim()}
              title="Gönder (Ctrl/⌘ + Enter)"
            >
              {loading
                ? <Loader2 size={16} className="ts-spin" />
                : <Send size={16} strokeWidth={2} />}
            </button>
          </div>
        ) : (
          <>
            <textarea
              className="chat-input"
              placeholder={placeholder}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={3}
              disabled={loading || isLocked}
            />
            <div className="chat-footer">
              <span className="chat-footer-hint">⌘/Ctrl + Enter ile gönder</span>
              <button
                type="button"
                className="btn-primary"
                onClick={send}
                disabled={loading || isLocked || !input.trim()}
              >
                {loading
                  ? <><Loader2 size={12} className="ts-spin" /><span>İşleniyor…</span></>
                  : <><Send size={12} strokeWidth={2} /><span>Gönder</span></>}
              </button>
            </div>
          </>
        )}
      </div>

      <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </>
  );
}


function LibrarySuggestionCard({ suggestion, manifest, onConsumed }) {
  const addLibraryToSec = useStore((s) => s.addLibraryBlockToSection);
  const [busy, setBusy]   = useState(false);
  const [done, setDone]   = useState(false);

  // Hangi section'a eklenecek? target_path "/blocks/N/children/-" parse et.
  // Yoksa son section'a düş.
  function pickSection() {
    const sections = manifest?.blocks || [];
    const tp = suggestion.target_path || '';
    const m = tp.match(/^\/blocks\/(\d+)/);
    if (m) {
      const idx = parseInt(m[1], 10);
      const sec = sections[idx];
      if (sec && sec.type === 'section_header') return sec.id;
    }
    // Fallback: son section
    for (let i = sections.length - 1; i >= 0; i--) {
      if (sections[i].type === 'section_header') return sections[i].id;
    }
    return null;
  }

  async function handleAdd() {
    if (busy || done) return;
    const sectionId = pickSection();
    if (!sectionId) {
      alert('Eklenebilecek bir bölüm yok. Önce bir bölüm oluştur.');
      return;
    }
    setBusy(true);
    try {
      const { block } = await fetchLibraryBlock(suggestion.library_id);
      addLibraryToSec(sectionId, block);
      setDone(true);
      onConsumed?.({ added: true });
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  function handleDismiss() {
    if (done) return;
    setDone(true);
    onConsumed?.({ added: false });
  }

  if (done) return null;

  return (
    <div className="chat-msg chat-msg--assistant chat-suggestion-card">
      <div className="chat-suggestion-head">
        <Library size={13} strokeWidth={1.8} />
        <span className="chat-suggestion-eyebrow">Kütüphaneden öneri</span>
      </div>
      <div className="chat-suggestion-title">{suggestion.name}</div>
      {suggestion.description && (
        <div className="chat-suggestion-desc">{suggestion.description}</div>
      )}
      {suggestion.reason && (
        <div className="chat-suggestion-reason">→ {suggestion.reason}</div>
      )}
      <div className="chat-suggestion-meta">
        <span className="lib-tag-chip">{suggestion.block_type}</span>
        {(suggestion.tags || []).map((t) => (
          <span key={t} className="lib-tag-chip">{t}</span>
        ))}
      </div>
      <div className="chat-suggestion-actions">
        <button
          type="button"
          className="lib-btn lib-btn--add"
          onClick={handleAdd}
          disabled={busy}
        >
          <Plus size={12} strokeWidth={2} />
          {busy ? 'Ekleniyor…' : 'Bunu ekle'}
        </button>
        <button
          type="button"
          className="lib-btn"
          onClick={handleDismiss}
          disabled={busy}
        >
          <XCircle size={12} strokeWidth={1.8} />
          İstemiyorum
        </button>
      </div>
    </div>
  );
}


function blockPlaceholder(block) {
  if (!block) return 'Değişikliği tarif edin…';
  switch (block.type) {
    case 'kpi':         return 'örn. YTD\'ye çevir, USD\'de göster…';
    case 'bar_chart':   return 'örn. ilk 5 şubeyi göster, artan sıralama…';
    case 'line_chart':
    case 'area_chart':  return 'örn. tahmin çizgisini kaldır, son 6 ay…';
    case 'pie_chart':   return 'örn. küçük dilimleri "Diğer"de topla…';
    case 'data_table':  return 'örn. tutara göre azalan sırala, ilk 20 satır…';
    case 'narrative':   return 'örn. daha temkinli ton, 2 cümleye düşür…';
    case 'section_header': return 'örn. başlığı "Q4 Özeti" yap…';
    default:            return 'Değişikliği tarif edin…';
  }
}

function findBlock(blocks, id) {
  if (!id || !Array.isArray(blocks)) return null;
  for (const b of blocks) {
    if (b.id === id) return b;
    if (Array.isArray(b.children)) {
      for (const c of b.children) {
        if (c.id === id) return c;
        // Carousel slides — 3. seviye
        if (c.type === 'carousel' && Array.isArray(c.children)) {
          for (const s of c.children) {
            if (s.id === id) return s;
          }
        }
      }
    }
  }
  return null;
}