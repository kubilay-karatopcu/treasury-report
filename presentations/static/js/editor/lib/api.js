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


/**
 * Force a re-run of the block's stored SQL against Oracle. Returns the new
 * `{ok, version, block}` shape so the caller can swap the block into the
 * manifest without a full reload.
 */
export async function refreshBlockData(blockId, newSql) {
  // newSql verilirse data_source.original_sql üzerine yazılır + execute edilir.
  // newSql null/undefined ise block'taki mevcut SQL kullanılır.
  const reqBody = newSql ? JSON.stringify({ sql: newSql }) : '{}';
  const resp = await fetch(`${API_BASE}/block/${encodeURIComponent(blockId)}/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: reqBody,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(body.error || `Tazele isteği başarısız (${resp.status})`);
    err.kind = body.kind;
    throw err;
  }
  return body;
}

/**
 * Help catalog — plot types, style fields, example prompts. Server hot-reloads
 * the JSON file from disk so we can edit it without a Flask restart.
 *
 * Lives one level up at /presentations/help.json (presentation-independent).
 */
export async function fetchHelp() {
  const collectionUrl = API_BASE.replace(/\/[^/]+$/, '');
  const resp = await fetch(`${collectionUrl}/help.json`, {
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) throw new Error(`Yardım yüklenemedi: ${resp.status}`);
  return resp.json();
}


// ── Excel uploads ───────────────────────────────────────────────────────────

export async function uploadParseFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  const resp = await fetch(`${API_BASE}/uploads/parse`, { method: 'POST', body: fd });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `Yükleme önizlenemedi: ${resp.status}`);
  }
  return resp.json();
}

export async function uploadParsePaste({ paste, tableName, hasHeader }) {
  const resp = await fetch(`${API_BASE}/uploads/parse`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ paste, table_name: tableName, has_header: hasHeader }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `Yapıştırma önizlenemedi: ${resp.status}`);
  }
  return resp.json();
}

export async function uploadCommitFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  const resp = await fetch(`${API_BASE}/uploads`, { method: 'POST', body: fd });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `Yükleme kaydedilemedi: ${resp.status}`);
  }
  return resp.json();
}

export async function uploadCommitPaste({ paste, tableName, hasHeader }) {
  const resp = await fetch(`${API_BASE}/uploads`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ paste, table_name: tableName, has_header: hasHeader }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `Yapıştırma kaydedilemedi: ${resp.status}`);
  }
  return resp.json();
}

export async function uploadDelete(uploadId) {
  const resp = await fetch(`${API_BASE}/uploads/${encodeURIComponent(uploadId)}`, {
    method: 'DELETE',
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `Silinemedi: ${resp.status}`);
  }
  return resp.json();
}


// ── Table preview (for the docs modal) ─────────────────────────────────────

/**
 * First-5000 rows of a catalog table — for the docs modal's data preview.
 * Server figures out Oracle vs Excel routing.
 */
export async function fetchTablePreview(tableId) {
  const resp = await fetch(
    `${API_BASE}/table/preview?table=${encodeURIComponent(tableId)}`,
  );
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `Önizleme alınamadı: ${resp.status}`);
  }
  return resp.json();
}