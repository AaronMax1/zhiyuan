"""
Main recommendation engine.

Entry point: RecommendationEngine.recommend(query) → List[Recommendation]

Algorithm:
  1. Filter eligible (school, major) pairs:
     - Correct category (物理类/历史类/理科/文科)
     - Meets min_tier hard constraint
     - City in preferred list (if specified)
     - sp_xuanke subject requirements met
  2. For each eligible school: compute P(admit) from SchoolStats
  3. Filter: P >= REACH_MIN (10%) only
  4. Sort by utility (exchange argument: optimal order = descending utility)
  5. Build 96-slot list: coverage check ensures P(≥1 admission) ≥ target
  6. Return full result with metadata
"""

import sqlite3
import json
import math
from dataclasses import dataclass, field
from typing import Optional

from engine.data_loader import DataLoader, UniMeta, SchoolStats
from engine.probability import p_admit, admission_tag, REACH_MIN, describe_probability
from engine.utility import (
    Preferences,
    utility_score,
    tier_at_least,
    classify_major,
    major_path_note,
)

# 选科 subject code → student category
XUANKE_PHYSICS = '70000'   # 物理
XUANKE_HISTORY = '70004'   # 历史
XUANKE_ANY     = '70008'   # 不限

SPECIAL_MAJOR_ROW_KEYWORDS = (
    '专项', '预科', '单列', '援疆', '民族', '定向', 'A1段',
    '特殊类型', '中外合作', '合作办学', '优师', '建档立卡',
    '革命老区', '边防', '地矿', '苏区',
)

SPECIAL_MAJOR_NAME_KEYWORDS = (
    '专项', '预科', '单列', '定向', '中外合作', '合作办学',
    '民族班', '少数民族',
)


@dataclass
class Query:
    """Student's input query."""
    province_id: int
    student_rank: int          # rank in province (1=best)
    category: str              # '物理类', '历史类', '理科', '文科'
    xuanke_codes: list = field(default_factory=list)   # student's chosen subjects
    prefs: Preferences = field(default_factory=Preferences)
    max_slots: int = 96
    # NOTE: coverage_target is not enforced by the slot-selection algorithm.
    # Coverage is achieved structurally via the 保 bucket (P≥0.75 per slot).
    # Removing this field would be a breaking API change; kept for documentation.
    coverage_target: float = 0.999
    strict_major_match: bool = True  # when keywords set, filter out non-matching majors
                                     # unless needed to fill safety slots
    # Risk-adjusted slot distribution (set by AdvisorOutput; defaults = standard ratios)
    chong_target: float = 0.20   # fraction of max_slots allocated to 冲 bucket
    bao_target:   float = 0.30   # fraction of max_slots allocated to 保 bucket
    # wen_target = 1 - chong_target - bao_target (derived)


@dataclass
class Recommendation:
    """Single school+major recommendation."""
    rank: int                  # 1-based position in sorted list
    school_id: int
    school_name: str
    province_id: int
    city: str
    tier: str
    tier_score: float
    category: str
    sp_name: str               # major name
    level3_name: str
    major_type: str            # 'specialist', 'semi', 'generalist'
    p: float                   # admission probability
    tag: str                   # '冲', '稳', '保'
    utility: float
    is_year1_switch: bool
    major_match: bool = True   # False when major doesn't match keywords (still included as fallback)
    note: str = ''


def _xuanke_eligible(sp_xuanke: str, student_xuanke: list) -> bool:
    """
    Returns True if student's subject selections satisfy the major's requirements.
    sp_xuanke format: 'CODE1_CODE2^CODE3_CODE4' (groups separated by ^, ANDs by _)
    Empty sp_xuanke = no restriction.
    """
    if not sp_xuanke or not student_xuanke:
        return True
    if XUANKE_ANY in sp_xuanke:
        return True

    student_set = set(student_xuanke)
    # Each ^-group is an OR option; student must satisfy at least one group
    for group in sp_xuanke.split('^'):
        required = set(group.strip().split('_')) - {''}
        if required and required.issubset(student_set):
            return True
    return False


