"""Claude LLM provider implementation."""

import json
import re
from collections.abc import Awaitable, Callable

from anthropic import APIError, AsyncAnthropic

from app.services.llm.provider import LLMProvider, LLMProviderError


class ClaudeProvider(LLMProvider):
    """Anthropic Claude provider implementation."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        if not self._api_key:
            raise LLMProviderError("Claude API key is missing. Check LLM_API_KEY.")
        # Apply 30 second timeout per NFR-P3
        self._client = AsyncAnthropic(api_key=self._api_key, timeout=30.0)

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate text using Claude API."""
        try:
            kwargs = {
                "model": "claude-3-5-sonnet-20240620",
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = await self._client.messages.create(**kwargs)
            # Claude's response.content is a list of blocks, usually one text block
            content = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )
            return content
        except APIError as e:
            raise LLMProviderError(f"Claude API error: {e!s}") from e
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling Claude: {e!s}") from e

    async def generate_structured(
        self,
        prompt: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate structured JSON using Claude API."""
        try:
            sys_msg = system_prompt or "You are a helpful assistant."
            if response_format:
                schema_str = json.dumps(response_format, indent=2)
                sys_msg += (
                    f"\n\nYou MUST reply with ONLY a valid JSON object matching this schema. "
                    f"Do NOT include any markdown formatting, code blocks, or explanations.\n"
                    f"{schema_str}"
                )

            kwargs = {
                "model": "claude-3-5-sonnet-20240620",
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "system": sys_msg,
            }

            response = await self._client.messages.create(**kwargs)
            content = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )

            # Robust JSON extraction ignoring conversational padding
            match = re.search(r"(\{[\s\S]*\})|(\[[\s\S]*\])", content)
            if not match:
                raise ValueError("No JSON object could be extracted from response.")
            content = match.group(0)

            return json.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            raise LLMProviderError("LLM returned invalid JSON structure.") from e
        except APIError as e:
            raise LLMProviderError(f"Claude API error: {e!s}") from e
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling Claude: {e!s}") from e

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[str]],
        max_tool_rounds: int = 10,
    ) -> str:
        raise LLMProviderError("generate_with_tools not implemented for Claude provider")
