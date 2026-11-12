"""The tool-calling loop between a chat model and the harness tools."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Protocol

from ..project import Project
from .tools import TOOLS, WRITE_TOOLS, call_tool

MAX_STEPS = 16
MAX_TOOL_RESULT_CHARS = 12_000

SYSTEM_PROMPT = """\
You help a data scientist map a CSV file onto a target JSON Schema.

You cannot edit the input data or the output records. The only thing you can change is the
mapping: one JSONata expression per output field. A deterministic engine applies the mapping to
every input row to produce one output record per row, and each record is validated against the
schema. Use the tools to look at the columns, the schema and the validation errors, then change
the mapping with set_field and remove_field.

How expressions work:
- An expression is evaluated against one input row: an object of column name -> string.
  Every input value is a string; convert with $number(), $boolean() and so on.
- Refer to a column by name, or in backticks if the name has spaces or punctuation:
  `Start Date`.
- Empty cells are absent from the row. An expression with no result leaves the field out of
  the record, which is right for optional fields.
- Arrays: [`City 1`, `City 2`] gives an array of the cells that are filled.
- JSONata turns a one-item result into the item itself. When the schema wants an array, end
  the expression with [] so a single result stays an array: $distinct([`Genre 1`, `Genre 2`])[]
- $now(), $millis() and $random() are not available: output must not change between runs.

How to work:
- Look before you write: check real values with profile_column or sample_rows, and try an
  expression with preview_expression before set_field.
- Never invent data. If the input has nothing for a field, leave the field unmapped and say so.
- Do not hide errors by weakening a value to fit; if rows genuinely violate the schema, report
  how many and why, and let the user decide.
- After changing the mapping, say briefly what you changed and what happened to the error count.
- You may suggest a different schema or extra validation rules, but you cannot change the schema.
"""


def _normalise_calls(calls: list, step: int) -> list[dict]:
    """Make a model's tool calls safe to send back to it.

    Small local models do not always follow the OpenAI shape: some give `arguments` as an object
    instead of a JSON string, and some leave out the call id. The endpoint rejects the next
    request if the history contains either, so both are repaired here.
    """
    out = []
    for i, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        arguments = fn.get("arguments")
        if arguments is None:
            arguments = "{}"
        elif not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        out.append(
            {
                "id": call.get("id") or f"call_{step}_{i}",
                "type": "function",
                "function": {"name": str(fn.get("name") or ""), "arguments": arguments},
            }
        )
    return out


class ChatClient(Protocol):
    def complete(self, model: str, messages: list[dict], tools: list[dict]) -> dict:
        """Return the assistant message as a dict: {"content": ..., "tool_calls": [...]}."""
        ...


class OpenAIChatClient:
    """Any OpenAI-compatible chat endpoint: Ollama, llama.cpp, vLLM, OpenRouter and so on."""

    def __init__(self, base_url: str, api_key: str = "none"):
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=600)

    def complete(self, model: str, messages: list[dict], tools: list[dict]) -> dict:
        response = self._client.chat.completions.create(
            model=model, messages=messages, tools=tools, temperature=0
        )
        message = response.choices[0].message.model_dump(exclude_none=True)
        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
        }


class Conversation:
    """One ongoing chat about one project. The model can be switched between turns."""

    def __init__(self, project: Project, lock: threading.RLock | None = None):
        self.project = project
        self.lock = lock or threading.RLock()
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def reset(self) -> None:
        del self.messages[1:]

    def turn(
        self,
        client: ChatClient,
        model: str,
        user_text: str,
        emit: Callable[[dict], None] = lambda event: None,
    ) -> None:
        """Run one user turn to completion, reporting each step through `emit`."""
        self.messages.append({"role": "user", "content": user_text})
        for step in range(MAX_STEPS):
            message = client.complete(model, self.messages, TOOLS)
            calls = _normalise_calls(message.get("tool_calls") or [], step)
            entry = {"role": "assistant", "content": message.get("content") or ""}
            if calls:
                entry["tool_calls"] = calls
            self.messages.append(entry)
            if entry["content"]:
                emit({"type": "assistant", "text": entry["content"], "model": model})
            if not calls:
                return
            for call in calls:
                name, arguments = call["function"]["name"], call["function"]["arguments"]
                emit({"type": "tool_call", "name": name, "arguments": arguments})
                with self.lock:
                    outcome = call_tool(self.project, name, arguments, author=model)
                text = json.dumps(outcome, ensure_ascii=False)
                if len(text) > MAX_TOOL_RESULT_CHARS:
                    text = text[:MAX_TOOL_RESULT_CHARS] + "… (truncated)"
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
                emit(
                    {
                        "type": "tool_result",
                        "name": name,
                        "ok": "error" not in outcome,
                        "changed_mapping": name in WRITE_TOOLS and "error" not in outcome,
                        "result": outcome,
                    }
                )
        emit({"type": "stopped", "text": f"Stopped after {MAX_STEPS} steps without finishing."})
