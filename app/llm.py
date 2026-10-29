"""Provider-neutral LLM interface used by the agent loop.

Each provider keeps its own conversation state (message list in its native format), so the
agent loop never touches provider-specific shapes.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMStep:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # end_turn | tool_use | refusal | max_tokens | error
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


@dataclass
class Conversation:
    system: str
    messages: list[Any] = field(default_factory=list)


class LLMProvider(Protocol):
    name: str
    model: str

    def start(self, system: str, history: list[dict]) -> Conversation: ...

    async def step(self, conv: Conversation, tools: list[ToolSpec], output_schema: dict) -> LLMStep: ...

    def add_tool_results(self, conv: Conversation, results: list[tuple[str, str, bool]]) -> None: ...


# ---------------- Anthropic ----------------


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        import anthropic

        s = get_settings()
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key or None, timeout=60, max_retries=2)
        self.model = s.anthropic_model
        self.effort = s.anthropic_effort
        self.use_fallbacks = s.anthropic_fallbacks
        self.fast = s.anthropic_speed == "fast" and self.model.startswith("claude-opus")  # fast mode is Opus-only

    def start(self, system: str, history: list[dict]) -> Conversation:
        return Conversation(system=system, messages=[{"role": m["role"], "content": m["content"]} for m in history])

    async def step(self, conv: Conversation, tools: list[ToolSpec], output_schema: dict) -> LLMStep:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=8000,
            # Static system prompt first so it can be cached across turns.
            system=[{"type": "text", "text": conv.system, "cache_control": {"type": "ephemeral"}}],
            messages=conv.messages,
            # Empty effort = model default (models without effort levels, e.g. claude-haiku-4-5, reject it)
            output_config={"format": {"type": "json_schema", "schema": output_schema}}
            | ({"effort": self.effort} if self.effort else {}),
        )
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        betas = []
        if self.use_fallbacks:
            # Server-side refusal fallback: if the model declines, the API re-runs on a fallback model.
            betas.append("server-side-fallback-2026-07-01")
            kwargs["fallbacks"] = "default"
        if self.fast:
            # Fast mode (Opus only): same model, faster output, premium price.
            betas.append("fast-mode-2026-02-01")
            kwargs["speed"] = "fast"
        started = time.monotonic()
        try:
            if betas:
                resp = await self.client.beta.messages.create(**kwargs, betas=betas)
            else:
                resp = await self.client.messages.create(**kwargs)
        except self._anthropic.BadRequestError as exc:
            if self.use_fallbacks and "fallback" in str(exc).lower():
                log.warning("fallbacks not accepted for this account/model, retrying without: %s", exc)
                self.use_fallbacks = False
                return await self.step(conv, tools, output_schema)
            raise
        latency = int((time.monotonic() - started) * 1000)

        # Append the full content (thinking + tool_use blocks) unchanged, as the API requires.
        conv.messages.append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, input=dict(b.input)) for b in resp.content if b.type == "tool_use"]
        stop = resp.stop_reason or "end_turn"
        if stop == "pause_turn":
            stop = "tool_use" if calls else "end_turn"
        return LLMStep(
            text=text,
            tool_calls=calls,
            stop_reason="tool_use" if calls else stop,
            input_tokens=resp.usage.input_tokens + (resp.usage.cache_read_input_tokens or 0),
            output_tokens=resp.usage.output_tokens,
            latency_ms=latency,
        )

    def add_tool_results(self, conv: Conversation, results: list[tuple[str, str, bool]]) -> None:
        conv.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_err}
                    for tid, content, is_err in results
                ],
            }
        )


# ---------------- OpenAI ----------------


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        import openai

        s = get_settings()
        self.client = openai.AsyncOpenAI(api_key=s.openai_api_key or None, timeout=60, max_retries=2)
        self.model = s.openai_model
        self.reasoning_effort = s.openai_reasoning_effort

    def start(self, system: str, history: list[dict]) -> Conversation:
        msgs: list[Any] = [{"role": "system", "content": system}]
        msgs += [{"role": m["role"], "content": m["content"]} for m in history]
        return Conversation(system=system, messages=msgs)

    async def step(self, conv: Conversation, tools: list[ToolSpec], output_schema: dict) -> LLMStep:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=conv.messages,
            max_completion_tokens=8000,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "agent_turn", "schema": output_schema, "strict": True},
            },
        )
        if self.reasoning_effort:  # empty for non-reasoning models (gpt-4.1, gpt-4o), which reject it
            kwargs["reasoning_effort"] = self.reasoning_effort
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        started = time.monotonic()
        resp = await self.client.chat.completions.create(**kwargs)
        latency = int((time.monotonic() - started) * 1000)
        choice = resp.choices[0]
        msg = choice.message
        assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        conv.messages.append(assistant)
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, input=json.loads(tc.function.arguments or "{}"))
            for tc in (msg.tool_calls or [])
        ]
        stop = "tool_use" if calls else ("refusal" if msg.refusal else "end_turn")
        return LLMStep(
            text=msg.content or "",
            tool_calls=calls,
            stop_reason=stop,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            latency_ms=latency,
        )

    def add_tool_results(self, conv: Conversation, results: list[tuple[str, str, bool]]) -> None:
        for tid, content, is_err in results:
            conv.messages.append({"role": "tool", "tool_call_id": tid, "content": ("ERROR: " if is_err else "") + content})


# ---------------- Fake (tests) ----------------


class FakeProvider:
    """Replays scripted steps. Each script item is a list of tool calls or a final AgentTurn dict."""

    name = "fake"
    model = "fake"

    def __init__(self, script: list[list[dict] | dict]) -> None:
        self.script = list(script)
        self.calls: list[Conversation] = []
        self._n = 0

    def start(self, system: str, history: list[dict]) -> Conversation:
        conv = Conversation(system=system, messages=list(history))
        self.calls.append(conv)
        return conv

    async def step(self, conv: Conversation, tools: list[ToolSpec], output_schema: dict) -> LLMStep:
        item = self.script.pop(0)
        if isinstance(item, list):
            calls = []
            for c in item:
                self._n += 1
                calls.append(ToolCall(id=f"call_{self._n}", name=c["name"], input=c["input"]))
            conv.messages.append({"role": "assistant", "tool_calls": [c.__dict__ for c in calls]})
            return LLMStep(text="", tool_calls=calls, stop_reason="tool_use")
        conv.messages.append({"role": "assistant", "content": json.dumps(item)})
        return LLMStep(text=json.dumps(item), tool_calls=[], stop_reason="end_turn")

    def add_tool_results(self, conv: Conversation, results: list[tuple[str, str, bool]]) -> None:
        conv.messages.append({"role": "tool_results", "content": results})


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = OpenAIProvider() if get_settings().llm_provider == "openai" else AnthropicProvider()
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    global _provider
    _provider = provider
