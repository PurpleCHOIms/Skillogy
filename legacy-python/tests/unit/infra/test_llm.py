"""Tests for skillogy.infra.llm — no real API calls made."""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_auth():
    """Force a fresh import of skillogy.infra.llm so monkeypatches take effect."""
    import skillogy.infra.llm as auth_mod
    importlib.reload(auth_mod)
    return auth_mod


# ---------------------------------------------------------------------------
# test_sdk_path
# ---------------------------------------------------------------------------

def test_sdk_path(monkeypatch):
    """When claude_agent_sdk is importable and SKILLOGY_FORCE_API_KEY is unset,
    get_llm_client() must return an _SDKClient and complete() must surface text from
    the SDK's query() async iterator."""

    # Build a fake claude_agent_sdk module with the symbols our auth module imports
    fake_sdk = types.ModuleType("claude_agent_sdk")

    class _FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class _FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    class _FakeClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    captured = {}

    async def _fake_query(prompt, options=None):
        captured["prompt"] = prompt
        captured["options"] = options
        yield _FakeAssistantMessage(content=[_FakeTextBlock("hello from fake")])

    fake_sdk.TextBlock = _FakeTextBlock
    fake_sdk.AssistantMessage = _FakeAssistantMessage
    fake_sdk.ClaudeAgentOptions = _FakeClaudeAgentOptions
    fake_sdk.query = _fake_query

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.delenv("SKILLOGY_FORCE_API_KEY", raising=False)

    auth = _reload_auth()

    client = auth.get_llm_client(model="claude-haiku-4-5")

    assert isinstance(client, auth._SDKClient), (
        f"Expected _SDKClient, got {type(client)}"
    )
    # Resolved model id is recorded even though Agent SDK doesn't accept a model selector
    assert client._model == "claude-haiku-4-5-20251001"

    # complete() must run the async query iterator and concatenate TextBlock content
    out = client.complete("ping", system="you are a test")
    assert out == "hello from fake"
    assert captured["prompt"] == "ping"
    assert captured["options"].kwargs.get("system_prompt") == "you are a test"
    assert captured["options"].kwargs.get("max_turns") == 1


# ---------------------------------------------------------------------------
# test_env_path
# ---------------------------------------------------------------------------

def test_env_path(monkeypatch):
    """When claude_agent_sdk is NOT importable but ANTHROPIC_API_KEY is set,
    get_llm_client() must return an _APIClient."""

    # Remove claude_agent_sdk from sys.modules so import fails
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)

    # Patch importlib.import_module to raise ImportError for claude_agent_sdk
    original_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "claude_agent_sdk":
            raise ImportError("no module named claude_agent_sdk")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.delenv("SKILLOGY_FORCE_API_KEY", raising=False)

    # Mock the Anthropic constructor so no real client is built
    mock_anthropic_instance = MagicMock()
    mock_anthropic_cls = MagicMock(return_value=mock_anthropic_instance)

    with patch("anthropic.Anthropic", mock_anthropic_cls):
        auth = _reload_auth()
        client = auth.get_llm_client(model="claude-haiku-4-5")

    assert isinstance(client, auth._APIClient), (
        f"Expected _APIClient, got {type(client)}"
    )


# ---------------------------------------------------------------------------
# test_no_creds_raises
# ---------------------------------------------------------------------------

def test_no_creds_raises(monkeypatch):
    """When neither SDK is available nor ANTHROPIC_API_KEY is set,
    get_llm_client() must raise RuntimeError with the expected message."""

    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)

    original_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "claude_agent_sdk":
            raise ImportError("no module named claude_agent_sdk")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SKILLOGY_FORCE_API_KEY", raising=False)

    auth = _reload_auth()

    with pytest.raises(RuntimeError) as exc_info:
        auth.get_llm_client()

    expected_fragment = "No LLM auth available"
    assert expected_fragment in str(exc_info.value), (
        f"Expected '{expected_fragment}' in error message, got: {exc_info.value}"
    )
