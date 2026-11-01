"""Deterministic context manager between SQLite conversation history, memories, and llama.cpp."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .memory import format_memories_for_system_prompt

CHAT_TEMPLATE_OVERHEAD_PER_MSG = 8
DEFAULT_MAX_HISTORY_MESSAGES = 40


class PromptTooLong(ValueError):
    """The system prompt and latest user message alone exceed the available context window."""


def default_token_counter(text: str) -> int:
    """Fallback deterministic token estimator when no model tokenizer is attached."""
    if not text:
        return 0
    words = text.encode("utf-8").split()
    char_est = (len(text.encode("utf-8")) + 3) // 4
    return max(len(words), char_est)


@dataclass
class ContextSelection:
    messages: list[dict]
    retrieved_memories: list[dict] = field(default_factory=list)
    included_history_count: int = 0
    omitted_history_count: int = 0
    prompt_token_estimate: int = 0
    token_budget: int = 0


class ContextManager:
    """Selects recent conversation messages and memories within the model's context budget.

    Never modifies or deletes persisted messages in SQLite; omitted messages are simply
    excluded from the in-memory prompt payload sent to llama.cpp.
    """

    def __init__(self, token_counter: Callable[[str], int] | None = None,
                 max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES):
        self.token_counter = token_counter or default_token_counter
        self.max_history_messages = max_history_messages

    def message_cost(self, content: str) -> int:
        return self.token_counter(content) + CHAT_TEMPLATE_OVERHEAD_MSG_COST

    def build_system_content(
        self,
        base_system_prompt: str,
        memories: list[dict],
        tool_instructions: str | None = None,
    ) -> str:
        base = (base_system_prompt or "").strip()
        parts = [p for p in (base, (tool_instructions or "").strip(), format_memories_for_system_prompt(memories)) if p]
        return "\n\n".join(parts)

    def select_context(
        self,
        *,
        system_prompt: str,
        history_messages: list[dict],
        latest_user_message: dict,
        n_ctx: int,
        max_tokens: int,
        memories: list[dict] | None = None,
        tool_instructions: str | None = None,
    ) -> ContextSelection:
        budget = n_ctx - max_tokens
        if budget <= 0:
            raise PromptTooLong(
                f"Context window ({n_ctx}) must be larger than reserved output tokens ({max_tokens})"
            )

        active_memories = list(memories or [])
        active_tools = tool_instructions
        user_cost = self.message_cost(latest_user_message["content"])

        # If tool instructions would crowd out a tight context window, drop tool instructions first
        if active_tools:
            with_tools = self.build_system_content(system_prompt, active_memories, active_tools)
            if self.message_cost(with_tools) + user_cost > budget - 48:
                active_tools = None

        # Ensure system prompt + memories + latest user message fit; trim memories first if tight.
        while True:
            sys_content = self.build_system_content(system_prompt, active_memories, active_tools)
            sys_cost = self.message_cost(sys_content)
            if sys_cost + user_cost <= budget or not active_memories:
                break
            active_memories.pop()

        if sys_cost + user_cost > budget:
            raise PromptTooLong(
                f"Message is too long: about {sys_cost + user_cost} tokens, limit is {budget} "
                f"(context {n_ctx} minus {max_tokens} reply tokens)"
            )

        # Bound how far back we look even if the context window is huge.
        total_prior = len(history_messages)
        bounded_history = (
            history_messages[-self.max_history_messages :]
            if self.max_history_messages and total_prior > self.max_history_messages
            else list(history_messages)
        )

        # Walk backward from the most recent historical turn so recent continuity is preserved.
        used = sys_cost + user_cost
        kept_reversed: list[dict] = []
        for msg in reversed(bounded_history):
            if msg.get("role") == "system":
                continue
            cost = self.message_cost(msg["content"])
            if used + cost > budget:
                break
            used += cost
            kept_reversed.append({"role": msg["role"], "content": msg["content"]})

        selected_history = list(reversed(kept_reversed))
        omitted = total_prior - len(selected_history)
        final_messages = [
            {"role": "system", "content": sys_content},
            *selected_history,
            {"role": "user", "content": latest_user_message["content"]},
        ]
        return ContextSelection(
            messages=final_messages,
            retrieved_memories=active_memories,
            included_history_count=len(selected_history),
            omitted_history_count=max(0, omitted),
            prompt_token_estimate=used,
            token_budget=budget,
        )


CHAT_TEMPLATE_OVERHEAD_MSG_COST = CHAT_TEMPLATE_OVERHEAD_PER_MSG
