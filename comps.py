"""Scrape eBay sold-listings search to get comp pricing for an item.

eBay's official Browse API requires OAuth + app registration, so this uses
the public sold-listings search page. The HTML structure is reasonably
stable but if eBay redesigns it the parser may need updating.
"""
from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urlencode

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 15

PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")


def lookup_sold_comps(query: str, max_results: int = 25) -> list[dict[str, Any]]:
    """Return a list of {title, price_text, price, url} dicts for recently sold items."""
    if not query.strip():
        return []

    params = {
        "_nkw": query,
        "LH_Sold": 1,
        "LH_Complete": 1,
        "_ipg": 60,
    }
    url = "https://www.ebay.com/sch/i.html?" + urlencode(params)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    html = r.text

    items: list[dict[str, Any]] = []
    # Each result lives in a <li class="s-item ..."> block. Use a tolerant
    # regex that grabs the title link, the URL, and the price span.
    block_re = re.compile(
        r'<li[^>]*class="[^"]*s-item[^"]*"[^>]*>(.*?)</li>',
        re.DOTALL,
    )
    title_re = re.compile(
        r'class="s-item__title"[^>]*>\s*(?:<span[^>]*>)?([^<]+)',
        re.DOTALL,
    )
    link_re = re.compile(r'class="s-item__link"[^>]*href="([^"]+)"')
    price_re = re.compile(
        r'class="s-item__price"[^>]*>\s*(?:<span[^>]*>)?\$?([^<]+)',
        re.DOTALL,
    )

    for block in block_re.finditer(html):
        chunk = block.group(1)
        tm = title_re.search(chunk)
        pm = price_re.search(chunk)
        lm = link_re.search(chunk)
        if not tm or not pm:
            continue
        title = unescape(tm.group(1)).strip()
        if title.lower() in {"shop on ebay", ""}:
            continue
        price_text = "$" + unescape(pm.group(1)).strip()
        price_match = PRICE_RE.search(price_text)
        price = float(price_match.group(1).replace(",", "")) if price_match else None
        items.append({
            "title": title,
            "price_text": price_text,
            "price": price,
            "url": lm.group(1) if lm else "",
        })
        if len(items) >= max_results:
            break
    return items


def summarize(comps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return min / max / median / average / count for the numeric prices."""
    prices = sorted(c["price"] for c in comps if c.get("price") is not None)
    if not prices:
        return None
    n = len(prices)
    median = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2
    return {
        "count": n,
        "min": prices[0],
        "max": prices[-1],
        "median": median,
        "average": sum(prices) / n,
    }
