/**
 * frontend/test_widget.js
 *
 * Runs widget.js inside jsdom (a real DOM implementation, not a mock) to
 * catch errors that a pure syntax check can't — undefined property access,
 * wrong element queries, event listener bugs, Shadow DOM issues, etc.
 *
 * This is NOT a substitute for testing in an actual browser before going
 * live — fetch/SSE streaming is mocked here, real network timing and
 * browser-specific quirks (Safari's ITP, mobile viewport behavior) can't
 * be caught this way. Run this after every widget.js change as a fast
 * smoke test; do a real-browser pass before shipping to production.
 *
 * Usage: node frontend/test_widget.js
 */

const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

let passed = 0;
let failed = 0;

function check(label, condition) {
  if (condition) {
    passed++;
    console.log("  \u2713 " + label);
  } else {
    failed++;
    console.log("  \u2717 " + label);
  }
}

function section(label) {
  console.log("\n" + label);
}

// ---------------------------------------------------------------------------
// Mock fetch — intercepts the widget's calls to /session/init, /chat,
// /context/product, and /products/:handle.json, returning canned responses
// so the widget's logic runs end-to-end without a real backend.
// ---------------------------------------------------------------------------

function makeMockFetch(log) {
  return function mockFetch(url, opts) {
    log.push({ url: url, method: opts && opts.method, headers: opts && opts.headers });

    if (typeof url === "string" && url.indexOf("/session/init") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          profile_id: "test-id",
          city: "Mumbai",
          season: "summer",
          is_returning: false,
          greeting: "Hey! What's your skin type?",
          weather: { temp: 30, humidity: 60 },
          initial_chips: { field: "category", multi_select: false, options: [{ value: "sunscreen", label: "Sunscreen" }] },
          returning_chips: { field: "returning_check", multi_select: false, options: [{ value: "same", label: "Same as before" }] },
          track_order: { label: "Track my order", url: "https://dotandkey.clickpost.ai/" },
        }),
      });
    }

    if (typeof url === "string" && url.indexOf("/context/product") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          found: true,
          sku: "DK_CCNS",
          title: "Cica + Niacinamide Sunscreen",
          questions: ["What does Niacinamide do for my skin?", "Is this fragrance-free?"],
          similar_same_category: [{ sku: "DK_WMCS", title: "Watermelon Sunscreen", price: 445 }],
          similar_routine: [],
        }),
      });
    }

    // fetchShopifyProductJson uses .json for title/tags (product page mode)
    if (typeof url === "string" && url.indexOf("/products/") === 0 && url.endsWith(".json")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ product: { title: "Cica Sunscreen", tags: [] } }),
      });
    }
    // loadProductVariants uses .js for availability + featured_image
    if (typeof url === "string" && url.indexOf("/products/") === 0 && url.endsWith(".js")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ title: "Cica Sunscreen", tags: [], variants: [], images: [], options: [] }),
      });
    }

    if (typeof url === "string" && url.indexOf("/chat") > -1) {
      // Simulate an SSE stream via a fake ReadableStream-like reader.
      const encoder = new TextEncoder();
      const events = [
        'data: {"token": "Hello"}\n\n',
        'data: {"token": " there!"}\n\n',
        'data: {"done": true, "playbook": "intake_profile", ' +
        '"suggested_chips": {"field": "skin_types", "multi_select": false, ' +
        '"options": [{"value": "oily", "label": "Oily"}]}}\n\n',
      ];
      let i = 0;
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => {
              if (i >= events.length) return Promise.resolve({ done: true });
              const chunk = encoder.encode(events[i++]);
              return Promise.resolve({ done: false, value: chunk });
            },
          }),
        },
      });
    }

    return Promise.reject(new Error("unexpected fetch: " + url));
  };
}

// ---------------------------------------------------------------------------
// Test 1 — widget boots cleanly on a homepage, bubble renders
// ---------------------------------------------------------------------------

async function testHomepageBoot() {
  section("Test 1: homepage boot");

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });

  const window = dom.window;
  const fetchLog = [];
  window.fetch = makeMockFetch(fetchLog);
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = () => "00000000-0000-0000-0000-000000000000";

  // localStorage is provided by jsdom automatically with pretendToBeVisual

  const widgetSrc = fs.readFileSync(path.join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);

  // give microtasks a tick to run (boot() is synchronous, but defensive)
  await new Promise((r) => setTimeout(r, 10));

  const host = window.document.getElementById("dk-advisor-host");
  check("shadow host created", !!host);
  check("shadow root attached", !!(host && host.shadowRoot));

  const shadow = host.shadowRoot;
  const bubble = shadow.querySelector(".dk-bubble");
  check("bubble rendered", !!bubble);
  check("bubble has nudge text", bubble && bubble.textContent.indexOf("routine") > -1);

  const panel = shadow.querySelector(".dk-panel");
  check("panel exists in DOM (hidden)", !!panel);
  check("panel is hidden initially", panel && panel.style.display === "none");

  return { dom, window, fetchLog };
}

// ---------------------------------------------------------------------------
// Test 2 — clicking the bubble expands the panel and fires /session/init
// ---------------------------------------------------------------------------

async function testExpandFlow() {
  section("Test 2: expand flow + session init");

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  const fetchLog = [];
  window.fetch = makeMockFetch(fetchLog);
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = () => "11111111-1111-1111-1111-111111111111";

  const widgetSrc = fs.readFileSync(path.join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);

  const host = window.document.getElementById("dk-advisor-host");
  const shadow = host.shadowRoot;
  const bubble = shadow.querySelector(".dk-bubble");

  bubble.dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 300)); // greeting streams via rAF; 300ms covers full text

  const panel = shadow.querySelector(".dk-panel");
  check("panel visible after click", panel.style.display === "flex");
  check("bubble hidden after click", bubble.style.display === "none");

  const sessionInitCall = fetchLog.find((c) => c.url.indexOf("/session/init") > -1);
  check("/session/init was called", !!sessionInitCall);
  check("/session/init sent X-Profile-Id header",
    !!(sessionInitCall && sessionInitCall.headers && sessionInitCall.headers["X-Profile-Id"]));

  const body = shadow.querySelector(".dk-body");
  const greeting = body.querySelector(".dk-msg-assistant");
  check("greeting message rendered", !!greeting);
  check("greeting text matches mock", greeting && greeting.textContent.indexOf("skin type") > -1);

  const chipRow = body.querySelector(".dk-chip-row");
  check("category chips rendered for new user", !!chipRow);
  check("chips include Sunscreen option",
    chipRow && chipRow.textContent.indexOf("Sunscreen") > -1);

  return { dom, window, fetchLog, shadow };
}

