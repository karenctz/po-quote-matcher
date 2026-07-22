"""Extracts reference info and line items from Cactoz POs/quotes and arbitrary customer PO PDFs."""
import io
import re
import pdfplumber
import pandas as pd
from PIL import Image

MONEY = r"[\d][\d,]*\.\d{2}"

REF_PATTERNS = [
    # requires a space before the colon ("No. : XYZ") to avoid matching the
    # Co./GST "Reg. No.: 123..." lines in the Cactoz letterhead, which have no space;
    # value must be on the same line so a blank field (e.g. "MAKERS NO. :") can't
    # accidentally grab the next line's word
    r"No\.[ \t]+:[ \t]*([A-Za-z0-9][\w\-/]+)",
    r"PURCHASE ORDER No\.?:?\s*([\w\-]+)",
    r"Purchase Order #\s*(\d+)",
    r"(\d{6,})\s*/\s*\w+\s+dated",  # e.g. "4503850363 / SGO dated 26.06.2026"
    r"PO number/date\s*([\w\-]+)",
]

TOTAL_PATTERNS = [
    r"Total \(SGD\)\s*(" + MONEY + r")",
    r"Amount Payable \(SGD\)\s*(" + MONEY + r")",
    r"TOTAL AMOUNT EXCL\.?\s*VAT\s*(" + MONEY + r")",
    r"Total:\s*(" + MONEY + r")\s*SGD",
    r"Total net value excl\. tax\s*SGD\s*(" + MONEY + r")",
    r"Grand Total\s*(" + MONEY + r")",
]

DATE_PATTERNS = [
    r"Order Date\s*:\s*([\d]{1,2}\s+\w+\s+\d{4})",
    r"\bDate\s*:\s*([\d]{1,2}\s+\w+\s+\d{4})",
    r"PO Date:\s*([\d/]+)",
    r"ISSUE DATE\s*:?\s*([\d]{1,2}\s+\w+\s+\d{4})",
    r"dated\s+([\d.]{10})",
]

QUOTE_REF_PATTERN = r"QUOTE\s*REF\.?:?\s*([A-Za-z0-9\-]+)"

PART_NO_RE = re.compile(r"^[A-Z0-9][A-Z0-9/\-:]{3,}$")
# "GST" alone used to match the "GST Reg. No.: ..." line in Cactoz's own
# letterhead, which repeats at the top of every page - on a multi-page quote
# that falsely looked like the "GST 9% ..." end-of-items subtotal line and
# cut off every item after the page break. Requiring a digit right after
# (as in "GST 9%") excludes the letterhead line, which is followed by "Reg.".
BOUNDARY_RE = re.compile(r"^(Sub-Total|GST\s*\d|Total|Amount Payable|Remarks|Authorised Signature|Note:|General Terms)", re.I)

# Cactoz's letterhead repeats at the top of every page of a multi-page quote;
# on the raw joined text these lines land between the last item of one page
# and the first item of the next, and would otherwise be misread as a
# continuation of the previous line item's description (see parse_cactoz_items).
CACTOZ_HEADER_SKIP_RE = re.compile(
    r"^(Page \d+ of \d+|Cactoz Pte Ltd|Block \d|Singapore \d|Tel:|Fax:|Co\. Reg\. No\.|GST Reg\. No\.|No\.\s*:|Date\s*:)",
    re.I,
)

CACTOZ_ITEM_FULL = re.compile(
    r"^(?P<no>\d+)\s+(?P<rest>.*?)\s+(?P<qty>\d+)\s+(?P<price>" + MONEY + r")\s+(?P<amount>" + MONEY + r")\s*$"
)
CACTOZ_ITEM_QTY_ONLY = re.compile(r"^(?P<no>\d+)\s+(?P<rest>.*?)\s+(?P<qty>\d+)\s*$")


def _split_part_no(rest):
    """rest is the free-text portion of a matched item line, before the
    trailing qty (and price/amount, if present). Returns (part_no, description)
    - a real Cactoz line item always leads with its part number."""
    first_tok = rest.split(" ", 1)[0] if rest else ""
    if PART_NO_RE.match(first_tok):
        return first_tok, rest[len(first_tok):].strip()
    return "", rest

