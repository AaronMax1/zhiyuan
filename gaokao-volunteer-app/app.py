#!/usr/bin/env python3
"""
High-level API service for the Gaokao volunteer advisor.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse
import csv
import io
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
HEBEI_LNWC_DATA_PATH = os.path.join(os.path.dirname(HERE), "data-pipeline", "output", "hebei_lnwc_loggedin.db")
HEBEI_ZSJH_DATA_PATH = os.path.join(os.path.dirname(HERE), "data-pipeline", "output", "hebei_zsjh_loggedin.db")
BATCH_LINES_DATA_PATH = os.path.join(os.path.dirname(HERE), "data-pipeline", "output", "batch_control_lines.db")
UNIFIED_ADMISSION_DATA_PATH = os.path.join(os.path.dirname(HERE), "data-pipeline", "output", "unified_admission.db")
STATIC_DIR = os.path.join(HERE, "static")
PORT = int(os.environ.get("PORT", "8000"))
VOLUNTEER_SORTED_CSV_PATH = "/Users/marui/Downloads/hebei-history-query-专业优先冲稳保排序.csv"
VOLUNTEER_SUPPLEMENT_CSV_PATH = "/Users/marui/Downloads/63334位次-稳保院校专业-去除手动清单已有.csv"
VOLUNTEER_MANUAL_CSV_PATH = "/Users/marui/Downloads/hebei-history-query-手动排序志愿清单.csv"


PROVINCE_CITY_KEYWORDS = {
    "河北": ["河北", "石家庄", "保定", "廊坊", "唐山", "秦皇岛", "邯郸", "邢台", "沧州", "衡水", "张家口", "承德"],
    "北京": ["北京"],
    "天津": ["天津"],
    "山东": ["山东", "济南", "青岛", "烟台", "潍坊", "临沂", "济宁", "淄博", "泰安", "威海", "日照"],
    "河南": ["河南", "郑州", "洛阳", "开封", "新乡", "焦作", "安阳", "南阳", "信阳", "商丘"],
    "山西": ["山西", "太原", "大同", "临汾", "运城", "长治", "晋中"],
    "内蒙古": ["内蒙古", "呼和浩特", "包头", "赤峰", "通辽", "鄂尔多斯"],
    "辽宁": ["辽宁", "沈阳", "大连", "锦州", "鞍山", "抚顺"],
    "吉林": ["吉林", "长春", "延边", "四平"],
    "黑龙江": ["黑龙江", "哈尔滨", "齐齐哈尔", "牡丹江", "大庆"],
    "江苏": ["江苏", "南京", "苏州", "无锡", "常州", "徐州", "南通", "扬州"],
    "浙江": ["浙江", "杭州", "宁波", "温州", "绍兴", "金华", "嘉兴"],
    "安徽": ["安徽", "合肥", "芜湖", "蚌埠", "马鞍山", "安庆"],
    "江西": ["江西", "南昌", "赣州", "九江", "景德镇"],
    "湖北": ["湖北", "武汉", "宜昌", "襄阳", "荆州"],
    "湖南": ["湖南", "长沙", "湘潭", "衡阳", "株洲"],
    "广东": ["广东", "广州", "深圳", "珠海", "佛山", "东莞", "汕头"],
    "广西": ["广西", "南宁", "桂林", "柳州", "北海"],
    "海南": ["海南", "海口", "三亚"],
    "重庆": ["重庆"],
    "四川": ["四川", "成都", "绵阳", "德阳", "南充", "宜宾"],
    "贵州": ["贵州", "贵阳", "遵义", "安顺"],
    "云南": ["云南", "昆明", "大理", "曲靖"],
    "陕西": ["陕西", "西安", "咸阳", "宝鸡", "延安"],
    "甘肃": ["甘肃", "兰州", "天水", "酒泉"],
    "宁夏": ["宁夏", "银川", "石嘴山", "吴忠"],
    "新疆": ["新疆", "乌鲁木齐", "伊犁", "喀什", "石河子"],
}


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


def csv_response(handler: SimpleHTTPRequestHandler, filename: str, rows: list[dict], columns: list[tuple[str, str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([row.get(key, "") for key, _ in columns])
    body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/csv;charset=utf-8")
    handler.send_header("Content-Disposition", f"attachment; filename={filename}")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def hebei_category_name(category: str) -> str:
    text = str(category or "")
    if "物理" in text or "理科" in text:
        return "物理科目组合"
    if "历史" in text or "文科" in text:
        return "历史科目组合"
    return ""


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    if not values:
        return default
    return values[0] if values[0] is not None else default


def _optional_int(value: str) -> int | None:
    if value == "":
        return None
    return int(value)


def _city_keyword(value: str) -> str:
    text = (value or "").strip()
    return text[:-1] if text.endswith("市") else text


def _filter_values(params: dict[str, list[str]], key: str, normalizer=None) -> list[str]:
    values: list[str] = []
    seen = set()
    for raw in params.get(key, []):
        text = str(raw or "").strip()
        if normalizer:
            text = normalizer(text)
        if text and text not in seen:
            values.append(text)
            seen.add(text)
    return values


def _append_like_any(where: list[str], sql_params: list[object], column: str, values: list[str]) -> None:
    if not values:
        return
    where.append("(" + " OR ".join([f"{column} LIKE ?" for _ in values]) + ")")
    sql_params.extend([f"%{value}%" for value in values])


def _province_keywords(params: dict[str, list[str]]) -> list[str]:
    values: list[str] = []
    seen = set()
    for province in _filter_values(params, "province"):
        for keyword in PROVINCE_CITY_KEYWORDS.get(province, [province]):
            if keyword and keyword not in seen:
                values.append(keyword)
                seen.add(keyword)
    return values


def _normalize_school_key(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"\s+", "", text)
    return text.replace("·", "")


def _append_province_filter(where: list[str], sql_params: list[object], school_col: str, profile_alias: str, params: dict[str, list[str]]) -> None:
    provinces = _filter_values(params, "province")
    if not provinces:
        return
    keywords = _province_keywords(params)
    profile_terms = [f"{profile_alias}.province = ?" for _ in provinces]
    fallback_terms = [f"{school_col} LIKE ?" for _ in keywords]
    where.append(
        "("
        + " OR ".join(profile_terms)
        + f" OR (({profile_alias}.province IS NULL OR {profile_alias}.province = '') AND ("
        + " OR ".join(fallback_terms)
        + "))"
        + ")"
    )
    sql_params.extend(provinces)
    sql_params.extend([f"%{value}%" for value in keywords])


def _append_city_filter(where: list[str], sql_params: list[object], school_col: str, profile_alias: str, params: dict[str, list[str]]) -> None:
    cities = _filter_values(params, "city", _city_keyword)
    if not cities:
        return
    profile_terms = [f"REPLACE({profile_alias}.city, '市', '') = ?" for _ in cities]
    fallback_terms = [f"{school_col} LIKE ?" for _ in cities]
    where.append(
        "("
        + " OR ".join(profile_terms)
        + f" OR (({profile_alias}.city IS NULL OR {profile_alias}.city = '') AND ("
        + " OR ".join(fallback_terms)
        + "))"
        + ")"
    )
    sql_params.extend(cities)
    sql_params.extend([f"%{value}%" for value in cities])


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
                "hebei_lnwc_db_path": status["primary_data_source"]["db_path"],
                "hebei_lnwc_db_exists": status["primary_data_source"]["ready"],
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
        if parsed.path == "/api/major-options":
            return self._major_options(parsed.query)
        if parsed.path == "/api/data-query":
            return self._data_query(parsed.query)
        if parsed.path == "/api/volunteer-list":
            return self._volunteer_list(parsed.query)
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/recommend":
            return self._recommend()
        if self.path == "/api/recommend/plan":
            return self._recommend_plan()
        if self.path == "/api/llm/step":
            return self._llm_step()
        if self.path == "/api/llm/chat":
            return self._llm_chat()
        if self.path == "/api/llm/major-chat":
            return self._llm_major_chat()
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
            "hebei_lnwc_db_path": HEBEI_LNWC_DATA_PATH,
            "db_exists": status["optional_engines"]["gaokao_advisor"]["db_exists"],
            "primary_gz_path": RUNTIME.recommendations.primary_repo.status.gz_path,
            "primary_message": status["primary_data_source"]["message"],
            "init_error": status["optional_engines"]["gaokao_advisor"]["init_error"],
            "next_step": "build data-pipeline/output/hebei_lnwc_loggedin.db and data-pipeline/output/score_segments.db",
        }, 503)
        return False

    def _rank(self, query_string: str):
        params = urllib.parse.parse_qs(query_string)
        try:
            province = params.get("province", ["河北"])[0]
            province_id = int(params.get("province_id", ["13"])[0])
            category = params["category"][0]
            score = int(params["score"][0])
            year = int(params.get("year", ["2025"])[0])
        except (KeyError, ValueError, IndexError) as exc:
            return json_response(self, {"error": f"bad params: {exc}"}, 400)

        row = RUNTIME.score_segments.score_to_rank(province, province_id, year, category, score)
        if row is None:
            return json_response(self, {"error": "no rank data for this province/category/year"}, 404)
        return json_response(self, {
            "rank": row.get("cumulative_rank"),
            "same_score_count": row.get("same_score_count"),
            "score_high": row.get("score_high"),
            "score_low": row.get("score_low"),
            "province": row.get("province") or province,
            "province_id": row.get("province_id") or province_id,
            "year": row.get("year") or year,
            "category": row.get("category") or category,
            "source_type": row.get("source_type"),
            "source_dataset": row.get("source_dataset"),
        })

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

    def _major_options(self, query_string: str):
        params = urllib.parse.parse_qs(query_string)
        category = params.get("category", [""])[0]
        education_level = params.get("education_level", [""])[0]
        if not os.path.exists(HEBEI_LNWC_DATA_PATH):
            return json_response(self, {"items": [], "error": "hebei lnwc db missing"}, 503)

        where = ["volunteer_type = '一志愿'", "major_name IS NOT NULL", "major_name <> ''"]
        sql_params: list[object] = []
        category_name = hebei_category_name(category)
        if category_name:
            where.append("category_name = ?")
            sql_params.append(category_name)
        if education_level == "本科":
            where.append("batch_name = '本科批'")
        elif education_level == "专科":
            where.append("batch_name = '专科批'")

        with sqlite3.connect(HEBEI_LNWC_DATA_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT major_name, COUNT(*) AS n, COUNT(DISTINCT school_name) AS school_count
                FROM hebei_lnwc_loggedin
                WHERE {" AND ".join(where)}
                GROUP BY major_name
                ORDER BY n DESC, major_name ASC
                """,
                sql_params,
            ).fetchall()
        return json_response(self, {
            "category": category,
            "education_level": education_level,
            "count": len(rows),
            "items": [
                {"name": row["major_name"], "records": row["n"], "school_count": row["school_count"]}
                for row in rows
            ],
        })

    def _data_query(self, query_string: str):
        params = urllib.parse.parse_qs(query_string)
        source = _first(params, "source", "history")
        try:
            page = max(1, int(_first(params, "page", "1")))
            page_size = min(100, max(10, int(_first(params, "page_size", "30"))))
        except ValueError:
            return json_response(self, {"error": "bad pagination params"}, 400)
        if source == "plan":
            return self._data_query_plan(params, page, page_size)
        return self._data_query_history(params, page, page_size)

    def _data_query_history(self, params: dict[str, list[str]], page: int, page_size: int):
        required = [HEBEI_LNWC_DATA_PATH, HEBEI_ZSJH_DATA_PATH, BATCH_LINES_DATA_PATH, UNIFIED_ADMISSION_DATA_PATH]
        missing = [path for path in required if not os.path.exists(path)]
        if missing:
            return json_response(self, {"error": "missing data files", "missing": missing}, 503)

        where = ["h.volunteer_type = '一志愿'"]
        sql_params: list[object] = []
        year = _first(params, "year", "")
        if year:
            where.append("h.year = ?")
            sql_params.append(int(year))
        batch = _first(params, "batch", "")
        if batch:
            where.append("h.batch_name = ?")
            sql_params.append(batch)
        category_name = hebei_category_name(_first(params, "category", ""))
        if category_name:
            where.append("h.category_name = ?")
            sql_params.append(category_name)
        school = _first(params, "school", "").strip()
        if school:
            where.append("h.school_name LIKE ?")
            sql_params.append(f"%{school}%")
        _append_province_filter(where, sql_params, "h.school_name", "sp", params)
        _append_city_filter(where, sql_params, "h.school_name", "sp", params)
        _append_like_any(where, sql_params, "h.major_name", _filter_values(params, "major"))
        for key, col, op in [
            ("score_min", "h.min_score", ">="),
            ("score_max", "h.min_score", "<="),
            ("rank_min", "h.min_rank", ">="),
            ("rank_max", "h.min_rank", "<="),
            ("tuition_max", "p.tuition", "<="),
        ]:
            value = _first(params, key, "")
            if value:
                where.append(f"{col} {op} ?")
                sql_params.append(int(value))
        if _first(params, "has_tuition", "") == "1":
            where.append("p.tuition IS NOT NULL AND p.tuition > 0")
        if _first(params, "current_plan_only", "") == "1":
            where.append("p.plan_count IS NOT NULL")

        line_filter = _first(params, "line_filter", "")
        line_delta = _optional_int(_first(params, "line_delta", ""))
        line_type = "本科线" if "undergrad" in line_filter else ("专科线" if "junior" in line_filter else "")
        default_line_type = "专科线" if batch == "专科批" else "本科线"
        display_line_type = line_type or default_line_type
        if line_filter and line_type:
            where.append("bl.score IS NOT NULL")
            if line_filter.startswith("below"):
                where.append("h.min_score < bl.score")
            elif line_filter.startswith("above"):
                where.append("h.min_score >= bl.score")
                if line_delta is not None:
                    where.append("h.min_score <= bl.score + ?")
                    sql_params.append(line_delta)
            elif line_filter.startswith("near"):
                delta = line_delta if line_delta is not None else 20
                where.append("h.min_score <= bl.score + ?")
                sql_params.append(delta)
        sort_key = _first(params, "sort", "rank")
        sort_dir = "ASC" if _first(params, "dir", "asc").lower() == "asc" else "DESC"
        history_sort_columns = {
            "rank": "h.min_rank",
            "min_score": "h.min_score",
            "avg_score": "h.avg_score",
            "line_diff": "line_diff",
            "tuition": "NULLIF(p.tuition, 0)",
            "plan_count": "p.plan_count",
            "year": "h.year",
        }
        sort_column = history_sort_columns.get(sort_key, "h.min_rank")
        order_sql = f"{sort_column} IS NULL, {sort_column} {sort_dir}, h.year DESC, h.min_rank ASC, h.min_score DESC"

        base_sql = f"""
            FROM hebei_lnwc_loggedin h
            LEFT JOIN (
                SELECT batch_name, category_name, school_code, major_code,
                       school_name, major_name,
                       MAX(plan_count) AS plan_count,
                       MAX(duration) AS duration,
                       MAX(tuition) AS tuition,
                       MAX(tuition_text) AS tuition_text,
                       MAX(subject_requirement) AS subject_requirement,
                       MAX(remarks) AS remarks
                FROM zsjh.hebei_zsjh_loggedin
                WHERE year = 2026
                GROUP BY batch_name, category_name, school_name, major_code, major_name
            ) p ON p.batch_name = h.batch_name
               AND p.category_name = h.category_name
               AND p.school_name = h.school_name
               AND p.major_name = h.major_name
            LEFT JOIN lines.batch_control_lines bl
              ON bl.province_id = 13
             AND bl.year = h.year
             AND bl.category = CASE
                WHEN h.category_name LIKE '%历史%' THEN '历史类'
                WHEN h.category_name LIKE '%物理%' THEN '物理类'
                ELSE h.category_name
             END
             AND bl.line_type = ?
            LEFT JOIN unified.school_profiles sp
              ON sp.school_key = NORMALIZE_SCHOOL_KEY(h.school_name)
            WHERE {" AND ".join(where)}
        """
        count_params = [display_line_type, *sql_params]
        offset = (page - 1) * page_size
        export_csv = _first(params, "export", "") == "csv"
        with sqlite3.connect(HEBEI_LNWC_DATA_PATH) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(f"ATTACH DATABASE ? AS zsjh", (HEBEI_ZSJH_DATA_PATH,))
            conn.execute(f"ATTACH DATABASE ? AS lines", (BATCH_LINES_DATA_PATH,))
            conn.execute(f"ATTACH DATABASE ? AS unified", (UNIFIED_ADMISSION_DATA_PATH,))
            conn.create_function("NORMALIZE_SCHOOL_KEY", 1, _normalize_school_key)
            total = conn.execute(f"SELECT COUNT(*) {base_sql}", count_params).fetchone()[0]
            limit_sql = "" if export_csv else "LIMIT ? OFFSET ?"
            row_params = [*count_params] if export_csv else [*count_params, page_size, offset]
            rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT h.year, h.batch_name, h.category_name, h.school_code, h.school_name,
                           h.major_code, h.major_name, h.min_score, h.avg_score, h.min_rank,
                           bl.score AS control_line,
                           CASE WHEN bl.score IS NOT NULL THEN h.min_score - bl.score END AS line_diff,
                           p.plan_count, p.duration, p.tuition, p.tuition_text,
                           p.subject_requirement, p.remarks,
                           h.source_url
                    {base_sql}
                    ORDER BY {order_sql}
                    {limit_sql}
                    """,
                    row_params,
                )
            ]
        for row in rows:
            row["school_life_url"] = default_life_link(row.get("school_name", ""))
        if export_csv:
            columns = [
                ("year", "年度"), ("batch_name", "批次"), ("category_name", "科类"),
                ("school_code", "院校代码"), ("school_name", "院校"), ("major_code", "专业代码"),
                ("major_name", "专业"), ("min_score", "最低分"), ("avg_score", "平均分"),
                ("min_rank", "最低位次"), ("control_line", "省控线"), ("line_diff", "线差"),
                ("plan_count", "计划数"), ("duration", "学制"), ("tuition", "学费"),
                ("tuition_text", "学费文本"), ("subject_requirement", "选科要求"),
                ("remarks", "备注"), ("source_url", "来源"),
            ]
            return csv_response(self, "hebei-history-query.csv", rows, columns)
        return json_response(self, {
            "source": "history",
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": rows,
            "data_notes": [
                "历史录取数据来自河北考试院历年录取查询。",
                "学费、计划数、学制、选科要求来自 2026 河北招生计划，按院校名称、专业代码/专业名称、批次、科类匹配；匹配不到通常表示今年招生计划未收录或专业名称调整。",
            ],
        })

    def _data_query_plan(self, params: dict[str, list[str]], page: int, page_size: int):
        missing = [path for path in [HEBEI_ZSJH_DATA_PATH, UNIFIED_ADMISSION_DATA_PATH] if not os.path.exists(path)]
        if missing:
            return json_response(self, {"error": "missing data files", "missing": missing}, 503)
        where = ["1=1"]
        sql_params: list[object] = []
        batch = _first(params, "batch", "")
        if batch:
            where.append("z.batch_name = ?")
            sql_params.append(batch)
        category_name = hebei_category_name(_first(params, "category", ""))
        if category_name:
            where.append("z.category_name = ?")
            sql_params.append(category_name)
        school = _first(params, "school", "").strip()
        if school:
            where.append("z.school_name LIKE ?")
            sql_params.append(f"%{school}%")
        _append_province_filter(where, sql_params, "z.school_name", "sp", params)
        _append_city_filter(where, sql_params, "z.school_name", "sp", params)
        _append_like_any(where, sql_params, "z.major_name", _filter_values(params, "major"))
        tuition_max = _first(params, "tuition_max", "")
        if tuition_max:
            where.append("z.tuition <= ?")
            sql_params.append(int(tuition_max))
        if _first(params, "has_tuition", "") == "1":
            where.append("z.tuition IS NOT NULL AND z.tuition > 0")
        sort_key = _first(params, "sort", "school")
        sort_dir = "ASC" if _first(params, "dir", "asc").lower() == "asc" else "DESC"
        plan_sort_columns = {
            "school": "z.school_name",
            "tuition": "NULLIF(z.tuition, 0)",
            "plan_count": "z.plan_count",
            "duration": "z.duration",
        }
        sort_column = plan_sort_columns.get(sort_key, "z.school_name")
        order_sql = f"{sort_column} IS NULL, {sort_column} {sort_dir}, z.school_name ASC, z.major_code ASC"
        offset = (page - 1) * page_size
        where_sql = " AND ".join(where)
        export_csv = _first(params, "export", "") == "csv"
        with sqlite3.connect(HEBEI_ZSJH_DATA_PATH) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(f"ATTACH DATABASE ? AS unified", (UNIFIED_ADMISSION_DATA_PATH,))
            conn.create_function("NORMALIZE_SCHOOL_KEY", 1, _normalize_school_key)
            from_sql = f"""
                FROM hebei_zsjh_loggedin z
                LEFT JOIN unified.school_profiles sp
                  ON sp.school_key = NORMALIZE_SCHOOL_KEY(z.school_name)
                WHERE {where_sql}
            """
            total = conn.execute(f"SELECT COUNT(*) {from_sql}", sql_params).fetchone()[0]
            limit_sql = "" if export_csv else "LIMIT ? OFFSET ?"
            row_params = [*sql_params] if export_csv else [*sql_params, page_size, offset]
            rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT z.year, z.batch_name, z.category_name, z.school_code, z.school_name,
                           z.major_code, z.major_name, z.plan_count, z.duration, z.tuition, z.tuition_text,
                           z.subject_requirement, z.remarks, z.source_url
                    {from_sql}
                    ORDER BY {order_sql}
                    {limit_sql}
                    """,
                    row_params,
                )
            ]
        for row in rows:
            row["school_life_url"] = default_life_link(row.get("school_name", ""))
        if export_csv:
            columns = [
                ("year", "年度"), ("batch_name", "批次"), ("category_name", "科类"),
                ("school_code", "院校代码"), ("school_name", "院校"), ("major_code", "专业代码"),
                ("major_name", "专业"), ("plan_count", "计划数"), ("duration", "学制"),
                ("tuition", "学费"), ("tuition_text", "学费文本"),
                ("subject_requirement", "选科要求"), ("remarks", "备注"), ("source_url", "来源"),
            ]
            return csv_response(self, "hebei-2026-plan-query.csv", rows, columns)
        return json_response(self, {
            "source": "plan",
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": rows,
            "data_notes": ["招生计划数据来自 2026 河北考试院招生计划查询，包含计划数、学制、学费和选科要求。"],
        })

    def _volunteer_list(self, query_string: str):
        params = urllib.parse.parse_qs(query_string)
        source = _first(params, "source", "sorted")
        csv_paths = {
            "sorted": VOLUNTEER_SORTED_CSV_PATH,
            "supplement": VOLUNTEER_SUPPLEMENT_CSV_PATH,
            "manual": VOLUNTEER_MANUAL_CSV_PATH,
        }
        csv_path = csv_paths.get(source, VOLUNTEER_SORTED_CSV_PATH)
        if not os.path.exists(csv_path):
            return json_response(self, {
                "error": "volunteer sorted csv missing",
                "path": csv_path,
            }, 404)
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            columns = reader.fieldnames or []
        return json_response(self, {
            "source": source,
            "path": csv_path,
            "count": len(rows),
            "columns": columns,
            "items": rows,
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
            filter_context = str(payload.get("filter_context") or "")
            if step not in {"candidate_pool", "strategy", "order", "charter"}:
                return json_response(self, {"error": "bad step"}, 400)
            if not plan:
                return json_response(self, {"error": "plan is required"}, 400)
            result = RUNTIME.llm_advisor.analyze_step(step, plan, config, filter_context=filter_context)
        except Exception as exc:
            return json_response(self, {"error": str(exc)}, 400)
        return json_response(self, result)

    def _llm_chat(self):
        try:
            payload = read_json(self)
            step = str(payload.get("step") or "")
            plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
            config = payload.get("llm_config") if isinstance(payload.get("llm_config"), dict) else {}
            message = str(payload.get("message") or "")
            history = payload.get("history") if isinstance(payload.get("history"), list) else []
            if step not in {"candidate_pool", "strategy", "order", "charter"}:
                return json_response(self, {"error": "bad step"}, 400)
            if not plan:
                return json_response(self, {"error": "plan is required"}, 400)
            result = RUNTIME.llm_advisor.chat_step(step, plan, message, history, config)
        except Exception as exc:
            return json_response(self, {"error": str(exc)}, 400)
        return json_response(self, result)

    def _llm_major_chat(self):
        try:
            payload = read_json(self)
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            config = payload.get("llm_config") if isinstance(payload.get("llm_config"), dict) else {}
            message = str(payload.get("message") or "")
            history = payload.get("history") if isinstance(payload.get("history"), list) else []
            result = RUNTIME.llm_advisor.chat_major_direction(context, message, history, config)
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
    print(f"Hebei LNWC database: {HEBEI_LNWC_DATA_PATH} exists={os.path.exists(HEBEI_LNWC_DATA_PATH)}", flush=True)
    print(f"Score segments: {RUNTIME.score_segments.db_path} ready={RUNTIME.score_segments.ready}", flush=True)
    print(f"Batch control lines: {RUNTIME.batch_lines.db_path} ready={RUNTIME.batch_lines.ready}", flush=True)
    print(f"LLM advisor: ready={RUNTIME.llm_advisor.status.ready} model={RUNTIME.llm_advisor.status.model}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
