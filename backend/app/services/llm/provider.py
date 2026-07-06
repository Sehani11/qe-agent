"""LLM Provider abstract base class.

All LLM interactions in the application MUST go through this interface.
Never call openai. or anthropic. SDK methods directly in business logic.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable


class LLMProvider(ABC):
    """Abstract base class for LLM provider integrations.

    Concrete implementations (ClaudeProvider, OpenAIProvider) must
    implement all abstract methods. Business logic services receive
    an LLMProvider instance from the factory — never a concrete class.
    """

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate a text completion from the LLM.

        Args:
            prompt: The user prompt to send to the LLM.
            system_prompt: Optional system-level instructions.

        Returns:
            The LLM's text response.

        Raises:
            LLMProviderError: If the LLM call fails.
        """

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate a structured (JSON) response from the LLM.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system-level instructions.
            response_format: Optional JSON schema for response structure.

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            LLMProviderError: If the LLM call fails or response is not valid JSON.
        """


    @abstractmethod
    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[str]],
        max_tool_rounds: int = 10,
    ) -> str:
        """Run a tool-calling agent loop until the model returns a final text response.

        The provider handles the provider-native message format and tool call protocol.
        Tool execution is delegated to tool_executor(tool_name, tool_args) -> str.

        Args:
            messages:       Initial conversation (system + user messages in provider format).
            tools:          Tool definitions in provider-native schema.
            tool_executor:  Async callable that executes a tool and returns its string result.
            max_tool_rounds: Maximum tool call iterations before forcing a final answer.

        Returns:
            The model's final text response after all tool calls are resolved.

        Raises:
            LLMProviderError: If any LLM call in the loop fails unrecoverably.
        """


class LLMProviderError(Exception):
    """Raised when an LLM provider call fails."""
