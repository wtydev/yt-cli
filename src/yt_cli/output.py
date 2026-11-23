"""Readable tables by default, JSON with --json."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any


def emit_json(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, default=str, ensure_ascii=False)
    sys.stdout.write("\n")


def fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if v != v:  # NaN
            return "-"
        return f"{v:,.1f}" if abs(v) >= 100 else f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def table(rows: Sequence[Mapping], columns: Sequence[str] | None = None, title: str | None = None,
          max_width: int = 60) -> str:
    if title:
        out = [title]
    else:
        out = []
    if not rows:
        out.append("  (none)")
        return "\n".join(out)
    columns = list(columns or rows[0].keys())
    cells = [[fmt(r.get(c)) for c in columns] for r in rows]
    cells = [[c if len(c) <= max_width else c[: max_width - 1] + "…" for c in row] for row in cells]
    widths = [max(len(col), *(len(row[i]) for row in cells)) for i, col in enumerate(columns)]
    numeric = [all(isinstance(r.get(c), (int, float)) or r.get(c) is None for r in rows) for c in columns]

    def line(vals: Sequence[str]) -> str:
        return "  ".join(v.rjust(w) if num else v.ljust(w) for v, w, num in zip(vals, widths, numeric)).rstrip()

    out.append(line(columns))
    out.append("  ".join("-" * w for w in widths))
    out.extend(line(r) for r in cells)
    return "\n".join(out)


def kv(pairs: Mapping[str, Any], title: str | None = None) -> str:
    out = [title] if title else []
    w = max((len(k) for k in pairs), default=0)
    out.extend(f"{k.ljust(w)}  {fmt(v)}" for k, v in pairs.items())
    return "\n".join(out)


def bar(value: float | None, scale: float = 1.0, width: int = 30) -> str:
    if value is None:
        return ""
    n = max(0, min(width, round(value / scale * width)))
    return "█" * n