// ---------------------------------------------------------------------------
// Test 3 — sending a chat message streams tokens and renders chips from
// the done event's UI data
// ---------------------------------------------------------------------------

async function testChatStreamAndChips() {
  section("Test 3: chat streaming + structured chip data from done event");

  const { window, shadow } = await testExpandFlow();

  const input = shadow.querySelector(".dk-input");
  const sendBtn = shadow.querySelector(".dk-send-btn");

  input.value = "my skin is oily";
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 5));
  check("send button enabled after typing", !sendBtn.disabled);

  sendBtn.dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));

  const body = shadow.querySelector(".dk-body");
  const userMsgs = body.querySelectorAll(".dk-msg-user");
  check("user message rendered", userMsgs.length > 0);
  check("user message text correct",
    userMsgs.length > 0 && userMsgs[userMsgs.length - 1].textContent === "my skin is oily");

  const assistantMsgs = body.querySelectorAll(".dk-msg-assistant");
  const lastAssistant = assistantMsgs[assistantMsgs.length - 1];
  check("assistant response streamed in", lastAssistant.textContent.indexOf("Hello there!") > -1);

  const chipRows = body.querySelectorAll(".dk-chip-row");
  const lastChipRow = chipRows[chipRows.length - 1];
  check("new chip row rendered from done event's suggested_chips",
    !!lastChipRow && lastChipRow.textContent.indexOf("Oily") > -1);
}

// ---------------------------------------------------------------------------
// Test 4 — product page: auto-fires loadProductContext on expand, skips
// greeting + category chips, shows track order chip immediately
// ---------------------------------------------------------------------------

async function testProductPageMode() {
  section("Test 4: product page — auto-fire recommendations");

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/products/cica-niacinamide-sunscreen",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  const fetchLog = [];
  window.fetch = makeMockFetch(fetchLog);
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = () => "22222222-2222-2222-2222-222222222222";

  const widgetSrc = fs.readFileSync(path.join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);

  const host = window.document.getElementById("dk-advisor-host");
  const shadow = host.shadowRoot;
  const bubble = shadow.querySelector(".dk-bubble");

  check("product-page bubble copy differs from homepage",
    bubble.textContent.indexOf("questions about this") > -1);

  bubble.dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));

  const sessionInitCall = fetchLog.find((c) => c.url.indexOf("/session/init") > -1);
  check("/session/init was called", !!sessionInitCall);

  const productCtxCall = fetchLog.find((c) => c.url.indexOf("/context/product") > -1);
  check("/context/product auto-called on expand", !!productCtxCall);

  const body = shadow.querySelector(".dk-body");
  const msgs = Array.from(body.querySelectorAll(".dk-msg-assistant"));
  const hasGreeting = msgs.some((m) => m.textContent.indexOf("skin type") > -1);
  check("no standard greeting on product page", !hasGreeting);

  const allChipRows = body.querySelectorAll(".dk-chip-row");
  const categoryChipRow = Array.from(allChipRows).find(
    (r) => r.textContent.indexOf("Sunscreen") > -1 &&
           r.textContent.indexOf("Track") === -1 &&
           r.textContent.indexOf("Niacinamide") === -1
  );
  check("no category chips on product page", !categoryChipRow);

  const trackOrderRow = Array.from(allChipRows).find(
    (r) => r.textContent.indexOf("Track my order") > -1
  );
  check("track order chip shown on product page", !!trackOrderRow);

  const questionChipRow = Array.from(allChipRows).find(
    (r) => r.textContent.indexOf("Niacinamide") > -1
  );
  check("product question chips rendered automatically", !!questionChipRow);
}

// ---------------------------------------------------------------------------
// Test 5 — product card rendering: top picks visually distinct from rest
// ---------------------------------------------------------------------------

async function testProductCardRendering() {
  section("Test 5: product cards — top picks visually distinct");

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = () => "33333333-3333-3333-3333-333333333333";

  // custom mock: /chat returns a done event WITH top_picks + remaining
  window.fetch = function (url, opts) {
    if (typeof url === "string" && url.indexOf("/session/init") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          profile_id: "x", city: "Mumbai", season: "summer",
          is_returning: false, greeting: "Hi!", weather: {},
        }),
      });
    }
    if (typeof url === "string" && url.indexOf("/chat") > -1) {
      const encoder = new window.TextEncoder();
      const events = [
        'data: {"token": "Here are some picks."}\n\n',
        'data: {"done": true, "playbook": "recommend", ' +
        '"top_picks": [{"sku":"A","title":"Cica Sunscreen","price":445,"category":"Sunscreen",' +
        '"url":"https://www.dotandkey.com/products/cica-sunscreen",' +
        '"image_url":"https://cdn.shopify.com/files/cica.jpg"}], ' +
        '"remaining": [{"sku":"B","title":"Watermelon Sunscreen","price":445,"category":"Sunscreen",' +
        '"url":"","image_url":""}]}\n\n',
      ];
      let i = 0;
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => {
              if (i >= events.length) return Promise.resolve({ done: true });
              return Promise.resolve({ done: false, value: encoder.encode(events[i++]) });
            }
          })
        },
      });
    }
    // Shopify product .js endpoint — returns a minimal variant so cart add works.
    // .js format: top-level (no `product` wrapper), prices in paise, available boolean.
    if (typeof url === "string" && url.indexOf("/products/") > -1 && url.endsWith(".js")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          variants: [{ id: 12345678, available: true, price: 44500 }],
          images: [],
          options: [],
        }),
      });
    }
    // Shopify cart add endpoint
    if (typeof url === "string" && url.indexOf("/cart/add.js") > -1) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
    }
    return Promise.reject(new Error("unexpected: " + url));
  };

  const widgetSrc = fs.readFileSync(path.join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);

  const host = window.document.getElementById("dk-advisor-host");
  const shadow = host.shadowRoot;
  shadow.querySelector(".dk-bubble").dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 30));

  const input = shadow.querySelector(".dk-input");
  input.value = "show me sunscreens";
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  shadow.querySelector(".dk-send-btn").dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));

  const body = shadow.querySelector(".dk-body");
  const topCard = body.querySelector(".dk-card-top");
  check("top pick card rendered with highlight class", !!topCard);
  check("top pick has 'Best match' badge",
    !!topCard && topCard.textContent.indexOf("Best match") > -1);

  const allCards = body.querySelectorAll(".dk-card");
  check("both top pick and remaining product rendered as cards", allCards.length === 2);

  const plainCards = body.querySelectorAll(".dk-card:not(.dk-card-top)");
  check("remaining product card has no 'Best match' badge",
    plainCards.length === 1 && plainCards[0].textContent.indexOf("Best match") === -1);

  // ---- hyperlink + image rendering (the actual feature being tested) ----
  const topLink = topCard.querySelector(".dk-card-link");
  check("top pick card is wrapped in a real <a> hyperlink", topLink.tagName === "A");
  // After loadProductVariants runs, the href must include ?variant=id so the
  // PDP opens the exact variant shown in the card (not Shopify's arbitrary default).
  check("hyperlink includes ?variant= param so PDP opens the correct variant",
    topLink.href === "https://www.dotandkey.com/products/cica-sunscreen?variant=12345678");
  check("hyperlink opens in a new tab", topLink.target === "_blank");

  const topImg = topCard.querySelector(".dk-card-img");
  const topImgEl = topImg.querySelector("img");
  check("top pick image renders an <img> with the real image URL",
    !!(topImgEl && topImgEl.src.indexOf("cica.jpg") > -1));

  // graceful degradation: a product with no url/image_url must still
  // render as a plain (non-broken) card, not a dead link or crash
  const plainCard = plainCards[0];
  const plainLink = plainCard.querySelector(".dk-card-link");
  check("product without a url renders as a plain div, not a dead <a> tag",
    plainLink.tagName === "DIV");
  const plainImg = plainCard.querySelector(".dk-card-img");
  check("product without an image_url has no <img> child",
    !plainImg.querySelector("img"));

  // Add to cart — mocked Shopify endpoints above let the full success path run
  const cta = topCard.querySelector(".dk-card-cta");
  check("add-to-cart button starts with solid filled style (no outline class)",
    !cta.className.includes("dk-added") && cta.textContent.indexOf("Add") > -1);
  cta.dispatchEvent(new window.Event("click", { bubbles: true }));
  // async: wait for mock fetch chain to resolve
  await new Promise((r) => setTimeout(r, 50));
  check("add-to-cart shows Added after successful cart API call",
    cta.textContent.indexOf("Added") > -1 && cta.classList.contains("dk-added"));
}