DECIMAL_RE = re.compile(MONEY)


def parse_number(s):
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def find_first(patterns, text):
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


# Ceiling on the total pixels a single rendered page can produce, sized well
# above any real business document - even an A0-sized page (33.1 x 46.8 in)
# still renders at the full default resolution under this budget. A page
# with an absurd/crafted declared size (independent of file size) gets
# scaled down proportionally instead of rejected - unless even the lowest
# usable DPI would still blow the budget (only possible for a page sized
# like a ~37x37 foot banner or bigger, which no real business document is),
# in which case that single page is skipped rather than rendered unsafely.
MAX_RENDER_PIXELS = 80_000_000
MIN_RENDER_DPI = 20


def _render_page_png(page, resolution=200):
    width_in = (page.width or 0) / 72
    height_in = (page.height or 0) / 72
    if width_in <= 0 or height_in <= 0:
        return None
    max_dpi_for_budget = (MAX_RENDER_PIXELS / (width_in * height_in)) ** 0.5
    resolution = min(resolution, max_dpi_for_budget)
    if resolution < MIN_RENDER_DPI:
        return None
    image = page.to_image(resolution=int(resolution)).original
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def reconstruct_ocr_text(lines):
    """Rebuilds a text blob from OCR line boxes (each {"text", "boundingBox":
    {"left", "top", "width", "height"}}), grouping lines that share the same
    visual row by vertical position and ordering each row left-to-right.

    AI Builder's OCR frequently splits a single visual row - a label and its
    value in a two-column form (e.g. "No." / ": KY-SQ2607-5041"), or a
    table's column headers ("Qty" / "Unit Price" / "Amount") - into separate
    output lines, even though they belong together. The existing regex-based
    parsers expect that content on one line (matching how pdfplumber's native
    text extraction already joins it), so this reconstructs that shape using
    the OCR engine's own coordinates rather than guessing from word order.
    """
    boxed = []
    for ln in lines:
        text = (ln.get("text") or "").strip()
        box = ln.get("boundingBox") or {}
        top, height, left = box.get("top"), box.get("height"), box.get("left")
        if not text or top is None or height is None or left is None:
            continue
        boxed.append({"text": text, "top": top, "height": height, "left": left, "center": top + height / 2})

    if not boxed:
        return ""

    boxed.sort(key=lambda b: b["center"])

    # Grouping is gated on the smaller of the two heights, not the larger -
    # otherwise a single large-font title (e.g. a page heading) can "reach"
    # far enough to bridge two unrelated small-text rows above and below it
    # into one merged row, scrambling their word order once sorted by x.
    rows = [[boxed[0]]]
    for b in boxed[1:]:
        prev = rows[-1][-1]
        threshold = min(b["height"], prev["height"]) * 0.6
        if abs(b["center"] - prev["center"]) <= threshold:
            rows[-1].append(b)
        else:
            rows.append([b])

    lines_out = []
    for row in rows:
        row.sort(key=lambda b: b["left"])
        lines_out.append(" ".join(b["text"] for b in row))
    return "\n".join(lines_out)


def get_text(path, _ocr_fallback=None):
    """Returns (text, ocr_used). Pages with no text layer (flat scans) are
    rendered to a PNG and passed to _ocr_fallback(png_bytes) -> list of OCR
    line boxes, if given (see reconstruct_ocr_text)."""
    pages = []
    ocr_used = False
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if not text.strip() and _ocr_fallback is not None:
                png_bytes = _render_page_png(page)
                if png_bytes is not None:
                    ocr_lines = _ocr_fallback(png_bytes) or []
                    text = reconstruct_ocr_text(ocr_lines)
                    ocr_used = True
            pages.append(text)
    return "\n".join(pages), ocr_used


def is_cactoz_template(text):
    # Checks the first couple of lines rather than requiring an exact-first-
    # line match: OCR'd letterheads can have "Cactoz Pte Ltd" merged onto the
    # same reconstructed row as an adjacent title/logo element and sorted
    # after it by horizontal position, rather than landing as its own line.
    head = "\n".join(text.strip().splitlines()[:2])
    return "Cactoz Pte Ltd" in head


