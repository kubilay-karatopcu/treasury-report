/* Phase 9.c — Keşif chat panel.
 *
 * Lives at the bottom of the left rail (same vertical position as the
 * Hazırlık + Sunum chat affordances, same Inter font stack, same row
 * heights). Always visible but collapsible to a single header bar so a
 * busy user can free up space.
 *
 * The LLM's job is narrow (spec §5.4): propose tables. Each proposal
 * renders as an inline card with a "Sepete ekle" CTA. When proposals
 * arrive, their ids flow up to the parent (`onHighlight`) so
 * GraphCanvas pulses the matching nodes for ~3 seconds.
 *
 * Exposes an imperative `chatHandle` ref the parent uses for two
 * cross-component actions:
 *   - openWithPrompt(text)  → expand + prefill the input + focus.
 *     Wired from the table detail card's "Sohbette göster" button.
 *   - clear()               → wipe history (also triggers the DELETE).
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import {
  MessageCircle, Send, Trash2, Plus, Loader2, ChevronDown, ChevronUp,
} from "lucide-react";


const ChatDrawer = forwardRef(function ChatDrawer({
  chatSendUrl,
  chatClearUrl,
  seedHistory,
  basketTableIds,
  onAddToBasket,
  onHighlight,
}, ref) {
  const [open, setOpen] = useState(true);
  const [history, setHistory] = useState(() => seedHistory || []);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const threadRef = useRef(null);
  const inputRef = useRef(null);

  // Auto-scroll the thread to the latest message on each append.
  useEffect(() => {
    if (!threadRef.current) return;
    threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [history, open]);

  const send = useCallback(async (overrideText) => {
    const text = (overrideText !== undefined ? overrideText : input).trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    const optimisticUser = {
      role: "user",
      text,
      ts: new Date().toISOString(),
      pending: true,
    };
    setHistory((prev) => [...prev, optimisticUser]);
    setInput("");

    try {
      const resp = await fetch(chatSendUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ message: text }),
      });
      if (!resp.ok) throw new Error(`chat HTTP ${resp.status}`);
      const data = await resp.json();
      setHistory(data.history || []);
      const ids = data.assistant_message?.highlights || [];
      if (ids.length && onHighlight) onHighlight(ids);
    } catch (err) {
      console.warn("Keşif chat:", err);
      setError("Bir sorun oldu, tekrar dener misiniz?");
      setHistory((prev) => prev.filter((t) => t !== optimisticUser));
    } finally {
      setSending(false);
    }
  }, [input, sending, chatSendUrl, onHighlight]);

  const onKeyDown = useCallback((e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      send();
    }
  }, [send]);

  const clearChat = useCallback(async () => {
    if (!chatClearUrl || sending) return;
    if (!window.confirm("Sohbet geçmişini silinsin mi?")) return;
    try {
      const resp = await fetch(chatClearUrl, {
        method: "DELETE",
        credentials: "include",
      });
      if (!resp.ok) throw new Error(`clear HTTP ${resp.status}`);
      setHistory([]);
    } catch (err) {
      console.warn("Keşif chat clear:", err);
    }
  }, [chatClearUrl, sending]);

  // Imperative handle for parent components to open + prefill the chat
  // (e.g., the detail card's "Sohbette göster" button).
  useImperativeHandle(ref, () => ({
    openWithPrompt(text) {
      setOpen(true);
      setInput(text);
      // Focus + caret at end so the user can keep typing immediately.
      setTimeout(() => {
        const el = inputRef.current;
        if (!el) return;
        el.focus();
        const len = el.value.length;
        try { el.setSelectionRange(len, len); } catch { /* IE shim — no-op */ }
      }, 50);
    },
    clear: clearChat,
  }), [clearChat]);

  return (
    <section className={`kesif-chat${open ? "" : " kesif-chat--collapsed"}`} aria-label="Keşif sohbeti">
      <header className="kesif-chat__header" onClick={() => setOpen((v) => !v)}>
        <span className="kesif-chat__title">
          <MessageCircle size={12} />
          Sohbet
        </span>
        <span className="kesif-chat__header-actions" onClick={(e) => e.stopPropagation()}>
          {history.length > 0 && open && (
            <button
              type="button"
              className="kesif-chat__icon-btn"
              onClick={clearChat}
              title="Geçmişi sil"
            >
              <Trash2 size={11} />
            </button>
          )}
          <button
            type="button"
            className="kesif-chat__icon-btn"
            onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
            title={open ? "Daralt" : "Aç"}
            aria-expanded={open}
          >
            {open ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
          </button>
        </span>
      </header>

      {open && (
        <>
          <div className="kesif-chat__thread" ref={threadRef}>
            {history.length === 0 && (
              <div className="kesif-chat__empty">
                Ne aradığını birkaç kelimeyle yaz; sana uygun tabloları öneririm.
              </div>
            )}
            {history.map((turn, i) => (
              <ChatTurn
                key={i}
                turn={turn}
                basketTableIds={basketTableIds}
                onAddToBasket={onAddToBasket}
                onHighlight={onHighlight}
              />
            ))}
            {sending && (
              <div className="kesif-chat__bubble kesif-chat__bubble--assistant kesif-chat__bubble--thinking">
                <Loader2 size={10} className="kesif-spin" /> Düşünüyor…
              </div>
            )}
            {error && <div className="kesif-chat__error">{error}</div>}
          </div>

          <footer className="kesif-chat__input-row">
            <textarea
              ref={inputRef}
              className="kesif-chat__input"
              placeholder="Aradığın veri… (Ctrl/Cmd+Enter)"
              value={input}
              rows={2}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={sending}
              aria-label="Mesaj"
            />
            <button
              type="button"
              className="kesif-chat__send"
              onClick={() => send()}
              disabled={!input.trim() || sending}
              title="Gönder (Ctrl/Cmd+Enter)"
            >
              {sending ? <Loader2 size={12} className="kesif-spin" /> : <Send size={12} />}
            </button>
          </footer>
        </>
      )}
    </section>
  );
});

