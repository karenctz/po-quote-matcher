import io
import pandas as pd
import streamlit as st

from extractor import extract_document
from matcher import compare_line_items, doc_pair_score, verdict

st.set_page_config(page_title="PO vs Customer Quote Matcher", layout="wide")


@st.cache_data(show_spinner=False)
def parse_pdf_bytes(file_bytes, filename):
    return extract_document(io.BytesIO(file_bytes))


def load_docs(files):
    docs = {}
    for f in files or []:
        docs[f.name] = parse_pdf_bytes(f.getvalue(), f.name)
    return docs


def render_editable_docs(docs, section_label):
    edited = {}
    for name, doc in docs.items():
        title = f"{name}  —  {doc['doc_type']}  —  Ref: {doc['reference_no'] or '?'}"
        with st.expander(title, expanded=False):
            meta_cols = st.columns(4)
            meta_cols[0].metric("Reference No.", doc["reference_no"] or "—")
            meta_cols[1].metric("Party", doc["party_name"] or "—")
            meta_cols[2].metric("Date", doc["order_date"] or "—")
            meta_cols[3].metric(
                "Total (SGD)",
                f"{doc['total_amount']:,.2f}" if doc["total_amount"] else "—",
            )
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
    return edited


def margin_icon(flag):
    if flag is None:
        return "—"
    return "⚠️" if flag else "✅"


st.title("PO ↔ Customer Quote Matcher")
st.caption(
    "Upload the Purchase Orders Cactoz sends to suppliers, and the customer authorization "
    "(a signed Cactoz quote, or the customer's own PO). The app extracts line items from both "
    "and flags quantity mismatches, missing items, or zero/negative margins."
)

col1, col2 = st.columns(2)
with col1:
    st.subheader("1a. Supplier POs (sent by Cactoz)")
    supplier_files = st.file_uploader(
        "Upload one or more supplier PO PDFs", type=["pdf"], accept_multiple_files=True, key="supplier_upload"
    )
with col2:
    st.subheader("1b. Customer signed quotes / customer POs")
    customer_files = st.file_uploader(
        "Upload one or more customer quote/PO PDFs", type=["pdf"], accept_multiple_files=True, key="customer_upload"
    )

supplier_docs = load_docs(supplier_files)
customer_docs = load_docs(customer_files)

st.divider()
st.subheader("2. Review & correct extracted line items")
st.caption(
    "Cactoz's own PO/quote template is parsed reliably. Customer POs come in many formats, so double-check "
    "these tables — edit any cell directly, or add/remove rows, before comparing."
)
tab1, tab2 = st.tabs(["Supplier POs", "Customer quotes / POs"])
with tab1:
    if not supplier_docs:
        st.info("Upload supplier PO PDFs above.")
    edited_supplier_items = render_editable_docs(supplier_docs, "supplier") if supplier_docs else {}
with tab2:
    if not customer_docs:
        st.info("Upload customer quote/PO PDFs above.")
    edited_customer_items = render_editable_docs(customer_docs, "customer") if customer_docs else {}

st.divider()
st.subheader("3. Compare a supplier PO against a customer quote/PO")

if not supplier_docs or not customer_docs:
    st.info("Upload at least one document on each side to run a comparison.")
else:
    supplier_name = st.selectbox("Supplier PO", list(supplier_docs.keys()))

    scores = {
        cname: doc_pair_score(edited_supplier_items[supplier_name], edited_customer_items[cname])
        for cname in customer_docs
    }
    customer_names = list(customer_docs.keys())
    best_customer = max(scores, key=scores.get) if scores else None
    default_idx = customer_names.index(best_customer) if best_customer in customer_names else 0
    customer_name = st.selectbox(
        "Customer quote / PO (best guess pre-selected based on item similarity)",
        customer_names,
        index=default_idx,
        format_func=lambda n: f"{n}   —   match score {scores[n]:.0%}",
    )

    s_items = edited_supplier_items[supplier_name]
    c_items = edited_customer_items[customer_name]
    result = compare_line_items(s_items, c_items)
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
            s, c = m["supplier_item"], m["customer_item"]
            rows.append({
                "Supplier item": s["description"],
                "Supplier qty": s.get("qty"),
                "Supplier unit price": s.get("unit_price"),
                "Customer item": c["description"],
                "Customer qty": c.get("qty"),
                "Customer unit price": c.get("unit_price"),
                "Qty match": "✅" if m["qty_match"] else "❌",
                "Margin (sell − buy)": m["margin"],
                "Margin OK": margin_icon(m["margin_flag"]),
                "Similarity": m["similarity"],
            })
        result_df = pd.DataFrame(rows)
        st.dataframe(result_df, width="stretch")
        st.download_button(
            "Download comparison as CSV",
            result_df.to_csv(index=False).encode("utf-8"),
            file_name=f"comparison_{supplier_name}_vs_{customer_name}.csv",
            mime="text/csv",
        )

    if result["unmatched_supplier"]:
        st.warning("Items in the supplier PO with no match in the customer doc:")
        st.dataframe(pd.DataFrame(result["unmatched_supplier"]), width="stretch")

    if result["unmatched_customer"]:
        st.warning("Items in the customer doc with no match in the supplier PO:")
        st.dataframe(pd.DataFrame(result["unmatched_customer"]), width="stretch")