def guess_party_name(text, is_cactoz):
    lines = [l for l in text.splitlines() if l.strip()]
    if is_cactoz:
        for l in lines:
            if re.search(r"No\.\s+:\s*", l):
                name = re.split(r"No\.\s+:\s*", l)[0].strip()
                if name:
                    return name
        return None
    # generic customer doc: first non-boilerplate line (skip timestamps, URLs,
    # title lines, and Bill-To/Ship-To column headers)
    skip_re = re.compile(
        r"https?://|\d{1,2}/\d{1,2}/\d{2,4}|\.com\b|^purchase order\b|"
        r"bill-to address|ship-to address|supplier details",
        re.I,
    )
    for l in lines[:8]:
        if skip_re.search(l):
            continue
        return l.strip()
    return lines[0] if lines else None


def parse_cactoz_items(text):
    lines = text.splitlines()
    header_idx = None
    for i, l in enumerate(lines):
        if re.search(r"Qty\.?", l, re.I) and re.search(r"(Unit Price|Amount)", l, re.I):
            header_idx = i
            break
    items = []
    if header_idx is None:
        return items
    for l in lines[header_idx + 1:]:
        l = l.strip()
        if not l:
            continue
        if BOUNDARY_RE.match(l):
            break
        if re.fullmatch(MONEY, l):
            # quotations show the pre-GST subtotal as a bare number with no label
            break
        if CACTOZ_HEADER_SKIP_RE.match(l):
            # letterhead repeated at the top of the next page - not a
            # continuation of the previous item, just noise to discard
            continue
        m = CACTOZ_ITEM_FULL.match(l)
        if m:
            part_no, desc = _split_part_no(m.group("rest").strip())
            items.append({
                "part_no": part_no,
                "description": desc,
                "qty": parse_number(m.group("qty")),
                "unit_price": parse_number(m.group("price")),
                "amount": parse_number(m.group("amount")),
            })
            continue
        m = CACTOZ_ITEM_QTY_ONLY.match(l)
        if m:
            part_no, desc = _split_part_no(m.group("rest").strip())
            items.append({
                "part_no": part_no,
                "description": desc,
                "qty": parse_number(m.group("qty")),
                "unit_price": None,
                "amount": None,
            })
            continue
        # continuation line: append to previous item's description
        if items and not re.match(r"^_+$", l):
            items[-1]["description"] = (items[-1]["description"] + " " + l).strip()
    return items


def parse_generic_items(text):
    """Best-effort line-item extraction for arbitrary customer PO formats.

    Rule of thumb that held up across Coupa / proprietary / SAP-style samples:
    take the first decimal-money token on a line as unit price and the last as
    the amount (skipping anything in between, e.g. a discount %), then infer
    qty = amount / price when a quantity column isn't reliably identifiable.
    """
    lines = text.splitlines()
    items = []
    skip_line_re = re.compile(
        r"^(Total|Sub-?total|Grand Total|Terms of|Currency|Payment terms|PO Date|Bill-To|Ship-To|Supplier Details|"
        r"General Info|Commodity:|Line #|SUBTOTAL|DISCOUNT|ISSUED BY|APPROVED BY|ACCOUNT NO|ADD'L INFO|"
        r"CERTIFICATE|MSDS|TECH ACC|PR NO|MAKER|CURRENCY)",
        re.I,
    )
    prev_text_line = None
    for l in lines:
        l = l.strip()
        if not l:
            continue
        if skip_line_re.match(l):
            continue
        decimals = DECIMAL_RE.findall(l)
        # a real item row has at least a unit price and an amount; single
        # stray decimals (weights, dates, spec-sheet fields) are noise. Track
        # the last plain-text line as a fallback description, since some
        # formats (e.g. SAP-style POs) put the item description on its own
        # line just above the qty/price/amount line.
        if len(decimals) < 2:
            if re.search(r"[A-Za-z]{3,}", l):
                prev_text_line = l
            continue
        price = parse_number(decimals[0])
        amount = parse_number(decimals[-1])
        if price is None or amount is None:
            continue
        qty = None
        if price and price > 0:
            ratio = amount / price
            if abs(ratio - round(ratio)) < 0.02 and round(ratio) > 0:
                qty = float(round(ratio))
        # description: strip the matched numeric tokens, trailing unit words,
        # and a leading row number (e.g. "1 Apple MB Air 13 M5" -> "Apple MB Air 13 M5")
        desc = l
        for d in decimals:
            desc = desc.replace(d, "")
        desc = re.sub(r"\b(each|pcs|ea|lot|unit|%)\b", "", desc, flags=re.I)
        desc = re.sub(r"\s{2,}", " ", desc).strip(" -\t")
        desc = re.sub(r"^\d{1,3}\s+(?=[A-Za-z])", "", desc)
        if not re.search(r"[A-Za-z]{3,}", desc) and prev_text_line:
            desc = prev_text_line
        prev_text_line = None
        if not desc:
            continue
        items.append({
            "part_no": "",
            "description": desc,
            "qty": qty,
            "unit_price": price,
            "amount": amount,
        })
    return items


