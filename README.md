# PO ↔ Customer Quote Matcher

Compares three kinds of documents around a sale:

- **Cactoz Quote** — the original quote Cactoz sent to the customer, before signing.
- **Customer doc** — what comes back: a signed copy of that quote, or the customer's own PO.
- **Supplier PO** — the Purchase Order Cactoz sends to a supplier to fulfil the order.

It runs two checks side by side:

- **3a. Cactoz Quote ↔ Customer doc** — did the customer alter anything (price, quantity, items) before signing back?
- **3b. Customer doc ↔ Supplier PO** — does what's being bought from the supplier match what was sold to the customer, with a healthy margin?

Customer documents come in all kinds of formats, including flat scans with
no extractable text at all (e.g. a signed copy photocopied and re-saved as
PDF) — those get OCR'd via an optional Power Automate + AI Builder flow (see
below); everything else is parsed directly.

## Running it

This app is hosted on Streamlit Community Cloud — there's no local install
for anyone to run. Colleagues just open the app's URL (and the shared
password, if one is set — see "Secrets" below).

## Deploying to Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io), sign in, and
   click "New app".
2. Point it at the `karenctz/po-quote-matcher` GitHub repo, branch `master`,
   main file path `app.py`. Deploy.
3. Once deployed, open the app's Settings → Secrets, and paste in the
   contents of `secrets.toml.example` (see below) with real values filled in.
4. Every push to `master` on GitHub auto-redeploys the app — nothing else to do.

## Secrets

Paste the keys from `secrets.toml.example` into Streamlit Community Cloud's
Settings → Secrets box for the hosted app, with real values filled in
(never commit real secrets to the repo):

- `app_password` — optional. If set, the whole app is gated behind this
  shared password (a simple text box, nothing fancy). Leave it unset for
  no gate at all.
- `power_automate_url` / `power_automate_secret` — optional, together they
  enable OCR for scanned documents. Leave both unset to skip OCR entirely.

## Setting up OCR for scanned signed documents (optional)

Signed customer copies are sometimes flat scans with no text layer — regular
PDF parsing can't read anything off them. OCR is done via a Power Automate
flow using AI Builder, which you'll need to build once in your own tenant
(Premium access required):

1. **Trigger**: add "When an HTTP request is received" (Premium connector).
   Leave the request body schema empty — the image is sent as a raw binary
   body (`Content-Type: image/png`), not JSON. The shared secret travels as
   a custom header instead (`X-Ocr-Secret`), since there's no JSON body to
   read it from.
   > Why not JSON + base64? The AI Builder "Recognize text" action's Image
   > input expects an actual binary file object. A hand-built
   > `base64ToBinary()`/`concat()` expression against a JSON string field
   > does not produce the same shape, and consistently fails with
   > `InvalidImage` even with a valid, correctly-encoded image — confirmed
   > by comparing against a working flow where Image was bound directly to
   > `Body` (dynamic content), not a typed expression.
2. **Check the secret**: add a Condition comparing
   `triggerOutputs()?['headers']?['x-ocr-secret']` (lowercase — headers
   arrive lowercased regardless of how the client sends them) against the
   value you'll put in `power_automate_secret`. Enter this via the
   Condition field's **Expression** tab, not by typing directly into the
   token box — the designer's dynamic-content box will otherwise silently
   turn it into literal text or an unrelated auto-matched token instead of
   a real expression. On the "if false" branch, add a Response action with
   status `401` and terminate that branch.
3. **Run OCR**: add the AI Builder action **"Recognize text in image or
   document"** (or "Extract information from images or PDF documents",
   whichever is available in your environment). Set its **Image** input to
   `Body` (pick it from dynamic content — it's the trigger's raw binary
   output). This just needs plain OCR text out — no per-field training
   required for v1. You can later swap in a custom-trained AI Builder
   document-processing model on your exact quote layout for higher accuracy
   without changing anything on the Python side; the only contract the app
   relies on is the response below.
5. **Response**: status `200`, body set to the Recognize-text action's whole
   **Body** output (not just its "Text" field) — pick it from dynamic
   content. This includes a `lines` array with each line's text and its
   `boundingBox` (left/top/width/height), which the app uses to reconstruct
   table rows and label/value pairs correctly (see
   `extractor.reconstruct_ocr_text()`). AI Builder's plain concatenated text
   often splits a single visual row — a form label and its value, or a
   table's column headers — into separate lines even when they belong
   together, which broke the regex-based parser; the coordinates let the
   app regroup them by actual position instead of guessing from word order.
6. Save and publish the flow, then test it with a real image before copying
   its HTTP trigger URL into `power_automate_url` — e.g. from PowerShell:
   ```powershell
   Invoke-RestMethod -Uri "<trigger URL>" -Method Post `
     -InFile "sample-scan.png" -ContentType "image/png" `
     -Headers @{ "X-Ocr-Secret" = "<your secret>" }
   ```

## How to use it

1. Upload documents into the three categories in section 1 — Cactoz Quotes,
   Customer docs, and Supplier POs. Any of the three can be uploaded in
   multiples.
2. Section 2 shows the auto-extracted line items per document, grouped into
   three tabs. Cactoz's own PO/quote template is parsed reliably; customer
   docs vary a lot in format (and scanned ones rely on OCR), so double-check
   these tables — edit any cell directly, or add/remove rows, before
   comparing.
3. Section 3 shows both comparisons at once (each only appears once you've
   uploaded documents on both its sides): Cactoz Quote vs. Customer doc, and
   Customer doc vs. Supplier PO. Each has its own pair of dropdowns — the
   app suggests the most likely pairing based on item-description
   similarity, but you can pick any documents.
4. Review the flagged issues (qty mismatches, unmatched items, price
   mismatches, thin/negative margin) directly in the comparison table.

## Sharing with colleagues

Just share the app's URL (and the password, if one is set) — that's it.

## Local development

If you're changing the code, you can still run it on your own machine for
testing: `pip install -r requirements.txt` then `streamlit run app.py`. To
test the password gate or OCR locally, copy `secrets.toml.example` to
`.streamlit/secrets.toml` with real values (already gitignored, so it's
never committed).

## Limitations

Customer document extraction is best-effort since every customer uses a
different template — always sanity-check the review table before relying
on a comparison. OCR'd (scanned) documents are noisier still: printed text
(item tables, headers) usually comes through reasonably well, but expect
more errors than native PDF text, and don't expect handwritten signatures
to OCR into anything meaningful — that's fine, they're not needed for the
comparison itself.
