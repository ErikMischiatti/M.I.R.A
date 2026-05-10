from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class LLMClientError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        model: str = "llama3.2:3b",
        base_url: str = "http://localhost:11434",
        timeout_s: float = 20.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

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
        except urllib.error.URLError as exc:
            raise LLMClientError(f"Could not connect to Ollama: {exc}") from exc
        except TimeoutError as exc:
            raise LLMClientError("Ollama request timed out.") from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise LLMClientError(f"Ollama API returned invalid JSON: {exc}") from exc