"""Gemini-powered helpers: AI-generated listings and category suggestions.

Uses gemini-2.5-flash (the current stable Flash model on the v1beta endpoint).
The old gemini-1.5-flash-latest alias was retired by Google — that was the
404 you saw earlier.

API key is stored in ~/FlipInventory/config.json. You can paste it via
Settings → Set Gemini API key in the GUI. Get one for free at
https://aistudio.google.com/apikey
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests

GEMINI_MODEL = "gemini-2.5-flash"
ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
TIMEOUT = 45


def _config_path() -> Path:
    return Path.home() / "FlipInventory" / "config.json"


def _read_config() -> dict[str, Any]:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_api_key() -> str | None:
    return _read_config().get("gemini_api_key")


def set_api_key(key: str) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cfg = _read_config()
    cfg["gemini_api_key"] = key.strip()
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


class GeminiError(RuntimeError):
    pass


def _call(prompt: str, api_key: str, *, json_mode: bool = False) -> str:
    body: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

    r = requests.post(
        f"{ENDPOINT}?key={api_key}",
        json=body,
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        try:
            err = r.json().get("error", {}).get("message", r.text)
        except Exception:
            err = r.text
        raise GeminiError(f"Gemini {r.status_code}: {err}")

    data = r.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError) as exc:
        raise GeminiError(f"Unexpected Gemini response: {data}") from exc


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        if text.endswith("```"):
            text = text[: -len("```")]
    return text.strip()


def generate_listing(item: dict[str, Any], api_key: str) -> dict[str, str]:
    """Return {"title": str (<=80 chars), "description": str}."""
    prompt = f"""You are writing an eBay listing for a used item being resold.

Item information:
- Current title: {item.get('title', '') or '(none)'}
- Brand: {item.get('brand', '') or '(unknown)'}
- Model: {item.get('model', '') or '(unknown)'}
- Category: {item.get('category', '') or '(unknown)'}
- Condition: {item.get('condition', '') or 'Used'}
- Seller notes: {item.get('notes', '') or '(none)'}
- Existing description: {item.get('description', '') or '(none)'}

Return JSON with exactly two fields:
  "title": A keyword-rich eBay title, 80 characters or fewer.
           Lead with brand and model. Include condition only if not "New".
           No emoji, no ALL CAPS gimmicks.
  "description": 4-7 sentences of plain text. Cover what the item is,
                 condition specifics, what's included, and a short closing
                 line. No markdown, no bullet symbols, no shipping promises.

Output JSON only."""
    raw = _call(prompt, api_key, json_mode=True)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_strip_code_fence(raw))
    title = (data.get("title") or "").strip()
    if len(title) > 80:
        title = title[:79] + "…"
    return {
        "title": title,
        "description": (data.get("description") or "").strip(),
    }


def suggest_category(item: dict[str, Any], api_key: str) -> str:
    prompt = f"""Suggest the single best product category for this resale item.

Title: {item.get('title', '')}
Brand: {item.get('brand', '')}
Notes: {item.get('notes', '')}

Reply with 1-4 words only. Examples: "Power Tools", "Books", "Cookware",
"Video Games", "Kids Clothing". No quotes, no punctuation, no preamble."""
    text = _call(prompt, api_key)
    return text.strip().strip('"').strip("'").splitlines()[0][:60]
