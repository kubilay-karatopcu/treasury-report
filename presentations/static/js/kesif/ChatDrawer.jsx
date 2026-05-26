/* Phase 9.c — Keşif chat panel.
 *
 * Visual style matches the Sunum (presentation view) "GENEL KOMUT"
 * chat-box: same .chat-box / .chat-messages / .chat-msg / .chat-input
 * hierarchy from editor.css. The outer .kesif-chat element only handles
 * positioning inside the left rail; everything inside uses the shared
 * Sunum classes so the two screens read identically.
 *
 * The LLM's job is narrow (spec §5.4): propose tables. Each proposal
 * renders as an inline card with a "Sepete ekle" CTA. When proposals
 * arrive, their ids flow up to the parent (`onHighlight`) so
 * GraphCanvas pulses the matching nodes for ~3 seconds.
 *
 * Exposes an imperative `chatHandle` ref the parent uses for two
 * cross-component actions:
 *   - openWithPrompt(text)  → focus + prefill the input.
 *     Wired from the table detail card's "Sohbette göster" button.
 *   - clear()               → wipe history (also triggers the DELETE).
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  MessageSquare, Send, Trash2, Plus, Loader2,
} from "lucide-react";


const ChatDrawer = forwardRef(function ChatDrawer({
  chatSendUrl,
  chatClearUrl,
  seedHistory,
  basketTableIds,
  onAddToBasket,
  onHighlight,
  title = "Sohbet",
  placeholder = "Aradığın veri… (Ctrl/Cmd+Enter)",
  emptyHint = "Ne aradığını birkaç kelimeyle yaz; sana uygun tabloları öneririm.",
  readOnly = false,
  readOnlyHint = null,
}, ref) {
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
  }, [history, sending]);

  const send = useCallback(async (overrideText) => {
    if (readOnly) return;
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
  }, [input, sending, chatSendUrl, onHighlight, readOnly]);

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

  // Imperative handle for parent components to prefill + focus.
  useImperativeHandle(ref, () => ({
    openWithPrompt(text) {
      setInput(text);
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
    <section className="kesif-chat" aria-label={title}>
      <div className="chat-box">
        <div className="chat-box-header">
          <MessageSquare size={11} strokeWidth={2} />
          <span>{title}</span>
          {history.length > 0 && !readOnly && (
            <button
              type="button"
              className="chat-box-target-clear"
              onClick={clearChat}
              title="Geçmişi sil"
              style={{ marginLeft: "auto" }}
            >
              <Trash2 size={12} />
            </button>
          )}
        </div>

        <div className="chat-messages ts-scroll" ref={threadRef}>
          {history.length === 0 && !sending && (
            <div className="chat-empty">{emptyHint}</div>
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
          {sending && <div className="chat-msg chat-msg--loading">Düşünüyor…</div>}
          {error && <div className="chat-msg chat-msg--error">{error}</div>}
        </div>

        {readOnly ? (
          <div className="chat-box-locked-warn" style={{ marginTop: 8, marginBottom: 0 }}>
            {readOnlyHint || "Bu sohbet şu an aktif değil."}
          </div>
        ) : (
          <>
            {/* Non-compact Sunum sidebar layout: textarea has a real
                border, footer below with hint + primary "Gönder" button.
                Matches editor/components/ChatBox.jsx exactly. */}
            <textarea
              ref={inputRef}
              className="chat-input"
              placeholder={placeholder}
              value={input}
              rows={3}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={sending}
              aria-label="Mesaj"
            />
            <div className="chat-footer">
              <span className="chat-footer-hint">⌘/Ctrl + Enter ile gönder</span>
              <button
                type="button"
                className="btn-primary"
                onClick={() => send()}
                disabled={!input.trim() || sending}
              >
                {sending
                  ? <><Loader2 size={12} className="ts-spin" /><span>İşleniyor…</span></>
                  : <><Send size={12} strokeWidth={2} /><span>Gönder</span></>}
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
});

export default ChatDrawer;


function ChatTurn({ turn, basketTableIds, onAddToBasket, onHighlight }) {
  const isUser = turn.role === "user";
  const isError = turn.status === "error";
  const hasProposals = turn.proposals && turn.proposals.length > 0;
  const hasDropped = turn.dropped && turn.dropped.length > 0;

  if (isUser) {
    return <div className="chat-msg chat-msg--user">{turn.text}</div>;
  }

  return (
    <div className={`chat-msg chat-msg--assistant${isError ? " chat-msg--error" : ""}`}>
      {turn.text && <div>{turn.text}</div>}
      {hasProposals && (
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
      {hasDropped && (
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
