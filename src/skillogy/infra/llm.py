"""LLM client provider with multi-provider auth.

Supported backends (auto-detected):
  1. Google Gemini   — when GOOGLE_API_KEY is set OR SKILLOGY_LLM=gemini
  2. Claude Agent SDK — when claude-agent-sdk is importable (inherits Claude Code auth)
  3. Anthropic API   — when ANTHROPIC_API_KEY is set

Override via SKILLOGY_LLM env var: "gemini" | "sdk" | "api".

Provider selection notes:
- Gemini path uses google-genai client.models.generate_content with system_instruction.
- Claude Agent SDK path uses the official query() async iterator. ClaudeAgentOptions
  does not expose a model selector — it uses whatever model Claude Code is configured
  with. The `model` argument is forwarded only on the API-key path and Gemini path.
"""

import importlib
import os
from typing import Optional

# Model ID normalization map
_MODEL_ALIASES = {
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}

# Default Gemini model — `gemini-pro-latest` is an alias that always tracks the
# strongest Pro release (currently Gemini 3.1 Pro as of 2026-04-26). Override via env:
#   SKILLOGY_GEMINI_MODEL=gemini-flash-latest          (cheap/fast alias)
#   SKILLOGY_GEMINI_MODEL=gemini-flash-lite-latest     (cheapest alias)
#   SKILLOGY_GEMINI_MODEL=gemini-3.1-pro-preview       (explicit pin)
_DEFAULT_GEMINI_MODEL = os.environ.get("SKILLOGY_GEMINI_MODEL", "gemini-pro-latest")


def _normalize_model(model: str) -> str:
    return _MODEL_ALIASES.get(model, model)


def _is_sdk_available() -> bool:
    try:
        importlib.import_module("claude_agent_sdk")
        return True
    except ImportError:
        return False


def _is_genai_available() -> bool:
    try:
        importlib.import_module("google.genai")
        return True
    except ImportError:
        return False


class LLMClient:
    """Thin wrapper around an LLM backend providing a uniform complete() interface."""

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        raise NotImplementedError


class _SDKClient(LLMClient):
    """Client backed by the official `claude-agent-sdk` query() iterator.

    Wraps the async `query()` API in a synchronous `complete()` call by running
    a fresh anyio event loop per call. For high-throughput indexing prefer the
    direct anthropic-SDK path (set SKILLOGY_FORCE_API_KEY=1).
    """

    def __init__(self, model: str) -> None:
        # Verify the SDK can be imported eagerly so failure surfaces at construction.
        # Actual imports done lazily inside complete() to keep test mocks simple.
        importlib.import_module("claude_agent_sdk")
        self._model = model

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,  # noqa: ARG002  (Agent SDK does not expose a max_tokens knob)
        temperature: float = 0.0,  # noqa: ARG002  (Agent SDK does not expose temperature)
    ) -> str:
        import anyio
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )

        options_kwargs: dict = {"max_turns": 1}
        if system is not None:
            options_kwargs["system_prompt"] = system
        options = ClaudeAgentOptions(**options_kwargs)

        async def _collect() -> str:
            chunks: list[str] = []
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
            return "".join(chunks)

        return anyio.run(_collect)


class _APIClient(LLMClient):
    """Client backed by the anthropic SDK using ANTHROPIC_API_KEY."""

    def __init__(self, model: str) -> None:
        from anthropic import Anthropic

        self._model = model
        self._client = Anthropic()

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)
        return response.content[0].text


class _GeminiClient(LLMClient):
    """Client backed by google-genai (Gemini Developer API).

    Reads GOOGLE_API_KEY from the environment. Default model: gemini-2.5-flash.
    """

    def __init__(self, model: str) -> None:
        from google import genai  # type: ignore[import-not-found]

        self._model = model
        self._genai = genai
        # Client() reads GOOGLE_API_KEY automatically; allow explicit override.
        api_key = os.environ.get("GOOGLE_API_KEY")
        self._client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        from google.genai import types  # type: ignore[import-not-found]

        config_kwargs: dict = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system is not None:
            config_kwargs["system_instruction"] = system

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        return response.text or ""


def _resolve_model_for_provider(provider: str, model: str) -> str:
    """Translate caller's model id to the provider-appropriate one."""
    if provider == "gemini":
        return model if model.startswith("gemini") else _DEFAULT_GEMINI_MODEL
    return _normalize_model(model)


def get_llm_client(model: str = "claude-haiku-4-5") -> LLMClient:
    """Return an LLMClient using the best available auth method.

    Priority:
    1. SKILLOGY_LLM env var — explicit provider override ("gemini" | "sdk" | "api")
    2. GOOGLE_API_KEY set → Gemini (preferred for indexing throughput / free tier)
    3. claude-agent-sdk importable AND SKILLOGY_FORCE_API_KEY not set → SDK
    4. ANTHROPIC_API_KEY set → Anthropic API
    5. Raises RuntimeError with a clear message
    """
    forced = os.environ.get("SKILLOGY_LLM", "").lower()
    force_api_key = os.environ.get("SKILLOGY_FORCE_API_KEY")

    # 1. Explicit provider override
    if forced == "gemini":
        if not _is_genai_available():
            raise RuntimeError("SKILLOGY_LLM=gemini set but google-genai is not installed.")
        return _GeminiClient(model=_resolve_model_for_provider("gemini", model))
    if forced == "sdk":
        if not _is_sdk_available():
            raise RuntimeError("SKILLOGY_LLM=sdk set but claude-agent-sdk is not installed.")
        return _SDKClient(model=_normalize_model(model))
    if forced == "api":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("SKILLOGY_LLM=api set but ANTHROPIC_API_KEY is not set.")
        return _APIClient(model=_normalize_model(model))

    # 2. Auto-detect: Gemini first if key present (preferred for indexing)
    if os.environ.get("GOOGLE_API_KEY") and _is_genai_available():
        return _GeminiClient(model=_resolve_model_for_provider("gemini", model))

    # 3. Claude Agent SDK
    if _is_sdk_available() and not force_api_key:
        return _SDKClient(model=_normalize_model(model))

    # 4. Anthropic API
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _APIClient(model=_normalize_model(model))

    raise RuntimeError(
        "No LLM auth available. Set GOOGLE_API_KEY (Gemini), install claude-agent-sdk"
        " (provided by Claude Code), or set ANTHROPIC_API_KEY."
    )
