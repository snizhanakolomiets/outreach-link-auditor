from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI

from .models import LLMDecision


class OutreachLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_output_tokens: int,
        system_prompt_path: Path,
    ) -> None:
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY is empty. Copy .env.example to .env and add your key, "
                "or use --dry-run. No OpenAI request was made."
            )
        if not system_prompt_path.exists():
            raise FileNotFoundError(f"System prompt not found: {system_prompt_path}")
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.system_prompt = system_prompt_path.read_text(encoding="utf-8")

    def evaluate(self, context: dict[str, Any]) -> LLMDecision:
        response = self.client.responses.parse(
            model=self.model,
            instructions=self.system_prompt,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                        }
                    ],
                }
            ],
            text_format=LLMDecision,
            max_output_tokens=self.max_output_tokens,
            reasoning={"effort": "low"},
            store=False,
        )
        if response.output_parsed is None:
            raise ValueError("The model returned no validated structured output.")
        return response.output_parsed
