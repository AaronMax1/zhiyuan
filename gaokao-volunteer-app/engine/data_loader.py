"""
DataLoader: pre-computes all DB-derived statistics needed by the engine.

Loads once at startup; all downstream modules receive a DataLoader instance.

Key outputs:
  - pool_totals:   {(province_id, year, category): int}
  - school_stats:  {(school_id, province_id, category): SchoolStats}
  - uni_meta:      {school_id: UniMeta}
"""

import sqlite3
import math
import os
from typing import Optional
from dataclasses import dataclass, field

from engine.drift import SWITCHING_2025, apply_drift

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'gaokao.db')

# Provinces where main undergrad batch merged into single name in new system
# Key: province_id → expected 本科 batch name patterns (inclusive)
UNDERGRAD_BATCH_INCLUDE = '%本科%'
UNDERGRAD_BATCH_EXCLUDE_PATTERNS = [
    '%专科%', '%提前%', '%艺术%', '%体育%', '%保送%',
    '%专项%', '%预科%', '%单列%', '%援疆%', '%民族%', '%定向%',
    '%A1段%',
]

# Provinces that use non-'本科' batch names for their main undergrad admission
# (浙江: '平行录取一/二段', 山东: '普通类一/二段').
# These are unambiguously undergraduate batches — 专科 is a separate batch.
UNDERGRAD_BATCH_EXPLICIT = {
    '普通类一段', '普通类二段',       # 山东
    '平行录取一段', '平行录取二段',    # 浙江
}

# A single misclassified special line can make a top school look much easier
# than its peer schools.  Use robust filtering for obvious year-level outliers.
RANK_OUTLIER_RATIO = 2.5
RANK_OUTLIER_ABS_FLOOR = 0.01

# Minimum years of data to use own σ; below this → peer shrinkage
MIN_YEARS_OWN_SIGMA = 3

# Year weights for μ computation (most recent = highest weight)
# 2025 gets highest weight — it's the most recent known outcome.
YEAR_WEIGHTS = {2025: 5, 2024: 4, 2023: 3, 2022: 2, 2021: 1, 2020: 1, 2019: 1}

# For 2025-switching provinces: use 理科 2024 data as base
SWITCHING_SOURCE_CATEGORY = '理科'
SWITCHING_SOURCE_YEAR = 2024

# Year-1 switching provinces have ~2× normal year-to-year variance.
# Defined once here; referenced in _load_school_stats and _compute_tier_sigmas.
YEAR1_SIGMA_FACTOR = 2.0

# ── 2025 Calibration corrections ────────────────────────────────────────────
# Derived from backtesting predicted μ/σ against actual 2025 cutoffs.
# Format: (province_id, category) → (mu_shift_sigmas, sigma_scale)
#   mu_shift_sigmas: add this many σ to μ (positive = school is harder than history)
#   sigma_scale:     multiply σ by this factor (>1 = more volatile than σ suggests)
# Only applied where |mu_shift| > 0.5σ or sigma_scale > 1.3.
# Source: accuracy_test.py §2025-backtest, clean set N=32,952.
PROVINCE_CALIBRATION: dict = {
    # Post-2025 structural corrections. Each entry: (mu_shift_sigmas, sigma_scale).
    # mu_shift: add this many σ to μ (positive = schools are harder than history predicts)
    # sigma_scale: multiply σ (structural noise amplifier)
    #
    # Methodology: leave-one-year-out cross-validation + structural analysis.
    # μ shifts ONLY retained where structural cause identified (not random oscillation).
    # σ scales retained where demographic/structural variance is chronic.

    # 江苏: 2025 批次合并 collapsed 历史类 pool 40.5% (94K→56K), permanent structural change.
    # 2021–2024 showed opposite bias (over-predicting selectivity) so this isn't a general
    # correction — it's specific to the post-merger equilibrium. Monitor in 2026.
    (32, '历史类'): (1.45,  1.00),
    # 江苏 物理类: pool oscillating, cross-year evidence weaker. Keep smaller shift.
    (32, '物理类'): (0.60,  1.00),

    # Northeast demographic decline: chronic year-to-year pool shrinkage
    # drives σ structurally higher. No μ shift (direction unpredictable).
    (22, '历史类'): (0.00,  1.80),   # 吉林 历史类
    (22, '物理类'): (0.00,  3.00),   # 吉林 物理类 — very high noise
    (23, '物理类'): (0.00,  1.80),   # 黑龙江 物理类

    # 广西: historically volatile (σ too small), no systematic μ bias
    (45, '历史类'): (0.00,  2.00),
    (45, '物理类'): (0.00,  2.60),

    # 甘肃 物理类: σ structurally small (sparse data)
    (62, '物理类'): (0.00,  1.60),

    # 贵州 物理类: mild σ inflation
    (52, '物理类'): (0.00,  1.80),

    # 辽宁 历史类, 湖北 历史类: cross-year oscillation shows no structural bias.
    # Corrections REMOVED — fitting noise would hurt 2026 predictions.
}

