from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol, cast


DEFAULT_NIGHTLY_MODEL = "gpt-5.4-nano"
DEFAULT_INIT_MODEL = "gpt-5.4-mini"


class ResponsesResource(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class AsyncOpenAIClient(Protocol):
    @property
    def responses(self) -> ResponsesResource: ...


def nightly_model() -> str:
    return os.environ.get("WIKI_KEEPER_NIGHTLY_MODEL", DEFAULT_NIGHTLY_MODEL)


def init_model() -> str:
    return os.environ.get("WIKI_KEEPER_INIT_MODEL", DEFAULT_INIT_MODEL)


def require_api_key(context: str) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(f"OPENAI_API_KEY is required for {context}")


def create_openai_client() -> AsyncOpenAIClient:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "openai package is required. Install dependencies with `pip install -e .`."
        ) from exc
    return cast(AsyncOpenAIClient, AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY")))


async def complete_json_schema(
    client: AsyncOpenAIClient,
    *,
    model: str,
    instructions: str,
    input_text: str,
    schema_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    response = await client.responses.create(
        model=model,
        instructions=instructions,
        input=input_text,
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    )
    parsed = response_parsed_json(response)
    if parsed is not None:
        return parsed
    return parse_json_object(response_text(response))


def response_text(response: Any) -> str:
    if isinstance(response, dict):
        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        output = response.get("output", [])
    else:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text:
            return output_text
        output = getattr(response, "output", [])

    chunks: list[str] = []
    for item in output or []:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", [])
        for part in content or []:
            if isinstance(part, dict):
                if part.get("type") in {"output_text", "text"} and part.get("text"):
                    chunks.append(part["text"])
            else:
                kind = getattr(part, "type", "")
                text = getattr(part, "text", None)
                if kind in {"output_text", "text"} and isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks).strip()


def response_parsed_json(response: Any) -> dict[str, Any] | None:
    output = response.get("output", []) if isinstance(response, dict) else getattr(response, "output", [])
    for item in output or []:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", [])
        for part in content or []:
            parsed = part.get("parsed") if isinstance(part, dict) else getattr(part, "parsed", None)
            if isinstance(parsed, dict):
                return parsed
    return None


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("Model response did not contain a JSON object")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Model response JSON must be an object")
    return data
