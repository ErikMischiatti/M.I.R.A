from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any


DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_TIMEOUT_S = 10.0


class LLMClientError(RuntimeError):
    pass


RAW_RESPONSE_PREVIEW_CHARS = 180


class OllamaClient:
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_s: float | None = None,
    ):
        self.model = (
            model
            if model is not None
            else os.getenv("MIRA_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        )
        self.base_url = (
            base_url
            if base_url is not None
            else os.getenv("MIRA_OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
        ).rstrip("/")
        self.timeout_s = (
            timeout_s
            if timeout_s is not None
            else self._timeout_from_environment()
        )

    def generate_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": schema,
            "options": {
                "temperature": temperature,
            },
        }

        raw_response = self._post_json("/api/generate", payload)

        response_text = raw_response.get("response", "")
        if not response_text:
            raise LLMClientError("Ollama returned an empty response.")

        response_text = self._clean_structured_response(response_text)

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as exc:
            preview = self._preview_text(response_text)
            raise LLMClientError(
                f"Ollama returned invalid JSON: {exc}. Raw preview: {preview}"
            ) from exc

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_s,
            ) as response:
                response_body = response.read().decode("utf-8")
        except (TimeoutError, socket.timeout) as exc:
            raise LLMClientError("Ollama request timed out.") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LLMClientError("Ollama request timed out.") from exc
            raise LLMClientError(f"Could not connect to Ollama: {exc}") from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise LLMClientError(f"Ollama API returned invalid JSON: {exc}") from exc

    def _timeout_from_environment(self) -> float:
        raw_timeout = os.getenv("MIRA_OLLAMA_TIMEOUT_S")
        if raw_timeout is None:
            return DEFAULT_OLLAMA_TIMEOUT_S

        try:
            timeout = float(raw_timeout)
        except ValueError:
            print(
                "[LLM] Invalid MIRA_OLLAMA_TIMEOUT_S; "
                f"using {DEFAULT_OLLAMA_TIMEOUT_S}s."
            )
            return DEFAULT_OLLAMA_TIMEOUT_S

        if timeout <= 0:
            print(
                "[LLM] MIRA_OLLAMA_TIMEOUT_S must be positive; "
                f"using {DEFAULT_OLLAMA_TIMEOUT_S}s."
            )
            return DEFAULT_OLLAMA_TIMEOUT_S

        return timeout

    def _clean_structured_response(self, response_text: str) -> str:
        cleaned = response_text.strip()
        fenced = self._strip_json_code_fence(cleaned)
        if fenced is not None:
            cleaned = fenced.strip()

        extracted = self._extract_first_json_object(cleaned)
        if extracted is not None:
            return extracted

        return cleaned

    def _strip_json_code_fence(self, text: str) -> str | None:
        if not text.startswith("```"):
            return None

        lines = text.splitlines()
        if len(lines) < 3:
            return None

        opening = lines[0].strip().lower()
        closing = lines[-1].strip()
        if opening not in {"```", "```json"} or closing != "```":
            return None

        return "\n".join(lines[1:-1])

    def _extract_first_json_object(self, text: str) -> str | None:
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escaped = False

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]

        return None

    def _preview_text(self, text: str) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= RAW_RESPONSE_PREVIEW_CHARS:
            return repr(normalized)
        return repr(normalized[:RAW_RESPONSE_PREVIEW_CHARS].rstrip() + "...")