// ---------------------------------------------------------------------------
// Test 6 — bursty network delivery is smoothed into gradual rendering
// (regression test for "first tokens slow, rest appear instantly")
// ---------------------------------------------------------------------------

async function testBurstySmoothing() {
  section("Test 6: typewriter queue smooths bursty SSE delivery");

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = () => "44444444-4444-4444-4444-444444444444";

  // Simulate the exact bug: a long pause (model "thinking"), then the
  // ENTIRE response arrives as ONE network read containing many SSE
  // events back-to-back — the real-world signature of a bursty stream.
  window.fetch = function (url) {
    if (typeof url === "string" && url.indexOf("/session/init") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          profile_id: "x", city: "Mumbai", season: "summer",
          is_returning: false, greeting: "Hi!", weather: {},
        }),
      });
    }
    if (typeof url === "string" && url.indexOf("/chat") > -1) {
      const encoder = new window.TextEncoder();
      const longText = "This is a much longer response with plenty of characters to " +
        "verify the queue drains gradually across multiple frames rather than " +
        "appearing all at once in a single paint.";
      // Pack the ENTIRE response into ONE chunk, delivered after a delay —
      // exactly what a "thinking then burst" stream looks like over the wire.
      const oneHugeBurst = 'data: {"token": "' + longText + '"}\n\n' +
        'data: {"done": true, "playbook": "general_qa"}\n\n';
      let delivered = false;
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => {
              if (delivered) return Promise.resolve({ done: true });
              delivered = true;
              // simulate the "long pause before the burst" with a delay
              return new Promise((resolve) => {
                setTimeout(() => {
                  resolve({ done: false, value: encoder.encode(oneHugeBurst) });
                }, 30);
              });
            },
          }),
        },
      });
    }
    return Promise.reject(new Error("unexpected: " + url));
  };

  const widgetSrc = fs.readFileSync(path.join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);

  const host = window.document.getElementById("dk-advisor-host");
  const shadow = host.shadowRoot;
  shadow.querySelector(".dk-bubble").dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));

  const input = shadow.querySelector(".dk-input");
  input.value = "tell me something";
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  shadow.querySelector(".dk-send-btn").dispatchEvent(new window.Event("click", { bubbles: true }));

  const body = shadow.querySelector(".dk-body");

  // Sample the assistant message's text length shortly after the burst
  // arrives (~30ms delay) but BEFORE the full drain would complete if it
  // were instant — if the queue is working, only a PARTIAL amount of text
  // should be visible at this point, not the full ~180-character response.
  await new Promise((r) => setTimeout(r, 60));
  const assistantMsgs = body.querySelectorAll(".dk-msg-assistant");
  const partialText = assistantMsgs[assistantMsgs.length - 1].textContent.length;

  check("text is rendering gradually, not all at once " +
    "(" + partialText + " of ~180 chars visible at 60ms)",
    partialText > 0 && partialText < 180);

  // Now wait for the full drain to complete and verify the complete text
  // eventually arrives intact.
  await new Promise((r) => setTimeout(r, 800));
  const finalText = assistantMsgs[assistantMsgs.length - 1].textContent;
  check("full response eventually renders completely",
    finalText.indexOf("multiple frames rather than") > -1);
}

// ---------------------------------------------------------------------------
// Test 7 — brand redesign: wordmark header, keyword subheadings (top
// picks only), strikethrough MRP pricing
// ---------------------------------------------------------------------------

