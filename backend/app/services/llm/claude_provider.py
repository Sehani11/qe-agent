"""Claude LLM provider implementation."""

import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass

from anthropic import APIError, AsyncAnthropic, RateLimitError

from app.services.llm.provider import (
    TOOL_LOOP_TEMPERATURE,
    LLMProvider,
    LLMProviderError,
    ToolLoopStats,
    call_label,
    elision_placeholder,
    record_usage,
    usage_int,
)

#: Used when no model is supplied by the caller or by LLM_MODEL. LLM_MODEL's own
#: default is an OpenAI name, so it cannot serve as the Claude fallback.
DEFAULT_CLAUDE_MODEL = "claude-3-5-sonnet-20240620"

#: The generation at which Anthropic removed the sampling parameters.
#:
#: `temperature`, `top_p` and `top_k` are REJECTED WITH A 400 from Claude 4.7
#: onwards — Opus 4.7/4.8 and the whole 5 family, including Sonnet 5. They are
#: still accepted on 4.6 and earlier, which this app also offers (Haiku 4.5 is
#: in the model picker, and DEFAULT_CLAUDE_MODEL is a 3.5), so the parameter
#: can be neither sent unconditionally nor dropped outright.
_SAMPLING_REMOVED_FROM = 4.7

#: Trailing release date on a pinned id, e.g. `claude-haiku-4-5-20251001`.
_MODEL_DATE_SUFFIX = re.compile(r"-\d{8}$")

#: The generation in a model id. Both layouts put it in the first digits once
#: the date is stripped: `claude-3-5-sonnet` -> 3.5, `claude-sonnet-4-5` -> 4.5,
#: `claude-sonnet-5` -> 5.
_MODEL_GENERATION = re.compile(r"(\d+)(?:-(\d+))?")


def _accepts_sampling_params(model: str) -> bool:
    """Whether `model` still takes `temperature`.

    Unknown or unparseable ids answer NO, and that asymmetry is deliberate:
    omitting the parameter works on every model, while sending it to one that
    dropped it fails the whole request. Each new generation has removed more of
    these, so guessing "supported" for a name released after this code was
    written is the guess that breaks.
    """
    generation = _MODEL_GENERATION.search(_MODEL_DATE_SUFFIX.sub("", model))
    if generation is None:
        return False
    major, minor = generation.group(1), generation.group(2) or "0"
    try:
        return float(f"{major}.{minor}") < _SAMPLING_REMOVED_FROM
    except ValueError:  # pragma: no cover - the regex only matches digits
        return False