def extract_document(path, _ocr_fallback=None):
    text, ocr_used = get_text(path, _ocr_fallback=_ocr_fallback)
    cactoz = is_cactoz_template(text)
    if cactoz:
        header = text.strip().splitlines()[0]
        doc_type = "Cactoz Purchase Order" if "PURCHASE ORDER" in text.upper() else "Cactoz Quotation"
        items = parse_cactoz_items(text)
    else:
        doc_type = "Customer PO"
        items = parse_generic_items(text)

    ref_no = find_first(REF_PATTERNS, text)
    order_date = find_first(DATE_PATTERNS, text)
    total_amount = parse_number(find_first(TOTAL_PATTERNS, text))
    quote_ref = find_first([QUOTE_REF_PATTERN], text)
    if quote_ref:
        quote_ref = quote_ref.split("/")[0].strip()
    party_name = guess_party_name(text, cactoz)

    return {
        "doc_type": doc_type,
        "reference_no": ref_no,
        "order_date": order_date,
        "party_name": party_name,
        "total_amount": total_amount,
        "referenced_quote_no": quote_ref,
        "line_items": items,
        "ocr_used": ocr_used,
        "raw_text": text,
    }


# --- Generic 2-document comparison (any quote, not just Cactoz/customer PO) ---
# Reuses the OCR plumbing above (reconstruct_ocr_text/_render_page_png) and
# parse_generic_items() so scanned images go through the same code path as a
# scanned PDF page, and adds a spreadsheet reader for Excel/CSV quotes.

COLUMN_KEYWORDS = {
    # identifier-style columns are checked before "description" below, so an
    # identifier column (e.g. "Product #") can never be claimed by the vaguer
    # "product"/"item" description keywords first
    "part_no": [
        "part no", "part number", "part#", "pn", "sku", "model", "material",
        "item no", "item code", "item #", "product no", "product number",
        "product code", "product #",
    ],
    # deliberately narrow: bare words like "item" or "product" also appear in
    # identifier headers ("Product #", "Item Code"), so a loose match here
    # would steal those columns before part_no's own (more specific) keywords
    # get a chance - see the description-column fallback in extract_spreadsheet
    "description": ["description", "desc"],
    "qty": ["qty", "quantity", "units"],
    "unit_price": ["unit price", "unit cost", "price", "rate", "cost"],
    "amount": ["amount", "total", "subtotal", "ext price", "extended price", "line total"],
}
# check longer/more specific keywords before shorter/generic ones sharing a
# substring (e.g. "unit price" before "price", "part no" before "item no")
_FIELD_ORDER = ["part_no", "unit_price", "amount", "qty", "description"]

SPREADSHEET_FOOTER_STOP_RE = re.compile(
    r"^(total|sub-?total|grand total|terms and conditions|pricing (&|and) ordering|amended by|vendors? terms|notes?:)\b",
    re.I,
)

HEADER_ROW_KEYWORDS = {
    "qty": ["qty", "quantity"],
    "identifier": ["part", "product", "item", "sku", "model"],
    "description": ["description", "desc"],
    "price": ["price", "amount", "cost"],
}


