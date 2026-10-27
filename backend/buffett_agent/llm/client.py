"""Model clients.

DeepSeekClient talks to any OpenAI-compatible /chat/completions endpoint, so
switching to another provider is a config change. Every call returns the raw
text plus token usage so cost can be logged per company.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Protocol

import requests

from ..config import LLMConfig


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class LLM(Protocol):
    def complete_json(self, system: str, user: str) -> LLMResult: ...


class DeepSeekClient:
    def __init__(self, cfg: LLMConfig, api_key: str | None = None):
        self.cfg = cfg
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("Set DEEPSEEK_API_KEY")

    def complete_json(self, system: str, user: str) -> LLMResult:
        # Keep `system` identical across calls so the provider's prefix cache can hit.
        payload = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        t0 = time.monotonic()
        r = requests.post(f"{self.cfg.base_url.rstrip('/')}/chat/completions",
                          headers={"Authorization": f"Bearer {self.api_key}"},
                          json=payload, timeout=self.cfg.timeout_s)
        r.raise_for_status()
        body = r.json()
        usage = body.get("usage", {})
        return LLMResult(text=body["choices"][0]["message"]["content"], model=body.get("model", self.cfg.model),
                         input_tokens=usage.get("prompt_tokens", 0),
                         output_tokens=usage.get("completion_tokens", 0),
                         latency_s=time.monotonic() - t0)


class ScriptedLLM:
    """Offline stand-in used by the demo and the tests.

    `responses` maps a company id to one canned answer or a list of answers that
    are returned in turn (so repeated runs can disagree, like a real model).
    A canned answer may be a dict (sent as JSON) or a raw string (to simulate
    malformed output). `leak_guesses` answers the anonymization leak check.
    """

    def __init__(self, responses: dict[str, object], leak_guesses: dict[str, str] | None = None,
                 model: str = "scripted"):
        self.responses = responses
        self.leak_guesses = leak_guesses or {}
        self.model = model
        self.calls = 0
        self._seen: dict[str, int] = {}

    def complete_json(self, system: str, user: str) -> LLMResult:
        from .prompts import LEAK_CHECK_SYSTEM
        self.calls += 1
        cid = user.split("\n", 1)[0].removeprefix("COMPANY_ID: ").strip()
        if system == LEAK_CHECK_SYSTEM:
            return LLMResult(text=json.dumps({"guess": self.leak_guesses.get(cid)}), model=self.model)
        resp = self.responses.get(cid)
        if resp is None:
            return LLMResult(text="{not json", model=self.model)
        if isinstance(resp, list):
            i = self._seen.get(cid, 0)
            self._seen[cid] = i + 1
            resp = resp[i % len(resp)]
        text = resp if isinstance(resp, str) else json.dumps(resp)
        return LLMResult(text=text, model=self.model, input_tokens=len(user) // 4, output_tokens=250)
