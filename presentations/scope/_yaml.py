"""Bool-safe YAML load / dump for scope contracts.

Scope filters carry canonical codes like ``ON`` (overnight maturity) and the
default PyYAML SafeLoader would coerce ``ON`` / ``OFF`` / ``YES`` / ``NO`` to
booleans under the YAML 1.1 rules. That would silently corrupt a maturity
value list. This mirrors the loader used by
:mod:`presentations.concepts.registry`: only ``true`` / ``false`` (any case)
stay boolean, matching the YAML 1.2 core schema.

``dump_yaml`` uses ``safe_dump`` with ``sort_keys=False`` so field order from
the Pydantic model is preserved (readable, stable output) and
``allow_unicode=True`` so Turkish labels round-trip without ``\\uXXXX`` escapes.
"""
from __future__ import annotations

import re
from typing import Any

import yaml


class _BoolSafeLoader(yaml.SafeLoader):
    """SafeLoader that does not treat YAML 1.1 booleans (on/off/yes/no) as
    bools, so codes like ``ON`` load as the string ``"ON"``."""


_BoolSafeLoader.yaml_implicit_resolvers = {
    ch: [(tag, regexp) for (tag, regexp) in resolvers
         if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_BoolSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_yaml(text: str) -> Any:
    """Parse scope YAML with the bool-safe loader."""
    return yaml.load(text, Loader=_BoolSafeLoader)


def dump_yaml(obj: Any) -> str:
    """Serialise a plain dict/list structure to YAML.

    Keeps insertion order (``sort_keys=False``) and emits unicode verbatim.
    Strings that would be re-read as booleans (``ON`` …) are quoted by the
    emitter, so a normal loader round-trips them as strings too.
    """
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True, default_flow_style=False)
