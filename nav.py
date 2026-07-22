"""Sidebar nav tweaks shared across app.py and pages/*.py."""
import streamlit as st


def hide_main_nav_entry():
    """Legacy multipage nav always lists the entry-point script (app.py) first,
    labelled 'app' after its filename. app.py is just a landing menu, not a
    page worth a nav entry of its own, so hide that one row."""
    st.html("""
    <style>
    [data-testid="stSidebarNav"] li:first-child { display: none; }
    </style>
    """)