class ClaudeProvider(LLMProvider):
    """Anthropic Claude provider implementation."""

    supports_tools = True

    def __init__(self, api_key: str, model: str | None = None) -> None:
        # Stripped, so a key that is only whitespace is treated as absent.
        # Unstripped it is truthy, passes this guard, and reaches the SDK as an
        # unsendable header — `Illegal header value b'Bearer '` — instead of the
        # message below, which says what to set.
        self._api_key = (api_key or "").strip()
        if not self._api_key:
            raise LLMProviderError(
                "No Anthropic API key is configured. Set ANTHROPIC_API_KEY "
                "to use Claude models."
            )
        self._model = model or DEFAULT_CLAUDE_MODEL
        # Apply 30 second timeout per NFR-P3
        self._client = AsyncAnthropic(api_key=self._api_key, timeout=30.0)

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate text using Claude API."""
        try:
            kwargs = {
                "model": self._model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
            if _accepts_sampling_params(self._model):
                kwargs["temperature"] = 0.7
            if system_prompt:
                kwargs["system"] = system_prompt

            response = await self._client.messages.create(**kwargs)
            # Claude's response.content is a list of blocks, usually one text block
            content = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )
            return content
        except RateLimitError as e:
            # Caught BEFORE APIError, which it subclasses: the other way
            # round the general handler wins and the retryable flag is
            # never set, restoring the bug with no visible change.
            raise LLMProviderError(
                "The AI service is busy right now (rate limit reached). "
                "Please try again in a few seconds.",
                retryable=True,
            ) from e
        except APIError as e:
            raise LLMProviderError(f"Claude API error: {e!s}") from e
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling Claude: {e!s}") from e

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Stream text token-by-token using Anthropic's streaming API.

        Overrides the base class's fallback, which yields the whole answer as a
        single chunk once it is complete. That fallback is a correct stream and
        a poor experience: now that the model picker makes Claude selectable for
        chat, it would leave the user watching an empty bubble for the length of
        a full generation while OpenAI streamed word by word — the same feature
        behaving differently depending on a dropdown.
        """
        try:
            kwargs: dict[str, object] = {
                "model": self._model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
            if _accepts_sampling_params(self._model):
                kwargs["temperature"] = temperature
            if system_prompt:
                kwargs["system"] = system_prompt

            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
        except RateLimitError as e:
            # Caught BEFORE APIError, which it subclasses: the other way
            # round the general handler wins and the retryable flag is
            # never set, restoring the bug with no visible change.
            raise LLMProviderError(
                "The AI service is busy right now (rate limit reached). "
                "Please try again in a few seconds.",
                retryable=True,
            ) from e
        except APIError as e:
            raise LLMProviderError(f"Claude API error: {e!s}") from e
        except LLMProviderError:
            raise
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
                "model": self._model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
                "system": sys_msg,
            }
            if _accepts_sampling_params(self._model):
                # Low, for the JSON this method has to parse back — on models
                # that dropped the parameter, the API default applies instead.
                kwargs["temperature"] = 0.2

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
        except RateLimitError as e:
            # Caught BEFORE APIError, which it subclasses: the other way
            # round the general handler wins and the retryable flag is
            # never set, restoring the bug with no visible change.
            raise LLMProviderError(
                "The AI service is busy right now (rate limit reached). "
                "Please try again in a few seconds.",
                retryable=True,
            ) from e
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
        stats: ToolLoopStats | None = None,
        tool_result_window: int = 0,
    ) -> str:
        """Run a tool-calling agent loop using Anthropic's tool-use protocol.

        Callers pass OpenAI-shaped messages and tools — that is the format
        `agentic_verification_service` builds, and it is shared by every
        provider — so both are translated here rather than at the call site.
        Keeping the translation inside the provider is what lets a caller swap
        providers without knowing either wire format.

        The round budget matches OpenAIProvider's: at most `max_tool_rounds`
        API calls total, with the last one reserved for a forced text answer.
        """
        try:
            system_prompt, conversation = _split_system(messages)
            conversation = _to_claude_messages(conversation)
            claude_tools = [_to_claude_tool(tool) for tool in tools]
            system_blocks = _cacheable_system(system_prompt)
            # Every tool result written so far, oldest first, so the older ones
            # can be elided once the window fills.
            result_slots: list[_ToolResultSlot] = []

            # Verification is a judgement and must be reproducible — see
            # TOOL_LOOP_TEMPERATURE. Omitted entirely on the models that
            # removed the parameter, where sending it is a 400 for the whole
            # request; those models are effectively low-variance by default.
            sampling: dict = (
                {"temperature": TOOL_LOOP_TEMPERATURE}
                if _accepts_sampling_params(self._model)
                else {}
            )

            # Rounds 0..(max_tool_rounds-2) may call tools; the final round
            # below is the forced answer, keeping total calls <= max_tool_rounds.
            for _round in range(max_tool_rounds - 1):
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    system=system_blocks,
                    messages=conversation,
                    tools=claude_tools,
                    **sampling,
                )
                _record(stats, response)

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    return _text_of(response.content)

                if stats is not None:
                    stats.tool_calls.append([block.name for block in tool_uses])

                # The assistant turn must be echoed back verbatim — the
                # tool_use blocks it carries are what each tool_result below
                # refers to by id.
                conversation.append(
                    {"role": "assistant", "content": response.content}
                )

                # Every result goes back in ONE user message. Splitting them
                # across several messages is accepted by the API but trains the
                # model out of requesting parallel calls, which costs a round
                # trip per tool on later scenarios.
                tool_results: list[dict] = []
                for block in tool_uses:
                    tool_input = dict(block.input)
                    # The executor is the CALLER's code, running inside this
                    # method's try. An exception from it was being reported as
                    # "Unexpected error calling Claude", sending anyone
                    # debugging it to the wrong module entirely.
                    try:
                        result = await tool_executor(block.name, tool_input)
                    except LLMProviderError:
                        raise
                    except Exception as exc:
                        raise LLMProviderError(
                            f"Tool '{block.name}' failed: {exc!s}"
                        ) from exc
                    result_block = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                    tool_results.append(result_block)
                    # Held by reference rather than by a key on the block
                    # itself: this bookkeeping is ours, and an unrecognised
                    # key inside a tool_result is rejected by the API.
                    result_slots.append(
                        _ToolResultSlot(
                            block=result_block,
                            label=call_label(block.name, tool_input),
                        )
                    )
                conversation.append({"role": "user", "content": tool_results})
                _elide_old_results(result_slots, tool_result_window)
                # Everything up to here is replayed verbatim on the next round,
                # and tool results are the bulk of it — a single file read can
                # be 10 000 tokens. Marking the newest one makes the next round
                # read the whole conversation so far from cache instead of
                # re-paying for it, which is most of the cost of a long loop.
                _roll_conversation_breakpoint(conversation)

            # Final round: no more tools, answer from what was gathered.
            conversation.append({
                "role": "user",
                "content": (
                    "You have used the maximum number of tool calls. "
                    "Based on the evidence gathered so far, provide your final "
                    "verdict. "
                    "Reply with ONLY a valid JSON object — no explanation, no markdown."
                ),
            })
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system_blocks,
                messages=conversation,
                tools=claude_tools,
                tool_choice={"type": "none"},
                **sampling,
            )
            _record(stats, response)
            return _text_of(response.content)
        except RateLimitError as e:
            # Caught BEFORE APIError, which it subclasses: the other way
            # round the general handler wins and the retryable flag is
            # never set, restoring the bug with no visible change.
            raise LLMProviderError(
                "The AI service is busy right now (rate limit reached). "
                "Please try again in a few seconds.",
                retryable=True,
            ) from e
        except APIError as e:
            raise LLMProviderError(f"Claude API error: {e!s}") from e
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling Claude: {e!s}") from e


