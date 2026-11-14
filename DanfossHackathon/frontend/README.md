# Competitor Intelligence Assistant: front end

A static HTML/CSS/JS front end with no build step and no dependencies. It runs from mock data
out of the box and switches to the real backend by changing one line.

## Run it

- **Quick look:** double-click `index.html`. It runs on mock data.
- **Served:** `python -m http.server 5500` in this folder, then open http://localhost:5500.
- **From the backend:** copy the folder into the backend's static directory
  (e.g. FastAPI `app.mount("/", StaticFiles(directory="ci-assistant-ui", html=True))`).

## Connect the backend

In `js/config.js`:

```js
API_BASE: "http://localhost:8000/api",
USE_MOCK: false,
```

If the backend runs on a different port than the page, enable CORS on it
(FastAPI: `CORSMiddleware` with `allow_origins=["*"]` is fine for the hackathon).

## Files

| File | Purpose | Who edits it |
|---|---|---|
| `index.html` | Page shell | rarely |
| `css/styles.css` | All styling. Colours are CSS variables at the top (`--brand: #ee071b`) | UI |
| `js/config.js` | Backend URL + mock switch | backend |
| `js/api.js` | The only place that calls the backend (3 functions) | rarely |
| `js/app.js` | Chat flow and rendering | UI |
| `js/voice.js` | 🎤 voice input on fields marked `data-voice` (phone recognizer in the app, Web Speech API in Chrome/Edge) | UI |
| `js/speech.js` | 🔊 read answers/results aloud + Hands-free mode (speak → auto-send → answer read aloud) | UI |
| `js/mobile.js` | Android app only: backend address + voice language settings (see `mobile/README.md`) | rarely |
| `js/mock-data.js` | Mock catalog + mock responses (stand-in for the backend) | anyone |
| `examples/*.json` | Exact JSON shapes the backend must return/accept | backend |

## Backend contract (5 endpoints)

### `GET /api/catalog`
Returns everything the user can pick. **This is what makes the app scalable:** new product
families, valve types, products and competitors appear in the UI as soon as the backend
returns them. No front-end change is needed. See `examples/catalog.json`.

- `tree`: nested nodes of any depth. A node with `type: "product"` is a leaf the user compares.
- `status`: `active` | `data-pending` (selectable, flagged "Limited data") | `coming-soon` (greyed out)
- `competitors[]`: `{ id, name, hasData }` (companies)
- `competitorProducts[]`: `{ id, competitorId, name, categoryId, equivalentOf[], datasets[], status }`
  (`status: "pending"` until extracted). Shown in the Competitors step for the matching category.
- `factors[]`: `{ id, label, group }`, the priority chips
- `applications[]`: options for the buyer step

Generate it from the product database (e.g. one row per product with `family`, `type`,
`product_id`, `status`) instead of hand-editing it once data batches start coming in.

### `POST /api/catalog/nodes`: add a family, type or product from the UI
Request: `{ "parentId": "proportional" | null, "label": "PVG 40", "desc": "...", "type": "group" | "product" }`
(`parentId: null` = new product family). Response: `{ "node": {...} | null, "issues": [Issue] }`.
The backend validates (duplicate name at the same level, missing name, adding under a product)
and returns `node: null` plus issues if it rejects. After success the UI re-fetches `/catalog`.

### `POST /api/datasets`: upload files from the UI (multipart/form-data)
Fields for **Danfoss** data: `source=danfoss`, `nodeId`, `files`.
Fields for **competitor** data: `source=competitor`, `competitorId` *or* `competitorName` (new
competitor), `productName` (model/series), `categoryId` (the Danfoss group it competes in, e.g.
`proportional`), optional `equivalentOf` (a Danfoss product id a person has confirmed), `files`.
The backend creates the competitor product if it doesn't exist, otherwise adds the files to it.
Response: `{ "uploaded": ["file.pdf"], "issues": [Issue] }`.

