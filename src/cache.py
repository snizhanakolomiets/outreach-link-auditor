from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import LLMDecision


class ResultCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_cache_v2 (
                domain TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                project_id TEXT NOT NULL,
                context_hash TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (domain, campaign_id, prompt_version, project_id)
            )
            """
        )
        self.connection.commit()

    def get(self, domain: str, campaign_id: str, prompt_version: str, project_id: str, context_hash: str) -> LLMDecision | None:
        row = self.connection.execute(
            """SELECT response_json, context_hash FROM llm_cache_v2
               WHERE domain=? AND campaign_id=? AND prompt_version=? AND project_id=?""",
            (domain, campaign_id, prompt_version, project_id),
        ).fetchone()
        if not row or row[1] != context_hash:
            return None
        try:
            return LLMDecision.model_validate(json.loads(row[0]))
        except (ValueError, json.JSONDecodeError):
            return None

    def set(self, domain: str, campaign_id: str, prompt_version: str, project_id: str, context_hash: str, result: LLMDecision) -> None:
        self.connection.execute(
            """
            INSERT INTO llm_cache_v2
                (domain, campaign_id, prompt_version, project_id, context_hash, response_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain, campaign_id, prompt_version, project_id) DO UPDATE SET
                context_hash=excluded.context_hash,
                response_json=excluded.response_json,
                created_at=CURRENT_TIMESTAMP
            """,
            (domain, campaign_id, prompt_version, project_id, context_hash, result.model_dump_json()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResultCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
