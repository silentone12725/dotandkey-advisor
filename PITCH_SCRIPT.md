# Pitch video script — Dot & Key Skin Advisor

**Runtime:** 5:00 · ~780 spoken words at ~155 wpm, with room to pause on screen actions.

**Audience:** the brand (as a prospective adopter) and engineers evaluating the
build. The demo carries the first; three short architecture asides carry the second.

---

## [0:00 – 0:25] Cold open — the problem

*Screen: dotandkey.com homepage, no widget. Scroll the product grid slowly.*

> Skincare is the hardest thing to buy online. A hundred and sixty products,
> and the honest answer to "which one is for me" depends on your skin type,
> your concerns, what you react to, what you can spend — and none of that fits
> in a search box.
>
> So most brands bolt a chatbot onto the site. And the chatbot confidently
> recommends a product that doesn't exist, or describes a benefit the product
> doesn't have. That's worse than no chatbot.
>
> This is the version that can't do that. Let me show you.

---

## [0:25 – 2:10] The demo — the actual journey

*Screen: the collapsed bubble in the corner, breathing glow. Click it.*

> This is running on the live Dot & Key storefront. The widget reads the page
> it's on — on the homepage it says "Find your perfect routine," on a sunscreen
> page it offers to find you the best sunscreen. It pulls the site's own logo
> at runtime, so it looks like it shipped with the store.

*Panel opens, greeting streams in.*

> First thing it did before saying hello: resolved my IP to my city, pulled
> live weather, and mapped that to one of four Indian seasons — summer,
> monsoon, post-monsoon, winter. That's a real retrieval filter, not
> decoration. What works in Mumbai in August is not what works in Delhi in
> January.

*Tap through chips: Sunscreen → Oily → Fragrance-free → Under ₹600 → Standard.*

> Now the intake. Six questions, fixed order, tappable chips — category, skin
> type, what to avoid, budget, size, then concerns. Notice I'm not typing.
> Every one of these questions is hardcoded. Zero LLM calls. It's the cheapest,
> fastest, most reliable part of the funnel, so there's no reason to let a
> model near it.

*Type instead: "I break out from anything with fragrance."*

> But free text works too — and this sentence just did something permanent.

*Results render.*

> It set a sensitivity flag on my profile. Not for this session — for sixty
> days. Every future search now boosts fragrance-free products, whether or not
> I mention it again.
>
> And here are the results. Two highlighted picks, then everything else. Look
> at the tags under the top cards — "Anti-acne, Lightweight, Niacinamide."

*Pause on the tags.*

> Those aren't written by a language model. They're pulled straight off graph
> edges. The model cannot invent a quality this product doesn't have, because
> the model never touched that text.

*Tap a shade swatch on a tinted sunscreen. Price and image update.*

> Variants load live from Shopify — real stock, real price. And it pre-selects
> the shade the graph recommended, not whatever Shopify happened to list first.

*Click "Add to cart." The site's own cart drawer opens; the widget slides left.*

> Add to cart is the real cart. The site's own drawer opens, and the widget
> gets out of the way.

---

## [2:10 – 3:10] The architectural bet

*Screen: split — `retrieval.py` on one side, `prompts/recommend.md` on the other.*

> Here's the decision the whole thing rests on.
>
> The language model never chooses which products you see. That's a
> deterministic Cypher query against a FalkorDB knowledge graph. Scoring is a
> formula I can show you.

*Highlight: `final_score = qi×80 + skin×30 + concern×25 + allergen×25 ± sensitivity ± behavioral`*

> Explicit query intent dominates everything. If you say "niacinamide," you get
> niacinamide — memory and learned behavior can't outvote you.
>
> The model's entire job is this file.

*Highlight the prompt: "Write ONE short line — that's the entire response."*

> One sentence. That's it. It's a phrasing layer, not a decision layer.
>
> Routing works the same way — a keyword fast path resolves most turns in under
> a millisecond, and the model only gets called when a message is genuinely
> ambiguous. Nine fixed playbooks, no open-ended tool loop.
>
> This started as a normal agent. Live testing killed that: the prose was long,
> repetitive, and occasionally wrong. Narrowing what the model is allowed to do
> made the product better and cheaper at the same time.

---

## [3:10 – 4:10] What the graph buys you

*Screen: the graph schema, then a quick FalkorDB query returning ingredient→concern edges.*

> The graph is the moat. Sixty-nine ingredient-to-concern relationships, each
> with strength and confidence. Ten capability axes scored per product — oil
> control, barrier repair, brightening, and so on. Products linked to each
> other with typed edges: more hydrating than, gentler than, budget alternative
> to, fragrance-free alternative to.
>
> That last set is why "show me a cheaper version of this" is a graph
> traversal, not a prompt and a prayer.
>
> On top of that sits behavioral learning. A purchase is worth ten points, a
> click two, a rejection minus eight — decayed over time, so last year's taste
> doesn't outrank this month's.

*Screen: the comparison engine's output as JSON or terminal text.*

> The same graph answers the harder questions. Ask why something is ranked
> first, or what you give up by taking the cheaper one, and it comes back with
> "Best oil control in sunscreens, number one of twenty-three" — computed from
> category-relative rankings across the whole catalog. Zero model calls. Every
> claim traces to an edge, so the reasoning is auditable line by line.

---

## [4:10 – 4:40] Deployment

*Screen: dotandkey.com with the widget on it. Then briefly, the proxy code.*

> One more thing worth seeing. Everything I just showed you was running on the
> genuine Dot & Key site. Not a mockup, not a staging clone.
>
> There's a reverse proxy that serves the real storefront and injects the
> widget before the closing body tag — and a Chrome extension that does the
> same thing client-side. Which means this can be evaluated on the live site
> with zero integration work from the brand's side. Nothing to deploy, no
> engineering ticket, no risk to the storefront.
>
> The widget itself is a single vanilla JavaScript file in a Shadow DOM. No
> build step, no framework, no CSS collisions with the host page.

---

## [4:40 – 5:00] Close

*Screen: `pytest` running — 973 passed.*

> Nine hundred and seventy-three backend tests, and the rule on this project is
> that nothing found in live testing gets fixed without a regression test.
>
> Deterministic retrieval, a model that only writes one sentence, and memory
> that survives the session. That's the whole idea.
>
> Happy to walk through any layer of it.

---

## Recording notes

- **Do the demo live, don't narrate stills.** The chip taps, the shade swatch,
  and the cart drawer sliding are the three moments that sell it.
- **Have the graph running before you hit record.** A failed Cypher query on
  camera costs more than the segment is worth.
- **Pause on the keyword tags** (`0:25–2:10` block). It's the single strongest
  proof point and it's easy to skim past.
- **Pre-warm the `pytest` run** so it finishes inside the closing lines.
- Cut the season-detection line first if you're running long — it's the most
  interesting fact that isn't load-bearing.

## Before recording

Confirm the demo path works end to end:

```bash
./scripts/check_dev.fish
```

Then run the suite so the closing shot is accurate:

```bash
pytest -q
```

If the count has drifted from 973, update the closing line to match.
