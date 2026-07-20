"""Extracts reference info and line items from Cactoz POs/quotes and arbitrary customer PO PDFs."""
import re
import pdfplumber

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

PART_NO_RE = re.compile(r"^[A-Z0-9][A-Z0-9/\-]{3,}$")
BOUNDARY_RE = re.compile(r"^(Sub-Total|GST|Total|Amount Payable|Remarks|Authorised Signature|Note:|General Terms)", re.I)

CACTOZ_ITEM_FULL = re.compile(
    r"^(?P<no>\d+)\s+(?P<rest>.*?)\s+(?P<qty>\d+)\s+(?P<price>" + MONEY + r")\s+(?P<amount>" + MONEY + r")\s*$"
)
CACTOZ_ITEM_QTY_ONLY = re.compile(r"^(?P<no>\d+)\s+(?P<rest>.*?)\s+(?P<qty>\d+)\s*$")

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


def get_text(path):
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def is_cactoz_template(text):
    head = text.strip().splitlines()[0] if text.strip() else ""
    return head.startswith("Cactoz Pte Ltd")


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
        m = CACTOZ_ITEM_FULL.match(l)
        if m:
            rest = m.group("rest").strip()
            part_no = ""
            desc = rest
            first_tok = rest.split(" ", 1)[0] if rest else ""
            if PART_NO_RE.match(first_tok):
                part_no = first_tok
                desc = rest[len(first_tok):].strip()
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
            rest = m.group("rest").strip()
            items.append({
                "part_no": "",
                "description": rest,
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


def extract_document(path):
    text = get_text(path)
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
        "raw_text": text,
    }