# score_segments stores 3+3 provinces as '3+3综合', but admission_scores and
# the UI use '综合'.  Map the user-facing name → DB name for segment lookups.
_SEGMENT_CAT: dict = {'综合': '3+3综合'}


def _median(vals: list[float]) -> float:
    vals = sorted(vals)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


@dataclass
class SchoolStats:
    """Per-school per-province per-category admission statistics."""
    school_id: int
    province_id: int
    category: str            # '物理类', '历史类', '理科', '文科'

    mu_rank: float           # weighted mean admission rank (pool-normalized then rescaled)
    sigma_rank: float        # std dev of admission rank
    mu_percentile: float     # mu_rank / pool_total_latest
    years_data: int          # number of years with valid data
    is_year1_switch: bool    # True = 2025 Year-1 switch, σ from Wave2 cross-section
    pool_total: int          # total pool size for this province/category (latest year)

    # For drift-corrected schools (is_year1_switch=True)
    source_rank_2024: Optional[float] = None   # pre-correction rank
    drift_applied_pp: Optional[float] = None    # drift in pp


# GB/T 2260 city code → Chinese city name (major cities only)
CITY_CODE_TO_NAME = {
    '1101': '北京', '1201': '天津', '3101': '上海', '5001': '重庆',
    '1301': '石家庄', '1302': '唐山', '1303': '秦皇岛', '1304': '邯郸',
    '1305': '邢台', '1306': '保定', '1307': '张家口', '1308': '承德',
    '1401': '太原', '1501': '呼和浩特', '2101': '沈阳', '2102': '大连',
    '2201': '长春', '2224': '延边', '2301': '哈尔滨', '3201': '南京',
    '3202': '无锡', '3203': '徐州', '3204': '常州', '3205': '苏州',
    '3206': '南通', '3207': '连云港', '3301': '杭州', '3302': '宁波',
    '3401': '合肥', '3501': '福州', '3502': '厦门', '3601': '南昌',
    '3701': '济南', '3702': '青岛', '3710': '威海', '4101': '郑州',
    '4201': '武汉', '4301': '长沙', '4401': '广州', '4402': '韶关',
    '4403': '深圳', '4404': '珠海', '4405': '汕头', '4501': '南宁',
    '4601': '海口', '5101': '成都', '5102': '自贡', '5103': '攀枝花',
    '5201': '贵阳', '5301': '昆明', '5401': '拉萨', '6101': '西安',
    '6102': '铜川', '6103': '宝鸡', '6104': '咸阳', '6201': '兰州',
    '6301': '西宁', '6401': '银川', '6501': '乌鲁木齐', '6590': '石河子',
}


@dataclass
class UniMeta:
    """University metadata for utility scoring."""
    school_id: int
    name: str
    province_id: int
    city: str          # GB/T 2260 city code
    province_name: str
    f985: int
    f211: int
    dual_class: int
    level: int     # 2001=本科, 2002=专科

    @property
    def city_name(self) -> str:
        """Human-readable city name (falls back to province name)."""
        return CITY_CODE_TO_NAME.get(self.city, self.province_name or self.city)

    @property
    def tier(self) -> str:
        if self.f985:
            return '985'
        if self.f211:
            return '211'
        if self.dual_class:
            return '双一流'
        if self.level == 2001:
            return '本科'
        return '专科'

    @property
    def tier_score(self) -> float:
        return {'985': 1.0, '211': 0.75, '双一流': 0.55, '本科': 0.30, '专科': 0.10}[self.tier]


