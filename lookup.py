"""Barcode lookup against free public APIs.

Strategy:
  - 10 or 13 digit barcode starting with 978/979 -> ISBN flow
        Open Library -> Google Books fallback
  - Anything else -> UPC/EAN flow via UPCitemdb trial endpoint
        (~100 requests/day per IP, no API key required)

Returns a dict shaped to match the columns in db.py. Missing keys are fine -
the GUI will let the user fill anything that's blank.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

USER_AGENT = "FlipInventory/1.0 (personal inventory tool)"
TIMEOUT = 10


def _digits(barcode: str) -> str:
    return re.sub(r"\D", "", barcode or "")


def _looks_like_isbn(code: str) -> bool:
    code = _digits(code)
    if len(code) == 10:
        return True
    if len(code) == 13 and code.startswith(("978", "979")):
        return True
    return False


def lookup(barcode: str) -> dict[str, Any]:
    """Return whatever we can find about this barcode.

    Always returns a dict with at least 'barcode'. The 'source' field tells
    you which API answered (or 'manual' if nothing did).
    """
    code = _digits(barcode)
    if not code:
        return {"barcode": barcode, "source": "manual"}

    if _looks_like_isbn(code):
        data = _lookup_openlibrary(code) or _lookup_google_books(code)
    else:
        data = _lookup_upcitemdb(code)

    if not data:
        return {"barcode": code, "source": "manual"}

    data["barcode"] = code
    return data


# ---------- Open Library (books, free, no key) ----------

def _lookup_openlibrary(isbn: str) -> dict[str, Any] | None:
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
    except Exception as exc:
        log.warning("Open Library lookup failed for %s: %s", isbn, exc)
        return None

    payload = body.get(f"ISBN:{isbn}")
    if not payload:
        return None

    authors = ", ".join(a.get("name", "") for a in payload.get("authors", []) if a.get("name"))
    publishers = ", ".join(p.get("name", "") for p in payload.get("publishers", []) if p.get("name"))
    cover = payload.get("cover", {}) or {}
    image_url = cover.get("large") or cover.get("medium") or cover.get("small")

    description_parts = []
    if authors:
        description_parts.append(f"By {authors}")
    if payload.get("publish_date"):
        description_parts.append(f"Published {payload['publish_date']}")
    if publishers:
        description_parts.append(f"Publisher: {publishers}")
    if payload.get("number_of_pages"):
        description_parts.append(f"{payload['number_of_pages']} pages")

    return {
        "title": payload.get("title", ""),
        "brand": authors or publishers,
        "category": "Books",
        "description": " | ".join(description_parts),
        "image_url": image_url,
        "source": "Open Library",
    }


# ---------- Google Books (books fallback, free, no key) ----------

def _lookup_google_books(isbn: str) -> dict[str, Any] | None:
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
    except Exception as exc:
        log.warning("Google Books lookup failed for %s: %s", isbn, exc)
        return None

    items = body.get("items") or []
    if not items:
        return None
    info = items[0].get("volumeInfo", {})
    image_links = info.get("imageLinks", {}) or {}
    image_url = (
        image_links.get("extraLarge")
        or image_links.get("large")
        or image_links.get("medium")
        or image_links.get("thumbnail")
        or image_links.get("smallThumbnail")
    )
    if image_url and image_url.startswith("http://"):
        image_url = "https://" + image_url[len("http://"):]

    authors = ", ".join(info.get("authors", []))
    return {
        "title": info.get("title", ""),
        "brand": authors or info.get("publisher", ""),
        "category": "Books",
        "description": info.get("description", "")[:4000] if info.get("description") else "",
        "image_url": image_url,
        "source": "Google Books",
    }


# ---------- UPCitemdb trial (general products, free, no key) ----------

def _lookup_upcitemdb(upc: str) -> dict[str, Any] | None:
    url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={quote(upc)}"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code == 429:
            log.warning("UPCitemdb rate-limited for %s (free tier ~100/day per IP)", upc)
            return None
        r.raise_for_status()
        body = r.json()
    except Exception as exc:
        log.warning("UPCitemdb lookup failed for %s: %s", upc, exc)
        return None

    items = body.get("items") or []
    if not items:
        return None
    item = items[0]
    images = item.get("images") or []
    image_url = images[0] if images else None

    return {
        "title": item.get("title", ""),
        "brand": item.get("brand", ""),
        "model": item.get("model", ""),
        "category": item.get("category", ""),
        "description": item.get("description", ""),
        "image_url": image_url,
        "source": "UPCitemdb",
    }


# ---------- Image download / cache ----------

def cache_image(url: str | None, cache_dir: Path) -> str | None:
    """Download the image into cache_dir and return the local file path."""
    if not url:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(url.split("?")[0]).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        ext = ".jpg"
    name = hashlib.sha1(url.encode("utf-8")).hexdigest() + ext
    dest = cache_dir / name
    if dest.exists():
        return str(dest)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(8192):
                fh.write(chunk)
        return str(dest)
    except Exception as exc:
        log.warning("Image download failed for %s: %s", url, exc)
        return None
