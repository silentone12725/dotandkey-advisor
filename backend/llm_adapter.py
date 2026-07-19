"""
backend/llm_adapter.py

Single entry point for all LLM calls. Provider is chosen by LLM_PROVIDER env var.
All providers speak the OpenAI /v1/chat/completions schema — swap by changing .env.

Supports two call modes:
  chat()          — streaming text response (playbooks, greeting)
  route()         — tool-calling response (router)
"""

import json
import logging
import os
from typing import AsyncGenerator, Optional

_log = logging.getLogger(__name__)

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(override=False)  # override=False so Docker-injected env vars win over .env


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _make_client() -> tuple[AsyncOpenAI, str]:
    """Return (client, model_name) for the configured provider."""
    provider = os.getenv("LLM_PROVIDER", "nim").lower()

    if provider == "nim":
        client = AsyncOpenAI(
            api_key=os.getenv("NIM_API_KEY", ""),
            base_url=os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        )
        model = os.getenv("NIM_MODEL", "qwen/qwen3.5-122b-a10b")

    elif provider == "ollama":
        client = AsyncOpenAI(
            api_key="ollama",           # Ollama ignores the key
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        )
        model = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")

    elif provider == "openai":
        client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    elif provider == "google":
        client = AsyncOpenAI(
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        )
        model = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")

    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}. "
                         "Choose nim | ollama | openai | google")

    return client, model


_client: Optional[AsyncOpenAI] = None
_model: Optional[str] = None
_provider: Optional[str] = None


def _get_client() -> tuple[AsyncOpenAI, str]:
    global _client, _model, _provider
    if _client is None:
        _provider = os.getenv("LLM_PROVIDER", "nim").lower()
        _client, _model = _make_client()
    return _client, _model


def _thinking_disabled_kwargs() -> dict:
    """Extra request-body kwargs to skip Qwen3.5's reasoning phase.

    NIM hosts Qwen3.5 with "thinking" mode on by default — the model
    burns a large, variable amount of time generating invisible reasoning
    tokens before any visible content appears, which is the dominant
    cause of inconsistent first-token latency (long pause, then the
    actual short answer streams out fast since it was already decided).
    Confirmed via NVIDIA's NIM docs: chat_template_kwargs.enable_thinking.

    Scoped to "nim" only for now — Ollama may use a different convention
    for the same toggle; verify before extending this to "ollama".
    """
    if _provider == "nim":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------

MAX_OUTPUT = int(os.getenv("MAX_OUTPUT_TOKENS", 300))


# ---------------------------------------------------------------------------
# chat() — streaming text for playbooks
# ---------------------------------------------------------------------------

async def chat(
    system: str,
    messages: list[dict],
    max_tokens: int = MAX_OUTPUT,
) -> AsyncGenerator[str, None]:
    """Stream response tokens for playbook calls.

    Yields individual text deltas as they arrive. The FastAPI SSE handler
    forwards each delta directly to the browser so the user sees text
    appearing word-by-word.

    Args:
        system:     The fully-assembled system prompt for this turn.
        messages:   List of {role, content} dicts (conversation history
                    + current user message).
        max_tokens: Hard cap on output tokens.
    """
    client, model = _get_client()

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, *messages],
            max_tokens=max_tokens,
            stream=True,
            temperature=0.7,
            extra_body=_thinking_disabled_kwargs(),
        )
    except Exception as exc:
        _log.error("stream create failed: %s", exc)
        yield f"(advisor error — {type(exc).__name__})"
        return

    token_count = 0
    async for chunk in stream:
        try:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # Qwen3 thinking mode: content may be None, check reasoning_content
            text = delta.content
            if not text:
                text = getattr(delta, "reasoning_content", None)
            if text:
                token_count += 1
                yield text
        except Exception as exc:
            _log.warning("chunk parse error: %s", exc)
            continue

    if token_count == 0:
        _log.warning("stream produced zero tokens. model=%s", model)
        yield "(no response — please try again)"


# ---------------------------------------------------------------------------
# route() — tool-calling for the router
# ---------------------------------------------------------------------------

async def route(
    system: str,
    user_message: str,
    tools: list[dict],
) -> dict:
    """Single non-streaming call that returns a tool invocation dict.

    Returns:
        {"tool": "playbook_name", "args": {...}}
        Falls back to {"tool": "general_qa", "args": {}} on any error.
    """
    client, model = _get_client()

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system",  "content": system},
                {"role": "user",    "content": user_message},
            ],
            tools=tools,
            tool_choice="required",     # must call a tool — never freetext
            max_tokens=100,             # tool calls are tiny
            temperature=0,              # deterministic routing
            extra_body=_thinking_disabled_kwargs(),
        )

        choice = resp.choices[0]
        if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
            tc = choice.message.tool_calls[0]
            args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            return {"tool": tc.function.name, "args": args}

    except Exception as exc:
        # log and fall through to fallback
        _log.error("router LLM error: %s", exc)

    return {"tool": "general_qa", "args": {}}


# ---------------------------------------------------------------------------
# one_shot() — single non-streaming call (used for greetings, JSON extraction)
# ---------------------------------------------------------------------------

async def one_shot(
    system: str,
    messages: list[dict],
    max_tokens: int = MAX_OUTPUT,
    temperature: float = 0.7,
    response_format: Optional[dict] = None,
) -> str:
    """Non-streaming single call. Returns complete response string."""
    client, model = _get_client()

    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": system}, *messages],
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=_thinking_disabled_kwargs(),
    )
    if response_format:
        kwargs["response_format"] = response_format

    resp = await client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message

    # Qwen3 models in "thinking" mode return content=None with the actual
    # text in reasoning_content. Fall back to that if content is empty.
    text = msg.content
    if not text:
        text = getattr(msg, "reasoning_content", None) or ""

    if not text:
        _log.warning("empty response. finish_reason=%s msg=%s",
                     resp.choices[0].finish_reason, msg)

    return text