async function testBrandRedesign() {
  section("Test 7: brand redesign — wordmark, keyword tags, strikethrough price");

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = () => "55555555-5555-5555-5555-555555555555";

  window.fetch = function (url) {
    if (typeof url === "string" && url.indexOf("/session/init") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          profile_id: "x", city: "Mumbai", season: "summer",
          is_returning: false, greeting: "Hi!", weather: {},
        }),
      });
    }
    if (typeof url === "string" && url.indexOf("/chat") > -1) {
      const encoder = new window.TextEncoder();
      const events = [
        'data: {"token": "Here are your matches."}\n\n',
        'data: {"done": true, "playbook": "recommend", ' +
        '"top_picks": [{"sku":"A","title":"Cica Sunscreen","price":595,' +
        '"compare_at_price":595,"category":"Sunscreen","url":"","image_url":"",' +
        '"promo_label":"Flat 15% OFF",' +
        '"keywords":["Anti-acne","Lightweight","Niacinamide"]}], ' +
        '"remaining": [{"sku":"B","title":"Watermelon Sunscreen","price":445,' +
        '"compare_at_price":445,"category":"Sunscreen","url":"","image_url":"",' +
        '"promo_label":"Upto 20% OFF + Free Gifts"},' +
        '{"sku":"C","title":"Vitamin C Sunscreen","price":399,' +
        '"compare_at_price":399,"category":"Sunscreen","url":"","image_url":""}]}\n\n',
      ];
      let i = 0;
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => {
              if (i >= events.length) return Promise.resolve({ done: true });
              return Promise.resolve({ done: false, value: encoder.encode(events[i++]) });
            }
          })
        },
      });
    }
    return Promise.reject(new Error("unexpected: " + url));
  };

  const widgetSrc = fs.readFileSync(path.join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);

  const host = window.document.getElementById("dk-advisor-host");
  const shadow = host.shadowRoot;

  // ---- wordmark header ----
  // Logo is now an SVG (innerHTML contains "DOT & KEY") or an <img> from the host page
  const wordmark = shadow.querySelector(".dk-wordmark, .dk-header-top img, .dk-header-top svg");
  check("wordmark element exists", !!wordmark);
  check("wordmark contains DOT & KEY branding",
    !!(wordmark && (
      wordmark.textContent.toLowerCase().indexOf("dot") > -1 ||
      (wordmark.innerHTML && wordmark.innerHTML.indexOf("DOT") > -1) ||
      wordmark.tagName === "IMG"
    )));

  const headerSub = shadow.querySelector(".dk-header-sub");
  check("header subtitle present", !!headerSub && headerSub.textContent.length > 0);

  // ---- trigger a recommend response with keyword + price data ----
  shadow.querySelector(".dk-bubble").dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 30));

  const input = shadow.querySelector(".dk-input");
  input.value = "show me sunscreens";
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  shadow.querySelector(".dk-send-btn").dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));

  const body = shadow.querySelector(".dk-body");

  // ---- keyword subheading: present on top pick, absent on remaining ----
  const topCard = body.querySelector(".dk-card-top");
  const topKeywords = topCard.querySelector(".dk-card-keywords");
  check("top pick shows a keyword subheading", !!topKeywords);
  check("keyword subheading text matches the 3 tags, dot-separated",
    topKeywords.textContent === "Anti-acne \u00B7 Lightweight \u00B7 Niacinamide");

  const plainCard = body.querySelector(".dk-card:not(.dk-card-top)");
  const plainKeywords = plainCard.querySelector(".dk-card-keywords");
  check("remaining product has NO keyword subheading (top-3-only rule)",
    !plainKeywords);

  // ---- strikethrough pricing from promo_label ----
  // Top pick: price=595, promo_label="Flat 15% OFF" \u2192 sale=506, strike=595
  const topPriceRow = topCard.querySelector(".dk-card-price-row");
  const topPrice = topPriceRow.querySelector(".dk-card-price");
  const topStrike = topPriceRow.querySelector(".dk-card-price-strike");
  check("promo_label sale price shown (Flat 15% OFF on \u20B9595 = \u20B9506)",
    topPrice.textContent === "\u20B9506");
  check("promo_label strikethrough MRP shown",
    !!topStrike && topStrike.textContent === "\u20B9595");

  // Remaining card with promo: price=445, promo_label="Upto 20% OFF" \u2192 sale=356, strike=445
  const restCards = Array.from(body.querySelectorAll(".dk-card:not(.dk-card-top)"));
  const promoRestCard = restCards[0];
  const promoRestPrice = promoRestCard.querySelector(".dk-card-price");
  const promoRestStrike = promoRestCard.querySelector(".dk-card-price-strike");
  check("remaining card promo sale price shown (20% OFF on \u20B9445 = \u20B9356)",
    promoRestPrice.textContent === "\u20B9356");
  check("remaining card promo strikethrough shown",
    !!promoRestStrike && promoRestStrike.textContent === "\u20B9445");

  // Remaining card without promo: no strikethrough
  const plainCard2 = restCards[1];
  const plainPriceRow = plainCard2 ? plainCard2.querySelector(".dk-card-price-row") : null;
  const plainStrike = plainPriceRow ? plainPriceRow.querySelector(".dk-card-price-strike") : null;
  check("no strikethrough shown when compare_at_price equals price (no real discount)",
    !plainStrike);
}

// ---------------------------------------------------------------------------
// Test 8 — tint shade accuracy
//
// Regression: before the fix, loadProductVariants picked the first available
// Shopify variant as the "default" but never (a) updated the card image to
// that variant's packshot, or (b) added ?variant=id to the product link.
// The result: card showed one shade's image while the CTA and PDP linked to
// a different shade — purchasing wrong shade was trivially reproducible.
//
// After the fix, image ↔ swatch ↔ ?variant= link are always consistent:
//   - loadProductVariants synchronises all three on initial render
//   - Clicking a swatch updates all three atomically
// ---------------------------------------------------------------------------

