"""
tests/test_llm_adapter.py

Verifies that llm_adapter.py resolves providers, model names, API keys, and
base URLs correctly from environment variables — without making real API calls.

Design notes:
- _make_client() reads env vars at call time (not import time), so env must
  stay active through the call, not just during module import.
- The autouse _clean_llm_env fixture saves env before each test and restores
  after, giving each test a clean slate it can modify freely.
- patch("dotenv.load_dotenv") prevents .env from re-loading during module
  re-import and overriding our test env vars.
- AsyncOpenAI is bound in the module namespace via `from openai import`, so
  it must be patched as patch.object(mod, "AsyncOpenAI"), not via openai module.

A live smoke test (requires NIM_API_KEY in env) is skipped automatically
when the key is absent.
"""

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import load_dotenv as _load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load project .env once at module level so _nim_key below (and the live
# smoke test's skipif guard) can see the real API key even when the process
# hasn't loaded it yet.  override=False so shell-level overrides still win.
_DOT_ENV = Path(__file__).resolve().parent.parent / ".env"
_load_dotenv(_DOT_ENV, override=False)


# ---------------------------------------------------------------------------
# Env keys managed during tests
# ---------------------------------------------------------------------------

_LLM_ENV_KEYS = [
    "LLM_PROVIDER",
    "NIM_API_KEY", "NIM_BASE_URL", "NIM_MODEL",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "GOOGLE_API_KEY", "GOOGLE_MODEL",
    "MAX_OUTPUT_TOKENS",
]


@pytest.fixture(autouse=True)
def _clean_llm_env():
    """Save all LLM env vars before each test and restore after."""
    saved = {k: os.environ.get(k) for k in _LLM_ENV_KEYS}
    yield
    sys.modules.pop("backend.llm_adapter", None)
    for k in _LLM_ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def _reload_adapter(env_overrides: dict) -> object:
    """Re-import llm_adapter with a specific env. Env stays active for caller.

    Clears all managed LLM env vars, applies overrides, then imports a fresh
    module with load_dotenv patched to a no-op so .env cannot override the
    test vars.  Env is NOT restored here — the autouse fixture does that.
    """
    for k in _LLM_ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env_overrides)
    sys.modules.pop("backend.llm_adapter", None)
    with patch("dotenv.load_dotenv"):          # prevent .env clobbering test env
        return importlib.import_module("backend.llm_adapter")


# ---------------------------------------------------------------------------
# Minimal async iterator for mocking streaming responses
# ---------------------------------------------------------------------------

class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _mock_chunk(text: str | None):
    """Build a minimal mock SSE chunk with choices[0].delta.content."""
    delta = MagicMock()
    delta.content = text
    delta.reasoning_content = None
    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=delta)]
    return chunk


# ---------------------------------------------------------------------------
# 1. Provider → model name resolution
# ---------------------------------------------------------------------------

class TestProviderModelResolution:
    def _model_for(self, provider: str, model_override: str | None = None) -> str:
        env: dict = {"LLM_PROVIDER": provider}
        if model_override is not None:
            env[f"{provider.upper()}_MODEL"] = model_override
        mod = _reload_adapter(env)
        with patch.object(mod, "AsyncOpenAI"):
            _, model = mod._make_client()
        return model

    def test_nim_default_model(self):
        assert self._model_for("nim") == "qwen/qwen3.5-122b-a10b"

    def test_nim_env_model_override(self):
        assert self._model_for("nim", "meta/llama-3.1-8b-instruct") == "meta/llama-3.1-8b-instruct"

    def test_ollama_default_model(self):
        assert self._model_for("ollama") == "qwen3.5:4b"

    def test_ollama_env_model_override(self):
        assert self._model_for("ollama", "llama3.2:3b") == "llama3.2:3b"

    def test_openai_default_model(self):
        assert self._model_for("openai") == "gpt-4o-mini"

    def test_openai_env_model_override(self):
        assert self._model_for("openai", "gpt-4o") == "gpt-4o"

    def test_google_default_model(self):
        assert self._model_for("google") == "gemini-2.0-flash"

    def test_google_env_model_override(self):
        assert self._model_for("google", "gemini-1.5-pro") == "gemini-1.5-pro"

    def test_unknown_provider_raises_value_error(self):
        mod = _reload_adapter({"LLM_PROVIDER": "anthropic"})
        with patch.object(mod, "AsyncOpenAI"), \
             pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            mod._make_client()


# ---------------------------------------------------------------------------
# 2. API key / base URL wiring per provider
# ---------------------------------------------------------------------------

