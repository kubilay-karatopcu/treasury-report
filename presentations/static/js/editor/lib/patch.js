/**
 * RFC 6902 JSON Patch — subset: replace, add, remove.
 * Mirrors presentations/patch.py — keep the two files in sync.
 */

function parsePath(path) {
  if (!path.startsWith('/')) throw new Error(`Path must start with '/': ${path}`);
  return path.slice(1).split('/');
}

function getAt(obj, parts) {
  for (const part of parts) {
    obj = Array.isArray(obj) ? obj[Number(part)] : obj[part];
  }
  return obj;
}

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function applyOne(state, patch) {
  const { op, path, value } = patch;
  const parts = parsePath(path);
  const parentParts = parts.slice(0, -1);
  const last = parts[parts.length - 1];

  const parent = parentParts.length ? getAt(state, parentParts) : state;

  if (op === 'replace') {
    if (Array.isArray(parent)) parent[Number(last)] = value;
    else parent[last] = value;

  } else if (op === 'add') {
    if (Array.isArray(parent)) {
      if (last === '-') parent.push(value);
      else parent.splice(Number(last), 0, value);
    } else {
      parent[last] = value;
    }

  } else if (op === 'remove') {
    if (Array.isArray(parent)) parent.splice(Number(last), 1);
    else delete parent[last];

  } else {
    throw new Error(`Unsupported op: ${op}`);
  }
}

/**
 * Return a deep-cloned new state with all patches applied atomically.
 * @param {object} state
 * @param {Array<{op:string, path:string, value?:*}>} patches
 * @returns {object}
 */
export function applyPatches(state, patches) {
  const newState = deepClone(state);
  for (const patch of patches) {
    applyOne(newState, patch);
  }
  return newState;
}

/**
 * Classify patches into meta, block-scoped, and structural buckets.
 * @param {Array} patches
 * @returns {{ meta: Array, blocks: Object.<number, Array>, structural: Array }}
 */
export function classifyPaths(patches) {
  const result = { meta: [], blocks: {}, structural: [] };

  for (const patch of patches) {
    const parts = parsePath(patch.path);

    if (parts[0] === 'meta') {
      result.meta.push(patch);

    } else if (parts[0] === 'blocks') {
      if (parts.length <= 2) {
        result.structural.push(patch);
      } else {
        const idx = parseInt(parts[1], 10);
        if (!isNaN(idx)) {
          if (!result.blocks[idx]) result.blocks[idx] = [];
          result.blocks[idx].push(patch);
        } else {
          result.structural.push(patch);
        }
      }

    } else {
      // Unknown top-level — bucket with meta; server validator will reject it.
      result.meta.push(patch);
    }
  }

  return result;
}
