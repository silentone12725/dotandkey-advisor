/**
 * extension/inject.js
 *
 * Content script — runs on dotandkey.com pages. Its only job is to drop
 * a real <script src="..."> tag pointing at our bundled widget.js into
 * the live page's DOM.
 *
 * Why a separate inject step instead of listing widget.js directly as
 * the content_script: Chrome content scripts execute in an "isolated
 * world" — they share the page's DOM but NOT its global JS scope, and
 * crucially, `document.currentScript` is null for content scripts
 * (they were never inserted via a <script> tag in the page itself).
 * widget.js relies on `document.currentScript.getAttribute("data-api-base")`
 * to read its config (see widget.js's SCRIPT_TAG detection at the top
 * of the file) — exactly like it would on a real dotandkey.com
 * deployment with <script src="widget.js" data-api-base="...">.
 *
 * Injecting a real <script> tag (sourced from the extension's own
 * bundled copy via chrome.runtime.getURL, declared in
 * web_accessible_resources) means widget.js runs EXACTLY as it would
 * in production — zero special-casing, zero changes to the
 * already-tested widget.js file itself.
 */

(function () {
    "use strict";

    // Avoid double-injection if this somehow runs twice.
    if (document.getElementById("dk-advisor-injected-script")) return;

    var script = document.createElement("script");
    script.id = "dk-advisor-injected-script";
    script.src = chrome.runtime.getURL("widget.js");

    // Local dev backend. Change this if you're testing against a
    // deployed instance instead of localhost.
    script.setAttribute("data-api-base", "http://localhost:8000");

    document.body.appendChild(script);
})();