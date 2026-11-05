from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from typing import Any

from fastapi.responses import StreamingResponse


def csv_response(filename: str, columns: list[str], rows: Iterable[dict[str, Any]]) -> StreamingResponse:
    """Stream CSV without buffering the whole result set in memory."""

    def generate():
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(generate(), media_type="text/csv; charset=utf-8", headers=headers)


def pagination_meta(page: int, page_size: int, total: int) -> dict:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
    }


def iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value
