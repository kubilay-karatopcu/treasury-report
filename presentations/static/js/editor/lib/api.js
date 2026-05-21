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

// ── User info + Dashboard publish ───────────────────────────────────────────

const PRES_BASE = API_BASE.replace(/\/[^/]+$/, '');  // /presentations/<pid> → /presentations

export async function fetchUserInfo() {
  const resp = await fetch(`${PRES_BASE}/user`, {
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) throw new Error(`Kullanıcı bilgisi alınamadı: ${resp.status}`);
  return resp.json();
}

export async function publishDashboard({ name, audience_sicils }) {
  const resp = await fetch(`${API_BASE}/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, audience_sicils }),
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Yayınlanamadı (${resp.status}): ${txt}`);
  }
  return resp.json();
}

export async function searchUsers(query) {
  const resp = await fetch(`${PRES_BASE}/users/search?q=${encodeURIComponent(query)}`, {
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) return [];
  return resp.json();
}

export async function fetchDeptMembers(dept) {
  const resp = await fetch(`${PRES_BASE}/dept/${encodeURIComponent(dept)}/members`, {
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) return [];
  return resp.json();
}

// ── Block Library ────────────────────────────────────────────────────────────

export async function fetchLibraryBlocks({ type, tag, q } = {}) {
  const params = new URLSearchParams();
  if (type) params.set('type', type);
  if (tag)  params.set('tag', tag);
  if (q)    params.set('q', q);
  const qs = params.toString();
  const url = qs ? `${PRES_BASE}/library?${qs}` : `${PRES_BASE}/library`;
  const resp = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!resp.ok) return [];
  return resp.json();
}

export async function fetchLibraryBlock(libraryId) {
  const resp = await fetch(`${PRES_BASE}/library/${encodeURIComponent(libraryId)}`, {
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Blok yüklenemedi (${resp.status}): ${txt}`);
  }
  return resp.json();  // { block, meta }
}

export async function saveBlockToLibrary({ block_id, name, description, tags, audience_sicils }) {
  const resp = await fetch(`${API_BASE}/blocks/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ block_id, name, description, tags, audience_sicils }),
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Kütüphaneye kaydedilemedi (${resp.status}): ${txt}`);
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
  if (handlers.onSuggestion) {
    es.addEventListener('suggestion', (e) => handlers.onSuggestion(JSON.parse(e.data)));
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
 * Phase 6.5 manual SQL execution for a block inside a presentation.
 *
 * Variables and overrides follow the Phase 6.5 Variable schema. The server
 * resolves them, expands binds (enum_multi → positional placeholders), and
 * executes through the existing duck.execute_block_sql plumbing so the
 * returned `block.data_source` slots into the renderer with no changes.
 *
 * Returns {ok, version, block, warnings} on success.
 * On error, throws an Error with .kind ∈ {sql, resolution, bind, gate, oracle, ...}.
 */
export async function runBlockManual(blockId, { query, variables, variableOverrides }) {
  const resp = await fetch(`${API_BASE}/block/${encodeURIComponent(blockId)}/run-manual`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      variables: variables || [],
      variable_overrides: variableOverrides || {},
    }),
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(body.error || `Manuel çalıştırma başarısız (${resp.status})`);
    err.kind = body.kind;
    err.warnings = body.warnings;
    throw err;
  }
  return body;
}


/**
 * Save an in-presentation block as a reusable Phase 6.5 block template.
 * Posts to the block store at /presentations/blocks/api/save with the
 * variable-aware shape (semantic_tag required for every variable).
 */
export async function saveBlockAsTemplate(payload) {
  // Derive the /blocks API base from the editor pathname:
  //   /proxy/8080/presentations/<pid>  →  /proxy/8080/presentations/blocks
  const blockApiBase = API_BASE.replace(/\/[^/]+$/, '') + '/blocks/api';
  const resp = await fetch(`${blockApiBase}/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error((body.errors || []).join('; ') || `Kaydetme başarısız (${resp.status})`);
    err.errors = body.errors;
    err.warnings = body.warnings;
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