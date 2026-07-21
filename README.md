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
2. Point it at the `karenctz/po-quote-matcher` GitHub repo, branch `main`,
   main file path `app.py`. Deploy.
3. Once deployed, open the app's Settings → Secrets, and paste in the
   contents of `secrets.toml.example` (see below) with real values filled in.
4. Every push to `main` on GitHub auto-redeploys the app — nothing else to do.

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
   Request body JSON schema:
   ```json
   {
     "type": "object",
     "properties": {
       "secret": { "type": "string" },
       "image_base64": { "type": "string" }
     }
   }
   ```
2. **Check the secret**: add a Condition comparing
   `triggerBody()?['secret']` against the value you'll put in
   `power_automate_secret`. On the "if false" branch, add a Response action
   with status `401` and terminate that branch.
3. **Decode the image**: use `base64ToBinary(triggerBody()?['image_base64'])`
   as the file content input.
4. **Run OCR**: add the AI Builder action **"Extract information from images
   or PDF documents"** (or "Recognize text in an image", whichever is
   available in your environment) on the decoded image. This just needs
   plain OCR text out — no per-field training required for v1. You can
   later swap in a custom-trained AI Builder document-processing model on
   your exact quote layout for higher accuracy without changing anything on
   the Python side; the only contract the app relies on is the final JSON
   shape below.
5. **Response**: status `200`, body:
   ```json
   { "text": "<the recognized text>" }
   ```
6. Save and publish the flow, then test it directly in the Power Automate
   portal (paste a base64-encoded sample image) before copying its HTTP
   trigger URL into `power_automate_url`.

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
