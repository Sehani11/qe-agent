"""Ollama-backed local LLM provider implementation."""

import json
import re
from collections.abc import Awaitable, Callable

import httpx

from app.services.llm.provider import LLMProvider, LLMProviderError, ToolLoopStats


class OllamaProvider(LLMProvider):
    """Local Ollama provider implementation."""

    supports_tools = False

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

        if not self._base_url:
            raise LLMProviderError("Ollama base URL is missing. Check OLLAMA_BASE_URL.")
        if not self._model:
            raise LLMProviderError("Ollama model is missing. Check OLLAMA_MODEL.")

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate text using the Ollama HTTP API."""
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.7},
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            content = data.get("response", "")
            if not isinstance(content, str):
                raise LLMProviderError("Ollama returned an invalid text response.")
            return content
        except httpx.HTTPError as e:
            raise LLMProviderError(f"Ollama API error: {e!s}") from e
        except ValueError as e:
            raise LLMProviderError("Ollama returned an invalid JSON response.") from e
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling Ollama: {e!s}") from e

    async def generate_structured(
        self,
        prompt: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate structured JSON using the Ollama HTTP API."""
        sys_msg = system_prompt or "You are a helpful assistant."
        if response_format:
            schema_str = json.dumps(response_format, indent=2)
            sys_msg += (
                "\n\nYou MUST reply with ONLY a valid JSON object matching this schema. "
                "Do NOT include markdown, code fences, or explanation text.\n"
                f"{schema_str}"
            )

        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "system": sys_msg,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.2},
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            content = data.get("response", "")
            if not isinstance(content, str):
                raise LLMProviderError("Ollama returned an invalid structured response.")

            match = re.search(r"(\{[\s\S]*\})|(\[[\s\S]*\])", content)
            if not match:
                raise LLMProviderError("Ollama returned invalid JSON structure.")

            parsed = json.loads(match.group(0))
            if not isinstance(parsed, dict):
                raise LLMProviderError("Ollama returned a non-object JSON payload.")
            return parsed
        except json.JSONDecodeError as e:
            raise LLMProviderError("Ollama returned invalid JSON structure.") from e
        except httpx.HTTPError as e:
            raise LLMProviderError(f"Ollama API error: {e!s}") from e
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling Ollama: {e!s}") from e

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[str]],
        max_tool_rounds: int = 10,
        stats: ToolLoopStats | None = None,
        tool_result_window: int = 0,
    ) -> str:
        raise LLMProviderError("generate_with_tools not implemented for Ollama provider")