export default ChatDrawer;


function ChatTurn({ turn, basketTableIds, onAddToBasket, onHighlight }) {
  const isUser = turn.role === "user";
  const isError = turn.status === "error";

  if (isUser) {
    return (
      <div className="kesif-chat__bubble kesif-chat__bubble--user">
        {turn.text}
      </div>
    );
  }

  return (
    <div className={`kesif-chat__bubble kesif-chat__bubble--assistant${isError ? " kesif-chat__bubble--error" : ""}`}>
      {turn.text && <div className="kesif-chat__text">{turn.text}</div>}
      {turn.proposals && turn.proposals.length > 0 && (
        <div className="kesif-chat__proposals">
          {turn.proposals.map((p, i) => (
            <ProposalCard
              key={i}
              proposal={p}
              inBasket={basketTableIds.has(`${p.schema}.${p.name}`)}
              onAddToBasket={onAddToBasket}
              onHighlight={onHighlight}
            />
          ))}
        </div>
      )}
      {turn.dropped && turn.dropped.length > 0 && (
        <div className="kesif-chat__dropped" title="Bu öneriler katalogda bulunmadığı için listelenmedi.">
          {turn.dropped.length} öneri katalog dışı (atlandı)
        </div>
      )}
    </div>
  );
}


function ProposalCard({ proposal, inBasket, onAddToBasket, onHighlight }) {
  const tid = `${proposal.schema}.${proposal.name}`;
  const score = Math.round((proposal.match_score ?? 0) * 100);
  return (
    <div
      className="kesif-chat__card"
      onMouseEnter={() => onHighlight?.([tid])}
    >
      <div className="kesif-chat__card-head">
        <div className="kesif-chat__card-name">{proposal.name}</div>
        <div className="kesif-chat__card-score" title={`Match score: ${proposal.match_score?.toFixed(2)}`}>
          {score}%
        </div>
      </div>
      <div className="kesif-chat__card-schema">{proposal.schema}</div>
      {proposal.rationale && (
        <div className="kesif-chat__card-rationale">{proposal.rationale}</div>
      )}
      {proposal.suggested_companion && (
        <div className="kesif-chat__card-companion">
          + Birlikte: <strong>{proposal.suggested_companion}</strong>
        </div>
      )}
      <button
        type="button"
        className="kesif-btn kesif-btn--primary kesif-chat__card-add"
        onClick={() => onAddToBasket?.(tid)}
        disabled={inBasket}
      >
        <Plus size={10} />
        {inBasket ? "Sepette" : "Sepete ekle"}
      </button>
    </div>
  );
}
