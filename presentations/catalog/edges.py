"""Phase 9.a — Edge computation for the network graph (spec §2.3).

Three edge kinds, ranked by strength:

1. ``lookup``         — declared FK lookup on a column. Solid line in 9.b.
2. ``shared_concept`` — two tables bind the same concept on some column.
                        Dashed line in 9.b.
3. ``manual``         — table doc lists peers in a ``related_tables`` array.

Multiple edges between two tables collapse into one; the strongest type
wins for styling (lookup > shared_concept > manual).

Edges are computed on the fly — never stored — because bindings change as
the data team edits concept_bindings. Computing once at request time keeps
the source of truth in the YAML.

Resolution rules:

- Lookup target tables are resolved by *name match*. If the target name is
  not present in the input set, the edge is dropped (broken FK reference,
  logged at debug). When the target name is ambiguous across schemas, the
  shortest-schema-distance wins; ties go to the first match (deterministic
  via input order).
- Shared-concept edges are emitted between every pair of tables that bind
  the same concept. A table with N concepts shared with M peers contributes
  N edges to each peer — collapsed via dedupe to one edge carrying the
  full concept list.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from itertools import combinations
from typing import Iterable

from presentations.catalog.models import Edge, TableEntry


log = logging.getLogger(__name__)


# Edge-kind ordering for collapse: highest wins.
_KIND_RANK = {"lookup": 3, "shared_concept": 2, "manual": 1}


def compute_edges(entries: Iterable[TableEntry]) -> list[Edge]:
    """Compute graph edges for the given catalog entries.

    The entries should be the *detail* shape (with ``columns`` /
    ``lookups`` / ``related_tables`` populated) — i.e., what
    ``CatalogLoader.get(...)`` returns. List-mode entries (no columns)
    yield only shared-concept edges, derived from ``concepts_bound``.
    """
    entries = list(entries)
    name_to_id = _index_by_name(entries)

    raw: list[Edge] = []
    raw.extend(_lookup_edges(entries, name_to_id))
    raw.extend(_shared_concept_edges(entries))
    raw.extend(_manual_edges(entries, name_to_id))

    return _collapse(raw)


# ── Lookup edges (kind 1) ─────────────────────────────────────────────────


def _lookup_edges(entries: list[TableEntry], name_to_id: dict[str, list[str]]) -> list[Edge]:
    out: list[Edge] = []
    for entry in entries:
        if not entry.lookups:
            continue
        source_id = entry.table_id
        for lk in entry.lookups:
            target_id = _resolve_target_id(lk.to_table, source_id, name_to_id)
            if target_id is None:
                log.debug(
                    "catalog.edges: lookup target %r from %s.%s not in catalog",
                    lk.to_table, entry.schema_name, entry.name,
                )
                continue
            out.append(Edge(
                source=source_id,
                target=target_id,
                kind="lookup",
                label=lk.from_column,
                concepts=[],
                strength=1.0,
            ))
    return out


def _resolve_target_id(
    target_name: str,
    source_id: str,
    name_to_id: dict[str, list[str]],
) -> str | None:
    """Pick the best matching table id for ``target_name``.

    The lookup spec only records the table name (not the schema), so we
    have to resolve against the index. Preference order:

    1. Same-schema match (most lookups are intra-schema).
    2. First match in input order (deterministic).
    """
    candidates = name_to_id.get(target_name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    source_schema = source_id.split(".", 1)[0]
    same_schema = [c for c in candidates if c.startswith(source_schema + ".")]
    if same_schema:
        return same_schema[0]
    return candidates[0]


def _index_by_name(entries: list[TableEntry]) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = defaultdict(list)
    for e in entries:
        idx[e.name].append(e.table_id)
    return idx


# ── Shared-concept edges (kind 2) ─────────────────────────────────────────


def _shared_concept_edges(entries: list[TableEntry]) -> list[Edge]:
    by_concept: dict[str, list[TableEntry]] = defaultdict(list)
    for entry in entries:
        for concept in entry.concepts_bound:
            by_concept[concept].append(entry)

    # Build a pair-keyed accumulator so a pair of tables sharing N concepts
    # contributes one edge with all N concepts listed.
    pair_concepts: dict[tuple[str, str], list[str]] = defaultdict(list)
    for concept, members in by_concept.items():
        if len(members) < 2:
            continue
        for a, b in combinations(members, 2):
            if a.table_id == b.table_id:
                continue
            key = tuple(sorted([a.table_id, b.table_id]))
            if concept not in pair_concepts[key]:
                pair_concepts[key].append(concept)

    out: list[Edge] = []
    for (s, t), concepts in pair_concepts.items():
        # Strength scales with shared-concept count, capped at 0.8 (lookups
        # outrank these). 1 concept → 0.4, 2 → 0.6, 3+ → 0.8.
        strength = min(0.8, 0.2 + 0.2 * len(concepts))
        out.append(Edge(
            source=s,
            target=t,
            kind="shared_concept",
            label=", ".join(concepts),
            concepts=list(concepts),
            strength=strength,
        ))
    return out


# ── Manual edges (kind 3) ─────────────────────────────────────────────────


def _manual_edges(entries: list[TableEntry], name_to_id: dict[str, list[str]]) -> list[Edge]:
    out: list[Edge] = []
    for entry in entries:
        related = entry.related_tables or []
        for ref in related:
            target_id = _resolve_target_id(ref, entry.table_id, name_to_id)
            if target_id is None or target_id == entry.table_id:
                continue
            out.append(Edge(
                source=entry.table_id,
                target=target_id,
                kind="manual",
                label=ref,
                concepts=[],
                strength=0.5,
            ))
    return out


# ── Collapse / dedupe ─────────────────────────────────────────────────────


def _collapse(edges: list[Edge]) -> list[Edge]:
    """Collapse duplicate (source, target) pairs — strongest kind wins.

    Direction is preserved for lookups (lookup is intrinsically directed
    from the FK column's owner to the dimension table). For shared_concept
    and manual edges, direction is arbitrary so we canonicalize to
    ``(min, max)`` to dedupe both orderings.
    """
    keyed: dict[tuple[str, str, str], Edge] = {}
    for edge in edges:
        if edge.kind == "lookup":
            key = (edge.source, edge.target, "lookup")
        else:
            a, b = sorted([edge.source, edge.target])
            key = (a, b, edge.kind)
        existing = keyed.get(key)
        if existing is None:
            keyed[key] = edge
            continue
        # Same kind, same pair: merge concepts (for shared_concept) and
        # take max strength.
        merged_concepts = list(existing.concepts)
        for c in edge.concepts:
            if c not in merged_concepts:
                merged_concepts.append(c)
        keyed[key] = Edge(
            source=existing.source,
            target=existing.target,
            kind=existing.kind,
            label=existing.label or edge.label,
            concepts=merged_concepts,
            strength=max(existing.strength, edge.strength),
        )

    # Now drop weaker-kind edges when a stronger one already connects the same
    # unordered pair.
    by_pair: dict[tuple[str, str], list[Edge]] = defaultdict(list)
    for edge in keyed.values():
        a, b = sorted([edge.source, edge.target])
        by_pair[(a, b)].append(edge)

    out: list[Edge] = []
    for pair, group in by_pair.items():
        # Sort by kind rank descending so the strongest kind survives.
        group.sort(key=lambda e: _KIND_RANK.get(e.kind, 0), reverse=True)
        out.append(group[0])
    # Stable-ish sort for deterministic output (helps tests).
    out.sort(key=lambda e: (e.source, e.target, e.kind))
    return out


# ── Cluster computation ───────────────────────────────────────────────────


def compute_clusters(entries: Iterable[TableEntry]) -> list[dict]:
    """Group nodes into clusters for the semantic-zoom layer (spec §4.2).

    The macro level shows one node per cluster. v1 clusters by department:
    every table with the same department lands in the same cluster;
    department-less tables (user uploads) form a synthetic "Yüklemelerim"
    cluster.

    Returns list of ``{id, label, node_ids}`` dicts (the §2.4 cluster shape).
    """
    by_dept: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        if entry.source == "user_upload":
            by_dept["__uploads__"].append(entry.table_id)
        else:
            by_dept[entry.department or "__other__"].append(entry.table_id)

    out: list[dict] = []
    for dept, node_ids in sorted(by_dept.items()):
        if dept == "__uploads__":
            label = "Yüklemelerim"
            cid = "c_uploads"
        elif dept == "__other__":
            label = "Diğer"
            cid = "c_other"
        else:
            label = dept.title()
            cid = f"c_{dept}"
        out.append({"id": cid, "label": label, "node_ids": sorted(node_ids)})
    return out


# ── Bipartite topology (concept hubs + table satellites) ─────────────────


CONCEPT_NODE_PREFIX = "concept:"


def concept_node_id(concept: str) -> str:
    """Stable id for a concept hub node. Matches the front-end's parser."""
    return f"{CONCEPT_NODE_PREFIX}{concept}"


def compute_bipartite_graph(entries: Iterable[TableEntry]) -> dict:
    """Build the table↔concept bipartite topology served to the renderer.

    Why bipartite: with N tables sharing one concept, the old
    table-to-table ``shared_concept`` edges fan out as C(N, 2) — 100
    tables binding ``as_of_time`` would emit 4950 edges. The bipartite
    form replaces that with N edges to one ``concept:as_of_time`` hub.
    The hub's degree IS the popularity signal; force layout naturally
    pulls common-concept tables into one orbital cluster.

    Returns a dict ``{nodes, edges, clusters}`` matching the §2.4 shape
    (same outer shape as :func:`compute_edges` consumers expect).

    Edge kinds emitted:
      - ``binds``  table → concept    (the new structural edge)
      - ``lookup`` table → table      (FK declaration, unchanged)
      - ``manual`` table → table      (from ``related_tables``, unchanged)

    Notably *not* emitted:
      - ``shared_concept`` — now expressed structurally via the shared
        hub a pair of tables connects to. The frontend computes "tables
        sharing concept X" by reading the hub's neighbours, not from a
        dedicated edge type.
    """
    entries = list(entries)
    name_to_id = _index_by_name(entries)

    # ── Table nodes ──────────────────────────────────────────────────
    table_nodes = []
    for entry in entries:
        table_nodes.append({
            "id": entry.table_id,
            "type": "table",
            "label": entry.name,
            "department": entry.department,
            "source": entry.source,
            "concepts": list(entry.concepts_bound),
            "usage_count": 0,
            "usage_score": 0.0,
        })

    # ── Concept hubs ─────────────────────────────────────────────────
    # usage_count = # of tables in this catalog binding this concept.
    concept_usage: dict[str, int] = defaultdict(int)
    for entry in entries:
        for concept in entry.concepts_bound:
            concept_usage[concept] += 1

    concept_nodes = []
    for concept, count in sorted(concept_usage.items()):
        concept_nodes.append({
            "id": concept_node_id(concept),
            "type": "concept",
            "label": concept,
            "department": None,
            "source": None,
            "concepts": [],
            "usage_count": count,
            "usage_score": 0.0,
        })

    # ── Bind edges (table → concept) — the ONLY edge type in the
    # bipartite emit. Table-to-table edges (lookup, manual) are
    # intentionally omitted: in the hub-and-spoke view they add visual
    # noise without changing the topology — a join relationship is
    # already implied when two tables both bind the same concept hub.
    # The legacy compute_edges() function still emits them for any
    # consumer that needs FK semantics directly.
    bind_edges = []
    for entry in entries:
        for concept in entry.concepts_bound:
            bind_edges.append({
                "source": entry.table_id,
                "target": concept_node_id(concept),
                "kind": "binds",
                "label": concept,
                "concepts": [concept],
                "strength": 0.7,
            })
    edges_payload = bind_edges

    # Clusters cover table nodes only — concept hubs are orthogonal.
    clusters = compute_clusters(entries)

    return {
        "nodes": table_nodes + concept_nodes,
        "edges": edges_payload,
        "clusters": clusters,
    }