def guess_spreadsheet_mapping(columns, sample_df=None):
    mapping = {}
    used = set()
    for field in _FIELD_ORDER:
        for col in columns:
            if col in used:
                continue
            col_norm = str(col).strip().lower()
            if any(kw in col_norm for kw in COLUMN_KEYWORDS[field]):
                mapping[field] = col
                used.add(col)
                break

    # Fallback for sheets whose header doesn't literally say "description"
    # (e.g. just "Item" or "Product"): the description column is reliably the
    # one with the longest average text, so pick the longest unclaimed column
    # instead of guessing from the header name at all.
    if "description" not in mapping and sample_df is not None:
        candidates = [c for c in columns if c not in used]
        if candidates:
            lengths = {c: sample_df[c].astype(str).str.len().mean() for c in candidates}
            mapping["description"] = max(lengths, key=lengths.get)

    return mapping


def _score_header_row(cells):
    """cells: lowercased, stripped, non-null string values from one row."""
    score = 0
    for keywords in HEADER_ROW_KEYWORDS.values():
        if any(kw in cell for cell in cells for kw in keywords):
            score += 1
    return score


def find_header_row(raw_df, max_scan=15):
    """raw_df has no header applied (positional integer columns). Scans the
    first max_scan rows for the one that looks most like a real column
    header (many spreadsheet exports bury the real header several rows down,
    under a metadata block). Returns (row_idx, score); score 0 means nothing
    looked like a header at all. Exposed (not prefixed with _) so a UI can
    compute a sensible default before letting a human override it."""
    best_idx, best_score = 0, 0
    for i in range(min(max_scan, len(raw_df))):
        cells = [str(c).strip().lower() for c in raw_df.iloc[i] if pd.notna(c)]
        score = _score_header_row(cells)
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx, best_score


_find_header_row = find_header_row  # internal alias for the rest of this module


def list_sheet_names(file_bytes, filename):
    """Returns None for a CSV (no sheet concept) or a list of sheet names for
    an Excel workbook - lets the UI offer a sheet picker only when relevant."""
    if filename.lower().endswith(".csv"):
        return None
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


def read_raw_sheet(file_bytes, filename, sheet_name=None):
    """Returns a single sheet/CSV as a DataFrame with no header applied
    (positional integer columns), so the real header row - which may not be
    row 0 - can be picked afterwards, by a human or by _find_header_row."""
    buf = io.BytesIO(file_bytes)
    if filename.lower().endswith(".csv"):
        return pd.read_csv(buf, header=None, dtype=str)
    xls = pd.ExcelFile(buf)
    name = sheet_name if sheet_name is not None else xls.sheet_names[0]
    return xls.parse(name, header=None, dtype=str)


def _read_sheets_raw(file_bytes, filename):
    """Returns {sheet_name: DataFrame} with no header applied, so header-row
    detection can run against the raw grid."""
    buf = io.BytesIO(file_bytes)
    if filename.lower().endswith(".csv"):
        return {"": pd.read_csv(buf, header=None, dtype=str)}
    xls = pd.ExcelFile(buf)
    return {name: xls.parse(name, header=None, dtype=str) for name in xls.sheet_names}


def _pick_best_sheet(sheets):
    """A workbook may have several sheets (BOM, pricing rollup, summary, ...)
    - picks whichever one has the header row that looks most like a real
    line-item table, since the first sheet isn't reliably the right one.
    Returns (sheet_name, header_idx, raw_df)."""
    best = None  # (score, sheet_name, header_idx, raw_df)
    for name, raw_df in sheets.items():
        header_idx, score = _find_header_row(raw_df)
        if best is None or score > best[0]:
            best = (score, name, header_idx, raw_df)
    return best[1], best[2], best[3]


def apply_header_row(raw_df, header_row):
    """Slices a header-less raw_df at header_row, using that row as the
    column names for everything below it."""
    header = [
        str(c).strip() if pd.notna(c) else f"col_{i + 1}"
        for i, c in enumerate(raw_df.iloc[header_row])
    ]
    df = raw_df.iloc[header_row + 1:].copy()
    df.columns = header
    return df.reset_index(drop=True)


