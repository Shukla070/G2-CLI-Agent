"""Thin Anthropic client wrapper for Lantern.

This module deliberately stays small: it only adapts the Anthropic SDK
interaction and exposes a ``messages.create()`` surface that the
orchestrator can call.
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

try:
    from anthropic import Anthropic as _SDKAnthropic
except ModuleNotFoundError:  # pragma: no cover - exercised only in stripped test envs
    _SDKAnthropic = None


anthropic = type("_AnthropicModule", (), {"Anthropic": _SDKAnthropic})


class AnthropicClient:
    """Small wrapper over the Anthropic SDK's ``messages.create()`` API."""

    DEFAULT_MODEL = "claude-sonnet-4-6"
    DEFAULT_MAX_TOKENS = 4096

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY must be set to use AnthropicClient.")

        sdk_cls = anthropic.Anthropic
        if sdk_cls is None:
            raise ModuleNotFoundError(
                "anthropic package is not installed in the current environment. "
                "Install project dependencies before using AnthropicClient."
            )

        self._client = sdk_cls(api_key=key)
        self.model = model or self.DEFAULT_MODEL
        self.messages = self._Messages(self._client, self.model)

    class _Messages:
        def __init__(self, client: Any, model: str) -> None:
            self._client = client
            self.model = model

        def create(self, **kwargs):
            kwargs.setdefault("model", self.model)
            kwargs.setdefault("max_tokens", AnthropicClient.DEFAULT_MAX_TOKENS)
            return self._client.messages.create(**kwargs)


__all__ = ["AnthropicClient"]