class DataLoader:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        # Thread-local storage for SQLite connections.
        # SQLite connections are NOT safe to share across threads even with
        # check_same_thread=False — concurrent cursor iterations corrupt state.
        # Each thread gets its own connection via the conn property.
        self._local = __import__('threading').local()

        self.pool_totals: dict = {}       # (province_id, year, category) → int
        self._latest_pool: dict = {}      # (province_id, category) → pool from most recent year
        self.school_stats: dict = {}      # (school_id, province_id, category) → SchoolStats
        self.uni_meta: dict = {}          # school_id → UniMeta
        self._tier_sigma: dict = {}       # tier → median σ_rank (for shrinkage)

        # Fail fast if the index coupling between advisor._MAJOR_DATA and
        # profile.MAJOR_CATEGORIES ever gets out of sync (e.g. someone adds a
        # category to one list but not the other).
        self._assert_major_category_alignment()

        self._load_pool_totals()
        self._load_uni_meta()
        self._load_school_stats()
        self._compute_tier_sigmas()

    @property
    def conn(self):
        """Per-thread SQLite connection (created on first access per thread)."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _assert_major_category_alignment(self):
        from engine.advisor import _MAJOR_DATA
        from engine.profile import MAJOR_CATEGORIES
        n_cats = len(MAJOR_CATEGORIES)
        n_data = len(_MAJOR_DATA)
        if n_cats != n_data:
            raise AssertionError(
                f"MAJOR_CATEGORIES ({n_cats} entries) and _MAJOR_DATA ({n_data} entries) "
                f"must have the same length. Update advisor._MAJOR_DATA when adding categories."
            )
        for i, (cat_display, _) in enumerate(MAJOR_CATEGORIES):
            cat_first = cat_display.split('/')[0].split('（')[0].strip()
            md_name = _MAJOR_DATA[i].name
            if cat_first not in md_name:
                raise AssertionError(
                    f"MAJOR_CATEGORIES[{i}] first token {cat_first!r} not found in "
                    f"_MAJOR_DATA[{i}].name={md_name!r}. Indices are misaligned — "
                    f"update advisor._MAJOR_DATA to match profile.MAJOR_CATEGORIES order."
                )

    # ── Pool totals ──────────────────────────────────────────────────────────

    def _load_pool_totals(self):
        rows = self.conn.execute("""
            SELECT province_id, year, category, MAX(cumulative) as total
            FROM score_segments
            GROUP BY province_id, year, category
        """).fetchall()
        for r in rows:
            self.pool_totals[(r['province_id'], r['year'], r['category'])] = r['total']

        # Pre-compute latest-year pool per (province, category).
        # Used as a fallback when historical years lack matching pool data
        # (e.g. 3+3 provinces only have 2025 score_segments; historical
        # admission_scores years 2020-2024 use the 2025 pool as a proxy).
        latest_year: dict = {}   # (province_id, category) → best year seen
        for (pid, yr, cat), pool in self.pool_totals.items():
            key = (pid, cat)
            if key not in latest_year or yr > latest_year[key]:
                latest_year[key] = yr
                self._latest_pool[key] = pool

        # Also track the latest year that has segment data, for score_to_rank fallback.
        # Needed when a province's category label changes across years
        # (e.g. 海南 dropped '3+3综合' in 2025; score_to_rank falls back to 2024).
        self._latest_segment_year: dict = latest_year  # (province_id, category) → year

    def get_pool_total(self, province_id: int, year: int, category: str) -> Optional[int]:
        cat = _SEGMENT_CAT.get(category, category)
        result = self.pool_totals.get((province_id, year, cat))
        if result:
            return result
        # Year not in score_segments — fall back to latest available year for
        # this province+category (e.g. 3+3 provinces only have 2025 data).
        return self._latest_pool.get((province_id, cat))

    def score_to_rank(self, province_id: int, year: int, category: str, score: int) -> Optional[int]:
        """
        Returns the provincial rank for a given raw score.
        rank = cumulative count of students scoring >= score (from 一分一段 table).

        Handles:
          - Exact single-point rows (score_high == score_low == score)
          - Top-band rows (score_high < score_low, e.g. '692及以上')
          - Score above table max → return top-band cumulative (rank ≈ 1..top_band)
          - Score below table min → return pool total
        """
        category = _SEGMENT_CAT.get(category, category)
        cur = self.conn.cursor()

        # Case 1: exact single-point row.
        #
        # Some official tables include zero-count placeholder rows above the
        # first positive score bucket.  Those rows are useful for display, but
        # they are not valid ranks.  Do not return rank=0 for a hypothetical
        # student score in that placeholder range; fall through to the bounds
        # logic, which treats scores above the highest positive bucket as rank 1.
        row = cur.execute("""
            SELECT cumulative FROM score_segments
            WHERE province_id=? AND year=? AND category=?
              AND score_high=? AND score_low=?
        """, [province_id, year, category, score, score]).fetchone()
        if row and row[0] > 0:
            # A few public 2025 tables contain local downward glitches in the
            # cumulative column.  Treat cumulative ranks as authoritative, but
            # enforce the monotonic invariant: a lower/equal score cannot have
            # a better rank than any higher score.
            prev = cur.execute("""
                SELECT MAX(cumulative) FROM score_segments
                WHERE province_id=? AND year=? AND category=?
                  AND score_high > ?
            """, [province_id, year, category, score]).fetchone()
            return max(row[0], prev[0] or 0)

        # Case 3: bounds (needed by Case 2 top-band check and out-of-range logic)
        bounds = cur.execute("""
            SELECT MIN(CASE WHEN cumulative > 0 THEN cumulative END) as top_rank,
                   MAX(cumulative) as pool,
                   MIN(score_high) as min_score,
                   MAX(CASE
                         WHEN cumulative > 0 AND score_high < score_low THEN score_low
                         WHEN cumulative > 0 THEN score_high
                       END) as max_score
            FROM score_segments
            WHERE province_id=? AND year=? AND category=?
        """, [province_id, year, category]).fetchone()
        if not bounds or bounds[0] is None:
            # No data for requested year+category — fall back to latest available year.
            # This handles cases like 海南 where '3+3综合' exists in 2024 but not 2025.
            fallback_yr = self._latest_segment_year.get((province_id, category))
            if fallback_yr and fallback_yr != year:
                return self.score_to_rank(province_id, fallback_yr, category, score)
            return None

        if score > bounds[3]:          # above table max → rank 1 (better than all observed)
            return 1
        if score < bounds[2]:          # below table min → pool total (worst rank)
            return bounds[1]

        # Case 2: score falls inside a band row (score_high < score_low)
        row = cur.execute("""
            SELECT cumulative, score_low FROM score_segments
            WHERE province_id=? AND year=? AND category=?
              AND score_low >= ? AND score_high <= ?
              AND score_high < score_low
            ORDER BY score_high DESC LIMIT 1
        """, [province_id, year, category, score, score]).fetchone()
        if row:
            # If score is at the TOP of the highest band (e.g. 750 in a 698–750 band),
            # the student outperforms everyone else observed → approximate as rank 1.
            if row[0] == bounds[0] and score >= row[1]:
                return 1
            return row[0]

        # Score is within range but no exact row (zero-count score) →
        # interpolate by finding next higher score's cumulative
        row = cur.execute("""
            SELECT MAX(cumulative) FROM score_segments
            WHERE province_id=? AND year=? AND category=?
              AND score_high >= ?
              AND cumulative > 0
        """, [province_id, year, category, score]).fetchone()
        return row[0] if row else bounds[1]

    def get_score_max(self, province_id: int, year: int, category: str) -> Optional[int]:
        """Returns the highest observed score in the segment table (MAX(score_high)).

        Uses score_high (not score_low) because the top-band row for provinces with
        a '698及以上' bucket has score_high=698 but score_low=750 (artificial sentinel).
        MAX(score_high) correctly returns 698 as the highest score anyone actually sat.
        """
        category = _SEGMENT_CAT.get(category, category)
        cur = self.conn.cursor()
        row = cur.execute("""
            SELECT MAX(score_high)
            FROM score_segments
            WHERE province_id=? AND year=? AND category=?
              AND cumulative > 0
        """, [province_id, year, category]).fetchone()
        if row and row[0] is not None:
            return row[0]
        # Fall back to latest available year (mirrors score_to_rank fallback)
        fallback_yr = self._latest_segment_year.get((province_id, category))
        if fallback_yr and fallback_yr != year:
            return self.get_score_max(province_id, fallback_yr, category)
        return None

    def rank_to_score(self, province_id: int, year: int, category: str, rank: int) -> Optional[int]:
        """
        Reverse lookup: given a rank, return the score threshold.
        Returns the score such that cumulative >= rank (i.e., the score a student
        at this rank would need to have achieved at minimum).
        """
        category = _SEGMENT_CAT.get(category, category)
        cur = self.conn.cursor()
        row = cur.execute("""
            SELECT score_high FROM score_segments
            WHERE province_id=? AND year=? AND category=?
              AND cumulative >= ?
            ORDER BY cumulative ASC LIMIT 1
        """, [province_id, year, category, rank]).fetchone()
        return row[0] if row else None

    # ── University metadata ──────────────────────────────────────────────────

    def _load_uni_meta(self):
        rows = self.conn.execute("""
            SELECT school_id, name, province_id, city,
                   COALESCE(province_name, '') as province_name,
                   COALESCE(f985, 0) as f985,
                   COALESCE(f211, 0) as f211,
                   COALESCE(dual_class, 0) as dual_class,
                   COALESCE(level, 2001) as level
            FROM universities
        """).fetchall()
        for r in rows:
            self.uni_meta[r['school_id']] = UniMeta(
                school_id=r['school_id'],
                name=r['name'],
                province_id=r['province_id'],
                city=r['city'] or '',
                province_name=r['province_name'] or '',
                f985=int(r['f985']), f211=int(r['f211']),
                dual_class=int(r['dual_class']), level=int(r['level'] or 2001),
            )

    # ── School stats ─────────────────────────────────────────────────────────

    def _is_undergrad_batch(self, batch_name: str) -> bool:
        batch_name = batch_name or ''
        if batch_name in UNDERGRAD_BATCH_EXPLICIT:
            return True
        if '本科' not in batch_name:
            return False
        for pat in UNDERGRAD_BATCH_EXCLUDE_PATTERNS:
            keyword = pat.strip('%')
            if keyword in batch_name:
                return False
        return True

    def _batch_priority(self, batch_name: str) -> int:
        """
        Prefer the main ordinary batch when multiple school lines exist in the
        same year. B/other段 is kept only if no cleaner main-batch row exists.
        """
        name = batch_name or ''
        if any(flag in name for flag in ('B段', 'C段', 'I段')):
            return 1
        return 0

    def _normalise_school_entries(self, entries: list[tuple]) -> list[tuple]:
        """
        Collapse duplicate year rows and remove obvious historical outliers.

        Input entries:  (year, rank, pool, batch_name)
        Output entries: (year, rank, pool)
        """
        from collections import defaultdict

        by_year = defaultdict(list)
        for year, rank, pool, batch_name in entries:
            by_year[year].append((year, rank, pool, batch_name))

        yearly = []
        for year in sorted(by_year):
            candidates = by_year[year]
            candidates.sort(key=lambda e: (self._batch_priority(e[3]), -e[1]))
            y, rank, pool, _batch = candidates[0]
            yearly.append((y, rank, pool))

        if len(yearly) < 3:
            return yearly

        percentiles = [(year, rank, pool, rank / pool) for year, rank, pool in yearly]
        med = _median([p for *_rest, p in percentiles])
        if med <= 0:
            return yearly

        deviations = [abs(p - med) for *_rest, p in percentiles]
        mad = _median(deviations)
        abs_threshold = max(RANK_OUTLIER_ABS_FLOOR, med * 0.75, mad * 3)
        latest_year = max(year for year, *_ in yearly)

        kept = []
        for year, rank, pool, p in percentiles:
            if year == latest_year:
                kept.append((year, rank, pool))
                continue
            high_outlier = p > med * RANK_OUTLIER_RATIO and (p - med) > abs_threshold
            low_outlier = p * RANK_OUTLIER_RATIO < med and (med - p) > abs_threshold
            if not (high_outlier or low_outlier):
                kept.append((year, rank, pool))

        return kept if len(kept) >= 2 else yearly

    def _load_school_stats(self):
        """
        For each (school, province, category), compute μ and σ from historical
        admission ranks, normalised by pool totals.

        For 2025-switching provinces: use actual 2025 物理/历史 stats when
        available; otherwise use 理科/文科 2024 + drift correction.
        For stable provinces: use available years with year weights.
        """
        # --- Step 1: Load all undergrad admission scores ---
        rows = self.conn.execute("""
            SELECT school_id, province_id, year, type_name, batch_name, min_rank
            FROM admission_scores
            WHERE min_rank IS NOT NULL AND min_rank > 0
            ORDER BY school_id, province_id, year
        """).fetchall()

        # Group by (school, province, effective_category)
        from collections import defaultdict
        grouped_raw = defaultdict(list)  # (school_id, province_id, category) → raw rows

        for r in rows:
            if not self._is_undergrad_batch(r['batch_name']):
                continue
            school_id = r['school_id']
            province_id = r['province_id']
            year = r['year']
            category = r['type_name']
            rank = r['min_rank']

            # Pool lookup — skip if no pool data
            pool = self.get_pool_total(province_id, year, category)
            if not pool:
                continue

            key = (school_id, province_id, category)
            grouped_raw[key].append((year, rank, pool, r['batch_name'] or ''))

        grouped = {
            key: self._normalise_school_entries(entries)
            for key, entries in grouped_raw.items()
        }

        # --- Step 2: Compute stats for ALL categories including 理科 for switching ---
        # We build 理科 stats for switching provinces too (stored in _li_stats_cache)
        # so Step 3 can borrow their temporal σ.
        self._li_stats_cache  = {}   # (school_id, province_id) → SchoolStats (理科)
        self._wen_stats_cache = {}   # (school_id, province_id) → SchoolStats (文科)

        for (school_id, province_id, category), entries in grouped.items():
            if province_id in SWITCHING_2025 and category == '理科':
                # Build temporarily into cache (not into school_stats for recommendations)
                stats = self._build_stats_obj(school_id, province_id, category, entries)
                if stats:
                    self._li_stats_cache[(school_id, province_id)] = stats
                continue

            if province_id in SWITCHING_2025 and category == '文科':
                # Cache 文科 stats so Step 3 can borrow temporal σ for 历史类 drift.
                stats = self._build_stats_obj(school_id, province_id, category, entries)
                if stats:
                    self._wen_stats_cache[(school_id, province_id)] = stats
                continue

            self._build_stats_from_entries(school_id, province_id, category, entries)
            if (province_id in SWITCHING_2025
                    and category in ('物理类', '历史类')
                    and any(y == 2025 for y, _, _ in entries)):
                self.school_stats[(school_id, province_id, category)].is_year1_switch = True

        # --- Step 3: 2025-switching provinces → drift-corrected prediction ---
        # Use 理科2024 data + apply_drift for μ.
        # For σ: use own temporal σ (from 理科 history) * YEAR1_SIGMA_FACTOR,
        # NOT the cross-sectional Wave2 σ (which overstates individual school uncertainty).
        li_entries = {k: v for k, v in grouped.items()
                      if k[1] in SWITCHING_2025 and k[2] == '理科'}

        for (school_id, province_id, _), entries in li_entries.items():
            # Find 2024 rank entry
            entry_2024 = next(
                ((y, r, p) for y, r, p in entries if y == SWITCHING_SOURCE_YEAR), None
            )
            if not entry_2024:
                continue

            _, rank_2024, pool_2024 = entry_2024
            pool_2025 = self.get_pool_total(province_id, 2025, '物理类')
            if not pool_2025:
                pool_2025 = pool_2024

            expected_rank = apply_drift(rank_2024, pool_2024, pool_2025)
            p_hist = rank_2024 / pool_2024
            drift_pp_val = (expected_rank - rank_2024 * pool_2025 / pool_2024) / pool_2025 * 100

            # σ: use own temporal σ (scaled to 2025 pool) × Year-1 factor.
            # Fall back to tier-peer σ if <2 years of own data.
            li_cache = self._li_stats_cache.get((school_id, province_id))
            if li_cache and li_cache.sigma_rank > 0 and li_cache.years_data >= 2:
                # Scale temporal σ from 理科 pool to 2025 物理类 pool
                temporal_sigma_frac = li_cache.sigma_rank / li_cache.pool_total
                sigma_rank = temporal_sigma_frac * pool_2025 * YEAR1_SIGMA_FACTOR
            else:
                # Fallback: will be patched by _compute_tier_sigmas × factor
                sigma_rank = None

            li_years = len(entries)
            key = (school_id, province_id, '物理类')
            if key in self.school_stats:
                continue
            self.school_stats[key] = SchoolStats(
                school_id=school_id,
                province_id=province_id,
                category='物理类',
                mu_rank=expected_rank,
                sigma_rank=sigma_rank or 0.0,   # patched by _compute_tier_sigmas if None
                mu_percentile=expected_rank / pool_2025,
                years_data=li_years,
                is_year1_switch=True,
                pool_total=pool_2025,
                source_rank_2024=rank_2024,
                drift_applied_pp=drift_pp_val,
            )

        # --- 文科→历史类 drift correction (parallel to 理科→物理类 above) ---
        # For 2025-switching provinces, 文科 2024 data is the base for 历史类 predictions.
        wen_entries = {k: v for k, v in grouped.items()
                       if k[1] in SWITCHING_2025 and k[2] == '文科'}

        for (school_id, province_id, _), entries in wen_entries.items():
            entry_2024 = next(
                ((y, r, p) for y, r, p in entries if y == SWITCHING_SOURCE_YEAR), None
            )
            if not entry_2024:
                continue

            _, rank_2024, pool_2024 = entry_2024
            pool_2025 = self.get_pool_total(province_id, 2025, '历史类')
            if not pool_2025:
                pool_2025 = pool_2024

            expected_rank = apply_drift(rank_2024, pool_2024, pool_2025)
            drift_pp_val = (expected_rank - rank_2024 * pool_2025 / pool_2024) / pool_2025 * 100

            # σ: use own temporal σ (scaled to 2025 pool) × Year-1 factor.
            # Fall back to tier-peer σ if <2 years of own data.
            wen_cache = self._wen_stats_cache.get((school_id, province_id))
            if wen_cache and wen_cache.sigma_rank > 0 and wen_cache.years_data >= 2:
                temporal_sigma_frac = wen_cache.sigma_rank / wen_cache.pool_total
                sigma_rank = temporal_sigma_frac * pool_2025 * YEAR1_SIGMA_FACTOR
            else:
                sigma_rank = None

            wen_years = len(entries)
            key = (school_id, province_id, '历史类')
            if key in self.school_stats:
                continue
            self.school_stats[key] = SchoolStats(
                school_id=school_id,
                province_id=province_id,
                category='历史类',
                mu_rank=expected_rank,
                sigma_rank=sigma_rank or 0.0,   # patched by _compute_tier_sigmas if None
                mu_percentile=expected_rank / pool_2025,
                years_data=wen_years,
                is_year1_switch=True,
                pool_total=pool_2025,
                source_rank_2024=rank_2024,
                drift_applied_pp=drift_pp_val,
            )

    def _build_stats_obj(self, school_id, province_id, category, entries):
        """Build and return a SchoolStats without adding to school_stats dict."""
        stats_key = (school_id, province_id, category)
        self._build_stats_from_entries(school_id, province_id, category, entries)
        return self.school_stats.pop(stats_key, None)

    def _build_stats_from_entries(self, school_id, province_id, category, entries):
        """Build SchoolStats from list of (year, rank, pool) tuples."""
        if not entries:
            return

        # Use rank normalised by pool to compute weighted mean percentile
        weighted_sum = 0.0
        weight_total = 0.0
        percentiles = []

        for year, rank, pool in entries:
            w = YEAR_WEIGHTS.get(year, 1)
            p = rank / pool
            weighted_sum += p * w
            weight_total += w
            percentiles.append(p)

        mu_percentile = weighted_sum / weight_total

        # Latest pool total (most recent year available)
        latest_year = max(y for y, _, _ in entries)
        latest_pool = next(pool for y, _, pool in entries if y == latest_year)

        mu_rank = mu_percentile * latest_pool

        # σ: use own history if ≥ MIN_YEARS_OWN_SIGMA years, else use None (shrinkage later)
        # Sample variance (÷ n−1) to avoid underestimating volatility at small n.
        years_data = len(entries)
        if years_data >= MIN_YEARS_OWN_SIGMA:
            n_var = max(years_data - 1, 1)   # guard: never divide by 0
            variance = sum((p - mu_percentile) ** 2 for p in percentiles) / n_var
            sigma_pp = math.sqrt(variance) * 100
            sigma_rank = math.sqrt(variance) * latest_pool
        else:
            sigma_rank = None  # filled in by _compute_tier_sigmas

        key = (school_id, province_id, category)
        self.school_stats[key] = SchoolStats(
            school_id=school_id,
            province_id=province_id,
            category=category,
            mu_rank=mu_rank,
            sigma_rank=sigma_rank or 0.0,  # placeholder; patched in _compute_tier_sigmas
            mu_percentile=mu_percentile,
            years_data=years_data,
            is_year1_switch=False,
            pool_total=latest_pool,
        )

    def _compute_tier_sigmas(self):
        """
        Compute per-tier median σ_rank (as fraction of pool).
        Used for hierarchical shrinkage when school has <3 years of data.
        """
        from collections import defaultdict
        tier_sigmas = defaultdict(list)

        for (school_id, province_id, category), stats in self.school_stats.items():
            if stats.years_data >= MIN_YEARS_OWN_SIGMA and stats.sigma_rank > 0:
                uni = self.uni_meta.get(school_id)
                if uni:
                    # Store as fraction of pool for cross-province comparability
                    tier_sigmas[uni.tier].append(stats.sigma_rank / stats.pool_total)

        self._tier_sigma = {}
        for tier, vals in tier_sigmas.items():
            vals.sort()
            self._tier_sigma[tier] = vals[len(vals) // 2]  # median

        # Patch schools with σ=0 using tier peer median
        for key, stats in self.school_stats.items():
            if stats.sigma_rank == 0:
                uni = self.uni_meta.get(stats.school_id)
                tier = uni.tier if uni else '本科'
                fallback_sigma_frac = self._tier_sigma.get(tier, 0.05)
                factor = YEAR1_SIGMA_FACTOR if stats.is_year1_switch else 1.0
                stats.sigma_rank = fallback_sigma_frac * stats.pool_total * factor

        # Apply 2025 calibration corrections (μ shift + σ scale)
        # These are derived from backtesting against actual 2025 admissions outcomes.
        for (school_id, province_id, category), stats in self.school_stats.items():
            corr = PROVINCE_CALIBRATION.get((province_id, category))
            if not corr:
                continue
            mu_shift_sigmas, sigma_scale = corr
            # Apply σ scale first (μ shift is in units of new σ)
            stats.sigma_rank = stats.sigma_rank * sigma_scale
            # Apply μ shift in rank units
            stats.mu_rank = stats.mu_rank + mu_shift_sigmas * stats.sigma_rank
            # Recompute percentile
            if stats.pool_total > 0:
                stats.mu_percentile = stats.mu_rank / stats.pool_total

    def get_stats(self, school_id: int, province_id: int, category: str) -> Optional[SchoolStats]:
        return self.school_stats.get((school_id, province_id, category))

    def summary(self) -> str:
        n_stats = len(self.school_stats)
        n_switch = sum(1 for s in self.school_stats.values() if s.is_year1_switch)
        n_sparse = sum(1 for s in self.school_stats.values() if s.years_data < MIN_YEARS_OWN_SIGMA)
        return (f"DataLoader: {len(self.uni_meta)} schools, "
                f"{n_stats} (school,province,cat) stats, "
                f"{n_switch} drift-corrected, "
                f"{n_sparse} with peer σ shrinkage")