@dataclass
class _ToolResultSlot:
    """One tool_result block, held by reference, and what produced it."""

    block: dict
    label: str
    elided: bool = False


def _record(stats: ToolLoopStats | None, response: object) -> None:
    """Add one Anthropic response's token usage to ``stats``.

    Anthropic splits the prompt three ways and its ``input_tokens`` counts only
    the uncached part, so the three are summed back into one total. Reporting
    the raw ``input_tokens`` here would make a well-cached Claude run look an
    order of magnitude cheaper than an identical OpenAI one purely because the
    vendors count differently.
    """
    if stats is None:
        return
    usage = getattr(response, "usage", None)
    uncached = usage_int(getattr(usage, "input_tokens", None))
    cache_read = usage_int(getattr(usage, "cache_read_input_tokens", None))
    cache_write = usage_int(getattr(usage, "cache_creation_input_tokens", None))
    record_usage(
        stats,
        prompt=uncached + cache_read + cache_write,
        cached=cache_read,
        output=usage_int(getattr(usage, "output_tokens", None)),
    )


def _elide_old_results(slots: list[_ToolResultSlot], window: int) -> None:
    """Replace all but the ``window`` newest tool results with a placeholder.

    Mutates the blocks in place. A window of 0 disables this, which is the
    default and the historical behaviour.

    Worth knowing before turning it on here: Anthropic already makes the replay
    cheap via the rolling cache breakpoint, and rewriting an earlier block
    changes the prefix, so every elision forces a cache miss and a fresh write
    of everything after it. On this provider the knob most likely costs more
    than it saves — it exists so that can be measured rather than argued, and
    the case it was built for is OpenAI's per-minute token ceiling.
    """
    if window <= 0 or len(slots) <= window:
        return
    for slot in slots[:-window]:
        if slot.elided:
            continue
        slot.block["content"] = elision_placeholder(slot.label)
        # Currently redundant — the caller re-rolls the breakpoint straight
        # after — but a breakpoint left on rewritten content would be a cache
        # entry that can never be hit again, and this keeps the function
        # correct on its own rather than by grace of the call order.
        slot.block.pop("cache_control", None)
        slot.elided = True


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Separate the system prompt from the conversation.

    Anthropic takes the system prompt as a top-level request parameter; a
    message with role "system" in the messages array is rejected. Callers build
    OpenAI-shaped messages where it is the first entry, so it is lifted out here.
    """
    system_parts: list[str] = []
    conversation: list[dict] = []

    for message in messages:
        if message.get("role") == "system":
            system_parts.append(str(message.get("content", "")))
        else:
            conversation.append(dict(message))

    return "\n\n".join(part for part in system_parts if part), conversation


def _cacheable_system(system_prompt: str) -> list[dict] | str:
    """Render the system prompt as a cacheable block, or leave it a plain string.

    Anthropic assembles the cache prefix in a fixed order — tools, then system,
    then messages — so one breakpoint at the end of the system prompt covers the
    tool definitions as well. That pair is byte-identical on every call of every
    scenario in a run (roughly 1 500 tokens of instructions and tool schemas
    re-sent tens of times), which makes it the cheapest possible thing to stop
    paying for repeatedly.

    An empty prompt has nothing to cache and stays a plain string, so a caller
    that sends no system message is unaffected. Below Anthropic's minimum
    cacheable length the breakpoint is simply ignored by the API rather than
    rejected, so this is safe for short prompts too — it just does nothing.
    """
    if not system_prompt:
        return system_prompt
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _roll_conversation_breakpoint(conversation: list[dict]) -> None:
    """Move the rolling cache breakpoint onto the newest tool_result block.

    Mutates ``conversation`` in place. Each round replays every earlier round in
    full, so without this the loop pays for the same tool output again on every
    subsequent call — quadratic in the number of rounds, over content where a
    single file read is 10 000 tokens.

    The breakpoint is *moved* rather than added: Anthropic caps a request at
    four breakpoints, and one per round would blow that cap on the fifth. Moving
    it costs nothing, because a cache lookup matches the longest prefix it can
    find — the previous round's entry is still a hit, and this round's write
    extends it.

    Only ``tool_result`` blocks are touched. The breakpoint the caller set on the
    shared prompt prefix sits on a text block and must survive, since that one is
    what the *other* scenarios in the run are reading from.
    """
    newest: dict | None = None

    for message in conversation:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                block.pop("cache_control", None)
                newest = block

    if newest is not None:
        newest["cache_control"] = {"type": "ephemeral"}


def _to_claude_messages(messages: list[dict]) -> list[dict]:
    """Translate caller messages into Anthropic shape, merging same-role runs.

    Two things happen here, and both are about a difference between the wire
    formats rather than about behaviour:

    First, a message flagged ``cache_control`` becomes a text block carrying an
    ephemeral cache breakpoint. That marks everything up to and including the
    block as a reusable prefix — the system prompt and the code evidence every
    scenario in a run shares — so later calls read it at a fraction of the input
    price instead of re-paying for it. The flag is this codebase's own and never
    reaches the API.

    Second, consecutive same-role messages are merged into one message of
    several blocks. Anthropic expects roles to alternate, while the caller
    writes OpenAI-shaped messages where two user turns in a row are ordinary.
    Merging is also what makes the caching work at all: the shared prefix and
    the per-scenario text have to be separate *blocks* of one user message so
    the breakpoint can fall between them.

    Content that is not a plain string (tool results, echoed assistant turns) is
    passed through untouched.
    """
    converted: list[dict] = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")

        if not isinstance(content, str):
            converted.append(
                {key: value for key, value in message.items() if key != "cache_control"}
            )
            continue

        # An empty text block is rejected by the API, and carries nothing.
        if not content:
            continue

        block: dict = {"type": "text", "text": content}
        if message.get("cache_control"):
            block["cache_control"] = {"type": "ephemeral"}

        previous = converted[-1] if converted else None
        if (
            previous is not None
            and previous.get("role") == role
            and isinstance(previous.get("content"), list)
        ):
            previous["content"].append(block)
        else:
            converted.append({"role": role, "content": [block]})

    return converted


def _to_claude_tool(tool: dict) -> dict:
    """Convert one OpenAI-shaped tool definition to Anthropic's shape.

    OpenAI nests the definition under `function` and calls the schema
    `parameters`; Anthropic keeps it flat and calls it `input_schema`. A tool
    already in Anthropic shape is passed through, so a caller that builds
    native definitions is not broken by this translation.
    """
    if "input_schema" in tool:
        return tool

    function = tool.get("function")
    if not isinstance(function, dict) or not function.get("name"):
        raise LLMProviderError(f"Unrecognized tool definition: {tool!r}")

    return {
        "name": function["name"],
        "description": function.get("description", ""),
        "input_schema": function.get(
            "parameters", {"type": "object", "properties": {}}
        ),
    }


def _text_of(content: list) -> str:
    """Join the text blocks of a response, ignoring thinking/tool_use blocks."""
    return "".join(block.text for block in content if block.type == "text")
