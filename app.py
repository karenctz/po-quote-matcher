import streamlit as st

from auth import check_password
from nav import hide_main_nav_entry

st.set_page_config(page_title="Quote Matcher", layout="centered")

if not check_password("Quote Matcher"):
    st.stop()

hide_main_nav_entry()

st.title("Quote Matcher")
st.caption("Choose what you want to compare.")

with st.container(border=True):
    st.subheader("1. Compare PO ↔ Customer Quote")
    st.write(
        "The full Cactoz workflow: upload the original Cactoz quote, the "
        "customer's signed copy or PO, and the supplier PO. Flags quantity "
        "mismatches, missing items, price differences, and zero/negative margins."
    )
    st.page_link(
        "pages/1_Compare_PO_vs_Customer_Quote.py",
        label="Open PO ↔ Customer Quote Matcher",
        icon=":material/request_quote:",
    )

with st.container(border=True):
    st.subheader("2. Compare any 2 documents")
    st.write(
        "For anything that isn't the Cactoz template: upload any two quotes, "
        "POs, or invoices — PDF, Excel/CSV, or an image/scan — and compare "
        "item, part number, description, qty, and price."
    )
    st.page_link(
        "pages/2_Compare_Any_2_Documents.py",
        label="Open Compare Any 2 Documents",
        icon=":material/compare_arrows:",
    )
