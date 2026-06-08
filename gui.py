"""Tkinter desktop GUI for the flip-inventory tracker.

Designed for a USB barcode scan gun: scanners type the barcode characters
followed by Enter, so the scan field is permanently focused and the Enter
key triggers a lookup + add. Network calls run on a background thread so
the UI doesn't freeze while waiting for the API.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from PIL import Image, ImageTk

import ai
import comps
import exporters
import lookup
from db import Database

APP_TITLE = "Flip Inventory"
DATA_DIR = Path.home() / "FlipInventory"
DB_PATH = DATA_DIR / "inventory.db"
IMAGE_CACHE = DATA_DIR / "images"
EXPORT_DIR = DATA_DIR / "exports"

CONDITIONS = [
    "New", "New other", "Open box", "Manufacturer refurbished",
    "Seller refurbished", "Used", "Very Good", "Good", "Acceptable",
    "For parts",
]
STATUSES = ["In Stock", "Listed", "Sold", "Returned", "Donated"]

TREE_COLUMNS = (
    ("barcode", "Barcode", 110),
    ("title", "Title", 320),
    ("brand", "Brand", 130),
    ("condition", "Condition", 90),
    ("quantity", "Qty", 50),
    ("cost", "Cost", 70),
    ("price", "Price", 70),
    ("status", "Status", 90),
)


class InventoryApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x780")
        self.minsize(1100, 640)

        self.db = Database(DB_PATH)
        self._lookup_queue: queue.Queue = queue.Queue()
        self._thumb_cache: dict[int, ImageTk.PhotoImage] = {}
        self._detail_image: ImageTk.PhotoImage | None = None
        self._selected_id: int | None = None

        self._build_layout()
        self._refresh_table()
        self.scan_entry.focus_set()
        self.after(100, self._drain_lookup_queue)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=26)

        # ---- top bar: scan + search ----
        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")

        ttk.Label(top, text="Scan barcode:", font=("", 11, "bold")).pack(side="left")
        self.scan_var = tk.StringVar()
        self.scan_entry = ttk.Entry(top, textvariable=self.scan_var, width=28, font=("", 13))
        self.scan_entry.pack(side="left", padx=(6, 6))
        self.scan_entry.bind("<Return>", self._on_scan_submit)

        ttk.Button(top, text="Add", command=self._on_scan_submit).pack(side="left")

        ttk.Label(top, text="    Search:").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(top, textvariable=self.search_var, width=24)
        search_entry.pack(side="left", padx=(6, 6))
        search_entry.bind("<KeyRelease>", lambda _e: self._refresh_table())

        ttk.Label(top, text="Status:").pack(side="left")
        self.status_filter = tk.StringVar(value="All")
        status_box = ttk.Combobox(
            top, textvariable=self.status_filter,
            values=["All", *STATUSES], width=10, state="readonly",
        )
        status_box.pack(side="left", padx=(4, 0))
        status_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_table())

        # ---- main split: list | detail ----
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=4)

        list_frame = ttk.Frame(body)
        body.add(list_frame, weight=3)

        cols = [c[0] for c in TREE_COLUMNS]
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="browse")
        for key, label, width in TREE_COLUMNS:
            anchor = "e" if key in {"quantity", "cost", "price"} else "w"
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "title"))
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)
        self.tree.bind("<Delete>", lambda _e: self._delete_selected())

        # ---- detail panel ----
        self.detail = ttk.Frame(body, padding=10)
        body.add(self.detail, weight=2)
        self._build_detail_panel()

        # ---- bottom bar ----
        bottom = ttk.Frame(self, padding=(10, 4, 10, 10))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Export eBay CSV", command=self._export_ebay).pack(side="left")
        ttk.Button(bottom, text="Export Facebook CSV", command=self._export_facebook).pack(side="left", padx=6)
        ttk.Button(bottom, text="Export Generic CSV", command=self._export_generic).pack(side="left")
        ttk.Button(bottom, text="Open Data Folder", command=self._open_data_folder).pack(side="left", padx=6)
        ttk.Button(bottom, text="Settings", command=self._open_settings).pack(side="left")

        self.status_var = tk.StringVar(value="Ready. Plug in your scan gun and start scanning.")
        ttk.Label(bottom, textvariable=self.status_var, anchor="e").pack(side="right", fill="x", expand=True)

    def _build_detail_panel(self) -> None:
        self.detail_fields: dict[str, tk.Variable] = {}

        self.image_label = ttk.Label(self.detail, text="(no image)", anchor="center",
                                     borderwidth=1, relief="solid", width=24)
        self.image_label.grid(row=0, column=0, columnspan=4, pady=(0, 10))
        self.image_label.configure(width=240)

        def add_row(row: int, label: str, key: str, *, width: int = 32, values: list[str] | None = None,
                    span: int = 3) -> None:
            ttk.Label(self.detail, text=label).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self.detail_fields[key] = var
            if values is not None:
                w = ttk.Combobox(self.detail, textvariable=var, values=values, width=width - 2)
            else:
                w = ttk.Entry(self.detail, textvariable=var, width=width)
            w.grid(row=row, column=1, columnspan=span, sticky="ew", pady=2, padx=(6, 0))

        add_row(1, "Title",     "title")
        add_row(2, "Barcode",   "barcode")
        add_row(3, "SKU",       "sku")
        add_row(4, "Brand",     "brand")
        add_row(5, "Model",     "model")
        add_row(6, "Category",  "category")
        add_row(7, "Condition", "condition", values=CONDITIONS)
        add_row(8, "Status",    "status",    values=STATUSES)
        add_row(9, "Location",  "location")

        # numeric row
        ttk.Label(self.detail, text="Cost").grid(row=10, column=0, sticky="w", pady=2)
        self.detail_fields["cost"] = tk.StringVar()
        cost_entry = ttk.Entry(self.detail, textvariable=self.detail_fields["cost"], width=10)
        cost_entry.grid(row=10, column=1, sticky="w", padx=(6, 4))
        ttk.Label(self.detail, text="Price").grid(row=10, column=1, sticky="e", padx=(0, 70))
        self.detail_fields["price"] = tk.StringVar()
        price_entry = ttk.Entry(self.detail, textvariable=self.detail_fields["price"], width=10)
        price_entry.grid(row=10, column=2, sticky="w")
        ttk.Label(self.detail, text="Qty").grid(row=10, column=2, sticky="e", padx=(0, 50))
        self.detail_fields["quantity"] = tk.StringVar()
        ttk.Entry(self.detail, textvariable=self.detail_fields["quantity"], width=6).grid(row=10, column=3, sticky="w")

        # Net profit (price - cost), recomputed live as you edit cost/price
        self.profit_var = tk.StringVar(value="Net profit  $0.00 (0%)")
        self.profit_label = ttk.Label(self.detail, textvariable=self.profit_var, foreground="#0a8a3a")
        self.profit_label.grid(row=10, column=3, sticky="e", padx=(0, 4))
        self.detail_fields["cost"].trace_add("write", lambda *_a: self._recalc_profit())
        self.detail_fields["price"].trace_add("write", lambda *_a: self._recalc_profit())

        ttk.Label(self.detail, text="Image URL").grid(row=11, column=0, sticky="w", pady=2)
        self.detail_fields["image_url"] = tk.StringVar()
        ttk.Entry(self.detail, textvariable=self.detail_fields["image_url"], width=32).grid(
            row=11, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=2,
        )

        ttk.Label(self.detail, text="Description").grid(row=12, column=0, sticky="nw", pady=(8, 2))
        self.description_text = tk.Text(self.detail, height=6, width=40, wrap="word")
        self.description_text.grid(row=12, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=(8, 2))

        ttk.Label(self.detail, text="Notes").grid(row=13, column=0, sticky="nw", pady=2)
        self.notes_text = tk.Text(self.detail, height=3, width=40, wrap="word")
        self.notes_text.grid(row=13, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=2)

        ai_row = ttk.Frame(self.detail)
        ai_row.grid(row=14, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Button(ai_row, text="AI Listing", command=self._ai_listing).pack(side="left")
        ttk.Button(ai_row, text="AI Category", command=self._ai_category).pack(side="left", padx=6)
        ttk.Button(ai_row, text="Sold-Comp Lookup", command=self._sold_comp_lookup).pack(side="left")

        button_row = ttk.Frame(self.detail)
        button_row.grid(row=15, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Button(button_row, text="Save", command=self._save_detail).pack(side="left")
        ttk.Button(button_row, text="Mark Sold", command=self._mark_sold).pack(side="left", padx=6)
        ttk.Button(button_row, text="Re-fetch from web", command=self._refetch).pack(side="left")
        ttk.Button(button_row, text="Delete", command=self._delete_selected).pack(side="right")

        for col in range(4):
            self.detail.columnconfigure(col, weight=1)

    # ------------------------------------------------------------------
    # Scan flow
    # ------------------------------------------------------------------
    def _on_scan_submit(self, _event: Any = None) -> None:
        code = self.scan_var.get().strip()
        if not code:
            return
        self.scan_var.set("")
        self.scan_entry.focus_set()
        existing = self.db.find_by_barcode(code)
        if existing:
            self._set_status(f"Already in inventory ({existing['title'] or code}); +1 quantity.")
            new_qty = (existing.get("quantity") or 0) + 1
            self.db.update_item(existing["id"], {"quantity": new_qty})
            self._refresh_table(select_id=existing["id"])
            return

        # Insert a placeholder row immediately, then enrich on a background thread.
        item_id = self.db.add_item({"barcode": code, "title": f"(looking up {code}…)", "source": "pending"})
        self._refresh_table(select_id=item_id)
        self._set_status(f"Looking up {code}…")
        threading.Thread(
            target=self._lookup_worker,
            args=(item_id, code),
            daemon=True,
        ).start()

    def _lookup_worker(self, item_id: int, code: str) -> None:
        try:
            data = lookup.lookup(code)
            local_image = lookup.cache_image(data.get("image_url"), IMAGE_CACHE)
            if local_image:
                data["image_path"] = local_image
            self._lookup_queue.put(("ok", item_id, data))
        except Exception as exc:  # pragma: no cover - defensive
            self._lookup_queue.put(("err", item_id, str(exc)))

    def _drain_lookup_queue(self) -> None:
        try:
            while True:
                kind, item_id, payload = self._lookup_queue.get_nowait()
                if kind == "ok":
                    payload.setdefault("title", f"Unknown ({payload.get('barcode')})")
                    self.db.update_item(item_id, payload)
                    src = payload.get("source", "manual")
                    self._set_status(f"Added item #{item_id} via {src}: {payload.get('title','')}")
                    self._refresh_table(select_id=item_id)
                else:
                    self._set_status(f"Lookup failed for item #{item_id}: {payload}")
        except queue.Empty:
            pass
        self.after(150, self._drain_lookup_queue)

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------
    def _refresh_table(self, *, select_id: int | None = None) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        items = self.db.list_items(
            search=self.search_var.get().strip() or None,
            status=self.status_filter.get(),
        )
        for item in items:
            self.tree.insert(
                "", "end", iid=str(item["id"]),
                values=(
                    item.get("barcode") or "",
                    item.get("title") or "",
                    item.get("brand") or "",
                    item.get("condition") or "",
                    item.get("quantity") or 0,
                    f"{item.get('cost') or 0:.2f}",
                    f"{item.get('price') or 0:.2f}",
                    item.get("status") or "",
                ),
            )
        if select_id is not None and self.tree.exists(str(select_id)):
            self.tree.selection_set(str(select_id))
            self.tree.see(str(select_id))
            self._load_detail(select_id)

    def _on_select_row(self, _event: Any) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self._load_detail(int(sel[0]))

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------
    def _load_detail(self, item_id: int) -> None:
        item = self.db.get_item(item_id)
        if not item:
            return
        self._selected_id = item_id
        for key, var in self.detail_fields.items():
            value = item.get(key)
            var.set("" if value is None else str(value))
        self.description_text.delete("1.0", "end")
        self.description_text.insert("1.0", item.get("description") or "")
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", item.get("notes") or "")
        self._show_image(item.get("image_path"))

    def _show_image(self, path: str | None) -> None:
        if not path or not Path(path).exists():
            self._detail_image = None
            self.image_label.configure(image="", text="(no image)")
            return
        try:
            img = Image.open(path)
            img.thumbnail((240, 240))
            self._detail_image = ImageTk.PhotoImage(img)
            self.image_label.configure(image=self._detail_image, text="")
        except Exception:
            self._detail_image = None
            self.image_label.configure(image="", text="(image error)")

    def _collect_detail(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, var in self.detail_fields.items():
            value = var.get().strip()
            if key in {"cost", "price"}:
                data[key] = _to_float(value)
            elif key == "quantity":
                data[key] = _to_int(value, default=1)
            else:
                data[key] = value
        data["description"] = self.description_text.get("1.0", "end").strip()
        data["notes"] = self.notes_text.get("1.0", "end").strip()
        return data

    def _save_detail(self) -> None:
        if self._selected_id is None:
            return
        data = self._collect_detail()
        if not data.get("title"):
            messagebox.showwarning(APP_TITLE, "Title is required.")
            return
        self.db.update_item(self._selected_id, data)
        self._set_status(f"Saved item #{self._selected_id}.")
        self._refresh_table(select_id=self._selected_id)

    def _mark_sold(self) -> None:
        if self._selected_id is None:
            return
        item = self.db.get_item(self._selected_id)
        if not item:
            return
        default = item.get("price") or 0
        price_str = simpledialog.askstring(
            APP_TITLE, "Sold for (USD):", initialvalue=f"{default:.2f}",
            parent=self,
        )
        if price_str is None:
            return
        self.db.mark_sold(self._selected_id, _to_float(price_str))
        self._set_status(f"Marked item #{self._selected_id} as sold.")
        self._refresh_table(select_id=self._selected_id)

    def _refetch(self) -> None:
        if self._selected_id is None:
            return
        item = self.db.get_item(self._selected_id)
        if not item or not item.get("barcode"):
            messagebox.showinfo(APP_TITLE, "This item has no barcode to look up.")
            return
        self._set_status(f"Re-fetching {item['barcode']}…")
        threading.Thread(
            target=self._lookup_worker,
            args=(self._selected_id, item["barcode"]),
            daemon=True,
        ).start()

    def _delete_selected(self) -> None:
        if self._selected_id is None:
            return
        if not messagebox.askyesno(APP_TITLE, "Delete this item?"):
            return
        self.db.delete_item(self._selected_id)
        self._selected_id = None
        self._refresh_table()

    # ------------------------------------------------------------------
    # Exporters
    # ------------------------------------------------------------------
    def _ask_export_path(self, default_name: str) -> Path | None:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save export as",
            defaultextension=".csv",
            initialdir=str(EXPORT_DIR),
            initialfile=default_name,
            filetypes=[("CSV", "*.csv")],
        )
        return Path(path) if path else None

    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    def _export_ebay(self) -> None:
        rows = self.db.list_items(status=self.status_filter.get())
        if not rows:
            messagebox.showinfo(APP_TITLE, "No items to export.")
            return
        path = self._ask_export_path(f"ebay-{self._stamp()}.csv")
        if not path:
            return
        exporters.export_ebay(rows, path)
        self._set_status(f"Exported {len(rows)} rows to {path}")
        messagebox.showinfo(APP_TITLE, f"Exported {len(rows)} rows.\n\nNote: fill in eBay Category IDs before bulk-uploading.")

    def _export_facebook(self) -> None:
        rows = self.db.list_items(status=self.status_filter.get())
        if not rows:
            messagebox.showinfo(APP_TITLE, "No items to export.")
            return
        path = self._ask_export_path(f"facebook-{self._stamp()}.csv")
        if not path:
            return
        exporters.export_facebook(rows, path)
        self._set_status(f"Exported {len(rows)} rows to {path}")
        messagebox.showinfo(APP_TITLE, f"Exported {len(rows)} rows for Facebook Commerce Manager.")

    def _export_generic(self) -> None:
        rows = self.db.list_items(status=self.status_filter.get())
        if not rows:
            messagebox.showinfo(APP_TITLE, "No items to export.")
            return
        path = self._ask_export_path(f"inventory-{self._stamp()}.csv")
        if not path:
            return
        exporters.export_generic(rows, path)
        self._set_status(f"Exported {len(rows)} rows to {path}")
        messagebox.showinfo(APP_TITLE, f"Exported {len(rows)} rows.")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def _open_data_folder(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        import os
        import subprocess
        import sys
        if sys.platform.startswith("win"):
            os.startfile(DATA_DIR)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(DATA_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(DATA_DIR)])

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    # ------------------------------------------------------------------
    # Net profit
    # ------------------------------------------------------------------
    def _recalc_profit(self) -> None:
        cost = _to_float(self.detail_fields["cost"].get())
        price = _to_float(self.detail_fields["price"].get())
        profit = price - cost
        pct = (profit / cost * 100) if cost > 0 else 0
        sign = "+" if profit >= 0 else "-"
        self.profit_var.set(f"Net profit  {sign}${abs(profit):,.2f} ({pct:+.0f}%)")
        self.profit_label.configure(foreground="#0a8a3a" if profit >= 0 else "#b00020")

    # ------------------------------------------------------------------
    # Settings (Gemini API key)
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        current = ai.get_api_key() or ""
        masked = (current[:4] + "…" + current[-4:]) if len(current) > 8 else current
        prompt = "Paste your Google Gemini API key.\nGet one free at https://aistudio.google.com/apikey"
        if current:
            prompt += f"\n\nCurrent key: {masked}"
        key = simpledialog.askstring(APP_TITLE, prompt, parent=self, show="*")
        if key is None:
            return
        ai.set_api_key(key)
        self._set_status("Gemini API key saved." if key else "Gemini API key cleared.")

    def _require_api_key(self) -> str | None:
        key = ai.get_api_key()
        if not key:
            messagebox.showinfo(
                APP_TITLE,
                "No Gemini API key set yet.\n\n"
                "Click Settings to paste one. Free keys: "
                "https://aistudio.google.com/apikey",
            )
            return None
        return key

    # ------------------------------------------------------------------
    # AI Listing
    # ------------------------------------------------------------------
    def _ai_listing(self) -> None:
        if self._selected_id is None:
            return
        key = self._require_api_key()
        if not key:
            return
        item = self._collect_detail()
        self._set_status("Generating listing with Gemini…")
        threading.Thread(
            target=self._ai_listing_worker,
            args=(self._selected_id, item, key),
            daemon=True,
        ).start()

    def _ai_listing_worker(self, item_id: int, item: dict[str, Any], key: str) -> None:
        try:
            result = ai.generate_listing(item, key)
            self.after(0, lambda: self._apply_ai_listing(item_id, result))
        except Exception as exc:
            msg = str(exc)
            self.after(0, lambda: self._set_status(f"AI Listing failed: {msg}"))
            self.after(0, lambda: messagebox.showerror(APP_TITLE, msg))

    def _apply_ai_listing(self, item_id: int, result: dict[str, str]) -> None:
        if self._selected_id != item_id:
            return
        if not messagebox.askyesno(
            APP_TITLE,
            f"Replace title and description with AI version?\n\n"
            f"New title:\n{result.get('title','')}\n\n"
            f"New description:\n{result.get('description','')[:300]}…",
        ):
            self._set_status("AI listing discarded.")
            return
        self.detail_fields["title"].set(result.get("title", ""))
        self.description_text.delete("1.0", "end")
        self.description_text.insert("1.0", result.get("description", ""))
        self._save_detail()

    # ------------------------------------------------------------------
    # AI Category
    # ------------------------------------------------------------------
    def _ai_category(self) -> None:
        if self._selected_id is None:
            return
        key = self._require_api_key()
        if not key:
            return
        item = self._collect_detail()
        self._set_status("Suggesting category…")
        threading.Thread(
            target=self._ai_category_worker,
            args=(self._selected_id, item, key),
            daemon=True,
        ).start()

    def _ai_category_worker(self, item_id: int, item: dict[str, Any], key: str) -> None:
        try:
            cat = ai.suggest_category(item, key)
            self.after(0, lambda: self._apply_ai_category(item_id, cat))
        except Exception as exc:
            msg = str(exc)
            self.after(0, lambda: self._set_status(f"AI Category failed: {msg}"))
            self.after(0, lambda: messagebox.showerror(APP_TITLE, msg))

    def _apply_ai_category(self, item_id: int, cat: str) -> None:
        if self._selected_id != item_id:
            return
        self.detail_fields["category"].set(cat)
        self._set_status(f"Category set to: {cat}")

    # ------------------------------------------------------------------
    # Sold-comp lookup
    # ------------------------------------------------------------------
    def _sold_comp_lookup(self) -> None:
        if self._selected_id is None:
            return
        title = self.detail_fields["title"].get().strip()
        brand = self.detail_fields["brand"].get().strip()
        model = self.detail_fields["model"].get().strip()
        default_query = " ".join(x for x in [brand, model, title] if x).strip() or title
        query = simpledialog.askstring(
            APP_TITLE, "Search eBay sold listings for:", initialvalue=default_query, parent=self,
        )
        if not query:
            return
        self._set_status(f"Looking up sold comps for: {query}…")
        threading.Thread(
            target=self._sold_comp_worker,
            args=(self._selected_id, query),
            daemon=True,
        ).start()

    def _sold_comp_worker(self, item_id: int, query: str) -> None:
        try:
            results = comps.lookup_sold_comps(query)
            summary = comps.summarize(results)
            self.after(0, lambda: self._show_sold_comps(item_id, query, results, summary))
        except Exception as exc:
            msg = str(exc)
            self.after(0, lambda: self._set_status(f"Comp lookup failed: {msg}"))
            self.after(0, lambda: messagebox.showerror(APP_TITLE, msg))

    def _show_sold_comps(
        self,
        item_id: int,
        query: str,
        results: list[dict[str, Any]],
        summary: dict[str, Any] | None,
    ) -> None:
        if self._selected_id != item_id:
            return
        if not results:
            messagebox.showinfo(APP_TITLE, f"No sold listings found for:\n{query}")
            return

        win = tk.Toplevel(self)
        win.title(f"Sold comps — {query}")
        win.geometry("760x520")

        header_text = f"{summary['count']} sold results" if summary else f"{len(results)} results"
        if summary:
            header_text += (
                f"   |   median ${summary['median']:.2f}"
                f"   |   avg ${summary['average']:.2f}"
                f"   |   range ${summary['min']:.2f}–${summary['max']:.2f}"
            )
        ttk.Label(win, text=header_text, padding=10, font=("", 10, "bold")).pack(fill="x")

        cols = ("price", "title")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("price", text="Sold price")
        tree.heading("title", text="Title")
        tree.column("price", width=110, anchor="e", stretch=False)
        tree.column("title", width=600, anchor="w")
        for r in results:
            tree.insert("", "end", values=(r["price_text"], r["title"]))
        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        vsb.pack(side="left", fill="y", padx=(0, 10), pady=10)

        if summary:
            btn_row = ttk.Frame(win)
            btn_row.pack(fill="x", padx=10, pady=(0, 10))
            ttk.Button(
                btn_row, text=f"Use median (${summary['median']:.2f}) as price",
                command=lambda: self._apply_comp_price(summary["median"], win),
            ).pack(side="left")
            ttk.Button(
                btn_row, text=f"Use average (${summary['average']:.2f})",
                command=lambda: self._apply_comp_price(summary["average"], win),
            ).pack(side="left", padx=6)
            ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="right")

    def _apply_comp_price(self, price: float, win: tk.Toplevel) -> None:
        self.detail_fields["price"].set(f"{price:.2f}")
        win.destroy()
        self._set_status(f"Price set to ${price:.2f} from sold comps.")


def _to_float(value: Any) -> float:
    try:
        return float(str(value).replace("$", "").replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app = InventoryApp()
    app.mainloop()


if __name__ == "__main__":
    main()
