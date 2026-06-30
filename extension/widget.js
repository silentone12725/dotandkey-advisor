/**
 * widget.js — Dot & Key Skin Advisor
 *
 * Drop-in embed for dotandkey.com:
 *   <script src="https://cdn.dotandkey.com/advisor/widget.js"
 *           data-api-base="https://advisor.dotandkey.com"></script>
 *
 * Self-contained: no build step, no external CSS/font dependencies,
 * renders inside a Shadow DOM so it can never collide with the host
 * page's styles (and the host page's styles can never leak in).
 *
 * Talks to the FastAPI backend via:
 *   POST /session/init     — on load, returns greeting + season
 *   POST /chat             — SSE stream, main conversation
 *   POST /context/product  — product page mode
 */

(function () {
    "use strict";

    // Version marker — increment when syncing to extension/widget.js so a
    // hard-refresh of the extension confirms the new bundle is active.
    console.log("[dk-advisor] WIDGET_VERSION 2 — image-based swatches, variant hint, 2-col grid");

    // ===========================================================================
    // Config
    // ===========================================================================

    var SCRIPT_TAG = document.currentScript ||
        (function () {
            var scripts = document.getElementsByTagName("script");
            return scripts[scripts.length - 1];
        })();

    var API_BASE = (SCRIPT_TAG && SCRIPT_TAG.getAttribute("data-api-base")) ||
        "http://localhost:8000";

    var PROFILE_KEY = "dk_advisor_id";

    // ===========================================================================
    // Profile ID — localStorage UUID, no server-side session needed to start
    // ===========================================================================

    function getOrCreateProfileId() {
        try {
            var id = localStorage.getItem(PROFILE_KEY);
            if (!id) {
                id = (crypto.randomUUID && crypto.randomUUID()) ||
                    "dk-" + Date.now() + "-" + Math.random().toString(36).slice(2);
                localStorage.setItem(PROFILE_KEY, id);
            }
            return id;
        } catch (e) {
            // localStorage unavailable (private browsing, etc.) — fall back to
            // an in-memory id for this page load only.
            return "dk-session-" + Date.now() + "-" + Math.random().toString(36).slice(2);
        }
    }

    var PROFILE_ID = getOrCreateProfileId();

    // ===========================================================================
    // Page detection
    // ===========================================================================

    function detectPageContext() {
        var path = window.location.pathname;
        if (path.indexOf("/products/") === 0 || path.indexOf("/products/") > -1) {
            var handle = path.split("/products/")[1];
            if (handle) handle = handle.split("?")[0].split("/")[0];
            return { type: "product", handle: handle };
        }
        return { type: "homepage" };
    }

    // ===========================================================================
    // Nudge bubble copy — varies by page so it feels relevant, not generic
    // ===========================================================================

    function nudgeCopyFor(pageCtx) {
        var path = window.location.pathname.toLowerCase();
        if (pageCtx.type === "product") {
            return "Got questions about this?";
        }
        if (path.indexOf("sunscreen") > -1) {
            return "Get the best sunscreen for you";
        }
        if (path.indexOf("moistur") > -1) {
            return "Find your perfect moisturizer";
        }
        return "Find your perfect routine";
    }

    // expose internals for the rest of the file (built up across sections)
    window.__dkAdvisor = window.__dkAdvisor || {};
    window.__dkAdvisor._internal = {
        API_BASE: API_BASE,
        PROFILE_ID: PROFILE_ID,
        detectPageContext: detectPageContext,
        nudgeCopyFor: nudgeCopyFor,
    };

    // ===========================================================================
    // Styles — design tokens from the brief:
    //   warm cream surface, coral accent (not generic chat-blue), sage for
    //   "best match" badges, flat pill chips (no heavy shadow), a slow
    //   breathing glow on the collapsed nudge bubble as the signature touch.
    // ===========================================================================

    var CSS = "" +
        "* { box-sizing: border-box; margin: 0; padding: 0; }" +
        ".dk-root {" +
        "  position: relative;" +
        "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;" +
        /* ── Dot & Key design token system ──────────────────────────────────
           Derived from the live site's actual element colors:

           --dk-pink       #E91E8C   Add to Cart button, active category pill
                                     → used ONLY on interactive/action elements
           --dk-pink-soft  #FCE4F4   10% tint → hover fills, icon backgrounds
           --dk-ink        #1A1A1A   Logo, body text, BESTSELLER badge, borders on top picks
           --dk-ink-muted  #888888   Subtitles, skin-type tags, placeholder
           --dk-white      #FFFFFF   Card surfaces, panel body, message bubbles
           --dk-surface    #F7F7F7   Chat body background (off-white, separates from cards)
           --dk-border     #E8E8E8   Card/panel borders — neutral gray, not pink
           --dk-gold       #F5A623   Star ratings
           --dk-strike     #999999   Strikethrough MRP prices
           --dk-sale       #FF6781   Discount/sale colour (from DK's own stylesheet)
           --dk-badge      #1A1A1A   BESTSELLER / TOP PICK badge pill
        ──────────────────────────────────────────────────────────────────── */
        "  --dk-pink: #E91E8C; --dk-pink-soft: #FCE4F4;" +
        "  --dk-ink: #1A1A1A; --dk-ink-muted: #888888;" +
        "  --dk-white: #FFFFFF; --dk-surface: #F7F7F7; --dk-border: #E8E8E8;" +
        "  --dk-gold: #F5A623; --dk-strike: #999999; --dk-sale: #FF6781;" +
        "  --dk-badge: #1A1A1A;" +
        "  --dk-cta-bg: #E91E8C; --dk-cta-hover: #C4186F;" +
        "}" +
        /* ── nudge bubble ─────────────────────────────────────────────────
           White pill, pink border (matches the site's active category pill
           style), subtle pink-tinted shadow so it feels native to the page. */
        ".dk-bubble {" +
        "  display: flex; align-items: center; gap: 10px; cursor: pointer;" +
        "  background: var(--dk-white); border: 1.5px solid var(--dk-pink);" +
        "  border-radius: 999px; padding: 10px 18px 10px 10px;" +
        "  box-shadow: 0 4px 20px rgba(233,30,140,0.18);" +
        "  transition: transform 0.15s ease, box-shadow 0.15s ease;" +
        "  animation: dk-fade-in 0.3s ease;" +
        "}" +
        ".dk-bubble:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(233,30,140,0.28); }" +
        ".dk-bubble-icon {" +
        "  width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0;" +
        "  background: var(--dk-pink-soft); display: flex; align-items: center;" +
        "  justify-content: center; position: relative;" +
        "}" +
        ".dk-bubble-icon::before {" +
        "  content: ''; position: absolute; inset: -5px; border-radius: 50%;" +
        "  border: 1.5px solid var(--dk-pink); opacity: 0.4;" +
        "  animation: dk-breathe 2.6s ease-in-out infinite;" +
        "}" +
        ".dk-bubble-text { font-size: 13.5px; font-weight: 600; color: var(--dk-ink); white-space: nowrap; }" +
        "@keyframes dk-breathe { 0%,100% { transform:scale(1); opacity:0.4; } 50% { transform:scale(1.18); opacity:0; } }" +
        "@keyframes dk-fade-in { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }" +
        "@media (prefers-reduced-motion:reduce) { .dk-bubble-icon::before,.dk-bubble { animation:none; } }" +
        /* ── panel shell ──────────────────────────────────────────────────
           White surface, neutral border — same card aesthetic as the site.
           Shadow is a neutral dark (not pink) to stay grounded. */
        ".dk-panel {" +
        "  width: 430px; max-width: calc(100vw - 24px); height: 88vh;" +
        "  max-height: 720px;" +
        "  background: var(--dk-white); border: 1px solid var(--dk-border);" +
        "  border-radius: 16px; display: flex; flex-direction: column;" +
        "  overflow: hidden; box-shadow: 0 12px 48px rgba(0,0,0,0.16);" +
        "  animation: dk-panel-in 0.22s cubic-bezier(.2,.9,.3,1);" +
        "}" +
        "@keyframes dk-panel-in { from { opacity:0; transform:translateY(12px) scale(0.98); } to { opacity:1; transform:translateY(0) scale(1); } }" +
        /* ── header ───────────────────────────────────────────────────────
           White background with 3px pink top bar — mirrors the site's own
           DOT & KEY logo in black, with a pink accent line above it. */
        ".dk-header {" +
        "  display: flex; flex-direction: column; padding: 13px 16px 10px;" +
        "  border-bottom: 1px solid var(--dk-border); background: var(--dk-white);" +
        "  flex-shrink: 0;" +
        "}" +
        ".dk-header-top { display: flex; align-items: center; gap: 8px; }" +
        ".dk-wordmark { font-size: 14px; font-weight: 800; color: var(--dk-ink); flex: 1; letter-spacing: 0.08em; text-transform: uppercase; }" +
        ".dk-header-sub { font-size: 10.5px; color: var(--dk-ink-muted); margin-top: 1px; letter-spacing: 0.02em; }" +
        ".dk-header { position: relative; }" +
        ".dk-close-btn {" +
        "  position: absolute; top: 10px; right: 10px;" +
        "  width: 26px; height: 26px; border: none; background: transparent;" +
        "  border-radius: 50%; cursor: pointer; display: flex; align-items: center;" +
        "  justify-content: center; color: var(--dk-ink-muted); transition: background 0.15s, color 0.15s;" +
        "}" +
        ".dk-close-btn:hover { background: var(--dk-pink-soft); color: var(--dk-pink); }" +
        /* ── chat body ────────────────────────────────────────────────────
           Slightly off-white so white message bubbles and cards lift off. */
        ".dk-body { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:14px; background:var(--dk-surface); }" +
        /* ── messages ─────────────────────────────────────────────────────
           Assistant: white card bubble, same border as site's product cards.
           User: pink fill (matches Add to Cart button colour — primary action). */
        ".dk-msg { font-size: 13.5px; line-height: 1.55; max-width: 88%; }" +
        ".dk-msg-assistant { align-self:flex-start; background:var(--dk-white); border:1px solid var(--dk-border); border-radius:14px 14px 14px 4px; padding:10px 13px; color:var(--dk-ink); }" +
        ".dk-msg-user { align-self:flex-end; background:var(--dk-pink); border-radius:14px 14px 4px 14px; padding:10px 13px; color:white; }" +
        ".dk-msg-cursor { display:inline-block; width:6px; height:13px; background:var(--dk-pink); margin-left:2px; animation:dk-blink 0.9s step-start infinite; vertical-align:middle; }" +
        "@keyframes dk-blink { 50% { opacity:0; } }" +
        /* ── chips ────────────────────────────────────────────────────────
           Default = outlined pill, same as inactive category pills on site.
           Hover = light pink fill + pink border.
           Selected = full pink fill + white text (matches active "All" pill). */
        ".dk-chips-container { display:flex; flex-direction:column; gap:6px; align-self:flex-start; max-width:100%; }" +
        ".dk-chip-multi-hint { font-size:11px; font-weight:600; color:var(--dk-pink); letter-spacing:0.01em; display:flex; align-items:center; gap:4px; }" +
        ".dk-chip-row { display:flex; flex-wrap:wrap; gap:7px; }" +
        ".dk-chip { font-size:12.5px; font-weight:500; padding:7px 13px; border-radius:999px; border:1px solid var(--dk-border); background:var(--dk-white); color:var(--dk-ink); cursor:pointer; transition:background 0.13s,border-color 0.13s,color 0.13s; }" +
        ".dk-chip:hover { background:var(--dk-pink-soft); border-color:var(--dk-pink); color:var(--dk-pink); }" +
        ".dk-chip.dk-chip-selected { background:var(--dk-pink); border-color:var(--dk-pink); color:white; }" +
        ".dk-chip-confirm { border:none; background:var(--dk-pink); color:white; font-size:12px; font-weight:600; padding:7px 14px; border-radius:999px; cursor:pointer; }" +
        /* dk-chip-link: a real <a href target=_blank> styled like a chip —
           used for "Track my order" (opens ClickPost directly) rather than
           a chip that sends a chat message. text-decoration/display reset
           since <a> isn't a button by default. */
        ".dk-chip-link { text-decoration:none; display:inline-flex; align-items:center; gap:4px; }" +
        /* ── combo bundle cards ───────────────────────────────────────────
           Shown ABOVE individual product picks. Full-width cards with a
           distinct coral/pink gradient header band so they stand out as
           deals, not just more products. Component product images show
           side-by-side inside the card. */
        ".dk-combos { display:flex; flex-direction:column; gap:8px; align-self:stretch; margin-bottom:4px; }" +
        ".dk-combos-label { font-size:10.5px; color:var(--dk-ink-muted); font-weight:700; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:0; }" +
        ".dk-combo-card { background:var(--dk-white); border:1.5px solid var(--dk-pink); border-radius:10px; padding:10px; position:relative; overflow:visible; }" +
        ".dk-combo-badge { position:absolute; top:-8px; left:10px; background:var(--dk-pink); color:white; font-size:9px; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; padding:3px 8px; border-radius:3px; }" +
        ".dk-combo-title { font-size:12px; font-weight:700; color:var(--dk-ink); margin:6px 0 6px; line-height:1.3; }" +
        ".dk-combo-components { display:flex; gap:6px; margin-bottom:7px; }" +
        ".dk-combo-comp-img { flex:1; height:56px; background:var(--dk-surface); border-radius:6px; overflow:hidden; display:flex; align-items:center; justify-content:center; }" +
        ".dk-combo-comp-img img { width:100%; height:100%; object-fit:cover; border-radius:6px; }" +
        ".dk-combo-comp-plus { display:flex; align-items:center; color:var(--dk-ink-muted); font-size:14px; font-weight:300; flex-shrink:0; }" +
        ".dk-combo-skin-tags { font-size:10px; color:var(--dk-pink); font-weight:600; margin-bottom:7px; }" +
        ".dk-combo-price-row { display:flex; align-items:baseline; gap:6px; margin-bottom:8px; }" +
        ".dk-combo-price { font-size:13px; font-weight:800; color:var(--dk-ink); }" +
        ".dk-combo-savings { font-size:10.5px; font-weight:700; color:var(--dk-sale); }" +
        ".dk-combo-cta { width:100%; border:none; background:var(--dk-pink); color:white; font-size:11.5px; font-weight:700; padding:8px; border-radius:6px; cursor:pointer; transition:opacity 0.13s; letter-spacing:0.02em; }" +
        ".dk-combo-cta:hover { opacity:0.88; }" +
        /* ── product cards ────────────────────────────────────────────────
           White card, neutral border — exact match of site's product card.
           Badge = black pill (BESTSELLER, TOP PICK, BEST MATCH).
           CTA = pink-outlined → pink-filled on hover (matches Add to Cart). */
        ".dk-products { display:flex; flex-direction:column; gap:8px; align-self:stretch; }" +
        /* both top-picks and more-options use the same 2-column grid */
        ".dk-product-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }" +
        ".dk-product-grid-sm { display:grid; grid-template-columns:1fr 1fr; gap:8px; }" +
        ".dk-card { background:var(--dk-white); border:1px solid var(--dk-border); border-radius:10px; padding:8px; position:relative; display:flex; flex-direction:column; }" +
        ".dk-card-top { border-color:var(--dk-ink); }" +
        ".dk-card-badge { position:absolute; top:-8px; left:8px; background:var(--dk-badge); color:white; font-size:9px; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; padding:3px 8px; border-radius:3px; }" +
        /* Square container — auto-sizes to card width so the full product image
           is always visible without cropping (object-fit:contain on a square). */
        ".dk-card-img { width:100%; aspect-ratio:1; background:var(--dk-white); border-radius:8px; margin-bottom:6px; overflow:hidden; display:flex; align-items:center; justify-content:center; }" +
        ".dk-card-img img { width:100%; height:100%; object-fit:contain; }" +
        ".dk-card-link { display:block; text-decoration:none; color:inherit; cursor:pointer; flex:1; }" +
        ".dk-card-title { font-size:11px; font-weight:600; color:var(--dk-ink); line-height:1.3; margin-bottom:3px; min-height:24px; }" +
        ".dk-card-price-row { display:flex; align-items:baseline; gap:5px; margin-bottom:3px; flex-wrap:wrap; }" +
        ".dk-card-price { font-size:12px; font-weight:700; color:var(--dk-ink); }" +
        ".dk-card-price-strike { font-size:10px; color:var(--dk-strike); text-decoration:line-through; }" +
        ".dk-card-keywords { font-size:9.5px; color:var(--dk-pink); font-weight:600; margin-bottom:6px; line-height:1.3; letter-spacing:0.01em; }" +
        ".dk-allergen-note { font-size:11px; color:var(--dk-ink-muted); background:var(--dk-surface); border:1px solid var(--dk-border); border-radius:8px; padding:6px 10px; align-self:stretch; line-height:1.5; }" +
        ".dk-profile-chips { display:flex; flex-wrap:wrap; gap:6px; align-self:flex-start; }" +
        ".dk-profile-chip { font-size:11px; font-weight:600; color:var(--dk-ink-muted); background:var(--dk-surface); border:1px solid var(--dk-border); border-radius:999px; padding:3px 10px; white-space:nowrap; }" +
        ".dk-budget-expand { font-size:12.5px; color:var(--dk-ink-muted); align-self:flex-start; font-style:italic; }" +
        /* CTA matches site's solid "ADD TO CART" button — filled pink, white text */
        ".dk-card-cta { width:100%; border:none; background:var(--dk-cta-bg); color:white; font-size:10.5px; font-weight:700; padding:6px; border-radius:6px; cursor:pointer; transition:background 0.15s; letter-spacing:0.04em; text-transform:uppercase; margin-top:auto; }" +
        ".dk-card-cta:hover { background:var(--dk-cta-hover); }" +
        ".dk-card-cta.dk-adding { opacity:0.6; cursor:default; }" +
        ".dk-card-cta.dk-added { background:var(--dk-surface); color:var(--dk-ink-muted); border:1px solid var(--dk-border); cursor:default; }" +
        ".dk-card-cta.dk-error { background:transparent; border:1.5px solid var(--dk-sale); color:var(--dk-sale); }" +
        ".dk-remaining-label { font-size:10.5px; color:var(--dk-ink-muted); font-weight:700; margin:2px 2px 0; text-transform:uppercase; letter-spacing:0.04em; }" +
        ".dk-card-promo { display:inline-flex; align-items:center; background:var(--dk-sale); color:white; font-size:9px; font-weight:700; padding:2px 7px; border-radius:3px; margin-left:0; letter-spacing:0.03em; white-space:nowrap; }" +
        /* dk-product-grid-sm cards are now identical to dk-product-grid cards —
           no overrides needed since both grids are 2-column and same gap */
        ".dk-variant-row { display:flex; gap:5px; flex-wrap:wrap; margin:5px 0; }" +
        ".dk-variant-pill { font-size:10px; font-weight:600; padding:3px 9px; border-radius:6px; border:1px solid #000; background:#fff; color:#000; cursor:pointer; line-height:1.4; }" +
        ".dk-variant-pill.dk-variant-selected { border:1px solid #ff40b1; background:#ff40b1; color:#fff; font-weight:700; }" +
        ".dk-swatch-row { display:flex; gap:5px; flex-wrap:wrap; margin:5px 0; align-items:center; }" +
        ".dk-swatch { width:22px; height:22px; border-radius:50%; cursor:pointer; border:2px solid transparent; outline:1px solid rgba(0,0,0,0.12); transition:border-color 0.12s,transform 0.12s,box-shadow 0.12s; flex-shrink:0; }" +
        ".dk-swatch:hover { transform:scale(1.08); }" +
        ".dk-swatch.dk-swatch-selected { border-color:rgba(0,0,0,0.45); transform:scale(1.06); outline:none; box-shadow:0 0 0 1.5px rgba(0,0,0,0.28); }" +
        /* ── footer / input ───────────────────────────────────────────────
           White bar, send button = pink (Add to Cart colour — primary action). */
        ".dk-footer { border-top:1px solid var(--dk-border); padding:10px 12px; display:flex; gap:8px; background:var(--dk-white); flex-shrink:0; }" +
        ".dk-input { flex:1; border:1px solid var(--dk-border); border-radius:999px; padding:9px 14px; font-size:13px; outline:none; font-family:inherit; color:var(--dk-ink); background:var(--dk-white); }" +
        ".dk-input::placeholder { color:var(--dk-ink-muted); }" +
        ".dk-input:focus { border-color:var(--dk-pink); }" +
        ".dk-send-btn { width:36px; height:36px; border-radius:50%; border:none; background:var(--dk-pink); color:white; cursor:pointer; display:flex; align-items:center; justify-content:center; flex-shrink:0; transition:opacity 0.13s; }" +
        ".dk-send-btn:disabled { opacity:0.35; cursor:default; }" +
        /* ── typing indicator ─────────────────────────────────────────────
           Dots tinted pink to match the palette accent. */
        ".dk-typing { display:flex; gap:4px; padding:4px 2px; }" +
        ".dk-typing span { width:6px; height:6px; border-radius:50%; background:var(--dk-pink-soft); border:1px solid var(--dk-pink); animation:dk-typing-bounce 1.1s infinite ease-in-out; }" +
        ".dk-typing span:nth-child(2) { animation-delay:0.15s; }" +
        ".dk-typing span:nth-child(3) { animation-delay:0.3s; }" +
        "@keyframes dk-typing-bounce { 0%,60%,100% { transform:translateY(0); opacity:0.5; } 30% { transform:translateY(-4px); opacity:1; } }" +
        ".dk-body::-webkit-scrollbar { width:4px; }" +
        ".dk-body::-webkit-scrollbar-thumb { background:var(--dk-border); border-radius:4px; }" +
        "@media (max-width:420px) { .dk-panel { width:100%; height:72vh; } }";

    // ===========================================================================
    // Shadow DOM scaffold
    // ===========================================================================

    function buildShadowRoot() {
        var host = document.createElement("div");
        host.id = "dk-advisor-host";
        host.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:2147483000;display:block;transition:right 0.32s cubic-bezier(0.25,0.46,0.45,0.94);";
        document.body.appendChild(host);

        var shadow = host.attachShadow({ mode: "open" });

        var styleEl = document.createElement("style");
        styleEl.textContent = CSS;
        shadow.appendChild(styleEl);

        var root = document.createElement("div");
        root.className = "dk-root";
        shadow.appendChild(root);


        return { host: host, shadow: shadow, root: root };
    }

    window.__dkAdvisor._internal.buildShadowRoot = buildShadowRoot;

    // ===========================================================================
    // Icons (inline SVG strings — no icon font/CDN dependency)
    // ===========================================================================

    var ICON_SPARKLE =
        '<svg width="16" height="16" viewBox="0 0 16 16" fill="none">' +
        '<path d="M8 1l1.4 4.6L14 7l-4.6 1.4L8 13l-1.4-4.6L2 7l4.6-1.4L8 1z" fill="#E8714A"/></svg>';

    var ICON_CLOSE =
        '<svg width="14" height="14" viewBox="0 0 14 14" fill="none">' +
        '<path d="M1 1l12 12M13 1L1 13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

    var ICON_SEND =
        '<svg width="15" height="15" viewBox="0 0 16 16" fill="none">' +
        '<path d="M2 8l12-6-4 6 4 6-12-6z" fill="white"/></svg>';

    // ===========================================================================
    // State
    // ===========================================================================

    var state = {
        expanded: false,
        sending: false,
        season: "",
        city: "",
        isReturning: false,
        pageCtx: null,
        productCtx: null,     // set when on a product page
        els: null,             // { host, shadow, root, bubble, panel, body, input, sendBtn }
        sessionPromise: null, // prefetched on boot, consumed on first expand
    };

    // ===========================================================================
    // SSE client — fetch + ReadableStream (EventSource can't send custom
    // headers/POST bodies, which we need for X-Profile-Id + message payload)
    // ===========================================================================

    /**
     * Streams /chat. Calls onToken(text) for each visible text chunk, and
     * onDone({playbook, ...uiData}) once when the stream ends.
     */
    function streamChat(message, productContext, onToken, onDone, onError) {
        var internal = window.__dkAdvisor._internal;

        fetch(internal.API_BASE + "/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Profile-Id": internal.PROFILE_ID,
                "Accept": "text/event-stream",
            },
            body: JSON.stringify({
                message: message,
                product_context: productContext || null,
            }),
        })
            .then(function (res) {
                if (!res.ok || !res.body) {
                    throw new Error("chat request failed: " + res.status);
                }
                var reader = res.body.getReader();
                var decoder = new TextDecoder();
                var buffer = "";

                function pump() {
                    return reader.read().then(function (result) {
                        if (result.done) return;
                        buffer += decoder.decode(result.value, { stream: true });

                        var parts = buffer.split("\n\n");
                        buffer = parts.pop(); // last part may be incomplete, keep in buffer

                        for (var i = 0; i < parts.length; i++) {
                            var chunk = parts[i];
                            if (!chunk || chunk.indexOf("data: ") !== 0) continue;
                            var jsonStr = chunk.slice(6);
                            var payload;
                            try {
                                payload = JSON.parse(jsonStr);
                            } catch (e) {
                                continue; // skip malformed chunk rather than crash the stream
                            }
                            if (payload.done) {
                                onDone(payload);
                            } else if (typeof payload.token === "string") {
                                onToken(payload.token);
                            }
                        }
                        return pump();
                    });
                }
                return pump();
            })
            .catch(function (err) {
                if (onError) onError(err);
            });
    }

    /** Non-streaming POST helper for /session/init and /context/product. */
    function postJSON(path, body) {
        var internal = window.__dkAdvisor._internal;
        return fetch(internal.API_BASE + path, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Profile-Id": internal.PROFILE_ID,
            },
            body: JSON.stringify(body || {}),
        }).then(function (res) {
            if (!res.ok) throw new Error(path + " failed: " + res.status);
            return res.json();
        });
    }

    window.__dkAdvisor._internal.streamChat = streamChat;
    window.__dkAdvisor._internal.postJSON = postJSON;
    window.__dkAdvisor._internal.ICONS = { sparkle: ICON_SPARKLE, close: ICON_CLOSE, send: ICON_SEND };
    window.__dkAdvisor._internal.state = state;

    // ===========================================================================
    // Small DOM helper
    // ===========================================================================

    function el(tag, className, html) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (html !== undefined) node.innerHTML = html;
        return node;
    }

    // ===========================================================================
    // Bubble (collapsed state)
    // ===========================================================================

    function renderBubble(root, onExpand) {
        var bubble = el("div", "dk-bubble");
        bubble.appendChild(el("div", "dk-bubble-icon", ICON_SPARKLE));
        bubble.appendChild(el("div", "dk-bubble-text", nudgeCopyFor(state.pageCtx)));
        bubble.addEventListener("click", onExpand);
        root.appendChild(bubble);
        return bubble;
    }

    // ===========================================================================
    // Panel (expanded state) — header, body, footer
    // ===========================================================================

    function renderPanel(root, onCollapse, onSend) {
        var panel = el("div", "dk-panel");

        // header — black wordmark bar, mirrors the real site's "DOT & KEY"
        // all-caps treatment rather than a generic chatbot label
        var header = el("div", "dk-header");
        var headerTop = el("div", "dk-header-top");
        // Try to grab the real logo from the host page; fall back to SVG wordmark
        (function () {
            var logoEl = null;
            try {
                var selectors = [
                    'a[class*="logo"] img', 'a[class*="brand"] img',
                    '.site-header__logo img', '.header__logo img',
                    'header .logo img', '.navbar-brand img',
                ];
                for (var si = 0; si < selectors.length && !logoEl; si++) {
                    logoEl = document.querySelector(selectors[si]);
                }
            } catch (e) {}
            if (logoEl && logoEl.src) {
                var logoImg = document.createElement("img");
                logoImg.src = logoEl.src;
                logoImg.alt = "DOT & KEY";
                logoImg.style.cssText = "height:20px;width:auto;object-fit:contain;max-width:120px;";
                headerTop.appendChild(logoImg);
            } else {
                var svgMark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
                svgMark.setAttribute("viewBox", "0 0 110 20");
                svgMark.setAttribute("height", "20");
                svgMark.style.flex = "1";
                svgMark.innerHTML =
                    '<text x="0" y="15" font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif" ' +
                    'font-size="13" font-weight="800" letter-spacing="1.5" fill="currentColor" ' +
                    'text-anchor="start">DOT &amp; KEY</text>';
                svgMark.style.color = "var(--dk-ink)";
                svgMark.classList.add("dk-wordmark");
                headerTop.appendChild(svgMark);
            }
        })();
        var closeBtn = el("button", "dk-close-btn", ICON_CLOSE);
        closeBtn.setAttribute("aria-label", "Close");
        closeBtn.addEventListener("click", onCollapse);
        header.appendChild(headerTop);
        header.appendChild(closeBtn);  // absolute position via CSS
        header.appendChild(el("div", "dk-header-sub", "Skin Advisor \u00B7 AI-powered"));
        panel.appendChild(header);

        // body (message list)
        var body = el("div", "dk-body");
        panel.appendChild(body);

        // footer (free-text input, always available alongside chips)
        var footer = el("div", "dk-footer");
        var input = el("input", "dk-input");
        input.type = "text";
        input.placeholder = "Type a message...";
        var sendBtn = el("button", "dk-send-btn", ICON_SEND);
        sendBtn.disabled = true;
        sendBtn.setAttribute("aria-label", "Send");

        function trySend() {
            var text = input.value.trim();
            if (!text || state.sending) return;
            input.value = "";
            sendBtn.disabled = true;
            onSend(text);
        }

        input.addEventListener("input", function () {
            sendBtn.disabled = !input.value.trim() || state.sending;
        });
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter") trySend();
        });
        sendBtn.addEventListener("click", trySend);

        footer.appendChild(input);
        footer.appendChild(sendBtn);
        panel.appendChild(footer);


        root.appendChild(panel);
        return { panel: panel, body: body, input: input, sendBtn: sendBtn };
    }

    // ===========================================================================
    // Messages
    // ===========================================================================

    function appendUserMessage(body, text) {
        var msg = el("div", "dk-msg dk-msg-user", escapeHtml(text));
        body.appendChild(msg);
        scrollToBottom(body);
        return msg;
    }

    function appendAssistantMessage(body) {
        var msg = el("div", "dk-msg dk-msg-assistant");
        var cursor = el("span", "dk-msg-cursor");
        msg.appendChild(cursor);
        body.appendChild(msg);
        scrollToBottom(body);
        return { msg: msg, cursor: cursor, text: "" };
    }

    function appendTypingIndicator(body) {
        var wrap = el("div", "dk-msg dk-msg-assistant dk-typing-wrap");
        var dots = el("div", "dk-typing", "<span></span><span></span><span></span>");
        wrap.appendChild(dots);
        body.appendChild(wrap);
        scrollToBottom(body);
        return wrap;
    }

    function escapeHtml(s) {
        var d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function scrollToBottom(body) {
        requestAnimationFrame(function () {
            body.scrollTop = body.scrollHeight;
        });
    }

    // ===========================================================================
    // Chips
    // ===========================================================================

    /**
     * Renders a chip row from a {field, multi_select, options} payload
     * (as emitted by backend/chip_options.py via the done event).
     * onSubmit(messageText) is called once the user confirms a selection
     * (single-select: immediate on tap; multi-select: tap to toggle + a
     * confirm button; free-text: inline input + its own send button).
     */
    function renderChips(body, chipData, onSubmit) {
        if (!chipData || !chipData.options || !chipData.options.length) return null;

        var container = el("div", "dk-chips-container");
        var selected = {};

        if (chipData.multi_select) {
            container.appendChild(el("div", "dk-chip-multi-hint", "✓ Select all that apply"));
        }

        var row = el("div", "dk-chip-row");

        chipData.options.forEach(function (opt) {
            var chip = el("div", "dk-chip", opt.label);
            chip.addEventListener("click", function () {
                if (chipData.multi_select) {
                    if (selected[opt.value]) {
                        delete selected[opt.value];
                        chip.classList.remove("dk-chip-selected");
                    } else {
                        selected[opt.value] = opt.label;
                        chip.classList.add("dk-chip-selected");
                    }
                } else {
                    container.remove();
                    onSubmit(opt.label);
                }
            });
            row.appendChild(chip);
        });

        if (chipData.multi_select) {
            var confirmBtn = el("button", "dk-chip-confirm", "Done");
            confirmBtn.addEventListener("click", function () {
                var labels = Object.keys(selected).map(function (k) { return selected[k]; });
                if (!labels.length) return;
                container.remove();
                onSubmit(labels.join(", "));
            });
            row.appendChild(confirmBtn);
        }

        container.appendChild(row);
        body.appendChild(container);
        scrollToBottom(body);
        return container;
    }

    /**
     * Renders a row of hyperlink chips — visually matches renderChips'
     * .dk-chip style, but each one is a real <a href target=_blank> that
     * opens an external page directly instead of sending a chat message.
     * Used for "Track my order" (backend/playbooks/other.py's track_order
     * emits this as link_chips) and the homepage opening row.
     */
    function renderLinkChips(body, linkChipsData) {
        if (!linkChipsData || !linkChipsData.length) return null;

        var container = el("div", "dk-chips-container");
        var row = el("div", "dk-chip-row");

        linkChipsData.forEach(function (item) {
            var link = el("a", "dk-chip dk-chip-link", escapeHtml(item.label));
            link.href = item.url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            row.appendChild(link);
        });

        container.appendChild(row);
        body.appendChild(container);
        scrollToBottom(body);
        return container;
    }

    window.__dkAdvisor._internal.renderBubble = renderBubble;
    window.__dkAdvisor._internal.renderPanel = renderPanel;
    window.__dkAdvisor._internal.appendUserMessage = appendUserMessage;
    window.__dkAdvisor._internal.appendAssistantMessage = appendAssistantMessage;
    window.__dkAdvisor._internal.appendTypingIndicator = appendTypingIndicator;
    window.__dkAdvisor._internal.renderChips = renderChips;
    window.__dkAdvisor._internal.renderLinkChips = renderLinkChips;
    window.__dkAdvisor._internal.scrollToBottom = scrollToBottom;

    // ===========================================================================
    // Product cards
    //
    // Per spec: ALL retrieved products are shown, but the top 3 are visually
    // highlighted (badge + tinted card) since the chat text already explains
    // WHY each of those three fits. Remaining products get a plain grid below
    // with no LLM commentary attached — title + price is enough there.
    // ===========================================================================

    function renderProductCard(product, isTopPick) {
        var card = el("div", "dk-card" + (isTopPick ? " dk-card-top" : ""));
        if (isTopPick) card.appendChild(el("div", "dk-card-badge", "Best match"));

        // The image + title + price are wrapped in a real hyperlink to the
        // product's actual dotandkey.com page when we have one (from the
        // graph's url/image_url fields — see scripts/fetch_product_media.py).
        // "Add to cart" stays OUTSIDE the link so tapping it doesn't also
        // trigger navigation.
        var hasLink = !!(product.url);
        var linkWrap = hasLink
            ? el("a", "dk-card-link")
            : el("div", "dk-card-link");
        if (hasLink) {
            var href = product.url.startsWith("http") ? product.url : "https://www.dotandkey.com" + product.url;
            linkWrap.href = href;
            linkWrap.target = "_blank";
            linkWrap.rel = "noopener noreferrer";
        }

        var img = el("div", "dk-card-img");
        if (product.image_url) {
            var imgEl = document.createElement("img");
            imgEl.src = product.image_url;
            imgEl.alt = product.title;
            imgEl.loading = "lazy";
            img.appendChild(imgEl);
        }
        linkWrap.appendChild(img);
        linkWrap.appendChild(el("div", "dk-card-title", escapeHtml(product.title)));

        // Price row — strikethrough MRP next to the discounted price when
        // compare_at_price is higher (mirrors the real site's "₹476 ₹595"
        // pattern). Falls back to a single price when there's no discount.
        // dotandkey discounts are cart-level (automatic codes), so Shopify's
        // compare_at_price always equals price. Derive the sale price from
        // promo_label instead: "Flat 15% OFF" → sale = round(price × 0.85).
        var priceRow = el("div", "dk-card-price-row");
        if (product.price) {
            var _rawPr  = parseFloat(product.price) || 0;
            var _pMatch = (product.promo_label || "").match(/(\d+)\s*%/);
            var _pct    = _pMatch ? parseInt(_pMatch[1], 10) : 0;
            var _salePr = _pct > 0 ? Math.round(_rawPr * (1 - _pct / 100)) : _rawPr;
            var _strike = _pct > 0 ? _rawPr
                : (product.compare_at_price > _rawPr ? product.compare_at_price : 0);
            priceRow.appendChild(el("span", "dk-card-price", "\u20B9" + _salePr));
            if (_strike) priceRow.appendChild(el("span", "dk-card-price-strike", "\u20B9" + _strike));
        }
        // Promo label badge
        if (product.promo_label) {
            priceRow.appendChild(el("span", "dk-card-promo", escapeHtml(product.promo_label)));
        }
        linkWrap.appendChild(priceRow);
        card.appendChild(linkWrap);

        // Keyword subheading — short "why this matches" tags, e.g.
        // "Anti-acne · Lightweight · Niacinamide". Only rendered for top picks.
        // Guard on isTopPick (not just product.keywords) because a top-pick object
        // that overflows into the "More Options" pool still carries keywords from
        // the backend — without this guard those tags would bleed into the grid.
        if (isTopPick && product.keywords && product.keywords.length) {
            var kw = el("div", "dk-card-keywords", escapeHtml(product.keywords.join(" \u00B7 ")));
            card.appendChild(kw);
        }

        var cta = el("button", "dk-card-cta", "Add to cart");
        cta.addEventListener("click", function (e) {
            e.preventDefault();
            addToCart(product, cta);
        });
        card.appendChild(cta);

        // Lazy-load variants from Shopify product JSON (progressive enhancement).
        // Stash the graph-stored shade name on the button so loadProductVariants
        // can find and pre-select the exact recommended shade rather than
        // defaulting to Shopify's arbitrary first variant.
        if (hasLink) {
            var vHandle = product.url.split("/products/")[1];
            if (vHandle) vHandle = vHandle.split("?")[0].split("/")[0];
            if (vHandle) {
                cta._variantHint = (product.variant || "").trim();
                requestAnimationFrame(function () {
                    loadProductVariants(vHandle, card, cta);
                });
            }
        }

        return card;
    }

    function renderProducts(body, topPicks, remaining) {
        if ((!topPicks || !topPicks.length) && (!remaining || !remaining.length)) return;

        var wrap = el("div", "dk-products");

        // Layout:
        //   Top section — first 2 top picks in a 2-column grid (highlighted)
        //   More options — all remaining cards in a 2-column grid (no cap)
        var highlights = (topPicks || []).slice(0, 2);
        var overflow   = (topPicks || []).slice(2);
        var allRest    = overflow.concat(remaining || []);

        if (highlights.length) {
            var topGrid = el("div", "dk-product-grid");
            highlights.forEach(function (p) {
                topGrid.appendChild(renderProductCard(p, true));
            });
            wrap.appendChild(topGrid);
        }

        if (allRest.length) {
            wrap.appendChild(el("div", "dk-remaining-label", "More options"));
            var restGrid = el("div", "dk-product-grid-sm");
            allRest.forEach(function (p) {
                restGrid.appendChild(renderProductCard(p, false));
            });
            wrap.appendChild(restGrid);
        }

        body.appendChild(wrap);
        // Scroll so the product section starts at the top of the body viewport —
        // this ensures both the top-picks row and the "more options" row are
        // simultaneously visible without the user needing to scroll.
        requestAnimationFrame(function () {
            body.scrollTop = wrap.offsetTop;
        });
    }

    /**
     * Opens the dotandkey cart drawer and selectively refreshes its contents,
     * matching the site's own renderContentsCustom pattern exactly:
     *
     *   1. GET /?sections=cart-drawer,cart-icon-bubble
     *      → Shopify returns JSON { "cart-drawer": "<html>", "cart-icon-bubble": "<html>" }
     *   2. DOMParser extracts the inner HTML of each section's target element
     *      and injects it into the live DOM (selective refresh, no page reload)
     *   3. cartDrawer.open() adds "animate"+"active" classes — same as the
     *      site's own add-to-cart button does
     *
     * _initCartDetection already watches the "active" class, so the widget
     * will automatically slide left when the drawer opens.
     *
     * Falls back to a plain event dispatch on localhost/demo where the
     * Shopify sections endpoint doesn't exist.
     */
    /**
     * Opens the dotandkey cart drawer and selectively refreshes its contents.
     *
     * Primary path: call cartDrawer.renderContentsCustom(sections, true) directly
     * on the <cart-drawer> custom element — this is the site's own method, so it
     * handles all DOM injection, is-empty removal, icon refresh, and open() in
     * exactly the same way their add-to-cart button does.
     *
     * Fallback: manual DOM injection for any edge case where renderContentsCustom
     * is unavailable (should not happen on dotandkey.com but makes the code safe).
     *
     * _initCartDetection already watches the "active" class added by open(), so
     * the widget automatically slides left when the drawer appears.
     */
    function _triggerCartRefresh() {
        fetch("/?sections=cart-drawer,cart-icon-bubble", { credentials: "same-origin" })
            .then(function (r) {
                if (!r.ok) throw new Error("sections " + r.status);
                return r.json();
            })
            .then(function (sections) {
                var cartDrawer = document.querySelector("cart-drawer");
                if (!cartDrawer) throw new Error("no cart-drawer element");

                // Primary: use the site's own method — it handles all DOM injection
                // and post-injection re-initialization internally
                if (typeof cartDrawer.renderContentsCustom === "function") {
                    cartDrawer.renderContentsCustom(sections, true /* openDrawer */);
                    return;
                }

                // Fallback: replicate renderContentsCustom manually
                function _html(raw, sel) {
                    return new DOMParser().parseFromString(raw, "text/html").querySelector(sel);
                }
                var cdEl = document.querySelector("#CartDrawer");
                if (cdEl && sections["cart-drawer"]) {
                    var n = _html(sections["cart-drawer"], "#CartDrawer");
                    if (n) cdEl.innerHTML = n.innerHTML;
                }
                var bEl = document.getElementById("cart-icon-bubble");
                if (bEl && sections["cart-icon-bubble"]) {
                    var n2 = _html(sections["cart-icon-bubble"], ".shopify-section");
                    if (n2) bEl.innerHTML = n2.innerHTML;
                }
                var di = document.querySelector("#CartDrawer .drawer__inner");
                if (di) di.classList.remove("is-empty");
                var dr = document.getElementsByClassName("drawer")[0];
                if (dr) dr.classList.remove("is-empty");
                if (typeof cartDrawer.refreshCartIconBubble === "function") {
                    cartDrawer.refreshCartIconBubble();
                }
                setTimeout(function () {
                    var ov = document.querySelector("#CartDrawer-Overlay");
                    if (ov) ov.addEventListener("click", function () {
                        if (typeof cartDrawer.close === "function") cartDrawer.close();
                    });
                    if (typeof cartDrawer.open === "function") cartDrawer.open();
                });
            })
            .catch(function () {
                // Demo / localhost — Shopify sections endpoint doesn't exist
                try {
                    document.documentElement.dispatchEvent(
                        new CustomEvent("cart:refresh", { bubbles: true })
                    );
                } catch (e) {}
            });
    }

    /**
     * Add to cart via Shopify's AJAX Cart API (same-origin on dotandkey.com).
     *
     * Flow:
     *   1. Extract the product handle from the stored URL
     *   2. Fetch /products/{handle}.json to get the first available variant ID
     *   3. POST to /cart/add.js with { id: variantId, quantity: 1 }
     *
     * Graceful degradation: on localhost/demo where Shopify endpoints are not
     * reachable, shows a retry state instead of silently failing.
     */
    function addToCart(product, btn) {
        if (btn.classList.contains("dk-adding") || btn.classList.contains("dk-added")) return;

        btn.textContent = "Adding...";
        btn.classList.add("dk-adding");
        btn.disabled = true;

        var handle = "";
        var productUrl = product.url || "";
        var urlParts = productUrl.split("/products/");
        if (urlParts.length > 1) {
            handle = urlParts[1].split("?")[0].split("/")[0].trim();
        }

        if (!handle) {
            btn.textContent = "View product";
            btn.classList.remove("dk-adding");
            btn.classList.add("dk-added");
            btn.disabled = false;
            if (productUrl) {
                btn.addEventListener("click", function () {
                    var href = productUrl.startsWith("http")
                        ? productUrl : "https://www.dotandkey.com" + productUrl;
                    window.open(href, "_blank", "noopener,noreferrer");
                }, { once: true });
            }
            return;
        }

        // Shortcut: variant already resolved by size/tint selector — skip re-fetch
        function _doCartAdd(variantId) {
            return fetch("/cart/add.js", {
                method: "POST",
                headers: { "Content-Type": "application/json", "Accept": "application/json" },
                body: JSON.stringify({ items: [{ id: variantId, quantity: 1 }] }),
            });
        }

        if (btn._variantId) {
            _doCartAdd(btn._variantId)
                .then(function (res) {
                    if (!res.ok) throw new Error("cart " + res.status);
                    btn.textContent = "Added ✓";
                    btn.classList.remove("dk-adding");
                    btn.classList.add("dk-added");
                    btn.disabled = true;
                    _triggerCartRefresh();
                })
                .catch(function (err) {
                    console.error("[dk-advisor] cart add failed:", err);
                    btn.textContent = "Try again";
                    btn.classList.remove("dk-adding");
                    btn.classList.add("dk-error");
                    btn.disabled = false;
                    btn.addEventListener("click", function () {
                        btn.classList.remove("dk-error");
                        addToCart(product, btn);
                    }, { once: true });
                });
            return;
        }

        fetch("/products/" + handle + ".json")
            .then(function (res) {
                if (!res.ok) throw new Error("product fetch " + res.status);
                return res.json();
            })
            .then(function (data) {
                var variants = data.product && data.product.variants;
                var variant = (variants || []).find(function (v) {
                    return v.available !== false;
                }) || (variants && variants[0]);
                if (!variant || !variant.id) throw new Error("no variant");
                return _doCartAdd(variant.id);
            })
            .then(function (res) {
                if (!res.ok) throw new Error("cart/add.js " + res.status);
                btn.textContent = "Added \u2713";
                btn.classList.remove("dk-adding");
                btn.classList.add("dk-added");
                btn.disabled = true;
                _triggerCartRefresh();
            })
            .catch(function (err) {
                console.error("[dk-advisor] add to cart failed:", err);
                btn.textContent = "Try again";
                btn.classList.remove("dk-adding");
                btn.classList.add("dk-error");
                btn.disabled = false;
                btn.addEventListener("click", function () {
                    btn.classList.remove("dk-error");
                    addToCart(product, btn);
                }, { once: true });
            });
    }

    // ===========================================================================
    // Shade colour registry — solid colour swatches, not product thumbnails.
    //
    // Two-tier lookup:
    //   1. Exact normalised title match   → authoritative per DK shade launch colour
    //   2. Word-boundary keyword fallback → covers future shades automatically
    //
    // Rules that prevented the previous palette from working correctly:
    //   ✗ "berry" matched inside "straw*berry*"     → fixed: \b word boundaries
    //   ✗ "medium" / "light" matched tint descriptors → fixed: descriptors removed
    //   ✗ "strawberry" came after "berry" in list    → fixed: more-specific first
    // ===========================================================================

    var _SHADE_EXACT = {
        // ── F1: Strawberry Dew Tinted Sunscreen — skin-tone range light→deep ──
        "peony - 00 very light":                    "#F2D5CE",
        "porcelain - 01 light":                     "#F0DDD0",
        "rose - 01a light with neutral undertone":  "#E8C4B8",
        "warm ivory - 02 light medium":             "#E8C898",
        "sand - 03 medium":                         "#C8A06A",
        "beige - 05 medium deep":                   "#A87848",
        "caramel - 07 deep":                        "#7A4A28",
        // ── F2: Ceramide + Peptide Lip Balm ──────────────────────────────────
        "warm nude":                                "#D4926A",
        "plush pink":                               "#E87898",
        "red romance":                              "#C83048",
        // ── F3: Barrier Repair Hydrating Lip Balm ────────────────────────────
        "strawberry red - high tinted":             "#E03850",
        "cherry crimson - high tinted":             "#A82038",
        "cocoa nude - medium tinted":               "#7B4A32",
        "blueberry bliss - non-tinted":             "#EDE8F2",  // non-tinted: pale lavender-grey, not purple
        // ── F4: Gloss Boss Lip Balm ───────────────────────────────────────────
        "strawberry crush high tinted":             "#E04858",
        "cherry pop medium tinted":                 "#D83058",
        "watermelon cool medium tinted":            "#E87890",
        "cocoa mint low tinted":                    "#6B4228",
        "watermelon rush high tinted":              "#C83860",
        // ── F5: Meltie Lip Balm ───────────────────────────────────────────────
        "strawberry glaze":                         "#E05068",
        "berry crumble":                            "#7A2858",
        // ── F6: Lip Plumping Mask ─────────────────────────────────────────────
        "turmeric oil and lingonberry medium tinted": "#C08060",
        // ── F7: Barrier Repair Hydrating Lip Balm Pack of 2 ──────────────────
        // Combo names blend two shades — each entry gets a unique colour that
        // visually sits between its two constituent shades so swatches remain
        // distinguishable even though they all share shade-name substrings.
        "cocoa nude + cherry crimson":      "#8F3C35",
        "strawberry red + cherry crimson":  "#D42040",
        "cherry crimson (pack of 2)":       "#8C1828",
        "cocoa nude + strawberry red":      "#B45040",
        "strawberry red (pack of 2)":       "#CC2840",
        "cocoa nude (pack of 2)":           "#5C3020",
        "blueberry bliss + cherry crimson": "#8C3060",
        "blueberry bliss + cocoa nude":     "#6A4860",
        "blueberry bliss + strawberry red": "#903050",
        "blueberry bliss (pack of 2)":      "#5A4878",
    };

    // Keyword fallback (word-boundary, most-specific first).
    // Only pigment/shade words — no tint-level descriptors like "medium"/"light".
    var _SHADE_KW = [
        ["strawberry",  "#E8415A"],
        ["cherry",      "#C03050"],
        ["watermelon",  "#E87090"],
        ["blueberry",   "#7A6898"],
        ["berry",       "#8B3A6B"],
        ["cocoa",       "#6B4228"],
        ["caramel",     "#8B5E3C"],
        ["peony",       "#F5B8C9"],
        ["rose",        "#E8A0A8"],
        ["porcelain",   "#F8EBD8"],
        ["warm ivory",  "#EDD9B0"],
        ["ivory",       "#F5E6C8"],
        ["nude",        "#D4926A"],
        ["sand",        "#D4A876"],
        ["beige",       "#C49A6C"],
        ["fair",        "#F0DBC4"],
    ];

    function _normShade(s) { return (s||"").toLowerCase().replace(/\s+/g," ").trim(); }

    function _swatchColor(label) {
        var exact = _SHADE_EXACT[_normShade(label)];
        if (exact) return exact;
        var l = _normShade(label);
        for (var i = 0; i < _SHADE_KW.length; i++) {
            var kw = _SHADE_KW[i][0];
            var hit = kw.indexOf(" ") > -1
                ? l.indexOf(kw) > -1
                : new RegExp("\\b" + kw + "\\b").test(l);
            if (hit) return _SHADE_KW[i][1];
        }
        return "#D0A080";
    }

    // ===========================================================================
    // Variant / size pills + tint swatches — lazy-loaded from Shopify product JSON
    // ===========================================================================

    function _isSizeLabel(label) {
        return /\d+\s*(ml|g|gm|mg|l|pack|pcs?)\b/i.test(label);
    }

    /**
     * Lazy-fetches /products/{handle}.js and injects size pills or tint
     * swatches into the card, above the CTA button.
     *
     * Uses the .js endpoint (not .json) because it provides:
     *   - variant.available (boolean) — filters out OOS shades, matching PDP
     *   - variant.featured_image.src — per-shade packshot, no extra lookup
     *   - prices in paise (integer) — divide by 100 for display
     * The .json endpoint omits `available` entirely and has null featured_image.
     */
    function loadProductVariants(handle, card, ctaBtn) {
        fetch("/products/" + handle + ".js")
            .then(function (res) {
                if (!res.ok) throw new Error(res.status);
                return res.json();
            })
            .then(function (data) {
                // .js is top-level (no `product` wrapper); all variants including OOS
                var variants = data.variants || [];

                // Resolve once — reused by both single and multi-variant paths.
                var cardImgEl = card.querySelector(".dk-card-img img");
                var linkEl    = card.querySelector(".dk-card-link");

                // Stamp ?variant=id onto the link so the PDP opens the exact shade/size
                // the card is currently showing, not Shopify's arbitrary default variant.
                function _pinLink(v) {
                    ctaBtn._variantId = v.id;
                    if (linkEl && linkEl.href) {
                        linkEl.href = linkEl.href.split("?")[0] + "?variant=" + v.id;
                    }
                }

                if (variants.length <= 1) {
                    if (variants[0]) {
                        _pinLink(variants[0]);
                        // Apply live Shopify price/strike even for single-variant products.
                        // .js returns prices in paise (e.g. 24900 = ₹249) — divide by 100.
                        var sv = variants[0];
                        var svPr  = Math.round((sv.price  || 0) / 100);
                        var svCap = Math.round((sv.compare_at_price || 0) / 100);
                        var svPrEl = card.querySelector(".dk-card-price");
                        var svStEl = card.querySelector(".dk-card-price-strike");
                        if (svPrEl && sv.price) svPrEl.textContent = "₹" + svPr;
                        if (svCap > svPr) {
                            if (!svStEl) {
                                svStEl = el("span", "dk-card-price-strike", "₹" + svCap);
                                if (svPrEl) svPrEl.parentNode.appendChild(svStEl);
                            } else {
                                svStEl.textContent = "₹" + svCap;
                                svStEl.style.display = "";
                            }
                        } else if (svStEl) {
                            svStEl.style.display = "none";
                        }
                    }
                    return;
                }

                // Returns the CDN URL for a variant's per-shade packshot, sized for
                // the card hero image.  The .js endpoint exposes featured_image.src
                // directly (the .json endpoint had it as null for all DK variants).
                // .js images array is URL strings; prepend https: if protocol-relative.
                var _firstProductImg = (data.images && data.images[0])
                    ? String(data.images[0]).replace(/^\/\//, "https://") : "";
                function _variantImgSrc(v, width) {
                    var fi = v.featured_image;
                    var raw = (fi && fi.src) ? fi.src : _firstProductImg;
                    var src = raw ? String(raw).replace(/^\/\//, "https://") : "";
                    if (!src) return "";
                    var w = width || 400;
                    return src.indexOf("?") > -1 ? src + "&width=" + w : src + "?width=" + w;
                }

                var options = data.options || [];
                var isTint = options.some(function (o) {
                    return /color|shade|tint|tone/i.test(o.name || "");
                });

                var priceEl   = card.querySelector(".dk-card-price");
                var strikeEl  = card.querySelector(".dk-card-price-strike");

                var rowClass  = isTint ? "dk-swatch-row" : "dk-variant-row";
                var variantRow = el("div", rowClass);

                // Resolve the recommended variant by matching the graph-stored shade
                // name against Shopify titles. Normalise whitespace so minor
                // differences ("Warm Ivory  - 02" vs "Warm Ivory - 02") don't block
                // a match. Falls back to first available when the hint is absent or
                // no title matches (e.g. product catalogue changed since last ingest).
                function _normalise(s) {
                    return (s || "").toLowerCase().replace(/\s+/g, " ").trim();
                }
                var variantHint = _normalise(ctaBtn._variantHint);
                var selVariant = (variantHint
                    ? variants.find(function (v) {
                        return _normalise(v.title) === variantHint;
                    })
                    : null)
                    || variants.find(function (v) { return v.available !== false; })
                    || variants[0];

                // Apply live Shopify price + strikethrough for a given variant.
                // .js returns prices as integers in paise (e.g. 24900 = \u20b9249);
                // divide by 100 to get rupees.
                function _applyPrice(v) {
                    var livePr  = Math.round((v.price  || 0) / 100);
                    var liveCap = Math.round((v.compare_at_price || 0) / 100);
                    if (priceEl && v.price) priceEl.textContent = "\u20B9" + livePr;
                    if (liveCap > livePr) {
                        if (!strikeEl) {
                            strikeEl = el("span", "dk-card-price-strike", "\u20B9" + liveCap);
                            if (priceEl) priceEl.parentNode.appendChild(strikeEl);
                        } else {
                            strikeEl.textContent = "\u20B9" + liveCap;
                            strikeEl.style.display = "";
                        }
                    } else if (strikeEl) {
                        strikeEl.style.display = "none";
                    }
                }

                // Sync card image, link URL, price, and variant ID to the selected
                // variant. Keeps image ↔ swatch ↔ "Add to cart" ↔ PDP link all
                // pointing at the same shade — the three were independent before.
                function _applyVariantDisplay(v) {
                    _pinLink(v);
                    _applyPrice(v);
                    // Update card packshot to the variant's per-shade product image.
                    // Uses image_id → product.images lookup (featured_image is null
                    // on all DK variants in the storefront JSON).
                    if (isTint && cardImgEl) {
                        var cardSrc = _variantImgSrc(v, 400);
                        if (cardSrc) cardImgEl.src = cardSrc;
                    }
                }

                // Apply immediately for the default variant (no click needed)
                _applyVariantDisplay(selVariant);

                function selectVariant(v, itemEl, allItems, itemClass) {
                    selVariant = v;
                    // Reset CTA so user can re-add a different size/shade
                    if (ctaBtn.classList.contains("dk-added") ||
                        ctaBtn.classList.contains("dk-error")) {
                        ctaBtn.classList.remove("dk-added", "dk-error");
                        ctaBtn.textContent = "Add to cart";
                        ctaBtn.disabled = false;
                    }
                    _applyVariantDisplay(v);
                    // Highlight selected item
                    allItems.forEach(function (el2) { el2.classList.remove(itemClass + "-selected"); });
                    itemEl.classList.add(itemClass + "-selected");
                }

                variants.forEach(function (v) {
                    var label = (v.title === "Default Title" ? "" : v.title || "").trim();
                    if (!label) return;
                    // Skip OOS variants — matches PDP behaviour (available=false means hidden)
                    if (v.available === false) return;

                    if (isTint) {
                        var sw = el("div", "dk-swatch" + (v.id === selVariant.id ? " dk-swatch-selected" : ""));
                        sw.style.background = _swatchColor(label);
                        sw.title = label;
                        sw.addEventListener("click", function () {
                            var all = Array.prototype.slice.call(variantRow.querySelectorAll(".dk-swatch"));
                            selectVariant(v, sw, all, "dk-swatch");
                        });
                        variantRow.appendChild(sw);
                    } else if (_isSizeLabel(label)) {
                        var pill = el("div", "dk-variant-pill" + (v.id === selVariant.id ? " dk-variant-selected" : ""), escapeHtml(label));
                        pill.addEventListener("click", function () {
                            var all = Array.prototype.slice.call(variantRow.querySelectorAll(".dk-variant-pill"));
                            selectVariant(v, pill, all, "dk-variant");
                        });
                        variantRow.appendChild(pill);
                    }
                });

                if (variantRow.children.length > 1) {
                    ctaBtn.parentNode.insertBefore(variantRow, ctaBtn);
                }
            })
            .catch(function () {
                // Silent fail — variants are progressive enhancement
            });
    }

    // ===========================================================================
    // Combo cards — shown ABOVE individual product picks when the backend
    // returns bundle deals that match the user's skin type.
    // ===========================================================================

    function renderComboCard(combo) {
        var card = el("div", "dk-combo-card");
        card.appendChild(el("div", "dk-combo-badge", "Bundle deal"));

        card.appendChild(el("div", "dk-combo-title", escapeHtml(combo.title)));

        // Component product images side-by-side
        if (combo.components && combo.components.length) {
            var compRow = el("div", "dk-combo-components");
            combo.components.forEach(function (comp, idx) {
                if (idx > 0) {
                    compRow.appendChild(el("div", "dk-combo-comp-plus", "+"));
                }
                var imgBox = el("div", "dk-combo-comp-img");
                if (comp.image_url) {
                    var imgEl = document.createElement("img");
                    imgEl.src = comp.image_url;
                    imgEl.alt = comp.title || "";
                    imgEl.loading = "lazy";
                    imgBox.appendChild(imgEl);
                }
                compRow.appendChild(imgBox);
            });
            card.appendChild(compRow);
        }

        // Skin type compatibility tags
        if (combo.matched_skin_types && combo.matched_skin_types.length) {
            var tags = combo.matched_skin_types.map(function (s) {
                return s.charAt(0).toUpperCase() + s.slice(1);
            }).join(" · ");
            card.appendChild(el("div", "dk-combo-skin-tags", escapeHtml(tags) + " skin"));
        }

        // Price row — show total + savings if compare_at > price
        var priceRow = el("div", "dk-combo-price-row");
        if (combo.price) {
            priceRow.appendChild(el("span", "dk-combo-price", "₹" + combo.price));
        }
        if (combo.compare_at_price && combo.compare_at_price > combo.price) {
            var saved = Math.round(combo.compare_at_price - combo.price);
            priceRow.appendChild(
                el("span", "dk-combo-savings", "Save ₹" + saved)
            );
        }
        card.appendChild(priceRow);

        // Shop combo CTA — links to the bundle's product page
        var cta = el("button", "dk-combo-cta", "Shop this combo →");
        cta.addEventListener("click", function (e) {
            e.preventDefault();
            if (combo.url) {
                var href = combo.url.startsWith("http")
                    ? combo.url
                    : "https://www.dotandkey.com" + combo.url;
                window.open(href, "_blank", "noopener,noreferrer");
            }
        });
        card.appendChild(cta);

        return card;
    }

    function renderCombos(body, combos) {
        if (!combos || !combos.length) return;
        var wrap = el("div", "dk-combos");
        wrap.appendChild(el("div", "dk-combos-label", "✨ Bundle deals for you"));
        combos.forEach(function (c) {
            wrap.appendChild(renderComboCard(c));
        });
        body.appendChild(wrap);
        scrollToBottom(body);
    }

    window.__dkAdvisor._internal.renderProducts = renderProducts;
    window.__dkAdvisor._internal.renderCombos = renderCombos;
    window.__dkAdvisor._internal.loadProductVariants = loadProductVariants;
    window.__dkAdvisor._internal.addToCart = addToCart;

    // ===========================================================================
    // Category chips shown on first homepage open (before any /chat call
    // exists to supply suggested_chips from the backend). Kept in sync by
    // hand with backend/chip_options.py's CATEGORY_CHIPS — if you add a
    // category there, mirror it here too.
    // ===========================================================================

    var INITIAL_CATEGORY_CHIPS = {
        field: "category",
        multi_select: false,
        options: [
            { value: "sunscreen", label: "Sunscreen" },
            { value: "moisturizer", label: "Moisturizer" },
            { value: "face_wash", label: "Face wash" },
            { value: "serum", label: "Serum" },
            { value: "lip_care", label: "Lip care" },
            { value: "eye_care", label: "Eye care" },
        ],
    };

    var RETURNING_USER_CHIPS = {
        field: "returning_check",
        multi_select: false,
        options: [
            { value: "same", label: "Same as before" },
            { value: "changed", label: "Something has changed" },
            { value: "concerns", label: "Have concerns with a previous purchase" },
        ],
    };

    // Shown alongside the opening chips on every homepage session (new AND
    // returning user) — a direct hyperlink, not a chat-triggering chip, so
    // it opens ClickPost in one tap instead of a round trip through /chat.
    // Mirrors backend/playbooks/other.py's TRACK_ORDER_LINK_CHIP — if that
    // URL changes, update it here too.
    var TRACK_ORDER_LINK_CHIP = [
        { label: "Track my order", url: "https://dotandkey.clickpost.ai/" },
    ];

    // ===========================================================================
    // sendMessage — the core conversational turn
    // ===========================================================================

    /**
     * sendMessage — streams a chat turn and renders it with a smooth,
     * consistent typewriter pace.
     *
     * Why a queue instead of rendering each network chunk directly: SSE
     * delivery is bursty in practice — a slow model "thinking" phase often
     * means several chunks arrive bunched together in one network read once
     * generation finally starts. Rendering each one synchronously in a tight
     * loop doesn't give the browser a chance to paint between them, so a
     * burst of 20 chunks can visually appear as one instant jump rather than
     * 20 frames of typing. Queuing characters and draining them via
     * requestAnimationFrame guarantees a steady visual pace regardless of
     * how clumped the underlying network delivery was.
     */
    function sendMessage(text) {
        var els = state.els;
        if (!els || state.sending) return;

        state.sending = true;
        els.sendBtn.disabled = true;

        appendUserMessage(els.body, text);
        var typingEl = appendTypingIndicator(els.body);

        var assistantBubble = null;
        var charQueue = [];
        var draining = false;
        var streamEnded = false;
        var pendingDone = null;

        function finishMessage() {
            if (assistantBubble) {
                assistantBubble.cursor.remove();
            } else {
                typingEl.remove();
            }
            state.sending = false;
            els.sendBtn.disabled = !els.input.value.trim();

            if (pendingDone) {
                if (pendingDone.suggested_chips) {
                    renderChips(els.body, pendingDone.suggested_chips, sendMessage);
                }
                if (pendingDone.link_chips && pendingDone.link_chips.length) {
                    renderLinkChips(els.body, pendingDone.link_chips);
                }
                if (pendingDone.combos && pendingDone.combos.length) {
                    renderCombos(els.body, pendingDone.combos);
                }
                // Compact profile summary row: "Dry skin • Fragrance-free • Under ₹300"
                if (pendingDone.profile_chips && pendingDone.profile_chips.length) {
                    var chipsRow = el("div", "dk-profile-chips");
                    pendingDone.profile_chips.forEach(function (label) {
                        chipsRow.appendChild(el("span", "dk-profile-chip", escapeHtml(label)));
                    });
                    els.body.appendChild(chipsRow);
                    scrollToBottom(els.body);
                }
                // Friendly one-liner when budget was silently expanded — no warning icons, no tier names
                if (pendingDone.budget_expansion_message) {
                    els.body.appendChild(
                        el("div", "dk-budget-expand", escapeHtml(pendingDone.budget_expansion_message))
                    );
                    scrollToBottom(els.body);
                }
                if (pendingDone.top_picks || pendingDone.remaining) {
                    renderProducts(els.body, pendingDone.top_picks, pendingDone.remaining);
                }
                if (pendingDone.collab_picks && pendingDone.collab_picks.length) {
                    var collabWrap = el("div", "dk-products");
                    collabWrap.appendChild(el("div", "dk-remaining-label", "Popular with similar skin types"));
                    var collabGrid = el("div", "dk-product-grid");
                    pendingDone.collab_picks.forEach(function (p) {
                        collabGrid.appendChild(renderProductCard(p, false));
                    });
                    collabWrap.appendChild(collabGrid);
                    els.body.appendChild(collabWrap);
                    scrollToBottom(els.body);
                }
            }
        }

        function drainStep() {
            if (charQueue.length === 0) {
                draining = false;
                if (streamEnded) finishMessage();
                return;
            }
            // Adaptive batch size: normally a gentle 3 chars/frame typewriter
            // pace (~180 chars/sec), but speed up if a big backlog has built up
            // (e.g. the whole response arrived in one burst) so a long answer
            // doesn't take unreasonably long to finish appearing.
            var batchSize = Math.max(3, Math.ceil(charQueue.length / 50));
            var batch = charQueue.splice(0, batchSize).join("");

            assistantBubble.text += batch;
            assistantBubble.msg.textContent = assistantBubble.text;
            assistantBubble.msg.appendChild(assistantBubble.cursor);
            scrollToBottom(els.body);

            requestAnimationFrame(drainStep);
        }

        function startDrain() {
            if (draining) return;
            draining = true;
            drainStep();
        }

        streamChat(
            text,
            state.productCtx,
            function onToken(token) {
                if (!assistantBubble) {
                    typingEl.remove();
                    assistantBubble = appendAssistantMessage(els.body);
                }
                for (var i = 0; i < token.length; i++) charQueue.push(token[i]);
                startDrain();
            },
            function onDone(payload) {
                pendingDone = payload;
                streamEnded = true;
                if (!draining) finishMessage(); // nothing left queued — finish now
                // else: drainStep() will call finishMessage() once the queue empties
            },
            function onError(err) {
                typingEl.remove();
                if (assistantBubble) assistantBubble.cursor.remove();
                var errMsg = el("div", "dk-msg dk-msg-assistant",
                    "Something went wrong on our end \u2014 mind trying that again?");
                els.body.appendChild(errMsg);
                scrollToBottom(els.body);
                state.sending = false;
                els.sendBtn.disabled = !els.input.value.trim();
                console.error("[dk-advisor] chat error:", err);
            }
        );
    }

    window.__dkAdvisor._internal.sendMessage = sendMessage;

    // ===========================================================================
    // Session init — always the same homepage-style flow regardless of page.
    // Per design: visiting a product page does NOT change default behavior.
    // ===========================================================================

    function _streamText(msgEl, text, onDone) {
        var i = 0;
        var CHARS_PER_FRAME = 3;
        function tick() {
            if (i >= text.length) { if (onDone) onDone(); return; }
            i = Math.min(i + CHARS_PER_FRAME, text.length);
            msgEl.textContent = text.slice(0, i);
            requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    function initHomeSession() {
        var els = state.els;
        var loadingEl = appendTypingIndicator(els.body);

        var p = (state.sessionPromise && state.sessionPromise.then(function (d) {
            return d || postJSON("/session/init", { page_context: "homepage" });
        })) || postJSON("/session/init", { page_context: "homepage" });
        p
            .then(function (data) {
                loadingEl.remove();
                state.season = data.season;
                state.city = data.city;
                state.isReturning = data.is_returning;

                var greetingMsg = appendAssistantMessage(els.body);
                _streamText(greetingMsg.msg, data.greeting, function () {
                    greetingMsg.cursor.remove();
                });
                if (data.is_returning) {
                    renderChips(els.body, RETURNING_USER_CHIPS, sendMessage);
                } else {
                    renderChips(els.body, INITIAL_CATEGORY_CHIPS, sendMessage);
                }
                renderLinkChips(els.body, TRACK_ORDER_LINK_CHIP);
                if (state.pageCtx.type === "product" && state.pageCtx.handle) {
                    renderProductModeOffer(state.pageCtx.handle);
                }
            })
            .catch(function (err) {
                loadingEl.remove();
                var errMsg = el("div", "dk-msg dk-msg-assistant",
                    "Hi! What are you looking for today?");
                els.body.appendChild(errMsg);
                console.error("[dk-advisor] session/init failed:", err);
            });
    }

    // ===========================================================================
    // Product page mode — strictly opt-in.
    //
    // Visiting /products/* no longer auto-fetches product context or shows
    // product-specific question chips. The widget behaves identically to
    // the homepage by default. A single button offers the product-aware
    // flow; everything below this point only runs if the user taps it.
    // ===========================================================================

    function fetchShopifyProductJson(handle) {
        return fetch("/products/" + handle + ".json")
            .then(function (res) {
                if (!res.ok) throw new Error("product fetch failed: " + res.status);
                return res.json();
            })
            .then(function (data) { return data.product; });
    }

    function renderSimilarProducts(body, ctx) {
        var sameCategory = ctx.similar_same_category || [];
        var routine = ctx.similar_routine || [];
        if (!sameCategory.length && !routine.length) return;

        var wrap = el("div", "dk-products");
        if (sameCategory.length) {
            wrap.appendChild(el("div", "dk-remaining-label", "Similar products"));
            var grid1 = el("div", "dk-product-grid");
            sameCategory.forEach(function (p) { grid1.appendChild(renderProductCard(p, false)); });
            wrap.appendChild(grid1);
        }
        if (routine.length) {
            wrap.appendChild(el("div", "dk-remaining-label", "Pairs well with this"));
            var grid2 = el("div", "dk-product-grid");
            routine.forEach(function (p) { grid2.appendChild(renderProductCard(p, false)); });
            wrap.appendChild(grid2);
        }
        body.appendChild(wrap);
        scrollToBottom(body);
    }

    /**
     * Renders the opt-in "Get recommendations related to this product"
     * button. This is the ONLY entry point into product-aware mode.
     */
    function renderProductModeOffer(handle) {
        var els = state.els;
        var row = el("div", "dk-chip-row");
        var btn = el("div", "dk-chip", "\u2728 Get recommendations related to this product");
        btn.addEventListener("click", function () {
            row.remove();
            loadProductContext(handle);
        });
        row.appendChild(btn);
        els.body.appendChild(row);
        scrollToBottom(els.body);
    }

    /**
     * Runs the product-aware flow on demand. Session is already
     * initialized at this point (homepage flow already ran), so this
     * only needs: fetch the Shopify product JSON, call /context/product,
     * and render question chips + similar products. No extra LLM call
     * for the transition line — it's a plain templated string, which
     * also means tapping the button feels instant.
     */
    function loadProductContext(handle) {
        var els = state.els;

        fetchShopifyProductJson(handle)
            .catch(function () {
                // Shopify fetch failed (e.g. running outside dotandkey.com, or a
                // non-Shopify demo page) — fall back to a title guess from the
                // URL slug so the rest of the flow still works.
                var guessedTitle = handle.replace(/-/g, " ");
                return { title: guessedTitle, tags: [] };
            })
            .then(function (product) {
                var transitionMsg = appendAssistantMessage(els.body);
                transitionMsg.cursor.remove();
                transitionMsg.msg.textContent =
                    "Got it \u2014 here's what people usually ask about " + product.title + ":";

                return postJSON("/context/product", {
                    handle: handle,
                    title: product.title,
                    tags: product.tags || [],
                });
            })
            .then(function (ctx) {
                state.productCtx = ctx.found ? ctx : null;
                if (ctx.questions && ctx.questions.length) {
                    var chipData = {
                        field: "product_question",
                        multi_select: false,
                        options: ctx.questions.map(function (q) {
                            return { value: q, label: q };
                        }),
                    };
                    renderChips(els.body, chipData, sendMessage);
                }
                renderSimilarProducts(els.body, ctx);
            })
            .catch(function (err) {
                console.error("[dk-advisor] product context load failed:", err);
                var errMsg = el("div", "dk-msg dk-msg-assistant",
                    "Couldn't pull up details for this product \u2014 ask me anything else in the meantime.");
                els.body.appendChild(errMsg);
                scrollToBottom(els.body);
            });
    }

    window.__dkAdvisor._internal.initHomeSession = initHomeSession;
    window.__dkAdvisor._internal.loadProductContext = loadProductContext;

    // ===========================================================================
    // Cart detection — slide widget left when dotandkey's cart drawer opens so
    // it doesn't obstruct the cart.
    //
    // dotandkey.com (Dawn theme) uses a <cart-drawer> custom element that gains
    // the class "active" when open and loses it when closed. We attach a
    // MutationObserver to that element and animate the host's `right` position
    // (transition is set in buildShadowRoot's inline style).
    // ===========================================================================

    function _initCartDetection() {
        var host = state.els.host;
        var OPEN_RIGHT = "420px";
        var CLOSED_RIGHT = "20px";

        function applyPosition(cartEl) {
            host.style.right = cartEl.classList.contains("active")
                ? OPEN_RIGHT : CLOSED_RIGHT;
        }

        function attachObserver(cartEl) {
            applyPosition(cartEl); // sync initial state immediately
            new MutationObserver(function () {
                applyPosition(cartEl);
            }).observe(cartEl, { attributes: true, attributeFilter: ["class"] });
        }

        var cartEl = document.querySelector("cart-drawer");
        if (cartEl) { attachObserver(cartEl); return; }

        // Drawer not in DOM yet — wait for it to appear (it's rendered at page
        // load on Shopify; this handles any deferred hydration edge case).
        new MutationObserver(function (_mutations, obs) {
            var cartEl = document.querySelector("cart-drawer");
            if (!cartEl) return;
            obs.disconnect();
            attachObserver(cartEl);
        }).observe(document.body, { childList: true, subtree: true });
    }

    // ===========================================================================
    // Bootstrap
    //
    // Both the bubble and panel are created ONCE on boot; collapse/expand
    // just toggles which is visible. (An earlier version destroyed and
    // rebuilt the panel on every collapse, which silently wiped the visible
    // conversation and re-fired /session/init each time — fixed here by
    // keeping both DOM trees alive and toggling display instead.)
    // ===========================================================================

    function expand() {
        if (state.expanded) return;
        state.expanded = true;
        state.els.bubbleEl.style.display = "none";
        state.els.panelEl.style.display = "flex";
        state.els.input.focus();

        if (!state.initialized) {
            state.initialized = true;
            // Always the same flow, regardless of page — product-aware mode
            // is opt-in only (see renderProductModeOffer / loadProductContext).
            initHomeSession();
        }
    }

    function collapse() {
        state.expanded = false;
        state.els.panelEl.style.display = "none";
        state.els.bubbleEl.style.display = "flex";
    }

    function boot() {
        if (document.getElementById("dk-advisor-host")) return; // already booted
        if (!document.body) {
            document.addEventListener("DOMContentLoaded", boot);
            return;
        }

        state.pageCtx = detectPageContext();
        state.initialized = false;
        // Use the promise fired by prefetch.js (document_start) if available,
        // otherwise start our own request now (demo page / non-extension context).
        state.sessionPromise = window.__dkSessionPromise ||
            postJSON("/session/init", { page_context: "homepage" })
                .catch(function () { return null; });

        var shadowRefs = buildShadowRoot();

        var bubbleEl = renderBubble(shadowRefs.root, expand);

        var panelRefs = renderPanel(shadowRefs.root, collapse, sendMessage);
        panelRefs.panel.style.display = "none"; // hidden until first expand

        state.els = {
            host: shadowRefs.host,
            shadow: shadowRefs.shadow,
            root: shadowRefs.root,
            bubbleEl: bubbleEl,
            panelEl: panelRefs.panel,
            body: panelRefs.body,
            input: panelRefs.input,
            sendBtn: panelRefs.sendBtn,
        };

        _initCartDetection();
    }

    boot();
})();