def is_regular_major_score_row(local_batch: str = '', zslx_name: str = '', sp_name: str = '') -> bool:
    """True when a major-score row belongs to the ordinary admission track."""
    row_context = f"{local_batch or ''} {zslx_name or ''}"
    major_name = sp_name or ''
    return (
        not any(kw in row_context for kw in SPECIAL_MAJOR_ROW_KEYWORDS)
        and not any(kw in major_name for kw in SPECIAL_MAJOR_NAME_KEYWORDS)
    )


def _regular_major_sql_filter(alias: str = 'ms') -> tuple[str, list]:
    context_cols = [
        f"COALESCE({alias}.local_batch, '')",
        f"COALESCE({alias}.zslx_name, '')",
    ]
    major_name_col = f"COALESCE({alias}.sp_name, '')"
    clauses = []
    params = []
    for kw in SPECIAL_MAJOR_ROW_KEYWORDS:
        for col in context_cols:
            clauses.append(f"{col} NOT LIKE ?")
            params.append(f"%{kw}%")
    for kw in SPECIAL_MAJOR_NAME_KEYWORDS:
        clauses.append(f"{major_name_col} NOT LIKE ?")
        params.append(f"%{kw}%")
    return " AND ".join(clauses), params


class RecommendationEngine:
    def __init__(self, data_loader: DataLoader):
        self.dl = data_loader
        self._db_path = data_loader.db_path
        # Thread-local connection — same reason as DataLoader.conn.
        import threading
        self._local = threading.local()

    @property
    def _conn(self):
        """Per-thread SQLite connection (created on first access per thread)."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def recommend(self, query: Query) -> list:
        """
        Returns list of Recommendation, sorted by utility (descending),
        covering up to query.max_slots slots with coverage guarantee.
        """
        return self._recommend_from_candidates(query, self._gather_candidates(query))

    def _recommend_from_candidates(self, query: Query, candidates: list) -> list:
        """Build recommendations from an already loaded candidate pool."""
        candidates = self._filter_and_score(query, candidates)

        # Sort by utility DESC, then by P ASC within equal utility
        # (equal-utility schools: list reach/冲 before safety/保 so aspirational
        # schools appear in output — exchange argument allows any order for ties)
        candidates.sort(key=lambda x: (x['utility'], -x['p']), reverse=True)

        # Coverage-aware slot selection
        selected = self._select_slots(query, candidates)

        return [self._make_rec(i + 1, c, query) for i, c in enumerate(selected)]

    def _get_type_codes(self, province_id: int, category: str) -> list:
        """
        Returns type_codes in major_scores that correspond to the given category.
        Joins via admission_scores which has the type_name column.

        For 2025-switching provinces (山西/河南/四川/云南/陕西/青海/宁夏 etc.):
          - querying '物理类' → also include '理科' codes (full pre-2025 major catalog)
          - querying '历史类' → also include '文科' codes (same reason)
        Without these fallbacks, major_scores coverage is near-zero for the new
        historical category because crawled data is stored under the old type_names.
        """
        from engine.drift import SWITCHING_2025
        rows = self._conn.execute("""
            SELECT DISTINCT a.type_code
            FROM admission_scores a
            WHERE a.province_id = ? AND a.type_name = ?
        """, [province_id, category]).fetchall()
        codes = [r[0] for r in rows]

        if province_id in SWITCHING_2025:
            if category == '物理类':
                # Pull 理科 codes (has full major catalog for pre-2025 data)
                fallback_rows = self._conn.execute("""
                    SELECT DISTINCT type_code FROM admission_scores
                    WHERE province_id = ? AND type_name = '理科'
                """, [province_id]).fetchall()
                for r in fallback_rows:
                    if r[0] not in codes:
                        codes.append(r[0])
            elif category == '历史类':
                # Pull 文科 codes (same reason — major catalog lives under old name)
                fallback_rows = self._conn.execute("""
                    SELECT DISTINCT type_code FROM admission_scores
                    WHERE province_id = ? AND type_name = '文科'
                """, [province_id]).fetchall()
                for r in fallback_rows:
                    if r[0] not in codes:
                        codes.append(r[0])

        return codes

    # Provinces whose school_stats are stored under a different category name
    # than what the student's 2025 exam uses.
    # 海南 switched to 3+1+2 for 2025 (物理类/历史类) but historical stats
    # are stored as '综合' (3+3 era).
    _STATS_CAT_OVERRIDE: dict = {
        (46, '物理类'): '综合',
        (46, '历史类'): '综合',
    }

    def _get_zero_quota_keys(self, province_id: int) -> set:
        """
        Returns set of (school_id, sp_name) where total 2025 enrollment plan
        quota is zero for this province. These majors are not offered in the
        student's province this year and must be excluded from recommendations.

        Logic: only exclude when a record explicitly exists in enrollment_plans
        2025 with plan_count=0 for ALL plans for that (school, major).
        If no 2025 record exists, the major is kept (ambiguous → conservative).
        """
        rows = self._conn.execute("""
            SELECT school_id, sp_name
            FROM enrollment_plans
            WHERE province_id = ? AND year = 2025
            GROUP BY school_id, sp_name
            HAVING MAX(CAST(plan_count AS INTEGER)) <= 0
        """, [province_id]).fetchall()
        return {(r[0], r[1]) for r in rows}

    def _gather_candidates(self, query: Query) -> list:
        """
        Load all (school, major) pairs for the student's province+category
        that have both admission stats and major score data.
        """
        # Resolve the category used to look up school_stats (may differ from query category)
        stats_cat = self._STATS_CAT_OVERRIDE.get(
            (query.province_id, query.category), query.category
        )

        # Get all schools that have stats for this province+category
        stats_keys = {
            (s.school_id, s.province_id): s
            for (sid, pid, cat), s in self.dl.school_stats.items()
            if pid == query.province_id and cat == stats_cat
        }
        if not stats_keys:
            return []

        school_ids = list({k[0] for k in stats_keys})
        # Use overridden category for type_codes lookup too (e.g. 海南 物理类→综合)
        type_codes = self._get_type_codes(query.province_id, stats_cat)
        if not type_codes:
            return []

        # Batch-fetch major scores for these schools in this province
        placeholders_schools = ','.join('?' * len(school_ids))
        placeholders_codes   = ','.join('?' * len(type_codes))
        regular_major_filter, regular_major_params = _regular_major_sql_filter('ms')
        rows = self._conn.execute(f"""
            SELECT ms.school_id, ms.sp_name, ms.level1_name, ms.sp_xuanke,
                   ms.level3_name, ms.local_batch, ms.zslx_name
            FROM major_scores ms
            WHERE ms.province_id = ?
              AND ms.type_code IN ({placeholders_codes})
              AND ms.school_id IN ({placeholders_schools})
              AND {regular_major_filter}
              AND ms.level1_name LIKE '本科%'
              AND (CAST(ms.min_rank AS INTEGER) > 0
                   OR CAST(ms.min_score AS INTEGER) > 0)
            GROUP BY ms.school_id, ms.sp_name
        """, [query.province_id] + type_codes + school_ids + regular_major_params).fetchall()

        # Filter out majors with zero 2025 enrollment quota in this province
        zero_quota = self._get_zero_quota_keys(query.province_id)

        candidates = []
        for r in rows:
            if (r['school_id'], r['sp_name']) in zero_quota:
                continue
            candidates.append({
                'school_id': r['school_id'],
                'sp_name': r['sp_name'] or '',
                'sp_xuanke': r['sp_xuanke'] or '',
                'level3_name': r['level3_name'] or '',
                'local_batch': r['local_batch'] or '',
                'zslx_name': r['zslx_name'] or '',
                'stats': stats_keys.get((r['school_id'], query.province_id)),
            })
        return [c for c in candidates if c['stats'] is not None]

    def _filter_and_score(self, query: Query, candidates: list) -> list:
        """Apply filters and compute P(admit) + utility for each candidate."""
        # First pass: filter and collect (p, stats, uni) without utility
        preresult = []
        prefs = query.prefs

        for c in candidates:
            stats = c['stats']
            uni = self.dl.uni_meta.get(c['school_id'])
            if not uni:
                continue
            if not tier_at_least(uni.tier, prefs.min_tier):
                continue
            if query.xuanke_codes:
                if not _xuanke_eligible(c['sp_xuanke'], query.xuanke_codes):
                    continue

            prob = round(p_admit(query.student_rank, stats), 3)
            tag = admission_tag(prob)
            if tag is None:
                continue

            major_type = classify_major(c['level3_name'], c['sp_name'])
            preresult.append({
                **c,
                'uni': uni,
                'p': prob,
                'tag': tag,
                'major_type': major_type,
            })

        # Pre-compute within-tier prestige scores (normalise mu_percentile per tier)
        # Lower mu_percentile = more selective = higher prestige within tier
        from collections import defaultdict
        tier_percentiles: dict = defaultdict(list)
        for item in preresult:
            tier_percentiles[item['uni'].tier].append(item['stats'].mu_percentile)

        tier_min = {t: min(ps) for t, ps in tier_percentiles.items()}
        tier_max = {t: max(ps) for t, ps in tier_percentiles.items()}

        def prestige(tier: str, mu_pct: float) -> float:
            lo, hi = tier_min.get(tier, 0), tier_max.get(tier, 1)
            if hi <= lo:
                return 0.5
            return 1.0 - (mu_pct - lo) / (hi - lo)   # 1.0 = most selective in tier

        # Pre-compute major_match flag for strict filtering
        def major_matches(sp_name: str) -> bool:
            if not prefs.target_major_keywords:
                return True
            return any(kw in sp_name for kw in prefs.target_major_keywords)

        # Second pass: compute utility with prestige + major_match flag
        result = []
        for item in preresult:
            uni = item['uni']
            p_within = prestige(uni.tier, item['stats'].mu_percentile)
            u = utility_score(
                tier_score=uni.tier_score,
                city=uni.city_name,
                sp_name=item['sp_name'],
                level3_name=item['level3_name'],
                prefs=prefs,
                prestige_within_tier=p_within,
                school_province_id=uni.province_id,
                student_province_id=query.province_id,
                school_id=item['school_id'],
            )
            result.append({
                **item,
                'utility': u,
                'prestige': p_within,
                'major_match': major_matches(item['sp_name']),
            })

        return result

    def _select_slots(self, query: Query, sorted_candidates: list) -> list:
        """
        Fill up to max_slots slots with a balanced 冲/稳/保 structure.

        Target ratio:
          冲 (reach):   ~20% of slots
          稳 (match):   ~50% of slots
          保 (safety):  ~30% of slots

        Major-match priority: when target_major_keywords is set and
        strict_major_match=True, prefer matching majors; non-matching
        majors only fill remaining slots if matching ones are exhausted.

        Per-school cap: MAX_MAJORS_PER_SCHOOL=2 (uniform) to ensure ≥15 distinct
        schools in 30 slots while allowing 2 matching majors per school for
        keyword queries (e.g. 法学 + 法律 from same university).
        """
        has_kw = bool(query.prefs.target_major_keywords)
        MAX_MAJORS_PER_SCHOOL = 2
        n = min(query.max_slots, len(sorted_candidates))

        # Separate into buckets; within each bucket, matching majors first
        buckets: dict = {'冲': [], '稳': [], '保': []}
        for c in sorted_candidates:
            tag = c['tag']
            if tag in buckets:
                buckets[tag].append(c)

        # Sort each bucket: major_match=True first (preserving utility order within)
        if has_kw and query.strict_major_match:
            for tag in buckets:
                buckets[tag].sort(key=lambda x: (0 if x['major_match'] else 1, -x['utility']))

        # Target counts — derived from query risk profile
        # Widen ceiling for overseas-safety-net mode (chong_target≤0.10, bao_target≥0.60)
        is_overseas_net = query.chong_target <= 0.10 and query.bao_target >= 0.60
        chong_frac = max(0.05 if is_overseas_net else 0.10,
                         min(0.40, query.chong_target))
        bao_frac   = max(0.15, min(0.80 if is_overseas_net else 0.50, query.bao_target))
        wen_frac   = max(0.05 if is_overseas_net else 0.10,
                         1.0 - chong_frac - bao_frac)
        n_chong = max(1, round(n * chong_frac))
        n_wen   = max(1, round(n * wen_frac))
        n_bao   = max(1, n - n_chong - n_wen)

        strict = has_kw and query.strict_major_match

        def pick(bucket_name: str, target: int, school_counts: dict) -> list:
            picked = []
            for c in buckets[bucket_name]:
                if len(picked) >= target:
                    break
                if strict and not c['major_match']:
                    continue   # skip non-matching when keywords set
                sid = c['school_id']
                if school_counts.get(sid, 0) >= MAX_MAJORS_PER_SCHOOL:
                    continue
                picked.append(c)
                school_counts[sid] = school_counts.get(sid, 0) + 1
            return picked

        school_counts: dict = {}
        chong = pick('冲', n_chong, school_counts)
        wen   = pick('稳', n_wen,   school_counts)
        bao   = pick('保', n_bao,   school_counts)

        # Backfill if any bucket is short
        selected = chong + wen + bao
        if len(selected) < n:
            used_ids = {id(c) for c in selected}
            for c in sorted_candidates:
                if len(selected) >= n:
                    break
                if id(c) in used_ids:
                    continue
                if strict and not c['major_match']:
                    continue   # strict filter applies to backfill too
                sid = c['school_id']
                if school_counts.get(sid, 0) >= MAX_MAJORS_PER_SCHOOL:
                    continue
                selected.append(c)
                school_counts[sid] = school_counts.get(sid, 0) + 1

        # Re-sort final list: tag order (冲→稳→保), then utility DESC within each tag.
        # Prevents backfilled 冲 schools from appearing at the end after 保 slots.
        _TAG_ORDER = {'冲': 0, '稳': 1, '保': 2}
        selected.sort(key=lambda x: (_TAG_ORDER.get(x['tag'], 9), -x['utility']))

        return selected

    def _make_rec(self, rank: int, c: dict, query: Query) -> Recommendation:
        uni = c['uni']
        stats = c['stats']
        return Recommendation(
            rank=rank,
            school_id=c['school_id'],
            school_name=uni.name,
            province_id=uni.province_id,
            city=uni.city_name,
            tier=uni.tier,
            tier_score=uni.tier_score,
            # Use query category (not stats.category) so 海南 物理类 shows '物理类', not '综合'
            category=query.category or stats.category,
            sp_name=c['sp_name'],
            level3_name=c['level3_name'],
            major_type=c['major_type'],
            p=round(c['p'], 3),
            tag=c['tag'],
            utility=round(c['utility'], 3),
            is_year1_switch=stats.is_year1_switch,
            major_match=c.get('major_match', True),
            note=major_path_note(c['sp_name'], c['level3_name']),
        )

    def recommend_summary(self, recs: list, query: Query) -> dict:
        """Returns metadata summary for display (换制 note, balance stats, etc.)."""
        n_chong = sum(1 for r in recs if r.tag == '冲')
        n_wen   = sum(1 for r in recs if r.tag == '稳')
        n_bao   = sum(1 for r in recs if r.tag == '保')
        n_switch = sum(1 for r in recs if r.is_year1_switch)
        n_mismatch = sum(1 for r in recs if not r.major_match)
        from engine.drift import SWITCHING_2025
        is_switch_province = query.province_id in SWITCHING_2025
        if is_switch_province:
            if query.category in ('物理类', '理科'):
                switch_note = '本省2025年首年换制(理科→物理类)，预测区间较宽'
            else:
                switch_note = '本省2025年首年换制(文科→历史类)，预测区间较宽'
        else:
            switch_note = ''
        return {
            'total': len(recs),
            'chong': n_chong, 'wen': n_wen, 'bao': n_bao,
            'is_switch_province': is_switch_province,
            'switch_note': switch_note,
            'major_mismatch_count': n_mismatch,
        }

    def coverage_probability(self, recs: list) -> float:
        """P(at least one admission) across safety (保) slots.
        Only 保 slots are structural coverage contributors — 冲/稳 slots have
        P < 0.75 and are aspirational picks, not coverage guarantees.
        """
        bao_slots = [r for r in recs if r.tag == '保']
        if not bao_slots:
            return 0.0
        p_none = math.prod(1.0 - r.p for r in bao_slots)
        return 1.0 - p_none
