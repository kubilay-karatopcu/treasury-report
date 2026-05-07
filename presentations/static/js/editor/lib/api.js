/**
 * API client for the presentations editor.
 *
 * URL strategy: editor is mounted at /presentations/{pid}, so we derive the
 * API base from window.location.pathname. This survives reverse-proxy
 * SCRIPT_NAME prefixes (e.g. /proxy/8080/...) without backend URL injection.
 */

const API_BASE = window.location.pathname.replace(/\/$/, '');

// ── Manifest ────────────────────────────────────────────────────────────────

export async function fetchManifest() {
  const resp = await fetch(`${API_BASE}/manifest`, {
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) throw new Error(`Manifest yüklenemedi: ${resp.status}`);
  return resp.json();
}

// ── Sources / Basket ────────────────────────────────────────────────────────

export async function fetchSources() {
  const resp = await fetch(`${API_BASE}/sources`, {
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) throw new Error(`Kaynaklar yüklenemedi: ${resp.status}`);
  return resp.json();
}

export async function updateBasket(basket) {
  const resp = await fetch(`${API_BASE}/basket`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ basket }),
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Basket güncellenemedi (${resp.status}): ${txt}`);
  }
  return resp.json();
}

export async function previewView(viewName) {
  const resp = await fetch(`${API_BASE}/duckdb/preview/${encodeURIComponent(viewName)}`, {
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Önizleme alınamadı (${resp.status}): ${txt}`);
  }
  return resp.json();
}

// ── Create new presentation ─────────────────────────────────────────────────

export async function createPresentation({ title, basket } = {}) {
  // Compute the index (collection) URL by stripping the {pid} segment.
  const collectionUrl = API_BASE.replace(/\/[^/]+$/, '/');
  const resp = await fetch(collectionUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, basket }),
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Yeni sunum oluşturulamadı (${resp.status}): ${txt}`);
  }
  return resp.json();
}

// ── Direct UI patch (bypasses LLM) ──────────────────────────────────────────

export async function submitPatches(patches) {
  const resp = await fetch(`${API_BASE}/patch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ patches }),
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Patch başarısız (${resp.status}): ${txt}`);
  }
  return resp.json();
}

// ── Snapshot ────────────────────────────────────────────────────────────────

export async function createSnapshot() {
  const resp = await fetch(`${API_BASE}/snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Snapshot oluşturulamadı (${resp.status}): ${txt}`);
  }
  return resp.json();
}

// ── Chat / SSE ──────────────────────────────────────────────────────────────

export async function postChatMessage(message, selectedBlockId) {
  const resp = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      selected_block_id: selectedBlockId || null,
    }),
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Chat isteği başarısız (${resp.status}): ${txt}`);
  }
  return resp.json();
}

/**
 * Open an SSE stream for the given chat token.
 * @returns {EventSource}
 */
export function openChatStream(token, handlers) {
  const url = `${API_BASE}/stream/${token}`;
  const es = new EventSource(url);

  if (handlers.onStatus) {
    es.addEventListener('status', (e) => handlers.onStatus(JSON.parse(e.data)));
  }
  if (handlers.onPatch) {
    es.addEventListener('patch', (e) => handlers.onPatch(JSON.parse(e.data)));
  }
  es.addEventListener('error', (e) => {
    if (e.data) {
      if (handlers.onError) handlers.onError(JSON.parse(e.data));
    } else {
      if (handlers.onError) handlers.onError({ message: 'Bağlantı kesildi.' });
      es.close();
    }
  });
  es.addEventListener('done', (e) => {
    if (handlers.onDone) handlers.onDone(JSON.parse(e.data));
    es.close();
  });

  return es;
}
