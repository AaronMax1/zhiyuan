#!/usr/bin/env python3
"""
High-level API service for the Gaokao volunteer advisor.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

from services.school_life import SchoolLifeRepository, default_life_link
from services.recommendation_service import RecommendationService
from services.score_segments import ScoreSegmentRepository
from services.batch_lines import BatchControlLineRepository
from services.six_step_agent import SixStepAgentService
from services.llm_advisor import LLMAdvisorService
from services.charter_checks import CharterCheckRepository


HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data", "gaokao.db")
UNIFIED_DATA_PATH = os.path.join(os.path.dirname(HERE), "data-pipeline", "output", "unified_admission.db")
STATIC_DIR = os.path.join(HERE, "static")
PORT = int(os.environ.get("PORT", "8000"))


def json_response(handler: SimpleHTTPRequestHandler, data: dict, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json;charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


class Runtime:
    def __init__(self):
        self.recommendations = RecommendationService(HERE)
        self.score_segments = ScoreSegmentRepository(HERE)
        self.batch_lines = BatchControlLineRepository(HERE)
        self.llm_advisor = LLMAdvisorService()
        self.charter_checks = CharterCheckRepository(HERE)
        self.agent = SixStepAgentService(self.recommendations, self.score_segments, self.llm_advisor, self.charter_checks, self.batch_lines)

    @property
    def ready(self) -> bool:
        return self.recommendations.ready

    @property
    def mode(self) -> str:
        return self.recommendations.status()["mode"]


RUNTIME = Runtime()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        print(fmt % args)

    def do_OPTIONS(self):
        json_response(self, {"ok": True})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            status = RUNTIME.recommendations.status()
            return json_response(self, {
                "ok": True,
                "db_path": DATA_PATH,
                "unified_db_path": status["primary_data_source"]["db_path"],
                "unified_db_exists": status["primary_data_source"]["ready"],
                "db_exists": status["optional_engines"]["gaokao_advisor"]["db_exists"],
                "engine_ready": RUNTIME.ready,
                "mode": RUNTIME.mode,
                "init_error": status["optional_engines"]["gaokao_advisor"]["init_error"],
                "score_segments": RUNTIME.score_segments.coverage(),
                "batch_control_lines": RUNTIME.batch_lines.coverage(),
                "llm_advisor": RUNTIME.llm_advisor.status.__dict__,
                "charter_checks_db": RUNTIME.charter_checks.db_path,
                **status,
                "primary_db": {
                    "ready": status["primary_data_source"]["ready"],
                    "db_path": status["primary_data_source"]["db_path"],
                    "gz_path": RUNTIME.recommendations.primary_repo.status.gz_path,
                    "message": status["primary_data_source"]["message"],
                },
            })
        if parsed.path == "/api/rank":
            return self._rank(parsed.query)
        if parsed.path == "/api/school-life":
            return self._school_life(parsed.query)
        if parsed.path == "/api/charter/checks":
            return self._charter_checks(parsed.query)
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/recommend":
            return self._recommend()
        if self.path == "/api/recommend/plan":
            return self._recommend_plan()
        if self.path == "/api/llm/step":
            return self._llm_step()
        if self.path == "/api/agent/message":
            return self._agent_message()
        return json_response(self, {"error": "not found"}, 404)

    def _require_engine(self) -> bool:
        if RUNTIME.ready:
            return True
        status = RUNTIME.recommendations.status()
        json_response(self, {
            "error": "recommendation engine is not ready",
            "db_path": DATA_PATH,
            "unified_db_path": UNIFIED_DATA_PATH,
            "db_exists": status["optional_engines"]["gaokao_advisor"]["db_exists"],
            "primary_gz_path": RUNTIME.recommendations.primary_repo.status.gz_path,
            "primary_message": status["primary_data_source"]["message"],
            "init_error": status["optional_engines"]["gaokao_advisor"]["init_error"],
            "next_step": "build data-pipeline/output/unified_admission.db or put admission_clean.db.gz under data/",
        }, 503)
        return False

    def _rank(self, query_string: str):
        advisor = RUNTIME.recommendations.advisor_orchestrator
        if advisor is None:
            return json_response(self, {
                "error": "rank conversion requires gaokao.db",
                "message": "当前主库可直接在推荐表单填写位次；分数转位次需要 data/gaokao.db 的一分一段表。",
            }, 503)
        params = urllib.parse.parse_qs(query_string)
        try:
            province_id = int(params["province_id"][0])
            category = params["category"][0]
            score = int(params["score"][0])
            year = int(params.get("year", ["2025"])[0])
        except (KeyError, ValueError, IndexError) as exc:
            return json_response(self, {"error": f"bad params: {exc}"}, 400)

        data_loader = advisor.data_loader
        rank = data_loader.score_to_rank(province_id, year, category, score)
        pool = data_loader.get_pool_total(province_id, year, category)
        max_score = data_loader.get_score_max(province_id, year, category)
        if rank is None:
            return json_response(self, {"error": "no rank data for this province/category/year"}, 404)
        return json_response(self, {"rank": rank, "pool": pool, "max_score": max_score})

    def _school_life(self, query_string: str):
        params = urllib.parse.parse_qs(query_string)
        school_name = params.get("school_name", [""])[0]
        if not school_name:
            return json_response(self, {"error": "school_name is required"}, 400)
        repo = SchoolLifeRepository()
        info = repo.find(school_name)
        if not info:
            return json_response(self, {
                "school_name": school_name,
                "found": False,
                "source_hint": default_life_link(school_name),
            })
        return json_response(self, {"found": True, "data": info.to_dict()})

    def _charter_checks(self, query_string: str):
        params = urllib.parse.parse_qs(query_string)
        school_name = params.get("school_name", [""])[0]
        try:
            limit = int(params.get("limit", ["50"])[0])
        except ValueError:
            limit = 50
        return json_response(self, {
            "db_path": RUNTIME.charter_checks.db_path,
            "items": RUNTIME.charter_checks.recent(limit=max(1, min(limit, 200)), school_name=school_name),
        })

    def _recommend(self):
        if not self._require_engine():
            return
        try:
            payload = read_json(self)
            result = RUNTIME.recommendations.recommend(payload)
        except Exception as exc:
            return json_response(self, {"error": str(exc)}, 400)
        return json_response(self, result)

    def _recommend_plan(self):
        if not self._require_engine():
            return
        try:
            payload = read_json(self)
            result = RUNTIME.agent.build_plan(payload)
        except Exception as exc:
            return json_response(self, {"error": str(exc)}, 400)
        return json_response(self, result)

    def _llm_step(self):
        try:
            payload = read_json(self)
            step = str(payload.get("step") or "")
            plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
            config = payload.get("llm_config") if isinstance(payload.get("llm_config"), dict) else {}
            if step not in {"candidate_pool", "strategy", "order", "charter"}:
                return json_response(self, {"error": "bad step"}, 400)
            if not plan:
                return json_response(self, {"error": "plan is required"}, 400)
            result = RUNTIME.llm_advisor.analyze_step(step, plan, config)
        except Exception as exc:
            return json_response(self, {"error": str(exc)}, 400)
        return json_response(self, result)

    def _agent_message(self):
        try:
            payload = read_json(self)
            result = RUNTIME.agent.inspect_message(payload)
        except Exception as exc:
            return json_response(self, {"error": str(exc)}, 400)
        return json_response(self, result)


if __name__ == "__main__":
    print(f"Serving at http://localhost:{PORT}", flush=True)
    print(f"Database: {DATA_PATH} exists={os.path.exists(DATA_PATH)}", flush=True)
    print(f"Unified database: {UNIFIED_DATA_PATH} exists={os.path.exists(UNIFIED_DATA_PATH)}", flush=True)
    print(f"Score segments: {RUNTIME.score_segments.db_path} ready={RUNTIME.score_segments.ready}", flush=True)
    print(f"Batch control lines: {RUNTIME.batch_lines.db_path} ready={RUNTIME.batch_lines.ready}", flush=True)
    print(f"LLM advisor: ready={RUNTIME.llm_advisor.status.ready} model={RUNTIME.llm_advisor.status.model}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
