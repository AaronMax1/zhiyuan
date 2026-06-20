"""Persistence for admissions charter check tasks."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any


class CharterCheckRepository:
    def __init__(self, app_dir: str):
        workspace_dir = os.path.dirname(app_dir)
        self.db_path = os.path.join(workspace_dir, "data-pipeline", "output", "charter_checks.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._prepare()

    def _prepare(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS charter_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    province TEXT,
                    category TEXT,
                    school_name TEXT NOT NULL,
                    major_name TEXT,
                    check_items TEXT NOT NULL,
                    source_hint TEXT,
                    search_url TEXT,
                    status TEXT NOT NULL,
                    result_summary TEXT,
                    evidence_url TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_charter_school ON charter_checks(school_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_charter_status ON charter_checks(status)")

    def save_plan_checks(self, profile: dict[str, Any], checks: list[dict[str, Any]]) -> int:
        if not checks:
            return 0
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        rows = []
        for item in checks:
            rows.append(
                (
                    now,
                    profile.get("province", ""),
                    profile.get("category", ""),
                    item.get("school_name", ""),
                    item.get("major_name", ""),
                    "、".join(item.get("must_check") or []),
                    item.get("source_hint", ""),
                    item.get("search_url", ""),
                    item.get("status", "pending_web_check"),
                    item.get("result_summary", ""),
                    item.get("evidence_url", ""),
                )
            )
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO charter_checks (
                    created_at, province, category, school_name, major_name,
                    check_items, source_hint, search_url, status,
                    result_summary, evidence_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def recent(self, limit: int = 50, school_name: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM charter_checks"
        params: list[Any] = []
        if school_name:
            sql += " WHERE school_name LIKE ?"
            params.append(f"%{school_name}%")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
