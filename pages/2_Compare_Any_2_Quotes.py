import io
import sys
import traceback

import pandas as pd
import streamlit as st

from auth import check_password, get_secret
from extractor import extract_any_document
from matcher import compare_line_items, verdict
from ocr import call_power_automate_ocr

st.set_page_config(page_title="Compare Any 2 Quotes", layout="wide")

if not check_password("Compare Any 2 Quotes"):
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
            traceback.print_exc(file=sys.stderr)
            st.warning(
                "OCR request failed — add line items manually for this document. "
                "(Details were written to the app's server logs.)"
            )
            return []

    return _fallback


st.title("Compare Any 2 Quotes")
st.caption(
    "Upload any two quotes — PDF, Excel/CSV, or an image/scanned document — and "
    "compare item, part number, description, qty, and price side by side. "
    "Unlike the main PO ↔ Customer ↔ Supplier workflow, this doesn't assume "
    "either document is Cactoz's own template."
)

ACCEPTED_TYPES = ["pdf", "xlsx", "xls", "csv", "png", "jpg", "jpeg", "tif", "tiff", "bmp"]
ocr_fallback = build_ocr_fallback()
if ocr_fallback is None:
    st.info(
        "No OCR service is configured (`power_automate_url` secret), so scanned "
        "images/PDFs with no text layer will come back with no auto-detected "
        "line items — you can still add them manually in the table below."
    )


@st.cache_data(show_spinner="Extracting document...")
def parse_bytes(file_bytes, filename, _ocr_fallback=None):
    return extract_any_document(file_bytes, filename, _ocr_fallback=_ocr_fallback)


def upload_and_review(label, key_prefix):
    st.subheader(label)
    up = st.file_uploader(f"Upload {label}", type=ACCEPTED_TYPES, key=f"{key_prefix}_upload")
    if up is None:
        return None

    data = up.getvalue()
    try:
        doc = parse_bytes(data, up.name, _ocr_fallback=ocr_fallback)
    except Exception as e:
        st.error(f"Couldn't read {up.name}: {e}")
        return None

    st.caption(f"{doc['doc_type']} · {len(doc['line_items'])} line item(s) detected")
    if doc.get("ocr_used") and doc["line_items"]:
        st.badge(
            "Scanned document — read via OCR, please verify accuracy",
            icon=":material/warning:",
            color="orange",
        )
    if not doc["line_items"]:
        st.caption("No line items were auto-detected — add rows manually below if needed.")

    df = pd.DataFrame(doc["line_items"], columns=["part_no", "description", "qty", "unit_price", "amount"])
    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        width="stretch",
        key=f"{key_prefix}_editor",
        column_config={
            "part_no": "Part No.",
            "description": "Description",
            "qty": st.column_config.NumberColumn("Qty"),
            "unit_price": st.column_config.NumberColumn("Unit Price", format="%.2f"),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
        },
    )
    return edited_df.to_dict("records")


col_a, col_b = st.columns(2)
with col_a:
    items_a = upload_and_review("Quote A", "quoteA")
with col_b:
    items_b = upload_and_review("Quote B", "quoteB")

st.divider()

if items_a is not None and items_b is not None:
    if st.button("Compare Quotes", type="primary"):
        st.session_state["any2_result"] = compare_line_items(items_a, items_b, mode="exact_match")
else:
    st.info("Upload both quotes above, review/correct the extracted rows, then click Compare Quotes.")

if "any2_result" in st.session_state:
    result = st.session_state["any2_result"]
    issues = verdict(result)

    st.subheader("Comparison Result")
    m1, m2, m3 = st.columns(3)
    m1.metric("Matched", len(result["matched"]))
    m2.metric("Only in Quote A", len(result["unmatched_a"]))
    m3.metric("Only in Quote B", len(result["unmatched_b"]))

    if not issues:
        st.success("All line items matched — no discrepancies found.")
    else:
        st.error(f"{len(issues)} issue(s) found:")
        for i in issues:
            st.write(f"- {i}")

    rows = []
    for m in result["matched"]:
        a, b = m["item_a"], m["item_b"]
        rows.append({
            "Status": "Mismatch" if (not m["qty_match"] or m["price_mismatch"]) else "Matched",
            "Part No. A": a.get("part_no"), "Part No. B": b.get("part_no"),
            "Description A": a.get("description"), "Description B": b.get("description"),
            "Qty A": a.get("qty"), "Qty B": b.get("qty"),
            "Price A": a.get("unit_price"), "Price B": b.get("unit_price"),
            "Qty Match": "✅" if m["qty_match"] else "❌",
            "Price Match": "✅" if not m["price_mismatch"] else "❌",
            "Similarity": m["similarity"],
        })
    for a in result["unmatched_a"]:
        rows.append({
            "Status": "Only in Quote A",
            "Part No. A": a.get("part_no"), "Part No. B": "",
            "Description A": a.get("description"), "Description B": "",
            "Qty A": a.get("qty"), "Qty B": "",
            "Price A": a.get("unit_price"), "Price B": "",
            "Qty Match": "", "Price Match": "", "Similarity": "",
        })
    for b in result["unmatched_b"]:
        rows.append({
            "Status": "Only in Quote B",
            "Part No. A": "", "Part No. B": b.get("part_no"),
            "Description A": "", "Description B": b.get("description"),
            "Qty A": "", "Qty B": b.get("qty"),
            "Price A": "", "Price B": b.get("unit_price"),
            "Qty Match": "", "Price Match": "", "Similarity": "",
        })

    result_df = pd.DataFrame(rows)
    status_order = {"Mismatch": 0, "Only in Quote A": 1, "Only in Quote B": 2, "Matched": 3}
    if not result_df.empty:
        result_df["_order"] = result_df["Status"].map(status_order)
        result_df = result_df.sort_values("_order").drop(columns="_order").reset_index(drop=True)

    def highlight_status(row):
        color = {
            "Matched": "background-color: #d4edda",
            "Mismatch": "background-color: #fff3cd",
            "Only in Quote A": "background-color: #f8d7da",
            "Only in Quote B": "background-color: #d1ecf1",
        }.get(row["Status"], "")
        return [color] * len(row)

    st.dataframe(
        result_df.style.apply(highlight_status, axis=1) if not result_df.empty else result_df,
        width="stretch",
    )

    if not result_df.empty:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            result_df.to_excel(writer, index=False, sheet_name="Comparison")
        st.download_button(
            "Download comparison as Excel",
            data=buf.getvalue(),
            file_name="quote_comparison.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
