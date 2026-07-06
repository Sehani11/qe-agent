"""OpenAI LLM provider implementation."""

import json
from collections.abc import Awaitable, Callable

from openai import AsyncOpenAI, OpenAIError, RateLimitError

from app.core.config import settings
from app.services.llm.provider import LLMProvider, LLMProviderError


class OpenAIProvider(LLMProvider):
    """OpenAI provider implementation."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        if not self._api_key:
            raise LLMProviderError("OpenAI API key is missing. Check LLM_API_KEY.")
        # Apply 30 second timeout per NFR-P3.
        # max_retries lets the SDK auto-retry 429s with exponential backoff,
        # honoring the Retry-After header — absorbs transient TPM bursts.
        self._client = AsyncOpenAI(api_key=self._api_key, timeout=30.0, max_retries=3)

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate text using OpenAI API."""
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = await self._client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=0.7,
            )
            return response.choices[0].message.content or ""
        except RateLimitError as e:
            raise LLMProviderError(
                "The AI service is busy right now (rate limit reached). "
                "Please try again in a few seconds."
            ) from e
        except OpenAIError as e:
            raise LLMProviderError(f"OpenAI API error: {e!s}") from e
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling OpenAI: {e!s}") from e

    async def generate_structured(
        self,
        prompt: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate structured JSON using OpenAI API via JSON mode."""
        try:
            messages = []
            sys_msg = system_prompt or "You are a helpful assistant."
            if response_format:
                schema_str = json.dumps(response_format, indent=2)
                sys_msg += (
                    f"\n\nYou MUST reply with ONLY a valid JSON object matching this schema:\n"
                    f"{schema_str}"
                )

            messages.append({"role": "system", "content": sys_msg})
            messages.append({"role": "user", "content": prompt})

            response = await self._client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMProviderError("LLM returned invalid JSON structure.") from e
        except RateLimitError as e:
            raise LLMProviderError(
                "The AI service is busy right now (rate limit reached). "
                "Please try again in a few seconds."
            ) from e
        except OpenAIError as e:
            raise LLMProviderError(f"OpenAI API error: {e!s}") from e
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling OpenAI: {e!s}") from e

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[str]],
        max_tool_rounds: int = 10,
    ) -> str:
        """Run a tool-calling agent loop using OpenAI function calling."""
        try:
            current_messages = list(messages)
            # Rounds 0..(max_tool_rounds-2) allow tool calls.
            # The final round is reserved for the forced text answer below —
            # keeping total API calls ≤ max_tool_rounds as required by AC 11.
            for _round in range(max_tool_rounds - 1):
                response = await self._client.chat.completions.create(
                    model=settings.llm_model,
                    messages=current_messages,
                    tools=tools,
                    tool_choice="auto",
                )
                choice = response.choices[0]

                if choice.finish_reason == "stop" or choice.message.tool_calls is None:
                    return choice.message.content or ""

                # Append assistant message with tool calls
                current_messages.append(choice.message.model_dump(exclude_none=True))

                # Execute each tool call and append results
                for tool_call in choice.message.tool_calls:
                    tool_args = json.loads(tool_call.function.arguments)
                    result = await tool_executor(tool_call.function.name, tool_args)
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })

            # Final round (max_tool_rounds-th call): force a text answer.
            # Passing tools= with tool_choice="none" is required by the OpenAI API.
            current_messages.append({
                "role": "user",
                "content": (
                    "You have used the maximum number of tool calls. "
                    "Based on the evidence gathered so far, provide your final verdict. "
                    "Reply with ONLY a valid JSON object — no explanation, no markdown."
                ),
            })
            response = await self._client.chat.completions.create(
                model=settings.llm_model,
                messages=current_messages,
                tools=tools,
                tool_choice="none",
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""
        except RateLimitError as e:
            raise LLMProviderError(
                "The AI service is busy right now (rate limit reached). "
                "Please try again in a few seconds."
            ) from e
        except OpenAIError as e:
            raise LLMProviderError(f"OpenAI API error: {e!s}") from e
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling OpenAI: {e!s}") from e
