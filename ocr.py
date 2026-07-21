"""Calls an OCR webhook (a Power Automate flow backed by AI Builder) to read
text from a page image. Kept separate from extractor.py so that module stays
a pure/testable text-parsing library with no network or Streamlit dependency."""
import base64

import requests


def call_power_automate_ocr(png_bytes, url, secret, timeout=60):
    response = requests.post(
        url,
        json={
            "secret": secret,
            "image_base64": base64.b64encode(png_bytes).decode("utf-8"),
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("text", "")
