from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


DEFAULT_ORCHESTRATOR_MODEL = "gpt-5-mini"
DEFAULT_READER_MODEL = "gpt-5-nano"
DEFAULT_ORCHESTRATOR_REASONING = "medium"
DEFAULT_READER_REASONING = "low"


def require_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is required for run-nightly and run_review"
        )


@dataclass
class ModelConfig:
    orchestrator_model: str = DEFAULT_ORCHESTRATOR_MODEL
    reader_model: str = DEFAULT_READER_MODEL
    orchestrator_reasoning: str = DEFAULT_ORCHESTRATOR_REASONING
    reader_reasoning: str = DEFAULT_READER_REASONING

    @classmethod
    def from_env(cls) -> "ModelConfig":
        return cls(
            orchestrator_model=os.environ.get(
                "WIKI_KEEPER_ORCHESTRATOR_MODEL", DEFAULT_ORCHESTRATOR_MODEL
            ),
            reader_model=os.environ.get(
                "WIKI_KEEPER_READER_MODEL", DEFAULT_READER_MODEL
            ),
            orchestrator_reasoning=os.environ.get(
                "WIKI_KEEPER_ORCHESTRATOR_REASONING",
                DEFAULT_ORCHESTRATOR_REASONING,
            ),
            reader_reasoning=os.environ.get(
                "WIKI_KEEPER_READER_REASONING", DEFAULT_READER_REASONING
            ),
        )


class LLMClient:
    def __init__(self, *, client: Any | None = None, config: ModelConfig | None = None):
        self.config = config or ModelConfig.from_env()
        self._client = client

    def _openai_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "openai package is required for nightly review. "
                "Install dependencies with `pip install -e .`."
            ) from exc
        self._client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        return self._client

    def complete_text(
        self, *, system_prompt: str, user_prompt: str, model: str, reasoning: str
    ) -> str:
        client = self._openai_client()
        if hasattr(client, "complete_text"):
            return client.complete_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model,
                reasoning=reasoning,
            )
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            reasoning={"effort": reasoning},
        )
        return _response_text(response)

    def complete_json(
        self, *, system_prompt: str, user_prompt: str, model: str, reasoning: str
    ) -> dict[str, Any]:
        text = self.complete_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            reasoning=reasoning,
        )
        return _parse_json_object(text)

    def complete_json_schema(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        reasoning: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._openai_client()
        if hasattr(client, "complete_json_schema"):
            return client.complete_json_schema(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model,
                reasoning=reasoning,
                schema_name=schema_name,
                schema=schema,
            )
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            reasoning={"effort": reasoning},
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        parsed = _response_parsed_json(response)
        if parsed is not None:
            return parsed
        text = _response_text(response)
        return _parse_json_object(text)


def _response_text(response: Any) -> str:
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


def _response_parsed_json(response: Any) -> dict[str, Any] | None:
    output = response.get("output", []) if isinstance(response, dict) else getattr(response, "output", [])
    for item in output or []:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", [])
        for part in content or []:
            if isinstance(part, dict):
                parsed = part.get("parsed")
            else:
                parsed = getattr(part, "parsed", None)
            if isinstance(parsed, dict):
                return parsed
    return None


def _parse_json_object(text: str) -> dict[str, Any]:
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
