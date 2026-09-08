# URL Shortener — React client

A small React + TypeScript client for the Governed URL Shortener API.

**This is not part of the assessed scope.** It was added after the graded
deliverable was complete. The orchestration engine — which is the actual
deliverable — has no UI and is not driven by one; it runs from
`python -m scenarios.cli run all` and is reviewed through its evidence bundles.
See the repository [`README.md`](../README.md), section 14.

## Running it

Node.js 20+. Start the API first, then this client in a second terminal.

```powershell
# terminal 1 — from the repository root
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# terminal 2
npm install
npm run dev
```

Open http://localhost:5173.

| Command | Does |
|---|---|
| `npm run dev` | Dev server with hot reload on :5173 |
| `npm run build` | Type-check and build to `dist/` |
| `npm run preview` | Serve the production build locally |

Point it at a different API with `VITE_API_BASE`:

```powershell
$env:VITE_API_BASE = "http://127.0.0.1:9000"; npm run dev
```

## What it does

- Shorten a destination, with API validation errors surfaced verbatim — try
  `javascript:alert(1)` or `ftp://example.com` and read the message.
- Copy the short link; follow it to record a click.
- Keep a per-browser list of links created in this session.
- View analytics per code: click count, recent timestamps, UTC-day buckets.
- Live health/readiness badge, polled every 10 seconds.

## Two constraints worth knowing

**There is no list endpoint.** The API exposes create, redirect, stats, health
and readiness — nothing enumerates links, and adding an endpoint just to feed a
UI would be the tail wagging the dog. So `useLinks.ts` keeps the codes this
browser created in `localStorage`. Clearing that list deletes nothing: the links
still resolve, because they live in the API's database.

**CORS is scoped, not wildcard.** This dev server on `:5173` is a different
origin from the API on `:8000`, so `app/main.py` registers `CORSMiddleware` with
an explicit allowlist — `http://localhost:5173` and `http://127.0.0.1:5173`, GET
and POST only, credentials off. That middleware registration is the only change
this client required in assessed code. Override with
`URL_SHORTENER_CORS_ORIGINS`, or set it to an empty string to turn cross-origin
access off entirely.

## Files

```
src/api.ts        typed API client; ApiError carries the HTTP status
src/useLinks.ts   localStorage-backed list of links created here
src/App.tsx       the single page: form, result, list, stats, health
src/App.css       styling, light and dark
```
