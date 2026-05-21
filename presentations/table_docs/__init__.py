"""Phase 6.5.b — extended table documentation.

Per-table YAML at ``s3://<bucket>/table_docs/<schema>/<table>.yaml`` carries
rich metadata used by:

- The LLM block-authoring prompt (suggested_variable, suggested_semantic_tag,
  distinct_values_sample).
- Phase 6.5.c's dashboard filter auto-binding (filterable, filter_role).
- Phase 7's binding inference (the `lookup` field on dimension columns).

The legacy ``presentations/catalog.json`` remains unchanged — it serves the
basket UI from Phases 1–6. The new TableDoc system is additive; tables that
haven't been migrated yet are simply absent from the LLM context and fall
back to the catalog's column listing.
"""
