#!/usr/bin/env python3
"""Build a placeholder Hebei 2026 admission-plan database.

The official 2026 Hebei admission-plan query is not open yet. This database is
kept separate from historical admission data so it can be replaced later by the
official plan crawler without changing recommendation logic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DB = ROOT / "data-pipeline" / "output" / "hebei_2026_plan.db"
REPORT = ROOT / "data-pipeline" / "output" / "hebei_2026_plan_report.md"


ROWS = [
    {
        "batch_name": "本科批",
        "category_name": "物理科目组合",
        "school_code": "1877",
        "school_name": "郑州大学[公办]",
        "major_code": "20",
        "major_name": "计算机科学与技术",
        "plan_count": 7,
        "tuition": 5700,
        "duration": "四年",
        "campus": "主校区",
        "subject_requirement": "物理+化学",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "本科批",
        "category_name": "物理科目组合",
        "school_code": "1877",
        "school_name": "郑州大学[公办]",
        "major_code": "17",
        "major_name": "电子信息类",
        "plan_count": 8,
        "tuition": 5700,
        "duration": "四年",
        "campus": "主校区",
        "subject_requirement": "物理+化学",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "本科批",
        "category_name": "物理科目组合",
        "school_code": "0522",
        "school_name": "杭州电子科技大学[公办]",
        "major_code": "19",
        "major_name": "电子信息类(通信学院)",
        "plan_count": 6,
        "tuition": 6000,
        "duration": "四年",
        "campus": "下沙校区",
        "subject_requirement": "物理+化学",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "本科批",
        "category_name": "物理科目组合",
        "school_code": "0558",
        "school_name": "河北工业大学(天津市)[公办]",
        "major_code": "39",
        "major_name": "电子信息工程",
        "plan_count": 12,
        "tuition": 5800,
        "duration": "四年",
        "campus": "天津校区",
        "subject_requirement": "物理+化学",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "本科批",
        "category_name": "物理科目组合",
        "school_code": "0787",
        "school_name": "华北电力大学(保定)[公办]",
        "major_code": "33",
        "major_name": "电子信息科学与技术",
        "plan_count": 6,
        "tuition": 5500,
        "duration": "四年",
        "campus": "保定校区",
        "subject_requirement": "物理+化学",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "本科批",
        "category_name": "物理科目组合",
        "school_code": "1125",
        "school_name": "南京邮电大学[公办]",
        "major_code": "02",
        "major_name": "电子信息工程",
        "plan_count": 4,
        "tuition": 6380,
        "duration": "四年",
        "campus": "仙林校区",
        "subject_requirement": "物理+化学",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "本科批",
        "category_name": "物理科目组合",
        "school_code": "0528",
        "school_name": "合肥工业大学[公办]",
        "major_code": "34",
        "major_name": "计算机科学与技术",
        "plan_count": 5,
        "tuition": 6050,
        "duration": "四年",
        "campus": "合肥校区",
        "subject_requirement": "物理+化学",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "本科批",
        "category_name": "物理科目组合",
        "school_code": "1623",
        "school_name": "武汉理工大学[公办]",
        "major_code": "38",
        "major_name": "计算机类",
        "plan_count": 8,
        "tuition": 5850,
        "duration": "四年",
        "campus": "校本部",
        "subject_requirement": "物理+化学",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "专科批",
        "category_name": "物理科目组合",
        "school_code": "1636",
        "school_name": "西安电力高等专科学校[公办]",
        "major_code": "01",
        "major_name": "发电厂及电力系统",
        "plan_count": 10,
        "tuition": 6500,
        "duration": "三年",
        "campus": "校本部",
        "subject_requirement": "物理",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
    {
        "batch_name": "专科批",
        "category_name": "物理科目组合",
        "school_code": "1414",
        "school_name": "深圳职业技术大学[公办]",
        "major_code": "04",
        "major_name": "电子信息工程技术",
        "plan_count": 5,
        "tuition": 6000,
        "duration": "三年",
        "campus": "校本部",
        "subject_requirement": "物理",
        "remarks": "Mock 数据，仅用于字段预留；以河北2026招生计划为准。",
    },
]


def main() -> None:
    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(OUT_DB) as conn:
        conn.execute("DROP TABLE IF EXISTS hebei_2026_plan")
        conn.execute(
            """
            CREATE TABLE hebei_2026_plan (
                year INTEGER NOT NULL,
                province TEXT NOT NULL,
                batch_name TEXT NOT NULL,
                category_name TEXT NOT NULL,
                school_code TEXT NOT NULL,
                school_name TEXT NOT NULL,
                major_code TEXT NOT NULL,
                major_name TEXT NOT NULL,
                plan_count INTEGER,
                tuition INTEGER,
                tuition_text TEXT,
                duration TEXT,
                campus TEXT,
                subject_requirement TEXT,
                remarks TEXT,
                source_system TEXT NOT NULL,
                source_url TEXT,
                source_file TEXT,
                confidence TEXT NOT NULL,
                is_mock INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO hebei_2026_plan (
                year, province, batch_name, category_name, school_code,
                school_name, major_code, major_name, plan_count, tuition,
                tuition_text, duration, campus, subject_requirement, remarks,
                source_system, source_url, source_file, confidence, is_mock
            )
            VALUES (
                2026, '河北', :batch_name, :category_name, :school_code,
                :school_name, :major_code, :major_name, :plan_count, :tuition,
                :tuition_text, :duration, :campus, :subject_requirement, :remarks,
                'mock_reserved_schema', '', 'data-pipeline/build_hebei_2026_plan_mock.py',
                'mock', 1
            )
            """,
            [
                {
                    **row,
                    "tuition_text": f"{row['tuition']}元/年" if row.get("tuition") else "",
                }
                for row in ROWS
            ],
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX idx_hebei_2026_plan_key
            ON hebei_2026_plan(batch_name, category_name, school_code, major_code)
            """
        )
        conn.execute("CREATE INDEX idx_hebei_2026_plan_school_major ON hebei_2026_plan(school_name, major_name)")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM hebei_2026_plan").fetchone()[0]

    REPORT.write_text(
        "\n".join(
            [
                "# 河北 2026 招生计划预留库",
                "",
                f"- Output DB: `{OUT_DB}`",
                f"- Rows: {count}",
                f"- Integrity check: `{integrity}`",
                "- Status: Mock placeholder. 官方招生计划开放后，用正式爬虫覆盖 `hebei_2026_plan` 表。",
                "- Join key: `batch_name + category_name + school_code + major_code`.",
                "- Fields reserved: plan_count, tuition, duration, campus, subject_requirement, remarks.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUT_DB.relative_to(ROOT)} rows={count}")
    print(f"Wrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
