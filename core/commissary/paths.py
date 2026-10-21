"""Several completed-download folders where upstream allows one.

Upstream stores ``download_source.torrent_download_path`` and
``download_source.usenet_download_path`` as a single string. A download client
that files each category into its own folder (Movies, TV, Anime, Music, ...)
needs several. The old Commissary fork stored a JSON list; the settings page
(a single text box) saves a string. Both are accepted:

    ["/dl/complete/Music", "/dl/complete/TV"]   legacy list
    "/dl/complete/Music; /dl/complete/TV"        what the settings box saves

``;`` and newlines separate entries. Upstream code that did ``str(value)`` on a
list produced a nonexistent path like ``"['/x', '/y']"``.
"""

from __future__ import annotations

import re
from typing import Any, List

_SEPARATORS = re.compile(r"[;\r\n]+")


def split_paths(value: Any) -> List[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else _SEPARATORS.split(str(value))
    out: List[str] = []
    for item in items:
        if item is None:
            continue
        path = str(item).strip()
        if path and path not in out:
            out.append(path)
    return out
