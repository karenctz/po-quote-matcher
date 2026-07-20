# PO ↔ Customer Quote Matcher

Compares the Purchase Orders Cactoz sends to suppliers against the customer
authorization that justified them (a signed Cactoz quote, or the customer's
own PO in whatever format they use) — flagging quantity mismatches, missing
items, or zero/negative margin.

## Running it

Just double-click `run.bat`. It handles everything:

- If Python isn't installed on this PC, it installs it automatically (via
  `winget`, built into Windows 11) — you'll be asked to double-click `run.bat`
  a second time once that finishes, since Windows needs a fresh window to
  pick up the new install.
- It installs the required packages (`streamlit`, `pdfplumber`, `pandas`)
  the first time, then launches the app.

This opens the app in your browser at `http://localhost:8501`. Nothing is
uploaded anywhere else — it all runs on your own PC.

## Sharing with colleagues

Just copy this whole folder to them (zip it, shared drive, Teams, etc.) —
there's nothing in it tied to a specific PC. They double-click `run.bat` and
it sets itself up (see above). If your company blocks `winget`, they'll need
to install Python manually from python.org first (tick "Add python.exe to
PATH" during setup), then run `run.bat` as normal.

## How to use it

1. Upload one or more supplier PO PDFs, and one or more customer quote/PO PDFs.
2. Cactoz's own PO/quote template is parsed reliably. Customer POs vary a lot
   in format, so check the "Customer quotes / POs" tab and correct any
   line-item cell that was mis-read before comparing (add/remove rows as
   needed — every cell is editable).
3. In the compare section, pick a supplier PO and a customer doc — the app
   suggests the most likely pairing based on how similar the item
   descriptions are, but you can pick any pair.
4. Review the flagged issues (qty mismatches, unmatched items, thin/negative
   margin) and download the comparison as CSV if you want a record.

## Limitations

Customer PO extraction is best-effort since every customer uses a different
template — always sanity-check the review table before relying on the
comparison.
