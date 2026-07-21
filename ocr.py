"""Calls an OCR webhook (a Power Automate flow backed by AI Builder) to read
text from a page image. Kept separate from extractor.py so that module stays
a pure/testable text-parsing library with no network or Streamlit dependency.

The image is sent as a raw binary body (not JSON-wrapped base64) because the
AI Builder "Recognize text" action expects an actual binary file object for
its Image input - a hand-built base64ToBinary()/concat() expression in the
flow does not produce the same shape and fails with InvalidImage.

The flow's Response action returns the recognized text as a plain-text body
(not JSON) - simpler to keep that instead of fighting Power Automate's
expression editor to wrap it into valid JSON."""
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
    return response.text
