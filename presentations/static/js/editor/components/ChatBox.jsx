import { useState, useRef, useEffect } from 'react';
import useStore from '../lib/store.js';
import { postChatMessage, openChatStream } from '../lib/api.js';

export default function ChatBox() {
  const [input, setInput] = useState('');
  const messagesRef = useRef(null);

  const chatHistory     = useStore((s) => s.chatHistory);
  const loading         = useStore((s) => s.loading);
  const setLoading      = useStore((s) => s.setLoading);
  const addChatMessage  = useStore((s) => s.addChatMessage);
  const applyPatches    = useStore((s) => s.applyPatches);
  const selectedBlockId = useStore((s) => s.selectedBlockId);

  // Auto-scroll to bottom on new messages.
  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  }, [chatHistory.length, loading]);

  async function send() {
    const msg = input.trim();
    if (!msg || loading) return;

    setInput('');
    addChatMessage({ role: 'user', text: msg });
    setLoading(true);

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
          }
          if (data.explanation) {
            addChatMessage({ role: 'assistant', text: data.explanation });
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
        onDone: () => setLoading(false),
      });
    } catch (err) {
      addChatMessage({ role: 'assistant', text: err.message, status: 'error' });
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const placeholder = selectedBlockId
    ? 'Seçili bloğu nasıl değiştirmek istersin?'
    : 'Sunuyu nasıl değiştirmek istersin?';

  return (
    <div className="chat-box">
      <div className="sidebar-label">Yapay Zeka</div>

      <div className="chat-messages" ref={messagesRef}>
        {chatHistory.length === 0 && !loading && (
          <div className="chat-empty">
            Bir blok seçip "1234" gibi bir sayı yaz, ya da "başlık: Yeni Başlık" dene.
          </div>
        )}
        {chatHistory.map((m, i) => (
          <div
            key={`${m.ts}_${i}`}
            className={
              `chat-msg chat-msg--${m.role}`
              + (m.status ? ` chat-msg--${m.status}` : '')
            }
          >
            {m.text}
          </div>
        ))}
        {loading && <div className="chat-msg chat-msg--loading">Düşünüyor…</div>}
      </div>

      <div className="chat-input-row">
        <textarea
          className="chat-input"
          placeholder={placeholder}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          disabled={loading}
        />
        <button
          className="chat-send-btn"
          onClick={send}
          disabled={loading || !input.trim()}
        >
          Gönder
        </button>
      </div>
    </div>
  );
}