class TestClientConfiguration:
    def _capture(self, provider: str, extra_env: dict | None = None) -> dict:
        env = {"LLM_PROVIDER": provider, **(extra_env or {})}
        mod = _reload_adapter(env)
        captured: dict = {}

        class _Capture:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch.object(mod, "AsyncOpenAI", _Capture):
            mod._make_client()
        return captured

    # NIM
    def test_nim_default_base_url(self):
        assert self._capture("nim")["base_url"] == "https://integrate.api.nvidia.com/v1"

    def test_nim_env_api_key(self):
        assert self._capture("nim", {"NIM_API_KEY": "my-nim-key"})["api_key"] == "my-nim-key"

    def test_nim_custom_base_url(self):
        kw = self._capture("nim", {"NIM_BASE_URL": "https://custom.example.com/v1"})
        assert kw["base_url"] == "https://custom.example.com/v1"

    # Ollama
    def test_ollama_api_key_is_literal_string(self):
        assert self._capture("ollama")["api_key"] == "ollama"

    def test_ollama_default_base_url(self):
        assert self._capture("ollama")["base_url"] == "http://localhost:11434/v1"

    def test_ollama_custom_base_url(self):
        kw = self._capture("ollama", {"OLLAMA_BASE_URL": "http://gpu-box:11434/v1"})
        assert kw["base_url"] == "http://gpu-box:11434/v1"

    # OpenAI
    def test_openai_env_api_key(self):
        assert self._capture("openai", {"OPENAI_API_KEY": "sk-test"})["api_key"] == "sk-test"

    def test_openai_default_base_url(self):
        assert self._capture("openai")["base_url"] == "https://api.openai.com/v1"

    def test_openai_custom_base_url(self):
        kw = self._capture("openai", {"OPENAI_BASE_URL": "https://proxy.example.com/v1"})
        assert kw["base_url"] == "https://proxy.example.com/v1"

    # Google
    def test_google_env_api_key(self):
        assert self._capture("google", {"GOOGLE_API_KEY": "goog-key"})["api_key"] == "goog-key"

    def test_google_uses_generativelanguage_base_url(self):
        assert "generativelanguage.googleapis.com" in self._capture("google")["base_url"]


# ---------------------------------------------------------------------------
# 3. Thinking-disabled kwargs
# ---------------------------------------------------------------------------

