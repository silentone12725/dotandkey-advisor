# Browser extension — live dotandkey.com overlay

Hooks the widget directly onto the real, live dotandkey.com in your own
browser. No demo page, no deployment, no cooperation from Dot & Key
needed — your local backend overlays the chat UI on top of their
actual site.

## What's here

```
extension/
  manifest.json   -- MV3 config: runs inject.js on dotandkey.com pages
  inject.js        -- content script, drops a real <script> tag into
                      the live page pointing at widget.js
  widget.js         -- exact copy of frontend/widget.js (already
                      covered by 49 jsdom tests) — keep these in sync
                      if you edit one
```

## Install (Chrome / Edge / Brave — any Chromium browser)

1. `chrome://extensions`
2. Toggle **Developer mode** (top-right)
3. **Load unpacked** → select the `extension/` folder
4. Make sure your backend is running: `./scripts/start_dev.fish`
5. Visit `https://www.dotandkey.com` — the nudge bubble should appear
   bottom-right, on the real site, with real products

## ⚠️ One thing to watch for: mixed content (HTTPS page → HTTP localhost)

dotandkey.com is HTTPS. Your local backend is plain HTTP
(`http://localhost:8000`). Whether the browser allows this varies by
Chrome version and is actively changing:

- **Modern Chrome** increasingly treats localhost/private-network
  requests from HTTPS pages as a *permission-gated* exception — you
  may see a one-time "dotandkey.com wants to access devices on your
  local network" prompt. Click **Allow** and it should work normally
  from then on.
- **Some configurations still hard-block it** as mixed content, with
  a console error like `Mixed Content: ... requested an insecure
  resource 'http://localhost:8000/...'. This request has been
  blocked.`

**Check first:** open DevTools (F12) → Console tab after loading the
page. If you see a Mixed Content error and no permission prompt
appeared, use the fallback below.

### Fallback — serve the backend over HTTPS too

Removes the mixed-content question entirely (HTTPS → HTTPS, same
scheme). One-time self-signed cert:

```fish
cd dotandkey-advisor
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=localhost"

uvicorn backend.app:app --reload --port 8000 \
  --ssl-keyfile key.pem --ssl-certfile cert.pem
```

Then:
1. Visit `https://localhost:8000/health` directly once and click
   through the "not secure" warning (this registers a one-time trust
   exception for the self-signed cert — `fetch()` calls can't trigger
   this dialog themselves, so this manual visit is required).
2. Edit `extension/inject.js`, change `data-api-base` to
   `https://localhost:8000`.
3. Reload the extension (`chrome://extensions` → refresh icon) and
   the dotandkey.com tab.

## Keeping widget.js in sync

This folder has its **own copy** of `widget.js` (Chrome extensions
can't reference files outside their own package via
`web_accessible_resources`). If you edit `frontend/widget.js`, copy it
here too:

```fish
cp frontend/widget.js extension/widget.js
```

Re-run `node frontend/test_widget.js` first — that test suite is what
actually validates the widget logic; this copy step is just packaging.

## Why this is safe / why it works at all

- The extension only activates on `dotandkey.com` — it does nothing
  on any other site.
- `inject.js` doesn't modify the real page's code, scripts, or
  network calls. It only appends one `<script src="...">` tag, the
  same as if Dot & Key had added it to their own theme.
- Widget calls to `/chat`, `/session/init`, `/context/product` are
  sent with `Origin: https://www.dotandkey.com` (the real page's
  origin, since the injected script runs in the page's own context) —
  this is why `CORS_ORIGINS` in your `.env` already needs
  `https://www.dotandkey.com` listed (it should already be there from
  earlier setup).
- Nothing is sent to Dot & Key's actual servers by the widget itself —
  all `/chat` etc. calls go to your `localhost:8000` backend. Only
  reading the page (product `.json` lookups) touches the real site,
  and only via its own public, unauthenticated endpoints.