Confirmed equivalent pairs (`equivalentOf`) are your **test set**: run the matcher on those
competitor products and report how often it picks the confirmed Danfoss product.
Suggested checks: file type, empty file, size limit, duplicate file, scanned/no-text PDF,
unknown competitor. Store each file with its node, source and upload date. This is the same
entry point a future scraper should call, so scraped and uploaded data share one pipeline.

### `POST /api/compare`
Request (`examples/compare_request.json`; also carries `competitorProductIds[]`):
```json
{ "path": ["valves","proportional","pvg32"], "productId": "pvg32",
  "competitors": ["rexroth"],               // [] = auto-find best matches
  "factors": { "price": "must", "flow": "nice" },
  "customFactors": ["Noise level"],
  "buyer": { "company": "AGCO", "application": "Tractor", "currentSupplier": "...", "notes": "..." },
  "chatNotes": ["free-text questions typed during the session"] }
```
Response (`examples/compare_response.json`), key parts:
- `anchor` / `matches[]`: columns of the table; each has a `key` used in `attributes[].values`
- `attributes[]`: `{ factor, label, unit, values: { <key>: { value, status, sources[], note } } }`
  - `status`: `ok` | `conflict` | `missing` | `outdated` | `unverified` | `inferred` | `converted` | `pending` | `sample`
- `matches[].knownEquivalent: true` shows "confirmed equivalent" instead of a match score
  - `factor` links the row to a priority chip; must-haves are sorted to the top
- `insights`: `{ differences[], benefits[], tradeoffs[] }`, each item `{ text, sources[] }`
- `flags[]`: `{ level, text }` (Issue, see below)
- `confidence`: `{ level: "high" | "medium" | "low", reason }`
- `sources[]`: `{ id, title, doc, page, url, date }`; `[n]` markers in the table link here

### `POST /api/match`: reverse flow (competitor product → closest Danfoss products)
Request: `{ "competitorProductId": "kawasaki-kmp", "buyer": {...}, "factors": {...}, "chatNotes": [] }`
Response: same shape as `/api/compare`, with the competitor product as `anchor` and Danfoss products in
`matches[]`. Opened from the header button **Find Danfoss match**.

Both `/api/compare` and `/api/match` are answered by `comparison_engine/` from the facts CSVs in
`data/repository/` (see the root README). A product with an upload but no facts CSV yet shows
`status: "pending"` cells ("Awaiting extraction").

### `POST /api/ask`
Request: `{ "question": "...", "context": <same object as /compare request> }`
Response: `{ "answer": "..." }`

## Issue / flag vocabulary
One `Issue = { level, text }` shape is used everywhere (uploads, new items, comparison results).

| level | Meaning | Example |
|---|---|---|
| `conflict` | Sources disagree, or name already exists | Max. flow 100 / 125 / 130 L/min |
| `missing` | Value not found in any source | No price for either product |
| `outdated` | Only supported by an old source | CAN bus claim from a 2017 deck |
| `unverified` | Not yet confirmed by a person | Auto-selected competitor match |
| `inferred` | Derived, not stated in a source | Section count estimated from drawing |
| `converted` | Unit converted by the system | psi → bar |
| `sample` | Placeholder data | Competitor values before the dataset is loaded |
| `error` | Rejected input | Unsupported file type |
| `warning` | Accepted, but check it | Small PDF, may be scanned |
| `pending` | Queued for processing | File awaiting extraction |
| `ok` | Done | Product added |

Unknown levels still render (shown as "Check"), so the backend can add new types safely.

## Mock data note
Danfoss values in `mock-data.js` come **only** from the official PVG 32 Technical Information
(BC152886483664, July 2024), with page numbers. Sales presentations and training decks are not
used as sources. Competitor values are sample placeholders (labelled "Sample") until a competitor
datasheet is uploaded; after upload they show "Awaiting extraction" until the backend fills them.
