/**
 * API client for the presentations editor.
 *
 * URL strategy: editor is mounted at /presentations/{pid}, so we derive the
 * API base from window.location.pathname. This survives reverse-proxy
 * SCRIPT_NAME prefixes (e.g. /proxy/8080/...) without backend URL injection.
 */

// Editor lives at /presentations/{pid}; Hazırlık lives at
// /presentations/hazirlik/{pid}. Shared API endpoints (/manifest, /uploads,
// /sources, …) are mounted at the PID root, so when this api.js is imported
// from the Hazırlık bundle we strip the `hazirlik/` segment so the URLs
// resolve to the same place. The replace is a no-op for the editor.
const API_BASE = window.location.pathname.replace(/\/$/, '').replace('/hazirlik/', '/');

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

/**
 * Create a snapshot. Phase 10D body fields are all optional — calling
 * createSnapshot() with no arg keeps the pre-10D behaviour.
 *
 * @param {object} [body]
 * @param {string} [body.title]          override for snapshot meta.title
 * @param {string} [body.description]    short description, persisted in meta
 * @param {string[]} [body.bound_experts] expert ids this snapshot is bound to
 */
export async function createSnapshot(body = undefined) {
  const init = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(`${API_BASE}/snapshot`, init);
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Snapshot oluşturulamadı (${resp.status}): ${txt}`);
  }
  return resp.json();
}

// ── Experts (Phase 10B/C/D) ─────────────────────────────────────────────────

/** List all experts visible to the current user. */
export async function listExperts() {
  const resp = await fetch(`/api/experts/`, { headers: { Accept: 'application/json' } });
  if (!resp.ok) throw new Error(`Uzman listesi alınamadı: ${resp.status}`);
  const payload = await resp.json();
  return payload.experts || [];
}

/**
 * Get LLM-backed suggestions for which experts a snapshot should be bound to.
 * Always returns an array (server falls back to keyword scoring when LLM is offline).
 */
export async function suggestExperts({ manifest, title, description }) {
  const resp = await fetch(`/api/experts/suggest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ manifest, title: title || '', description: description || '' }),
  });
  if (!resp.ok) {
    // Suggestions are a nice-to-have; silently degrade so the user can still pick manually.
    return [];
  }
  const payload = await resp.json();
  return payload.suggestions || [];
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
export async function runBlockManual(blockId, { query, variables, variableOverrides, scanOnly }) {
  const payload = {
    query,
    variables: variables || [],
    variable_overrides: variableOverrides || {},
  };
  if (scanOnly) payload.scan_only = true;
  const resp = await fetch(`${API_BASE}/block/${encodeURIComponent(blockId)}/run-manual`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
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
 * Phase 6.5.c — apply dashboard filter state across every block.
 *
 * Body: `{ filter_state: { <filter_id>: <value> } }`.
 * Server walks each block, resolves variables via bindings + filter state,
 * checks cache (exact / subset / miss), executes if needed, and returns
 * per-block status.
 */
export async function applyDashboardFilters(filterState) {
  const resp = await fetch(`${API_BASE}/apply-filters`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filter_state: filterState }),
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok || !body.ok) {
    const err = new Error(body.error || `Apply başarısız (${resp.status})`);
    throw err;
  }
  return body;  // {ok, version, blocks: [{id, status, ...}, ...]}
}


/**
 * Phase 7 — propose dashboard filters from the blocks' concept bindings.
 * Returns ``[{id, semantic_tag, type, label, allowed_values?, default, source}]``.
 * Concept-native blocks (LLM-authored, source_tables + sentinel, no variables)
 * surface here; the legacy variable-based proposals are computed client-side.
 */
export async function fetchConceptFilterSuggestions() {
  try {
    const resp = await fetch(`${API_BASE}/concepts/filter-suggestions`, {
      headers: { Accept: 'application/json' }, cache: 'no-store',
    });
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body.suggestions) ? body.suggestions : [];
  } catch (_e) {
    return [];
  }
}


/**
 * List Phase 6.5 block templates from the BlockStore. Used by AddBlockPanel's
 * "Şablonlar" tab.
 */
export async function fetchBlockTemplates({ q, team, tag, vizType } = {}) {
  const blockApiBase = API_BASE.replace(/\/[^/]+$/, '') + '/blocks/api';
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (team) params.set('team', team);
  if (tag) params.set('tag', tag);
  if (vizType) params.set('viz_type', vizType);
  const qs = params.toString();
  const resp = await fetch(`${blockApiBase}/list${qs ? '?' + qs : ''}`);
  if (!resp.ok) throw new Error(`Şablon listesi alınamadı (${resp.status})`);
  return resp.json();
}


/**
 * Load a single Phase 6.5 block template by (team, id, version).
 */
export async function fetchBlockTemplate(team, id, version) {
  const blockApiBase = API_BASE.replace(/\/[^/]+$/, '') + '/blocks/api';
  const resp = await fetch(`${blockApiBase}/${encodeURIComponent(team)}/${encodeURIComponent(id)}/${version}`);
  if (!resp.ok) throw new Error(`Şablon yüklenemedi (${resp.status})`);
  return resp.json();
}


/**
 * Save an in-presentation block as a reusable Phase 6.5 block template.
 * Posts to the block store at /presentations/blocks/api/save with the
 * variable-aware shape (semantic_tag required for every variable).
 */
export async function saveBlockAsTemplate(payload) {
  // Derive the /blocks API base robustly — works from the editor
  // (/presentations/<pid>), the new-block page (/presentations/blocks/new),
  // and the template editor (/presentations/blocks/edit/...). Anchoring on the
  // "/presentations" segment avoids the double "/blocks" the old
  // strip-last-segment derivation produced on /blocks/* pages.
  const path = window.location.pathname;
  const i = path.indexOf('/presentations/');
  const root = i >= 0
    ? path.slice(0, i + '/presentations'.length)
    : API_BASE.replace(/\/[^/]+$/, '');
  const blockApiBase = `${root}/blocks/api`;
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


/**
 * Phase 7 — per-column concept status for the docs modal.
 *
 * `tableId` is "SCHEMA.TABLE" (e.g. "EDW.DEPOSITS_DAILY"). Returns
 * `{ schema, table, columns: { COL: { filterable, filter_role,
 * suggested_concept, bound_concept, transform } } }`. Returns an empty
 * `columns` map (never throws) so the docs modal degrades gracefully when the
 * concept registry isn't configured.
 */
export async function fetchTableConcepts(tableId) {
  const dot = String(tableId || '').indexOf('.');
  if (dot < 0) return { columns: {} };
  const schema = tableId.slice(0, dot);
  const table = tableId.slice(dot + 1);
  try {
    const resp = await fetch(
      `${PRES_BASE}/concepts/api/table-columns`
        + `?schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}`,
      { headers: { Accept: 'application/json' }, cache: 'no-store' },
    );
    if (!resp.ok) return { columns: {} };
    const body = await resp.json();
    return { ...body, columns: body.columns || {} };
  } catch (_e) {
    return { columns: {} };
  }
}