def items_from_mapped_dataframe(df, mapping):
    """df has real column names applied (see apply_header_row); mapping is
    {field: column_name} for whichever of part_no/description/qty/unit_price/
    amount were identified (by guess_spreadsheet_mapping or picked by hand)."""
    items = []
    for _, row in df.iterrows():
        first_cell = next((str(v).strip() for v in row if pd.notna(v) and str(v).strip()), "")
        if SPREADSHEET_FOOTER_STOP_RE.match(first_cell):
            # everything past a "Total"/"Terms and Conditions"/etc. row is
            # boilerplate footer text, not further line items
            break
        desc = str(row[mapping["description"]]).strip() if "description" in mapping and pd.notna(row[mapping["description"]]) else ""
        if not desc or desc.lower() == "nan":
            continue
        part_no = str(row[mapping["part_no"]]).strip() if "part_no" in mapping and pd.notna(row[mapping["part_no"]]) else ""
        qty = parse_number(row[mapping["qty"]]) if "qty" in mapping else None
        unit_price = parse_number(row[mapping["unit_price"]]) if "unit_price" in mapping else None
        amount = parse_number(row[mapping["amount"]]) if "amount" in mapping else None

        # A row with a description but no part number, qty, price, or amount
        # is very likely a wrapped continuation of the previous row's
        # description (common in BOM-style exports where a long description
        # spills onto a second row with everything else blank), not a new item.
        if items and not part_no and qty is None and unit_price is None and amount is None:
            items[-1]["description"] = (items[-1]["description"] + " " + desc).strip()
            continue

        items.append({
            "part_no": part_no,
            "description": desc,
            "qty": qty,
            "unit_price": unit_price,
            "amount": amount,
        })
    return items


def extract_spreadsheet(file_bytes, filename, sheet_name=None, header_row=None, mapping=None):
    """Auto-detects sheet/header/column mapping unless overridden - callers
    that want a human to confirm/adjust those first (see pages/2_Compare_Any_2_Documents.py)
    can pass sheet_name, header_row and/or mapping explicitly instead."""
    if sheet_name is None and header_row is None:
        sheets = _read_sheets_raw(file_bytes, filename)
        sheet_name, header_row, raw_df = _pick_best_sheet(sheets)
    else:
        raw_df = read_raw_sheet(file_bytes, filename, sheet_name=sheet_name)
        if header_row is None:
            header_row, _ = _find_header_row(raw_df)

    df = apply_header_row(raw_df, header_row)
    if mapping is None:
        mapping = guess_spreadsheet_mapping(list(df.columns), sample_df=df)

    items = items_from_mapped_dataframe(df, mapping)

    return {
        "doc_type": "Spreadsheet quote",
        "reference_no": None,
        "order_date": None,
        "party_name": None,
        "total_amount": None,
        "referenced_quote_no": None,
        "line_items": items,
        "ocr_used": False,
        "raw_text": "",
    }


def _to_png_bytes(image_bytes):
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def extract_image(file_bytes, _ocr_fallback=None):
    png_bytes = _to_png_bytes(file_bytes)
    text = ""
    if _ocr_fallback is not None:
        ocr_lines = _ocr_fallback(png_bytes) or []
        text = reconstruct_ocr_text(ocr_lines)
    items = parse_generic_items(text) if text else []

    return {
        "doc_type": "Scanned image quote",
        "reference_no": None,
        "order_date": None,
        "party_name": None,
        "total_amount": None,
        "referenced_quote_no": None,
        "line_items": items,
        "ocr_used": True,
        "raw_text": text,
    }


def extract_any_document(file_bytes, filename, _ocr_fallback=None):
    """Dispatches by extension: PDF (existing Cactoz/customer-PO parser),
    Excel/CSV (spreadsheet reader), or an image (OCR via _ocr_fallback,
    same plumbing a scanned PDF page uses)."""
    name = filename.lower()
    if name.endswith(".pdf"):
        return extract_document(io.BytesIO(file_bytes), _ocr_fallback=_ocr_fallback)
    if name.endswith((".xlsx", ".xls", ".csv")):
        return extract_spreadsheet(file_bytes, name)
    if name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")):
        return extract_image(file_bytes, _ocr_fallback=_ocr_fallback)
    raise ValueError(f"Unsupported file type: {filename}")
