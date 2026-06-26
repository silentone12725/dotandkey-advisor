# Dot & Key Skin Advisor

A retrieval-grounded conversational product advisor for [dotandkey.com](https://www.dotandkey.com) — an Indian skincare D2C brand. Runs as an embeddable widget and as a Chrome extension that injects directly onto the real site.

## Architecture

```
graph/          FalkorDB schema + CSV→graph taxonomy ingest
backend/        FastAPI application
  app.py          API entry point (FastAPI + SSE)
  router.py       Two-layer router: keyword fast-path → LLM fallback
  retrieval.py    Cypher query builder, fallback ladder, ranking pipeline
  query_intent.py Query-intent scoring: exact → synonym → intent → fuzzy
  sensitivity_memory.py  Persistent allergen/sensitivity preference memory
  behavioral_learning.py Behavioral learning from clicks/purchases/rejects
  profile.py      Redis-backed user profile store (60-day TTL)
  llm_adapter.py  Provider-agnostic LLM client (NIM / Ollama / OpenAI / Google)
  match_keywords.py  Deterministic keyword chips for product cards
  vague_intent.py    Tier-3 LLM interpretation for vague queries
  playbooks/
    intake_profile.py  Keyword extraction → profile update
    recommend.py       Retrieval + ranking + streaming recommendation
    other.py           Allergen check, routine, handoff playbooks
frontend/
  widget.js       Vanilla JS widget, Shadow DOM, no build step
extension/        Manifest V3 Chrome extension (injects widget on dotandkey.com)
tests/            628 backend tests (pytest), 49 jsdom, 6 extension tests
```

## Ranking Pipeline

Every recommendation runs the following layers in order:

| Priority | Layer | Source |
|---|---|---|
| 1 | **Explicit query intent** | `query_intent.py` — exact ingredient/attribute match |
| 2 | **Synonym expansion** | "vitamin b3" → niacinamide, "unscented" → fragrance free |
| 3 | **Intent concepts** | "dark spots" → vitamin c, "acne" → salicylic acid |
| 4 | **Fuzzy matching** | "niacinimide" → niacinamide (edit-distance ≤ 2) |
| 5 | **Sensitivity memory** | Persistent fragrance/eczema/allergy preferences |
| 6 | **Behavioral learning** | Ingredient/texture/claim boosts from click/purchase history |
| 7 | **Skin type + concerns** | Profile match from Cypher graph |
| 8 | **Allergen baseline** | Count of free-from allergens |

Explicit user query always overrides memory and learned behavior.

## Scoring Formula

```
final_score =
    query_intent_score × 80      ← ingredient/attribute match
  + skin_type_score   × 30
  + concern_score     × 25
  + allergen_score    × 25
  − fragrance_penalty              ← only when allergen intent + fragranced product
  + memory_boost                   ← from sensitivity_memory (avoid_fragrance, eczema, etc.)
  − memory_penalty
  + behavioral_boost               ← from behavioral_learning (click/purchase history)
  − behavioral_penalty
```

## Quick Start

**Requires**: Docker, Python 3.11+, fish shell (or adapt scripts to bash).

```fish
# 1. Start FalkorDB + backend
./scripts/start_dev.fish

# 2. (First run only) Ingest the product catalog
python3 scripts/csv_to_graph.py --host localhost --port 6379 --graph dotandkey

# 3. Open the demo page
open frontend/index.html
```

### Environment

Copy `.env.example` to `.env` and fill in:

```
LLM_PROVIDER=nim          # nim | ollama | openai | google
NIM_API_KEY=...
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NIM_MODEL=qwen/qwen3.5-122b-a10b

FALKORDB_HOST=localhost
FALKORDB_PORT=6379
REDIS_HOST=localhost
REDIS_PORT=6379

CORS_ORIGINS=http://localhost:5500,https://www.dotandkey.com
```

### Docker Compose

```fish
docker compose up
```

Starts FalkorDB on `:6379` / `:3000` (browser UI) and the backend on `:8000`.

## Tests

```fish
# Backend (628 tests, ~35 s, no live services needed for most)
pytest

# Frontend widget (49 jsdom tests)
cd frontend && node test_widget.js

# Extension content-script (6 tests)
cd extension && node test_inject.js
```

The live-graph regression suite (`tests/test_retrieval_regression.py`) requires a running FalkorDB instance with the catalog loaded — it's skipped automatically when FalkorDB is unreachable.

## Chrome Extension

Load `extension/` as an unpacked extension in Chrome developer mode. The extension injects `widget.js` onto `https://www.dotandkey.com/*` via a content script.

> **Note**: Chrome may block `fetch('http://localhost:8000/...')` as mixed content on an HTTPS page. See `extension/README.md` for the HTTPS workaround using a self-signed certificate.

## Behavioral Learning

The advisor learns from user interactions across sessions:

| Event | Weight |
|---|---|
| Purchase | +10 |
| Shortlist | +5 |
| Repeated click | +4 |
| Click | +2 |
| Skip | −3 |
| Reject | −8 |

Preferences time-decay (×0.9 after 30 days, ×0.7 after 90, ×0.5 after 180). Users can reset with "forget my learned preferences".

## Sensitivity Memory

Sensitivity statements are detected once and persist across sessions:

- "I react to fragrance" → `avoid_fragrance=True` → fragrance-free products ranked higher permanently
- "I have eczema" → `eczema_prone=True` → ceramide + FF products boosted
- "I have allergies" → `allergy_prone=True` → allergen-free products boosted
- "forget my fragrance preference" → clears the flag

## Design Constraints

- **Not an agent** — the router picks one of 6 fixed playbooks. Retrieval is deterministic Cypher; the LLM only writes one short sentence.
- **No LLM product selection** — products are chosen by the graph. The LLM never sees the full catalog.
- **FalkorDB ordering rule** — all hard `MATCH` clauses must precede every `OPTIONAL MATCH` in the same chain (FalkorDB requirement).

## Stack

| Layer | Technology |
|---|---|
| Graph DB | FalkorDB (Redis-compatible, Cypher) |
| Session/Profile store | Redis (same FalkorDB instance) |
| Backend | FastAPI + Server-Sent Events |
| LLM | Provider-agnostic (NIM/Ollama/OpenAI/Google) |
| Frontend | Vanilla JS, Shadow DOM, no build step |
| Extension | Chrome MV3 |
| Tests | pytest, fakeredis, jsdom |