async function testTintShadeAccuracy() {
  section("Test 8: tint shade accuracy — image/URL/swatch must always match");

  // Mirrors real Shopify structure: variants have image_id; product.images maps
  // image_id → CDN src. featured_image is always null in DK's storefront JSON.
  // .js format: featured_image.src directly on variant (not image_id lookup),
  // prices in paise (29900 = ₹299), available is a boolean.
  const VARIANTS = [
    { id: 2001, title: "Warm Nude",       available: true,  price: 29900, compare_at_price: null,
      featured_image: { src: "https://cdn.example.com/warm-nude.jpg",   id: 9101 } },
    { id: 2002, title: "Strawberry Crush", available: true,  price: 29900, compare_at_price: null,
      featured_image: { src: "https://cdn.example.com/strawberry.jpg",  id: 9102 } },
    { id: 2003, title: "Berry Pink",       available: false, price: 29900, compare_at_price: null,
      featured_image: { src: "https://cdn.example.com/berry.jpg",       id: 9103 } },
  ];

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = () => "tint-test-uuid";

  window.fetch = function (url) {
    if (url.indexOf("/session/init") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          profile_id: "x", city: "Mumbai", season: "summer",
          is_returning: false, greeting: "Hi!", weather: {},
        }),
      });
    }
    if (url.indexOf("/products/vc-lip-balm.js") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          id: 999,
          title: "Vitamin C + E Lip Balm",
          variants: VARIANTS,
          images: [],   // .js images are URL strings; featured_image.src on variants is used instead
          options: [{ name: "Shade" }],
        }),
      });
    }
    return Promise.reject(new Error("unexpected fetch: " + url));
  };

  const widgetSrc = fs.readFileSync(path.join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);

  const internal = window.__dkAdvisor._internal;

  // Build a minimal card DOM that loadProductVariants operates on —
  // mirrors what renderProductCard produces for a product with a URL and image.
  const doc = window.document;
  const card = doc.createElement("div");
  card.className = "dk-card";

  const linkEl = doc.createElement("a");
  linkEl.className = "dk-card-link";
  linkEl.href = "https://www.dotandkey.com/products/vc-lip-balm";
  linkEl.target = "_blank";

  const imgBox = doc.createElement("div");
  imgBox.className = "dk-card-img";
  const imgEl = doc.createElement("img");
  imgEl.src = "https://cdn.example.com/graph-ingest-image.jpg";  // graph-stored image
  imgBox.appendChild(imgEl);
  linkEl.appendChild(imgBox);
  card.appendChild(linkEl);

  const priceEl = doc.createElement("span");
  priceEl.className = "dk-card-price";
  priceEl.textContent = "₹299";
  card.appendChild(priceEl);

  const ctaBtn = doc.createElement("button");
  ctaBtn.className = "dk-card-cta";
  ctaBtn.textContent = "Add to cart";
  card.appendChild(ctaBtn);

  doc.body.appendChild(card);

  // ── before variant load ──────────────────────────────────────────────────
  check("card image starts as graph-stored image (before variant load)",
    imgEl.src === "https://cdn.example.com/graph-ingest-image.jpg");
  check("link starts without ?variant param",
    linkEl.href === "https://www.dotandkey.com/products/vc-lip-balm");

  // ── trigger loadProductVariants ──────────────────────────────────────────
  internal.loadProductVariants("vc-lip-balm", card, ctaBtn);
  await new Promise((r) => setTimeout(r, 50));

  // Default variant = first AVAILABLE = Warm Nude (id 2001).
  // Berry Pink is skipped because available:false.
  check("default variant is first available (Warm Nude, not out-of-stock Berry Pink)",
    ctaBtn._variantId === 2001);

  // _variantImgSrc appends ?width=400 for the card hero image
  check("card image updated to default variant packshot (Warm Nude)",
    imgEl.src === "https://cdn.example.com/warm-nude.jpg?width=400");

  check("link URL includes ?variant= for the default variant (Warm Nude)",
    linkEl.href === "https://www.dotandkey.com/products/vc-lip-balm?variant=2001");

  // Berry Pink is available=false — shown with dk-swatch-oos class but not hidden.
  // All 3 variants get swatches (OOS shown at full opacity, "Sold out" when selected).
  const swatches = card.querySelectorAll(".dk-swatch");
  check("all three swatches rendered including OOS Berry Pink", swatches.length === 3);

  const selectedSwatches = card.querySelectorAll(".dk-swatch-selected");
  check("exactly one swatch is pre-selected", selectedSwatches.length === 1);
  check("pre-selected swatch title is 'Warm Nude'",
    selectedSwatches[0].title === "Warm Nude");

  // ── simulate user clicking Strawberry Crush ──────────────────────────────
  const strawberrySwatch = Array.from(swatches).find((s) => s.title === "Strawberry Crush");
  check("Strawberry Crush swatch exists", !!strawberrySwatch);

  strawberrySwatch.dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 10));

  check("after clicking Strawberry Crush — variant ID updated",
    ctaBtn._variantId === 2002);

  check("after clicking Strawberry Crush — card image updated to strawberry packshot",
    imgEl.src === "https://cdn.example.com/strawberry.jpg?width=400");

  check("after clicking Strawberry Crush — link URL updated to ?variant=2002",
    linkEl.href === "https://www.dotandkey.com/products/vc-lip-balm?variant=2002");

  check("after clicking Strawberry Crush — only Strawberry swatch is selected",
    card.querySelectorAll(".dk-swatch-selected").length === 1 &&
    card.querySelector(".dk-swatch-selected").title === "Strawberry Crush");

  // ── mismatch regression: verify the old broken state is prevented ────────
  // Before the fix, clicking Strawberry Crush would update the CTA variant ID
  // but leave the card image as Warm Nude and the link without ?variant=.
  // The checks above already prove that can't happen, but make the contract
  // explicit by asserting the image and URL are NEVER stale after a swatch click.
  check("image and URL are consistent after swatch change (no stale Warm Nude image)",
    imgEl.src !== "https://cdn.example.com/warm-nude.jpg?width=400");
  check("image and URL are consistent after swatch change (no bare URL without variant)",
    linkEl.href.indexOf("?variant=") > -1);

  // ── single-variant product also gets a ?variant= link ───────────────────
  const cardSv = doc.createElement("div");
  cardSv.className = "dk-card";
  const linkSv = doc.createElement("a");
  linkSv.className = "dk-card-link";
  linkSv.href = "https://www.dotandkey.com/products/cica-cream";
  cardSv.appendChild(linkSv);
  const ctaSv = doc.createElement("button");
  ctaSv.className = "dk-card-cta";
  cardSv.appendChild(ctaSv);
  doc.body.appendChild(cardSv);

  window.fetch = function (url) {
    if (url.indexOf("/products/cica-cream.js") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          variants: [{ id: 3001, available: true, price: 59500, featured_image: null }],
          images: [], options: [],
        }),
      });
    }
    return Promise.reject(new Error("unexpected fetch: " + url));
  };

  internal.loadProductVariants("cica-cream", cardSv, ctaSv);
  await new Promise((r) => setTimeout(r, 50));

  check("single-variant product: ?variant= is added to the link",
    linkSv.href === "https://www.dotandkey.com/products/cica-cream?variant=3001");
  check("single-variant product: ctaBtn._variantId is set",
    ctaSv._variantId === 3001);

  // ── variant hint: recommended shade drives pre-selection ─────────────────
  // Root-cause regression: p.variant (graph shade name) was not in the
  // retrieval RETURN clause, so the widget always picked Shopify variant[0]
  // instead of the shade the recommendation intended.
  //
  // After the fix, renderProductCard stores product.variant on cta._variantHint
  // and loadProductVariants matches it against Shopify titles to pre-select
  // the correct swatch, independent of Shopify's listing order.
  const cardHint = doc.createElement("div");
  cardHint.className = "dk-card";
  const linkHint = doc.createElement("a");
  linkHint.className = "dk-card-link";
  linkHint.href = "https://www.dotandkey.com/products/ceramide-lip-balm";
  const imgBoxHint = doc.createElement("div");
  imgBoxHint.className = "dk-card-img";
  const imgHint = doc.createElement("img");
  imgHint.src = "https://cdn.example.com/generic.jpg";
  imgBoxHint.appendChild(imgHint);
  linkHint.appendChild(imgBoxHint);
  cardHint.appendChild(linkHint);
  const ctaHint = doc.createElement("button");
  ctaHint.className = "dk-card-cta";
  // Simulate renderProductCard stashing the recommended shade
  ctaHint._variantHint = "Red Romance";
  cardHint.appendChild(ctaHint);
  doc.body.appendChild(cardHint);

  const HINT_VARIANTS = [
    { id: 4001, title: "Warm Nude",   available: true, price: 24900, compare_at_price: null,
      featured_image: { src: "https://cdn.example.com/warm-nude-hint.jpg",   id: 8001 } },
    { id: 4002, title: "Plush Pink",  available: true, price: 24900, compare_at_price: null,
      featured_image: { src: "https://cdn.example.com/plush-pink-hint.jpg",  id: 8002 } },
    { id: 4003, title: "Red Romance", available: true, price: 24900, compare_at_price: null,
      featured_image: { src: "https://cdn.example.com/red-romance-hint.jpg", id: 8003 } },
  ];

  window.fetch = function (url) {
    if (url.indexOf("/products/ceramide-lip-balm.js") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          variants: HINT_VARIANTS,
          images: [],
          options: [{ name: "Color" }],
        }),
      });
    }
    return Promise.reject(new Error("unexpected fetch: " + url));
  };

  internal.loadProductVariants("ceramide-lip-balm", cardHint, ctaHint);
  await new Promise((r) => setTimeout(r, 50));

  check("variant hint: recommended shade 'Red Romance' is pre-selected (not Shopify variant[0] Warm Nude)",
    ctaHint._variantId === 4003);

  check("variant hint: link URL points to ?variant=4003 (Red Romance)",
    linkHint.href === "https://www.dotandkey.com/products/ceramide-lip-balm?variant=4003");

  const hintSwatches = cardHint.querySelectorAll(".dk-swatch");
  check("variant hint: all 3 shade swatches rendered", hintSwatches.length === 3);

  const hintSelected = cardHint.querySelector(".dk-swatch-selected");
  check("variant hint: pre-selected swatch title is 'Red Romance'",
    !!hintSelected && hintSelected.title === "Red Romance");

  check("variant hint: 'Warm Nude' swatch is NOT selected (would have been pre-selected without the fix)",
    Array.from(hintSwatches).find((s) => s.title === "Warm Nude") &&
    !Array.from(hintSwatches).find((s) => s.title === "Warm Nude").classList.contains("dk-swatch-selected"));

  // Whitespace normalisation: a double-space in the graph ('Warm Ivory  - 02')
  // must still match Shopify's 'Warm Ivory  - 02' after normalise().
  const cardWS = doc.createElement("div");
  cardWS.className = "dk-card";
  const linkWS = doc.createElement("a");
  linkWS.className = "dk-card-link";
  linkWS.href = "https://www.dotandkey.com/products/tinted-spf";
  cardWS.appendChild(linkWS);
  const ctaWS = doc.createElement("button");
  ctaWS.className = "dk-card-cta";
  ctaWS._variantHint = "Warm Ivory  - 02 Light Medium";   // double-space as in graph
  cardWS.appendChild(ctaWS);
  doc.body.appendChild(cardWS);

  window.fetch = function (url) {
    if (url.indexOf("/products/tinted-spf.js") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          variants: [
            { id: 5001, title: "Peony - 00 Very Light",         available: true, price: 54900, compare_at_price: null,
              featured_image: { src: "https://cdn.example.com/peony.jpg",      id: 7001 } },
            { id: 5002, title: "Warm Ivory  - 02 Light Medium", available: true, price: 54900, compare_at_price: null,
              featured_image: { src: "https://cdn.example.com/warm-ivory.jpg", id: 7002 } },
            { id: 5003, title: "Sand - 03 Medium",              available: true, price: 54900, compare_at_price: null,
              featured_image: { src: "https://cdn.example.com/sand.jpg",       id: 7003 } },
          ],
          images: [],
          options: [{ name: "Color" }],
        }),
      });
    }
    return Promise.reject(new Error("unexpected fetch: " + url));
  };

  internal.loadProductVariants("tinted-spf", cardWS, ctaWS);
  await new Promise((r) => setTimeout(r, 50));

  check("whitespace normalisation: 'Warm Ivory  - 02' (double-space) still matches Shopify variant",
    ctaWS._variantId === 5002);
  check("whitespace normalisation: link URL points to ?variant=5002",
    linkWS.href === "https://www.dotandkey.com/products/tinted-spf?variant=5002");
}

