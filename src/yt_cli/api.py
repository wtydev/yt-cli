"""Small helpers shared by every module that talks to Google's APIs."""

from __future__ import annotations

from collections.abc import Iterator

from googleapiclient.errors import HttpError


def pages(collection, **kw) -> Iterator[dict]:
    """Yield each page of `collection.list(**kw)`, following nextPageToken."""
    req = collection.list(**kw)
    while req is not None:
        resp = req.execute()
        yield resp
        req = collection.list_next(req, resp)


def status(e: HttpError) -> int:
    return int(e.status_code)


def describe(e: HttpError) -> str:
    return f"{e.status_code} {e.error_details or e.reason}"
