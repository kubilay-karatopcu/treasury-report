"""Phase 6.5.c — per-block DuckDB result cache.

Two key ideas (spec §4.3, §4.4):

1. **Cache key** = sha256(block_id | version | normalized resolved variables).
   Two requests for the same block with the same resolved values share the
   cached DuckDB view, no Oracle round-trip.

2. **Subset routing**. When a request's resolved variables are a *subset* of
   a cached parent's variables (narrower date range, fewer enum values in
   an IN list, narrower number range), we can DERIVE the result from the
   parent by running a DuckDB filter on the cached view — still no Oracle
   call.

LRU eviction (spec §4.4) drops the least-recently-accessed entries once the
session's DuckDB file passes 2 GB on disk. Eviction runs lazily before each
write, not on a timer.
"""
