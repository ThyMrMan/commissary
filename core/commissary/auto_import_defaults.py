"""The auto-import confidence default, in one place.

The worker (``core/auto_import_worker.py``) and the settings endpoint
(``api/auto_import.py``) each carried their own literal 0.9, so the value the
settings screen showed could drift from the one the worker used.

What the number means (``AutoImportWorker._match_tracks``):

    overall = identification_confidence * avg_track_confidence * coverage

a PRODUCT of three fractions, so it falls far faster than "90% sure" suggests.
A complete, correctly tagged album identified at 0.9 scores ~0.85 and is under
0.9; it imports only through the separate "any one track matched >= 0.8"
bypass. A complete untagged album (titles from filenames, ~0.5-0.8 a track)
scores ~0.47-0.72: below 0.7 it lands in "needs identification", not even in
review. The legacy fork lowered the default to 0.45 (Release 1.9.9). Upstream
3.5.7's settings drawer is built on three bands -- import above the line,
review from 70% to the line, identify below 70% -- with a slider floor of 70%,
so the value is left at upstream's 0.9 here until that is decided.
"""

DEFAULT_CONFIDENCE_THRESHOLD = 0.9

__all__ = ["DEFAULT_CONFIDENCE_THRESHOLD"]
