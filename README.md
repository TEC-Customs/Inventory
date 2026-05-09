# Flip Inventory

Desktop inventory tracker for buying and re-selling tools, books, and random
finds. Scan a barcode with a USB scan gun → the app pulls title, brand, image,
and description from free public APIs → save it → export to eBay or Facebook
Marketplace when you're ready to list.

No browser. No hosting. Runs as a single Python window on your PC.

## Setup

1. Install Python 3.10+ from [python.org](https://www.python.org/downloads/).
   On Windows and macOS, Tkinter is included. On Linux: `sudo apt install python3-tk`.
2. From this folder:

   ```
   pip install -r requirements.txt
   python inventory.py
   ```

3. Plug your USB barcode scan gun into the PC. It behaves as a keyboard.

## Daily workflow

1. The **Scan barcode** field is auto-focused. Squeeze the trigger — the
   scanner types the digits and hits Enter, which adds the item.
2. The app calls the lookup APIs in the background and fills in title, brand,
   description, and a product image.
3. Click any row to edit cost, asking price, condition, location, notes, etc.
4. **Mark Sold** records the sold price and timestamp.
5. **Export eBay / Facebook / Generic CSV** writes a CSV you can upload to
   eBay Seller Hub → File Management or Facebook Commerce Manager.

The status filter at the top lets you export only `In Stock` items, only
`Listed`, etc.

## Where your data lives

Everything is stored under `~/FlipInventory/`:

- `inventory.db` — SQLite database (back this up)
- `images/` — cached product photos
- `exports/` — default save location for CSV exports

## Barcode lookup sources

- **ISBN / books** → Open Library, falling back to Google Books. Free, no key.
- **UPC / EAN** → UPCitemdb trial endpoint. Free but rate-limited to roughly
  100 requests per IP per day. If you hit the limit, the item is added with
  just the barcode and you can fill in the title manually.

If you start scanning hundreds of items per day, swap in a paid key inside
`lookup.py` (Barcode Lookup, GoUPC, or the paid UPCitemdb tier).

## Export notes

- **eBay File Exchange** — the template is filled but you must add an eBay
  category ID per row (column `*Category`) before bulk-uploading. Condition
  IDs and the action header are pre-set.
- **Facebook Marketplace** — uses Meta's Commerce Manager catalog feed
  format. Required fields are filled; price defaults to USD.
- **Generic CSV** — every column from the database, useful for spreadsheets
  or accounting.
