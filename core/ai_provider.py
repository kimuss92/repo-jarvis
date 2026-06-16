# core/ai_provider.py
"""
Unified Gemini provider for JARVIS.

Use this module instead of repeating SDK wrappers in every action file.
Requires:
    pip install -U google-genai
"""

from __future__ import annotations

import json
import re
from typing import Any

from google import genai
from google.genai import types as gtypes

from core.utils import get_api_key


DEFAULT_FAST_MODEL = "gemini-2.5-flash-lite"
DEFAULT_SMART_MODEL = "gemini-2.5-flash"


class GeminiModel:
    """Small compatibility wrapper around google-genai."""

    def __init__(
        self,
        model_name: str | None = None,
        system_instruction: str | None = None,
        **kwargs: Any,
    ):
        self.model_name = model_name or kwargs.get("model") or kwargs.get("model_name") or DEFAULT_FAST_MODEL
        self.system_instruction = system_instruction or kwargs.get("system_instruction")

    def generate_content(self, contents: Any, *, response_mime_type: str | None = None):
        api_key = get_api_key()
        config = None
        if self.system_instruction or response_mime_type:
            config = gtypes.GenerateContentConfig(
                system_instruction=self.system_instruction,
                response_mime_type=response_mime_type,
            )
        client = genai.Client(api_key=api_key)
        return client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )


def generate_text(
    prompt: Any,
    *,
    model: str = DEFAULT_FAST_MODEL,
    system_instruction: str | None = None,
) -> str:
    response = GeminiModel(model, system_instruction).generate_content(prompt)
    return (getattr(response, "text", "") or "").strip()


def generate_json(
    prompt: Any,
    *,
    model: str = DEFAULT_FAST_MODEL,
    system_instruction: str | None = None,
    fallback: dict | None = None,
) -> dict:
    try:
        response = GeminiModel(model, system_instruction).generate_content(
            prompt,
            response_mime_type="application/json",
        )
        text = (response.text or "").strip()
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        return json.loads(text)
    except Exception:
        return fallback or {}
