import io
import sys
import traceback

import pandas as pd
import streamlit as st

from extractor import extract_document
from matcher import compare_line_items, doc_pair_score, verdict
from ocr import call_power_automate_ocr

st.set_page_config(page_title="PO vs Customer Quote Matcher", layout="wide")


def get_secret(key):
    # st.secrets.get() still raises StreamlitSecretNotFoundError when no
    # secrets.toml exists anywhere at all (e.g. a fresh local checkout with
    # no secrets configured), rather than returning the default - guard it.
    try:
        return st.secrets.get(key)
    except Exception:
        return None


def password_ok(entered, expected):
    return not expected or entered == expected


def check_password():
    expected = get_secret("app_password")
    if not expected:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("PO ↔ Customer Quote Matcher")
    pw = st.text_input("Password", type="password", key="password_input")
    if pw:
        if password_ok(pw, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


if not check_password():
    st.stop()


def build_ocr_fallback():
    url = get_secret("power_automate_url")
    secret = get_secret("power_automate_secret")
    if not url:
        return None

    def _fallback(png_bytes):
        try:
            return call_power_automate_ocr(png_bytes, url, secret)
        except Exception:
            # Don't show the raw exception in the UI - requests' error messages
            # include the full request URL, which embeds the Flow's auth token.
            # Full details still go to the app's server-side logs for debugging.
            traceback.print_exc(file=sys.stderr)
            st.warning(
                "OCR request failed — add line items manually for this document. "
                "(Details were written to the app's server logs.)"
            )
            return []

    return _fallback


@st.cache_data(show_spinner="Extracting document (scanned PDFs may take a moment)...")
def parse_pdf_bytes(file_bytes, filename, _ocr_fallback=None):
    return extract_document(io.BytesIO(file_bytes), _ocr_fallback=_ocr_fallback)


def load_docs(files, _ocr_fallback=None):
    docs = {}
    raw_bytes = {}
    for f in files or []:
        data = f.getvalue()
        docs[f.name] = parse_pdf_bytes(data, f.name, _ocr_fallback=_ocr_fallback)
        raw_bytes[f.name] = data
    return docs, raw_bytes


@st.dialog("Original PDF", width="large")
def _show_pdf_dialog(name, data):
    st.caption(name)
    st.pdf(data, height=700)


def render_editable_docs(docs, raw_bytes, section_label):
    edited = {}
    for name, doc in docs.items():
        with st.container(border=True):
            header_cols = st.columns([5, 1], vertical_alignment="center")
            header_cols[0].markdown(
                f"**{name}**  \n{doc['doc_type']}  —  Ref: {doc['reference_no'] or '?'}"
            )
            if header_cols[1].button(
                "View PDF",
                icon=":material/description:",
                key=f"viewbtn_{section_label}_{name}",
                width="stretch",
            ):
                _show_pdf_dialog(name, raw_bytes[name])

            if doc.get("ocr_used"):
                st.badge(
                    "Scanned document — it has been read via OCR, please verify accuracy",
                    icon=":material/warning:",
                    color="orange",
                )

            with st.expander("Review & edit line items", expanded=False):
                meta_cols = st.columns(4)
                meta_values = [
                    ("Reference No.", doc["reference_no"] or "—"),
                    ("Party", doc["party_name"] or "—"),
                    ("Date", doc["order_date"] or "—"),
                    ("Total (SGD)", f"{doc['total_amount']:,.2f}" if doc["total_amount"] else "—"),
                ]
                for col, (label, value) in zip(meta_cols, meta_values):
                    col.caption(label)
                    col.write(value)
                if doc.get("referenced_quote_no"):
                    st.caption(f"Document references quote: {doc['referenced_quote_no']}")
                if not doc["line_items"]:
                    st.caption("No line items were auto-detected — add rows manually below if needed.")
                df = pd.DataFrame(
                    doc["line_items"], columns=["part_no", "description", "qty", "unit_price", "amount"]
                )
                edited_df = st.data_editor(
                    df,
                    num_rows="dynamic",
                    width="stretch",
                    key=f"editor_{section_label}_{name}",
                    column_config={
                        "part_no": "Part No.",
                        "description": "Description",
                        "qty": st.column_config.NumberColumn("Qty"),
                        "unit_price": st.column_config.NumberColumn("Unit Price", format="%.2f"),
                        "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
                    },
                )
                edited[name] = edited_df.to_dict("records")
                if doc.get("ocr_used"):
                    st.caption(
                        "⚡ This document had no text layer (a scan) and was read via OCR — "
                        "double-check the extracted numbers carefully before comparing."
                    )
    return edited


def margin_icon(flag):
    if flag is None:
        return "—"
    return "⚠️" if flag else "✅"


def price_match_icon(mismatch):
    if mismatch is None:
        return "—"
    return "❌" if mismatch else "✅"


def render_compare_block(section_title, label_a, docs_a, items_a_map, label_b, docs_b, items_b_map, mode, key_prefix):
    st.subheader(section_title)

    name_a = st.selectbox(label_a, list(docs_a.keys()), key=f"{key_prefix}_a")

    scores = {nb: doc_pair_score(items_a_map[name_a], items_b_map[nb]) for nb in docs_b}
    names_b = list(docs_b.keys())
    best_b = max(scores, key=scores.get) if scores else None
    default_idx = names_b.index(best_b) if best_b in names_b else 0
    name_b = st.selectbox(
        f"{label_b} (best guess pre-selected based on item similarity)",
        names_b,
        index=default_idx,
        format_func=lambda n: f"{n}   —   match score {scores[n]:.0%}",
        key=f"{key_prefix}_b",
    )

    result = compare_line_items(items_a_map[name_a], items_b_map[name_b], mode=mode)
    issues = verdict(result)

    if not issues:
        st.success("All line items matched — no discrepancies found.")
    else:
        st.error(f"{len(issues)} issue(s) found:")
        for i in issues:
            st.write(f"- {i}")

    if result["matched"]:
        rows = []
        for m in result["matched"]:
            a, b = m["item_a"], m["item_b"]
            row = {
                f"{label_a} item": a["description"],
                f"{label_a} qty": a.get("qty"),
                f"{label_a} unit price": a.get("unit_price"),
                f"{label_b} item": b["description"],
                f"{label_b} qty": b.get("qty"),
                f"{label_b} unit price": b.get("unit_price"),
                "Qty match": "✅" if m["qty_match"] else "❌",
            }
            if mode == "margin":
                row["Margin (sell − buy)"] = m["margin"]
                row["Margin OK"] = margin_icon(m["margin_flag"])
            else:
                row["Price match"] = price_match_icon(m["price_mismatch"])
            row["Similarity"] = m["similarity"]
            rows.append(row)
        result_df = pd.DataFrame(rows)
        st.dataframe(
            result_df,
            width="stretch",
            column_config={
                f"{label_a} item": st.column_config.TextColumn(f"{label_a} item", pinned=True),
            },
        )

    if result["unmatched_a"]:
        st.warning(f"Items in the {label_a} with no match in the {label_b}:")
        st.dataframe(pd.DataFrame(result["unmatched_a"]), width="stretch")

    if result["unmatched_b"]:
        st.warning(f"Items in the {label_b} with no match in the {label_a}:")
        st.dataframe(pd.DataFrame(result["unmatched_b"]), width="stretch")


st.title("PO ↔ Customer Quote Matcher")
st.caption(
    "Upload the original Cactoz quote, the customer's authorization (a signed copy of that "
    "quote, or the customer's own PO), and the Purchase Order Cactoz sends to suppliers. The "
    "app extracts line items from each and flags quantity mismatches, missing items, price "
    "differences, or zero/negative margins."
)

ocr_fallback = build_ocr_fallback()

col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("1a. Cactoz Quotes (original, before signing)")
    quote_files = st.file_uploader(
        "Upload one or more Cactoz quote PDFs", type=["pdf"], accept_multiple_files=True, key="quote_upload"
    )
with col2:
    st.subheader("1b. Customer signed quotes / customer POs")
    customer_files = st.file_uploader(
        "Upload one or more customer quote/PO PDFs", type=["pdf"], accept_multiple_files=True, key="customer_upload"
    )
with col3:
    st.subheader("1c. Supplier POs (sent from Cactoz to suppliers)")
    supplier_files = st.file_uploader(
        "Upload one or more supplier PO PDFs", type=["pdf"], accept_multiple_files=True, key="supplier_upload"
    )

quote_docs, quote_bytes = load_docs(quote_files, _ocr_fallback=ocr_fallback)
customer_docs, customer_bytes = load_docs(customer_files, _ocr_fallback=ocr_fallback)
supplier_docs, supplier_bytes = load_docs(supplier_files, _ocr_fallback=ocr_fallback)

st.divider()
st.subheader("2. Review & correct extracted line items")
st.caption(
    "Cactoz's own PO/quote template is parsed reliably. Customer POs come in many formats, and "
    "scanned signed copies rely on OCR, so double-check these tables — edit any cell directly, "
    "or add/remove rows, before comparing."
)
st.html("""
<style>
.st-key-review_tabs [data-testid="stTab"] {
    font-size: 1.15rem;
    font-weight: 600;
    padding: 0.75rem 1.5rem;
}
</style>
""")
tab1, tab2, tab3 = st.tabs(
    [
        ":material/request_quote: Cactoz Quotes",
        ":material/mark_email_read: Customer quotes / POs",
        ":material/local_shipping: Supplier POs",
    ],
    key="review_tabs",
)
with tab1:
    if not quote_docs:
        st.info("Upload Cactoz quote PDFs above.")
    edited_quote_items = render_editable_docs(quote_docs, quote_bytes, "quote") if quote_docs else {}
with tab2:
    if not customer_docs:
        st.info("Upload customer quote/PO PDFs above.")
    edited_customer_items = render_editable_docs(customer_docs, customer_bytes, "customer") if customer_docs else {}
with tab3:
    if not supplier_docs:
        st.info("Upload supplier PO PDFs above.")
    edited_supplier_items = render_editable_docs(supplier_docs, supplier_bytes, "supplier") if supplier_docs else {}

st.divider()
st.subheader("3. Compare documents")

if quote_docs and customer_docs:
    render_compare_block(
        "3a. Cactoz Quote ↔ Customer signed copy (integrity check)",
        "Cactoz Quote", quote_docs, edited_quote_items,
        "Customer doc", customer_docs, edited_customer_items,
        mode="exact_match", key_prefix="cmp_quote_customer",
    )
else:
    st.info("Upload at least one Cactoz Quote and one Customer doc to run this comparison.")

st.divider()

if customer_docs and supplier_docs:
    render_compare_block(
        "3b. Customer doc ↔ Supplier PO (margin / fulfilment check)",
        "Customer doc", customer_docs, edited_customer_items,
        "Supplier PO", supplier_docs, edited_supplier_items,
        mode="margin", key_prefix="cmp_customer_supplier",
    )
else:
    st.info("Upload at least one Customer doc and one Supplier PO to run this comparison.")
