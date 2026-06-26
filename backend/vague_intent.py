"""
backend/vague_intent.py

Tier 3 query understanding: LLM-based interpretation for vague user queries.

Called from recommend.py ONLY when Tier 1+2 deterministic extraction yields
no tokens (query_tokens is empty). Adds 1-2 s latency only for vague queries;
precise ingredient/attribute queries are never sent here.

LLM output: a JSON object with ranked ingredient/attribute tokens.

  {"tokens": ["ceramide", "fragrance free"], "confidence": 0.83}

Tokens are returned only when confidence >= 0.5. On any error the function
returns [] (graceful degradation — recommendation still works with Cypher
skin_score / concern_score ordering).
"""

import json
import logging

_log = logging.getLogger(__name__)

_SYSTEM = """\
You are a skincare product intent extractor for an Indian D2C brand.
Given a user query, identify the specific skincare ingredients or product \
attributes they are likely looking for.

Return ONLY valid JSON (no markdown, no explanation):
{"tokens": [<list of lowercase ingredient/attribute strings>], "confidence": <0.0–1.0>}

Rules:
- tokens must be ingredient names or product attributes that appear in skincare products
  (e.g. "ceramide", "niacinamide", "vitamin c", "fragrance free", "tinted", "lightweight")
- maximum 3 tokens
- confidence reflects how certain you are (below 0.5 means the query is too vague)
- if completely unclear, return {"tokens": [], "confidence": 0.0}

Examples:
"my skin gets angry when i try new products"
→ {"tokens": ["ceramide", "fragrance free"], "confidence": 0.80}

"i want glass skin"
→ {"tokens": ["hyaluronic acid", "vitamin c"], "confidence": 0.75}

"something i can use every day for college"
→ {"tokens": ["lightweight"], "confidence": 0.45}

"my lips keep peeling"
→ {"tokens": ["hyaluronic acid"], "confidence": 0.85}

"i want my skin to look like a filter"
→ {"tokens": ["vitamin c", "hyaluronic acid"], "confidence": 0.60}
"""


async def interpret_vague_query(user_message: str) -> list[str]:
    """Return a list of ingredient/attribute tokens derived from LLM interpretation.

    Returns [] when:
    - the LLM response has confidence < 0.5
    - JSON parsing fails
    - the LLM call itself fails (network / timeout)

    Never raises — always degrades gracefully.
    """
    from backend.llm_adapter import one_shot  # local import avoids circular deps at module load

    try:
        response = await one_shot(
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=60,
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(response)
        confidence = float(data.get("confidence", 0))
        if confidence < 0.5:
            _log.debug("VAGUE_INTENT | confidence=%.2f < 0.5 — skipping", confidence)
            return []
        tokens = [str(t).lower().strip() for t in data.get("tokens", []) if t]
        _log.debug(
            "VAGUE_INTENT | tokens=%s | confidence=%.2f", tokens, confidence
        )
        return tokens
    except Exception as exc:
        _log.debug("VAGUE_INTENT | failed: %s", exc)
        return []
