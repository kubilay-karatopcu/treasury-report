/* Phase 9.c — Keşif chat drawer.
 *
 * Bottom-edge collapsible drawer. The LLM's job is *narrow*: it proposes
 * tables. Every proposal renders as an inline card the user can click to
 * add to the basket — the LLM never mutates the basket directly (spec
 * §5.4). When proposals arrive, their ids flow up to the parent so
 * GraphCanvas can pulse the matching nodes for ~3 seconds.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  MessageCircle, Send, X, Trash2, Plus, Loader2, ChevronDown,
} from "lucide-react";


export default function ChatDrawer({
  chatSendUrl,
  chatClearUrl,
  seedHistory,
  basketTableIds,
  onAddToBasket,
  onHighlight,
}) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState(() => seedHistory || []);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const threadRef = useRef(null);

  // Auto-scroll the thread to the latest message on each append.
  useEffect(() => {
    if (!threadRef.current) return;
    threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [history, open]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    // Optimistic: drop the user turn immediately so the input clears
    // and the user sees their own message reflected.
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
      // Server returns the canonical history — replace ours wholesale.
      setHistory(data.history || []);
      // Fire the pulse signal so GraphCanvas highlights the proposals.
      const ids = data.assistant_message?.highlights || [];
      if (ids.length && onHighlight) onHighlight(ids);
    } catch (err) {
      console.warn("Keşif chat:", err);
      setError("Bir sorun oldu, tekrar dener misiniz?");
      // Roll back the optimistic user turn so the user can retry.
      setHistory((prev) => prev.filter((t) => t !== optimisticUser));
    } finally {
      setSending(false);
    }
  }, [input, sending, chatSendUrl, onHighlight]);

  const onKeyDown = useCallback((e) => {
    // Cmd/Ctrl+Enter → send. Plain Enter inserts a newline.
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

  // Quick visual signal on the toggle: unread assistant messages since
  // the user last opened the drawer.
  const lastSeenRef = useRef(history.length);
  const unread = useMemo(() => {
    if (open) return 0;
    return Math.max(0, history.length - lastSeenRef.current);
  }, [history.length, open]);
  useEffect(() => {
    if (open) lastSeenRef.current = history.length;
  }, [open, history.length]);

  return (
    <>
      {!open && (
        <button
          type="button"
          className="kesif-chat__fab"
          onClick={() => setOpen(true)}
          title="Keşif sohbeti — bana ne aradığını söyle"
        >
          <MessageCircle size={16} />
          <span>Sohbet</span>
          {unread > 0 && <span className="kesif-chat__fab-badge">{unread}</span>}
        </button>
      )}

      {open && (
        <div className="kesif-chat__drawer" role="dialog" aria-label="Keşif sohbeti">
          <header className="kesif-chat__header">
            <div className="kesif-chat__title">
              <MessageCircle size={14} />
              Keşif sohbeti
            </div>
            <div className="kesif-chat__header-actions">
              <button
                type="button"
                className="kesif-chat__icon-btn"
                onClick={clearChat}
                title="Geçmişi sil"
                disabled={history.length === 0}
              >
                <Trash2 size={13} />
              </button>
              <button
                type="button"
                className="kesif-chat__icon-btn"
                onClick={() => setOpen(false)}
                title="Daralt"
              >
                <ChevronDown size={14} />
              </button>
            </div>
          </header>

          <div className="kesif-chat__thread" ref={threadRef}>
            {history.length === 0 && (
              <div className="kesif-chat__empty">
                <p>Ne aradığını birkaç kelimeyle yaz; sana uygun tabloları öneririm.</p>
                <p className="kesif-chat__empty-hint">
                  Örnek: "şube performansı", "Q4 mevduat hareketi", "rakip
                  faiz oranları"
                </p>
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
                <Loader2 size={12} className="kesif-spin" /> Düşünüyor…
              </div>
            )}
            {error && <div className="kesif-chat__error">{error}</div>}
          </div>

          <footer className="kesif-chat__input-row">
            <textarea
              className="kesif-chat__input"
              placeholder="Aradığın veriyi yaz… (Ctrl/Cmd+Enter ile gönder)"
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
              onClick={send}
              disabled={!input.trim() || sending}
              title="Gönder (Ctrl/Cmd+Enter)"
            >
              {sending ? <Loader2 size={14} className="kesif-spin" /> : <Send size={14} />}
            </button>
          </footer>
        </div>
      )}
    </>
  );
}


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
        <Plus size={11} />
        {inBasket ? "Sepette" : "Sepete ekle"}
      </button>
    </div>
  );
}
