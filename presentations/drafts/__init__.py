"""Phase 9.a — Draft presentation lifecycle.

A draft is a transient placeholder presentation that holds the basket while
the user is in Keşif. The draft only materializes into a real presentation
when the user clicks "Hazırlık'a geç" — preventing the presentations list
from filling with abandoned, empty drafts.

See :mod:`presentations.drafts.manager` for the public surface.
"""
from presentations.drafts.manager import (
    DraftManager,
    DraftRecord,
    DraftError,
)

__all__ = ["DraftManager", "DraftRecord", "DraftError"]
