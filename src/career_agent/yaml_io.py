"""Reading YAML through libyaml when the installed PyYAML carries it.

`yaml.safe_load` always uses the pure-Python scanner, and the search
configuration is about 48 KB of YAML. Measured 2026-09-23 on the worked
example: `GET /api/profile` parsed it twice per request and spent roughly
240 ms doing so; `/api/preferences`, `/api/sources` and
`/api/source-maintenance` each spent 250-270 ms in the same scanner.

`CSafeLoader` is the same safe constructor over libyaml's C parser: it builds
exactly the same Python objects (plain dicts, lists, strings, numbers,
dates -- nothing that can execute) and raises the same `yaml.YAMLError`
family, only about six times faster. PyYAML's Windows, macOS and Linux wheels
ship it; where a build does not, this falls back to the pure-Python
`SafeLoader`, which is what `yaml.safe_load` uses anyway.

No caching here on purpose. Every caller still reads the file it asked for,
so an edit on disk is seen on the next request exactly as before.
"""

from __future__ import annotations

from typing import Any

import yaml

#: The fastest SAFE loader available. Never `yaml.Loader` or `FullLoader`.
SAFE_LOADER: type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def safe_load(stream: str | bytes) -> Any:
    """`yaml.safe_load`, through libyaml when it is present."""
    return yaml.load(stream, Loader=SAFE_LOADER)  # noqa: S506 -- a safe loader
