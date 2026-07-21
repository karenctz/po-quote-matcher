"""Calls an OCR webhook (a Power Automate flow backed by AI Builder) to read
text from a page image. Kept separate from extractor.py so that module stays
a pure/testable text-parsing library with no network or Streamlit dependency.

The image is sent as a raw binary body (not JSON-wrapped base64) because the
AI Builder "Recognize text" action expects an actual binary file object for
its Image input - a hand-built base64ToBinary()/concat() expression in the
flow does not produce the same shape and fails with InvalidImage.

The flow's Response action returns the AI Builder action's full JSON output
(a "lines" array, each with its text and a boundingBox), not just flat text -
a single concatenated string doesn't reliably preserve which words belong on
the same visual row (e.g. a label and its value in a two-column form, or a
table's column headers), since AI Builder often outputs those as separate
lines even when they're visually on the same row. extractor.reconstruct_ocr_text()
uses the coordinates to regroup them correctly."""
import requests


def call_power_automate_ocr(png_bytes, url, secret, timeout=60):
    response = requests.post(
        url,
        data=png_bytes,
        headers={
            "Content-Type": "image/png",
            "X-Ocr-Secret": secret,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("lines", [])