// ---------------------------------------------------------------------------
// Test 9 — shade colour registry
//
// Swatches are solid colour circles.  The registry is driven by Shopify
// variant titles so no product images appear inside swatches.
//
// Verifies:
//   1. Every known DK shade title resolves to a non-default, non-generic colour.
//   2. Every shade within a product family has a distinct colour (no collisions).
//   3. The old substring bug ("berry" matching "strawberry") is gone — word
//      boundaries are used in the fallback palette.
//   4. Descriptor words ("medium", "light", "deep") no longer produce false
//      colour collisions.
//   5. The card hero image is STILL updated from the Shopify variant image
//      (image_id lookup), while the swatch background is the shade colour.
// ---------------------------------------------------------------------------

// jsdom normalises background hex values to "rgb(r, g, b)" — this helper
// converts "#rrggbb" to the same string so assertions can compare directly.
function hexToRgb(hex) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgb(${r}, ${g}, ${b})`;
}

async function testShadeRegistry() {
  section("Test 9: shade colour registry — clean colour circles, no product thumbnails");

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  window.crypto = { randomUUID: () => "shade-reg-uuid" };
  window.fetch = (url) => {
    if (url.indexOf("/session/init") > -1) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        profile_id: "x", city: "Mumbai", season: "summer",
        is_returning: false, greeting: "Hi!", weather: {},
      })});
    }
    return Promise.reject(new Error("unexpected boot fetch: " + url));
  };
  const widgetSrc = require("fs").readFileSync(require("path").join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);
  const internal = window.__dkAdvisor._internal;
  const doc = window.document;

  // ── 1. Colour accuracy + no-collision checks for every DK shade family ────

  const FAMILIES = {
    "F1 Tinted SPF": [
      ["Peony - 00 Very Light",                    "#f2d5ce"],
      ["Porcelain - 01 Light",                     "#f0ddd0"],
      ["Warm Ivory  - 02 Light Medium",            "#e8c898"],   // double-space as in Shopify
      ["Sand - 03 Medium",                         "#c8a06a"],
      ["Rose - 01A Light with Neutral Undertone",  "#e8c4b8"],
      ["Beige - 05 Medium Deep",                   "#a87848"],
      ["Caramel - 07  Deep",                       "#7a4a28"],   // double-space as in Shopify
    ],
    "F2 Ceramide Lip Balm": [
      ["Warm Nude",   "#d4926a"],
      ["Plush Pink",  "#e87898"],
      ["Red Romance", "#c83048"],
    ],
    "F3 Hydrating Lip Balm": [
      ["Strawberry Red - High Tinted",   "#e03850"],
      ["Cherry Crimson - High Tinted",   "#a82038"],
      ["Cocoa Nude - Medium Tinted",     "#7b4a32"],
      ["Blueberry Bliss - Non-Tinted",   "#ede8f2"],  // non-tinted: pale lavender-grey
    ],
    "F4 Gloss Boss": [
      ["Strawberry Crush High Tinted",   "#e04858"],
      ["Cherry Pop Medium Tinted",       "#d83058"],
      ["Watermelon Cool Medium Tinted",  "#e87890"],
      ["Cocoa Mint Low Tinted",          "#6b4228"],
      ["Watermelon Rush High Tinted",    "#c83860"],
    ],
    "F5 Meltie": [
      ["Strawberry Glaze", "#e05068"],
      ["Berry Crumble",    "#7a2858"],
    ],
  };

  const DEFAULT_BROWN = hexToRgb("#d0a080");

  for (const [family, shades] of Object.entries(FAMILIES)) {
    function makeColorCard(href) {
      const card = doc.createElement("div");
      card.className = "dk-card";
      const link = doc.createElement("a");
      link.className = "dk-card-link";
      link.href = href;
      const imgBox = doc.createElement("div");
      imgBox.className = "dk-card-img";
      const img = doc.createElement("img");
      img.src = "https://cdn.example.com/graph.jpg";
      imgBox.appendChild(img);
      link.appendChild(imgBox);
      card.appendChild(link);
      const cta = doc.createElement("button");
      cta.className = "dk-card-cta";
      card.appendChild(cta);
      doc.body.appendChild(card);
      return { card, cta };
    }

    const { card, cta } = makeColorCard("https://www.dotandkey.com/products/test-color");
    const variants = shades.map(([title], i) => ({
      id: 6000 + i, title, available: true,
      price: 29900, compare_at_price: null, featured_image: null,
    }));

    window.fetch = (url) => {
      if (url.indexOf("/products/test-color.js") > -1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({
          variants, images: [], options: [{ name: "Color" }],
        })});
      }
      return Promise.reject(new Error("unexpected: " + url));
    };

    internal.loadProductVariants("test-color", card, cta);
    await new Promise((r) => setTimeout(r, 60));

    const swatches = Array.from(card.querySelectorAll(".dk-swatch"));
    check(`${family}: swatch count matches variant count (${shades.length})`,
      swatches.length === shades.length);

    const seenColors = {};
    shades.forEach(([title, expectedHex], i) => {
      const sw = swatches[i];
      // jsdom normalises "#rrggbb" → "rgb(r, g, b)" on read-back
      const bg = sw.style.background || "";
      const expectedRgb = hexToRgb(expectedHex);

      check(`${family} — "${title}": exact shade colour is ${expectedHex} (${expectedRgb})`,
        bg === expectedRgb);

      check(`${family} — "${title}": not the generic default fallthrough`,
        bg !== DEFAULT_BROWN);

      check(`${family} — "${title}": colour is distinct within family`,
        !seenColors[bg]);
      seenColors[bg] = title;

      // Regression: "berry" used to match inside "straw*berry*"
      if (title.toLowerCase().indexOf("strawberry") > -1) {
        const PURPLE = hexToRgb("#8b3a6b");
        check(`${family} — "${title}": NOT purple (old berry-matches-strawberry regression)`,
          bg !== PURPLE);
      }
      // Regression: "medium"/"high" tint-level descriptors used to cause collisions
      if (title.toLowerCase().indexOf("medium") > -1 ||
          title.toLowerCase().indexOf("high") > -1) {
        const OLD_TAN = hexToRgb("#c4956a");
        check(`${family} — "${title}": NOT old descriptor-collision tan`,
          bg !== OLD_TAN);
      }
    });

    doc.body.removeChild(card);
  }

  // ── 2. Card hero image updates from featured_image.src (not swatch colour)
  // .js format: featured_image.src directly on variant, images is string array.
  const HERO_VARIANTS = [
    { id: 801, title: "Warm Nude",  available: true, price: 24900, compare_at_price: null,
      featured_image: { src: "https://cdn.shopify.com/s/files/warm-nude-hero.jpg", id: 901 } },
    { id: 802, title: "Plush Pink", available: true, price: 24900, compare_at_price: null,
      featured_image: { src: "https://cdn.shopify.com/s/files/plush-pink-hero.jpg", id: 902 } },
  ];

  const heroCard = doc.createElement("div");
  heroCard.className = "dk-card";
  const heroLink = doc.createElement("a");
  heroLink.className = "dk-card-link";
  heroLink.href = "https://www.dotandkey.com/products/hero-test";
  const heroImgBox = doc.createElement("div");
  heroImgBox.className = "dk-card-img";
  const heroImg = doc.createElement("img");
  heroImg.src = "https://cdn.example.com/graph-initial.jpg";
  heroImgBox.appendChild(heroImg);
  heroLink.appendChild(heroImgBox);
  heroCard.appendChild(heroLink);
  const heroCta = doc.createElement("button");
  heroCta.className = "dk-card-cta";
  heroCard.appendChild(heroCta);
  doc.body.appendChild(heroCard);

  window.fetch = (url) => {
    if (url.indexOf("/products/hero-test.js") > -1) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        variants: HERO_VARIANTS, images: [], options: [{ name: "Color" }],
      })});
    }
    return Promise.reject(new Error("unexpected: " + url));
  };
  internal.loadProductVariants("hero-test", heroCard, heroCta);
  await new Promise((r) => setTimeout(r, 60));

  const heroSwatches = Array.from(heroCard.querySelectorAll(".dk-swatch"));

  // Swatches must be solid colours (background property), not images
  // jsdom normalises hex → rgb on readback; check the rgb equivalent of #d4926a.
  // When background shorthand is set, jsdom also sets backgroundImage="none" (not a url).
  const swBg = heroSwatches[0].style.background;
  const swBgImg = heroSwatches[0].style.backgroundImage;
  check("hero-test: swatch[0] background is shade colour (not a CDN url())",
    swBg === hexToRgb("#d4926a") &&
    (swBgImg === "" || swBgImg === "none"));

  // But hero image IS updated from image_id → product.images
  check("hero-test: card hero image updated to variant packshot at ?width=400",
    heroImg.src === "https://cdn.shopify.com/s/files/warm-nude-hero.jpg?width=400");

  // Clicking second swatch updates hero image AND variant ID
  heroSwatches[1].dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 10));
  check("hero-test: clicking Plush Pink updates hero image",
    heroImg.src === "https://cdn.shopify.com/s/files/plush-pink-hero.jpg?width=400");
  check("hero-test: clicking Plush Pink updates ?variant= link",
    heroLink.href.endsWith("?variant=802"));
  check("hero-test: Plush Pink swatch is now selected",
    heroSwatches[1].classList.contains("dk-swatch-selected"));
}

// ---------------------------------------------------------------------------
// Test 10 — "Track my order" hyperlink chip: rendered on homepage open AND
// surfaced again in chat responses to tracking questions. Must be a real
// <a href target=_blank> (not a chip that sends a chat message), so a tap
// goes straight to ClickPost instead of round-tripping through /chat.
// ---------------------------------------------------------------------------

async function testTrackOrderLinkChip() {
  section("Test 10: track-order hyperlink chip");

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://www.dotandkey.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  const fetchLog = [];

  function mockFetch(url, opts) {
    fetchLog.push({ url: url, method: opts && opts.method });
    if (typeof url === "string" && url.indexOf("/session/init") > -1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          profile_id: "test-id", city: "Mumbai", season: "summer",
          is_returning: false, greeting: "Hey!", weather: { temp: 30, humidity: 60 },
          initial_chips: { field: "category", multi_select: false, options: [{ value: "sunscreen", label: "Sunscreen" }] },
          returning_chips: { field: "returning_check", multi_select: false, options: [{ value: "same", label: "Same as before" }] },
          track_order: { label: "Track my order", url: "https://dotandkey.clickpost.ai/" },
        }),
      });
    }
    if (typeof url === "string" && url.indexOf("/chat") > -1) {
      const encoder = new TextEncoder();
      const events = [
        'data: {"token": "You can track your order below."}\n\n',
        'data: {"done": true, "playbook": "track_order", ' +
        '"link_chips": [{"label": "Track my order", "url": "https://dotandkey.clickpost.ai/"}]}\n\n',
      ];
      let i = 0;
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => {
              if (i >= events.length) return Promise.resolve({ done: true });
              return Promise.resolve({ done: false, value: encoder.encode(events[i++]) });
            },
          }),
        },
      });
    }
    return Promise.reject(new Error("unexpected fetch: " + url));
  }

  window.fetch = mockFetch;
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = () => "33333333-3333-3333-3333-333333333333";

  const widgetSrc = fs.readFileSync(path.join(__dirname, "widget.js"), "utf8");
  window.eval(widgetSrc);

  const host = window.document.getElementById("dk-advisor-host");
  const shadow = host.shadowRoot;
  const bubble = shadow.querySelector(".dk-bubble");
  bubble.dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 50));

  const body = shadow.querySelector(".dk-body");

  // Homepage opening: link chip rendered alongside the entry chips
  const openingLinkChip = Array.from(body.querySelectorAll("a.dk-chip-link"))
    .find((a) => a.textContent === "Track my order");
  check("homepage: 'Track my order' link chip rendered on open",
    !!openingLinkChip);
  check("homepage: link chip href is the real ClickPost URL",
    !!openingLinkChip && openingLinkChip.href === "https://dotandkey.clickpost.ai/");
  check("homepage: link chip opens in a new tab",
    !!openingLinkChip && openingLinkChip.target === "_blank");
  check("homepage: link chip has rel=noopener (no window.opener leak)",
    !!openingLinkChip && openingLinkChip.rel.indexOf("noopener") > -1);
  check("link chip is a real <a> tag, not a chat-sending div",
    !!openingLinkChip && openingLinkChip.tagName === "A");

  // User explicitly asks — chat response's link_chips also renders one
  const input = shadow.querySelector(".dk-input");
  const sendBtn = shadow.querySelector(".dk-send-btn");
  input.value = "where is my order";
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 5));
  sendBtn.dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));

  const linkChipsAfterChat = Array.from(body.querySelectorAll("a.dk-chip-link"))
    .filter((a) => a.textContent === "Track my order");
  check("chat: asking 'where is my order' surfaces a second link chip from link_chips",
    linkChipsAfterChat.length >= 2);
}

// ---------------------------------------------------------------------------
// Run all
// ---------------------------------------------------------------------------

(async function main() {
  try {
    await testHomepageBoot();
    await testChatStreamAndChips();   // also exercises testExpandFlow internally
    await testProductPageMode();
    await testProductCardRendering();
    await testBurstySmoothing();
    await testBrandRedesign();
    await testTintShadeAccuracy();
    await testShadeRegistry();
    await testTrackOrderLinkChip();
  } catch (err) {
    console.error("\nFATAL ERROR during test run:", err);
    failed++;
  }

  console.log("\n" + "=".repeat(50));
  console.log(passed + " passed, " + failed + " failed");
  process.exit(failed > 0 ? 1 : 0);
})();