class TestThinkingDisabledKwargs:
    def test_nim_disables_thinking(self):
        mod = _reload_adapter({"LLM_PROVIDER": "nim"})
        mod._provider = "nim"
        assert mod._thinking_disabled_kwargs() == {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    @pytest.mark.parametrize("provider", ["ollama", "openai", "google"])
    def test_non_nim_returns_empty_dict(self, provider):
        mod = _reload_adapter({"LLM_PROVIDER": provider})
        mod._provider = provider
        assert mod._thinking_disabled_kwargs() == {}


# ---------------------------------------------------------------------------
# 4. MAX_OUTPUT_TOKENS from env (read at import time)
# ---------------------------------------------------------------------------

class TestMaxOutputTokens:
    def test_default_is_300(self):
        mod = _reload_adapter({"LLM_PROVIDER": "nim"})
        assert mod.MAX_OUTPUT == 300

    def test_custom_value_respected(self):
        mod = _reload_adapter({"LLM_PROVIDER": "nim", "MAX_OUTPUT_TOKENS": "512"})
        assert mod.MAX_OUTPUT == 512


# ---------------------------------------------------------------------------
# 5. Singleton caching
# ---------------------------------------------------------------------------

class TestSingletonCaching:
    def test_fresh_module_has_no_client(self):
        mod = _reload_adapter({"LLM_PROVIDER": "ollama"})
        assert mod._client is None
        assert mod._model is None

    def test_get_client_caches_after_first_call(self):
        mod = _reload_adapter({"LLM_PROVIDER": "ollama"})
        mock_instance = MagicMock()
        MockOAI = MagicMock(return_value=mock_instance)

        with patch.object(mod, "AsyncOpenAI", MockOAI):
            c1, m1 = mod._get_client()
            c2, m2 = mod._get_client()

        assert c1 is c2
        assert m1 == m2
        assert MockOAI.call_count == 1   # constructor called exactly once

    def test_get_client_sets_provider_global(self):
        mod = _reload_adapter({"LLM_PROVIDER": "ollama"})
        with patch.object(mod, "AsyncOpenAI"):
            mod._get_client()
        assert mod._provider == "ollama"


# ---------------------------------------------------------------------------
# 6. chat() / route() / one_shot() pass correct args
# ---------------------------------------------------------------------------

class TestCallShapes:
    def test_chat_uses_stream_true_and_nim_thinking_kwargs(self):
        mod = _reload_adapter({"LLM_PROVIDER": "nim"})
        mock_create = AsyncMock(return_value=_AsyncIter([]))
        mod._client = MagicMock()
        mod._client.chat.completions.create = mock_create
        mod._model    = "test-model"
        mod._provider = "nim"

        async def _run():
            async for _ in mod.chat("sys", [{"role": "user", "content": "hi"}]):
                pass

        asyncio.run(_run())

        kw = mock_create.call_args.kwargs
        assert kw["model"] == "test-model"
        assert kw["stream"] is True
        assert kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_chat_yields_content_tokens(self):
        mod = _reload_adapter({"LLM_PROVIDER": "nim"})
        chunks = [_mock_chunk("Hello "), _mock_chunk("world")]
        mock_create = AsyncMock(return_value=_AsyncIter(chunks))
        mod._client = MagicMock()
        mod._client.chat.completions.create = mock_create
        mod._model    = "test-model"
        mod._provider = "nim"

        async def _run():
            return [t async for t in mod.chat("sys", [{"role": "user", "content": "hi"}])]

        tokens = asyncio.run(_run())
        assert tokens == ["Hello ", "world"]

    def test_chat_yields_fallback_on_empty_stream(self):
        mod = _reload_adapter({"LLM_PROVIDER": "nim"})
        mock_create = AsyncMock(return_value=_AsyncIter([]))
        mod._client = MagicMock()
        mod._client.chat.completions.create = mock_create
        mod._model    = "test-model"
        mod._provider = "nim"

        async def _run():
            return [t async for t in mod.chat("sys", [{"role": "user", "content": "hi"}])]

        tokens = asyncio.run(_run())
        assert tokens == ["(no response — please try again)"]

    def test_route_uses_tool_choice_required_and_temp_zero(self):
        mod = _reload_adapter({"LLM_PROVIDER": "ollama"})

        tc = MagicMock()
        tc.function.name = "recommend"
        tc.function.arguments = '{"category": "sunscreen"}'
        choice = MagicMock()
        choice.finish_reason = "tool_calls"
        choice.message.tool_calls = [tc]
        mock_resp = MagicMock()
        mock_resp.choices = [choice]

        mod._client = MagicMock()
        mod._client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mod._model    = "test-model"
        mod._provider = "ollama"

        result = asyncio.run(mod.route("sys", "show me a sunscreen", tools=[]))

        assert result == {"tool": "recommend", "args": {"category": "sunscreen"}}
        kw = mod._client.chat.completions.create.call_args.kwargs
        assert kw["tool_choice"] == "required"
        assert kw["temperature"] == 0

    def test_route_falls_back_to_general_qa_on_error(self):
        mod = _reload_adapter({"LLM_PROVIDER": "ollama"})
        mod._client = MagicMock()
        mod._client.chat.completions.create = AsyncMock(side_effect=Exception("timeout"))
        mod._model    = "test-model"
        mod._provider = "ollama"

        assert asyncio.run(mod.route("sys", "hello", tools=[])) == {
            "tool": "general_qa", "args": {}
        }

    def test_one_shot_returns_message_content(self):
        mod = _reload_adapter({"LLM_PROVIDER": "openai"})
        msg = MagicMock()
        msg.content = "Niacinamide serum works well."
        msg.reasoning_content = None
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=msg, finish_reason="stop")]

        mod._client = MagicMock()
        mod._client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mod._model    = "gpt-4o-mini"
        mod._provider = "openai"

        result = asyncio.run(mod.one_shot("sys", [{"role": "user", "content": "ping"}]))
        assert result == "Niacinamide serum works well."

    def test_one_shot_falls_back_to_reasoning_content_when_content_is_none(self):
        """Qwen3 thinking mode returns content=None; reasoning_content has the answer."""
        mod = _reload_adapter({"LLM_PROVIDER": "nim"})
        msg = MagicMock()
        msg.content = None
        msg.reasoning_content = "Thinking... answer: ok"
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=msg, finish_reason="stop")]

        mod._client = MagicMock()
        mod._client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mod._model    = "nim-model"
        mod._provider = "nim"

        result = asyncio.run(mod.one_shot("sys", [{"role": "user", "content": "ping"}]))
        assert result == "Thinking... answer: ok"


# ---------------------------------------------------------------------------
# 7. Live smoke test — skipped unless NIM_API_KEY is set in current env
# ---------------------------------------------------------------------------

_nim_key = os.getenv("NIM_API_KEY", "")


@pytest.mark.skipif(not _nim_key, reason="NIM_API_KEY not set — skipping live model call")
def test_live_nim_one_shot_returns_nonempty_string():
    """Fire one real NIM call using credentials from .env."""
    # The autouse fixture cleared env vars before this test ran — reload .env.
    _load_dotenv(_DOT_ENV, override=True)
    sys.modules.pop("backend.llm_adapter", None)
    from backend.llm_adapter import one_shot

    result = asyncio.run(one_shot(
        system="You are a helpful assistant. Reply in one word only.",
        messages=[{"role": "user", "content": "Say 'ok'."}],
        max_tokens=10,
        temperature=0,
    ))
    assert isinstance(result, str) and len(result.strip()) > 0, \
        f"NIM returned empty: {result!r}"
