from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.request
from typing import Any


DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_TIMEOUT_S = 10.0


logger = logging.getLogger(__name__)


class LLMClientError(RuntimeError):
    pass


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

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise LLMClientError(f"Ollama returned invalid JSON: {exc}") from exc

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
            logger.warning(
                "Invalid MIRA_OLLAMA_TIMEOUT_S; using %ss.",
                DEFAULT_OLLAMA_TIMEOUT_S,
            )
            return DEFAULT_OLLAMA_TIMEOUT_S

        if timeout <= 0:
            logger.warning(
                "MIRA_OLLAMA_TIMEOUT_S must be positive; using %ss.",
                DEFAULT_OLLAMA_TIMEOUT_S,
            )
            return DEFAULT_OLLAMA_TIMEOUT_S

        return timeout
