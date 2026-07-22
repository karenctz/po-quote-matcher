"""Shared password gate for every page for this multipage app (imported by
app.py and by pages/*.py, since Streamlit doesn't share module-level state
across page files)."""
import streamlit as st


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


def check_password(title):
    expected = get_secret("app_password")
    if not expected:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title(title)
    pw = st.text_input("Password", type="password", key="password_input")
    if pw:
        if password_ok(pw, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False
