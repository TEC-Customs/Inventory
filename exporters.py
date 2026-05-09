"""CSV exporters for eBay File Exchange, Facebook Marketplace, and a raw dump.

Both marketplace formats are templates - eBay and Facebook tweak required
columns now and then. Open the file in their bulk-upload tool, fix any
warnings (usually category ID or shipping profile name), and re-upload.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable


# ---------- Generic dump ----------

GENERIC_COLUMNS = [
    "id", "barcode", "sku", "title", "brand", "model", "category", "condition",
    "quantity", "cost", "price", "status", "location", "weight_oz",
    "description", "notes", "image_url", "image_path", "source",
    "created_at", "updated_at", "sold_at", "sold_price",
]


def export_generic(rows: Iterable[dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=GENERIC_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return out_path


# ---------- eBay File Exchange ----------
#
# Reference: https://www.isipl.com/file-exchange/ (eBay's File Exchange template).
# The first column header carries site/currency/version metadata; eBay parses
# it as a single string. Edit the version if eBay bumps it.

EBAY_HEADERS = [
    "*Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)",
    "CustomLabel",          # your SKU
    "*Category",            # eBay category ID (you must fill this in)
    "*Title",               # max 80 chars
    "Subtitle",
    "*Description",
    "*ConditionID",         # 1000=New, 1500=New other, 3000=Used, 7000=For parts
    "PicURL",               # pipe-separated URLs
    "*Quantity",
    "*Format",              # FixedPrice
    "*StartPrice",
    "*Duration",            # GTC = Good Till Cancelled
    "*Location",            # ZIP code
    "Brand",
    "MPN",                  # manufacturer part number / model
    "*ReturnsAcceptedOption",
    "ReturnsWithinOption",
    "RefundOption",
    "ShippingCostPaidByOption",
]

EBAY_CONDITION_MAP = {
    "new":            "1000",
    "new other":      "1500",
    "new with tags":  "1000",
    "open box":       "1500",
    "manufacturer refurbished": "2000",
    "seller refurbished":       "2500",
    "used":           "3000",
    "very good":      "4000",
    "good":           "5000",
    "acceptable":     "6000",
    "for parts":      "7000",
    "for parts or not working": "7000",
}


def _ebay_condition(cond: str | None) -> str:
    if not cond:
        return "3000"
    return EBAY_CONDITION_MAP.get(cond.strip().lower(), "3000")


def _truncate(text: str | None, n: int) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def export_ebay(rows: Iterable[dict[str, Any]], out_path: Path, default_zip: str = "") -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(EBAY_HEADERS)
        for row in rows:
            writer.writerow([
                "Add",
                row.get("sku", ""),
                "",                                      # Category - fill in eBay
                _truncate(row.get("title"), 80),
                "",
                row.get("description", "") or row.get("title", ""),
                _ebay_condition(row.get("condition")),
                row.get("image_url", "") or "",
                row.get("quantity") or 1,
                "FixedPrice",
                row.get("price") or "",
                "GTC",
                row.get("location") or default_zip,
                row.get("brand", ""),
                row.get("model", ""),
                "ReturnsAccepted",
                "Days_30",
                "MoneyBack",
                "Seller",
            ])
    return out_path


# ---------- Facebook Marketplace / Commerce catalog ----------
#
# Reference: Meta Commerce Manager bulk product upload spec.
# FB will reject rows missing required fields - the GUI prompts you to
# supply price/condition before exporting.

FB_HEADERS = [
    "id", "title", "description", "availability", "condition", "price",
    "link", "image_link", "brand", "google_product_category",
    "quantity_to_sell_on_facebook", "sale_price", "item_group_id",
]


FB_CONDITION_MAP = {
    "new":         "new",
    "new other":   "new",
    "open box":    "new",
    "used":        "used",
    "very good":   "used",
    "good":        "used",
    "acceptable":  "used",
    "refurbished": "refurbished",
    "manufacturer refurbished": "refurbished",
    "seller refurbished":       "refurbished",
    "for parts":   "used",
}


def _fb_condition(cond: str | None) -> str:
    if not cond:
        return "used"
    return FB_CONDITION_MAP.get(cond.strip().lower(), "used")


def _fb_price(value: Any) -> str:
    try:
        return f"{float(value or 0):.2f} USD"
    except (TypeError, ValueError):
        return "0.00 USD"


def export_facebook(rows: Iterable[dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FB_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            qty = int(row.get("quantity") or 1)
            writer.writerow({
                "id": row.get("sku") or f"item-{row.get('id')}",
                "title": _truncate(row.get("title"), 150),
                "description": row.get("description", "") or row.get("title", ""),
                "availability": "in stock" if qty > 0 and row.get("status") != "Sold" else "out of stock",
                "condition": _fb_condition(row.get("condition")),
                "price": _fb_price(row.get("price")),
                "link": "",
                "image_link": row.get("image_url", "") or "",
                "brand": row.get("brand", ""),
                "google_product_category": row.get("category", ""),
                "quantity_to_sell_on_facebook": qty,
                "sale_price": "",
                "item_group_id": "",
            })
    return out_path
