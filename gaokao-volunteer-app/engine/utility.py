"""
Utility scoring for the exchange-argument sort.

U(school, major) = w_tier × tier_score
                 + w_city × city_score
                 + w_major × major_type_score

Exchange argument guarantee: optimal slot ordering = descending U.
(Proof: for independent admission events, E[U(A before B)] > E[U(B before A)] iff U_A > U_B)

Major classification: specialist vs generalist
  specialist  = career path requires this specific degree (licensed professions)
  generalist  = school tier dominates; major is transferable credential
  semi        = strong professional path but license not strictly required
"""

from dataclasses import dataclass, field
from typing import Optional

# ── Major type classification ─────────────────────────────────────────────
# Key = level3_name (from raw_json) or sp_name keyword
# Determined by: does China have a mandatory 职业资格证书 tied to this major?

SPECIALIST_LEVEL3 = {
    # 医学 — 执业医师/护士证
    '临床医学类', '口腔医学类', '中医学类', '中西医结合类',
    '针灸推拿类', '护理学类',
    # 药学 — 执业药师
    '药学类', '中药学类',
    # 法学 — 法律职业资格
    '法学类',
    # 建筑/土木 — 注册建筑师/结构工程师/建造师
    '建筑类', '土木类',
    # 教育 — 教师资格证 (师范类)
    '教育学类',
    # 医学技术
    '医学技术类',
}

SEMI_SPECIALIST_LEVEL3 = {
    # 会计 — CPA路径强但非必须
    '财务会计类', '会计学类',
    # 城乡规划 — 注册规划师
    '城乡规划类',
    # 测绘 — 测绘资质
    '测绘类',
    # 卫生检验/公共卫生
    '公共卫生与预防医学类',
}

# Everything else → generalist
# (计算机/电子/机械/材料/化工/经管/文史哲/理学/传媒 等)

SPECIALIST_KEYWORDS = {
    '临床', '口腔', '中医', '针灸', '护理', '助产',
    '药学', '中药', '法学', '建筑学', '土木工程',
    '师范', '教育学',
}

def classify_major(level3_name: str, sp_name: str) -> str:
    """
    Returns 'specialist', 'semi', or 'generalist'.
    level3_name from raw_json; sp_name is the specific major name.
    """
    if level3_name in SPECIALIST_LEVEL3:
        return 'specialist'
    if level3_name in SEMI_SPECIALIST_LEVEL3:
        return 'semi'
    # Keyword fallback on sp_name
    for kw in SPECIALIST_KEYWORDS:
        if kw in sp_name:
            return 'specialist'
    return 'generalist'


# ── Specialty school domain-leader bonus ──────────────────────────────────
#
# These schools are industry leaders in their domain despite not holding a
# 985/211 label.  When a student's major_keywords overlap a school's specialty,
# the bonus is added to tier_score before computing effective_tier.
#
# Rationale: 本科 tier_score = 0.30.  Adding 0.35 lifts a 政法 school to ~0.65,
# comparable to mid-211 prestige for law-focused students — which reflects
# actual graduate outcomes and bar-exam pass rates.
#
# Keyword matching: partial containment (student_kw ⊆ school_kw or vice-versa).
# Only applies when prefs.target_major_keywords is non-empty.

SPECIALTY_SCHOOLS: dict[int, dict[str, float]] = {

    # ── 政法类 (五院四系) ──────────────────────────────────────────────────
    # 本科 (0.30) → boosted to ~0.65 for law queries
    # Invariant: boosted_tier = 0.30 + bonus must stay below 211 minimum effective_tier
    # (0.75 × 0.80 = 0.60).  So bonus < 0.30 for all 本科 schools.
    180:  {'法学': 0.28, '法律': 0.28, '知识产权': 0.28, '国际法': 0.28,
            '刑事': 0.28, '民商法': 0.28, '侦查': 0.28, '诉讼': 0.28},   # 西南政法大学
    323:  {'法学': 0.28, '法律': 0.28, '知识产权': 0.28, '国际法': 0.28,
            '刑事': 0.28, '民商法': 0.28, '诉讼': 0.28},                   # 华东政法大学
    376:  {'法学': 0.25, '法律': 0.25, '知识产权': 0.23, '刑事': 0.23},   # 西北政法大学
    1011: {'法学': 0.18, '法律': 0.18},                                    # 上海政法学院

    # ── 财经类 ────────────────────────────────────────────────────────────
    # 本科 (0.30) → boosted to ~0.55-0.58 for finance/economics queries
    229:  {'金融': 0.28, '经济': 0.28, '会计': 0.28, '财务': 0.28,
            '统计': 0.25, '贸易': 0.25, '财经': 0.28},                     # 东北财经大学
    175:  {'金融': 0.25, '经济': 0.25, '会计': 0.25, '财务': 0.25,
            '统计': 0.22, '财经': 0.25},                                    # 江西财经大学
    164:  {'金融': 0.25, '经济': 0.25, '会计': 0.25, '财务': 0.25,
            '财经': 0.25},                                                   # 南京财经大学
    258:  {'金融': 0.22, '经济': 0.22, '会计': 0.22, '财务': 0.22},        # 浙江财经大学
    84:   {'金融': 0.22, '经济': 0.22, '会计': 0.22, '财务': 0.22},        # 天津财经大学

    # ── 外语类 ────────────────────────────────────────────────────────────
    # 本科 (0.30) → boosted to ~0.55-0.58 for language queries
    290:  {'英语': 0.28, '翻译': 0.28, '日语': 0.28, '法语': 0.28,
            '德语': 0.28, '外语': 0.28, '国际': 0.22, '西班牙': 0.28},    # 广东外语外贸大学
    193:  {'英语': 0.28, '翻译': 0.28, '日语': 0.28, '法语': 0.28,
            '外语': 0.28, '国际': 0.22},                                    # 四川外国语大学
    87:   {'英语': 0.25, '翻译': 0.25, '日语': 0.25, '法语': 0.25,
            '外语': 0.25, '国际': 0.20},                                    # 天津外国语大学
    216:  {'英语': 0.25, '翻译': 0.25, '日语': 0.25, '外语': 0.25},        # 大连外国语大学
    373:  {'英语': 0.22, '翻译': 0.22, '日语': 0.22, '外语': 0.22},        # 西安外国语大学

    # ── 医学类 (非双一流/非211 但省级强校) ───────────────────────────────
    # 本科 (0.30) → boosted to ~0.50-0.55 for medical queries
    168:  {'临床': 0.25, '口腔': 0.22, '医学': 0.22, '护理': 0.18,
            '药学': 0.18},                                                   # 南京医科大学
    178:  {'临床': 0.22, '口腔': 0.20, '医学': 0.20, '护理': 0.18},        # 重庆医科大学
    295:  {'临床': 0.20, '口腔': 0.18, '医学': 0.18},                       # 广州医科大学

    # ── 师范/教育类 ────────────────────────────────────────────────────────
    589: {'师范': 0.22, '教育': 0.22, '学前教育': 0.20,
          '体育': 0.16, '思想政治': 0.18, '心理学': 0.18},                   # 首都师范大学
    473: {'师范': 0.18, '教育': 0.18, '学前教育': 0.16,
          '思想政治': 0.16},                                                  # 福建师范大学
    241: {'师范': 0.18, '教育': 0.18, '学前教育': 0.16,
          '体育': 0.14},                                                       # 浙江师范大学
    622: {'师范': 0.16, '教育': 0.16, '学前教育': 0.14,
          '思想政治': 0.14},                                                  # 山东师范大学

    # ── 邮电/电子/通信类 ───────────────────────────────────────────────────
    160: {'电子': 0.25, '通信': 0.25, '集成电路': 0.24, '微电子': 0.24,
          '半导体': 0.22, '信号': 0.22, '信息工程': 0.22},                   # 南京邮电大学
    159: {'电子': 0.25, '通信': 0.22, '集成电路': 0.23, '微电子': 0.23,
          '半导体': 0.22, '信号': 0.20, '自动化': 0.18},                    # 杭州电子科技大学
    184: {'电子': 0.22, '通信': 0.25, '集成电路': 0.20, '微电子': 0.20,
          '信号': 0.22, '信息工程': 0.22},                                    # 重庆邮电大学
    532: {'电子': 0.18, '通信': 0.18, '微电子': 0.18, '信号': 0.18},        # 桂林电子科技大学

    # ── 交通/轨道/海事类 ───────────────────────────────────────────────────
    488: {'交通': 0.22, '交通运输': 0.22, '交通工程': 0.22,
          '道路': 0.18, '桥梁': 0.18, '轨道': 0.22, '车辆工程': 0.18},       # 兰州交通大学
    157: {'交通': 0.20, '交通运输': 0.20, '交通工程': 0.20,
          '道路': 0.18, '桥梁': 0.18, '轨道': 0.20, '车辆工程': 0.18},       # 华东交通大学
    1018: {'交通': 0.20, '交通运输': 0.20, '交通工程': 0.20,
           '道路': 0.20, '桥梁': 0.20, '港口': 0.18, '航海': 0.16},          # 重庆交通大学

    # ── 电力/电气/能源类 ───────────────────────────────────────────────────
    393: {'电气': 0.24, '电力': 0.24, '能源': 0.20,
          '新能源': 0.20, '智能电网': 0.22, '储能': 0.18},                  # 东北电力大学
    317: {'电气': 0.22, '电力': 0.22, '能源': 0.20,
          '新能源': 0.20, '智能电网': 0.20, '储能': 0.18},                  # 上海电力大学
    146: {'电气': 0.18, '电力': 0.18, '能源': 0.16,
          '智能电网': 0.16},                                                  # 南京工程学院
    1041: {'电气': 0.16, '电力': 0.16, '能源': 0.14,
           '智能电网': 0.14},                                                 # 沈阳工程学院

    # ── 建筑/土木/规划类 ───────────────────────────────────────────────────
    351: {'建筑学': 0.26, '建筑': 0.24, '土木': 0.24, '城乡规划': 0.24,
          '工程管理': 0.20, '道路': 0.18, '桥梁': 0.18, '工程造价': 0.18},   # 西安建筑科技大学
    572: {'建筑学': 0.22, '建筑': 0.22, '土木': 0.20, '城乡规划': 0.20,
          '工程管理': 0.18, '工程造价': 0.18},                               # 北京建筑大学
    212: {'建筑学': 0.20, '建筑': 0.20, '土木': 0.20, '城乡规划': 0.18,
          '工程管理': 0.18, '工程造价': 0.18},                               # 沈阳建筑大学
    528: {'建筑学': 0.18, '建筑': 0.18, '土木': 0.18, '城乡规划': 0.16,
          '工程管理': 0.16, '工程造价': 0.16},                               # 山东建筑大学
    322: {'建筑学': 0.18, '建筑': 0.18, '土木': 0.18, '城乡规划': 0.16,
          '工程管理': 0.16, '工程造价': 0.16},                               # 安徽建筑大学

    # ── 农林/环境/生态类 ───────────────────────────────────────────────────
    169: {'农学': 0.20, '农业': 0.18, '林学': 0.24, '园艺': 0.18,
          '环境': 0.18, '生态': 0.20, '水产': 0.14, '动物': 0.16},          # 南京林业大学
    287: {'农学': 0.22, '农业': 0.22, '林学': 0.18, '园艺': 0.20,
          '环境': 0.18, '生态': 0.18, '水产': 0.18, '动物': 0.20},          # 华南农业大学
    468: {'农学': 0.18, '农业': 0.18, '林学': 0.18, '园艺': 0.18,
          '环境': 0.16, '生态': 0.16, '水产': 0.16, '动物': 0.16},          # 福建农林大学
}


EXTRA_DOMAIN_LEADER_SCHOOLS: dict[int, set[str]] = {
    # 211/双一流 already have tier prestige; this list lets major-first scoring
    # still recognise domain strength as专业匹配质量, not just school tier.
    569: {'法学', '法律', '知识产权', '国际法', '刑事', '民商法', '诉讼'},  # 中国政法大学
    414: {'法学', '法律', '知识产权', '国际法', '刑事', '民商法', '诉讼'},  # 中南财经政法大学
    52:  {'师范', '教育', '学前教育', '体育', '思想政治', '心理学'},        # 北京师范大学
    131: {'师范', '教育', '学前教育', '体育', '思想政治', '心理学'},        # 华东师范大学
    142: {'师范', '教育', '学前教育', '体育', '思想政治', '心理学'},        # 东北师范大学
    420: {'师范', '教育', '学前教育', '体育', '思想政治', '心理学'},        # 华中师范大学
    334: {'师范', '教育', '学前教育', '体育', '思想政治', '心理学'},        # 陕西师范大学
    115: {'师范', '教育', '学前教育', '体育', '思想政治', '心理学'},        # 南京师范大学
    98:  {'师范', '教育', '学前教育', '体育', '思想政治', '心理学'},        # 华南师范大学
    58:  {'师范', '教育', '学前教育', '体育', '思想政治', '心理学'},        # 湖南师范大学
    661: {'电子', '通信', '集成电路', '微电子', '半导体', '信号', '信息工程'},  # 电子科技大学
    57:  {'电子', '通信', '集成电路', '微电子', '半导体', '信号', '信息工程'},  # 西安电子科技大学
    48:  {'电子', '通信', '集成电路', '微电子', '半导体', '信号', '信息工程'},  # 北京邮电大学
    38:  {'交通', '交通运输', '交通工程', '道路', '桥梁', '轨道', '车辆工程'},  # 北京交通大学
    51:  {'交通', '交通运输', '交通工程', '道路', '桥梁', '轨道', '车辆工程'},  # 西南交通大学
    33:  {'交通', '交通运输', '交通工程', '航海', '海事', '轮机', '港口'},      # 大连海事大学
    36:  {'交通', '交通运输', '交通工程', '道路', '桥梁', '车辆工程'},          # 长安大学
    831: {'电气', '电力', '能源', '新能源', '智能电网', '储能'},            # 华北电力大学（北京）
    591: {'电气', '电力', '能源', '新能源', '智能电网', '储能'},            # 华北电力大学（保定）
    43:  {'农学', '农业', '林学', '园艺', '环境', '生态', '水产', '动物'},  # 北京林业大学
    419: {'农学', '农业', '林学', '园艺', '环境', '生态', '水产', '动物'},  # 东北林业大学
    113: {'农学', '农业', '林学', '园艺', '环境', '生态', '水产', '动物'},  # 南京农业大学
    417: {'农学', '农业', '林学', '园艺', '环境', '生态', '水产', '动物'},  # 华中农业大学
    137: {'农学', '农业', '林学', '园艺', '环境', '生态', '水产', '动物'},  # 东北农业大学
    100: {'农学', '农业', '林学', '园艺', '环境', '生态', '水产', '动物'},  # 四川农业大学
    332: {'农学', '农业', '林学', '园艺', '环境', '生态', '水产', '动物'},  # 西北农林科技大学
}


def _keyword_overlap(student_kw: str, school_kw: str) -> bool:
    return bool(student_kw and school_kw and (student_kw in school_kw or school_kw in student_kw))


def _specialty_bonus(school_id: int, target_major_keywords: list) -> float:
    """
    Returns the specialty boost for this school given the student's major keywords.
    0.0 if no match or school not in SPECIALTY_SCHOOLS.
    """
    if not school_id or school_id not in SPECIALTY_SCHOOLS:
        return 0.0
    if not target_major_keywords:
        return 0.0
    best = 0.0
    school_spec = SPECIALTY_SCHOOLS[school_id]
    for student_kw in target_major_keywords:
        for school_kw, bonus in school_spec.items():
            if _keyword_overlap(student_kw, school_kw):
                if bonus > best:
                    best = bonus
    return best


def _domain_leader_match(school_id: int, target_major_keywords: list) -> bool:
    """True when the school is a recognised domain leader for the requested major."""
    if not school_id or not target_major_keywords:
        return False

    specialty = SPECIALTY_SCHOOLS.get(school_id)
    if specialty:
        for student_kw in target_major_keywords:
            if any(_keyword_overlap(student_kw, school_kw) for school_kw in specialty):
                return True

    extra = EXTRA_DOMAIN_LEADER_SCHOOLS.get(school_id)
    if extra:
        for student_kw in target_major_keywords:
            if any(_keyword_overlap(student_kw, school_kw) for school_kw in extra):
                return True

    return False


# ── Major path-risk labels ─────────────────────────────────────────────────
#
# These majors are not excluded.  They are flagged because undergraduate
# outcomes depend heavily on school tier, internships, postgraduate study,
# exams, or family resources.  The practical product invariant is:
# "do not let a path-dependent major silently rank first for a neutral user."

LOW_PATH_CLARITY_MAJOR_KEYWORDS = (
    '工商管理',
    '市场营销',
    '人力资源管理',
    '旅游管理',
    '酒店管理',
    '会展经济',
    '公共管理',
    '公共事业管理',
    '行政管理',
)


def is_low_path_clarity_major(sp_name: str, level3_name: str = '') -> bool:
    """True for broad/path-dependent majors that need an explicit plan."""
    text = f'{sp_name or ""} {level3_name or ""}'
    return any(kw in text for kw in LOW_PATH_CLARITY_MAJOR_KEYWORDS)


def major_path_note(sp_name: str, level3_name: str = '') -> str:
    """Short user-facing note for path-dependent majors."""
    if not is_low_path_clarity_major(sp_name, level3_name):
        return ''
    return '路径依赖强：建议提前确认读研、考公、实习或家庭资源路径'


def _low_path_clarity_penalty(sp_name: str, level3_name: str,
                              prefs: 'Preferences') -> float:
    if not is_low_path_clarity_major(sp_name, level3_name):
        return 0.0
    explicit_match = bool(
        prefs.target_major_keywords
        and any(kw in sp_name for kw in prefs.target_major_keywords)
    )
    base = 0.04 if explicit_match else 0.10
    return min(0.25, base + prefs.path_unclear_major_penalty)


# ── Preferences ───────────────────────────────────────────────────────────

@dataclass
class Preferences:
    """
    Student preferences collected via questionnaire.
    All fields optional — defaults give maximum coverage.
    """
    # Hard constraints (filter before scoring)
    min_tier: str = '专科'        # '985', '211', '双一流', '本科', '专科'
    preferred_cities: list = field(default_factory=list)   # [] = no preference
    target_major_keywords: list = field(default_factory=list)  # [] = no preference
    major_type_preference: Optional[str] = None   # 'specialist', 'generalist', None

    prefer_home_province: bool = False   # True = boost all schools in student's province

    # Soft weights (must sum approximately to 1.0)
    w_tier: float = 0.50
    w_city: float = 0.30
    w_major: float = 0.20

    # Extra soft penalty for broad/path-dependent majors under risk-sensitive
    # family profiles.  Default 0 keeps manual mode conservative.
    path_unclear_major_penalty: float = 0.0

    def __post_init__(self):
        total = self.w_tier + self.w_city + self.w_major
        if abs(total - 1.0) > 0.01:
            # Normalize
            self.w_tier /= total
            self.w_city /= total
            self.w_major /= total


TIER_ORDER = ['985', '211', '双一流', '本科', '专科']

def tier_at_least(school_tier: str, min_tier: str) -> bool:
    """Returns True if school_tier >= min_tier in prestige."""
    try:
        return TIER_ORDER.index(school_tier) <= TIER_ORDER.index(min_tier)
    except ValueError:
        return True


# ── Utility scorer ─────────────────────────────────────────────────────────

def utility_score(
    tier_score: float,
    city: str,
    sp_name: str,
    level3_name: str,
    prefs: Preferences,
    prestige_within_tier: float = 0.5,
    school_province_id: int = 0,
    student_province_id: int = 0,
    school_id: int = 0,
) -> float:
    """
    Computes utility U ∈ [0, 1] for a (school, major) pair.

    tier_score:            from UniMeta.tier_score (985=1.0, 211=0.75, ...)
    city:                  school's city string (human-readable)
    sp_name:               specific major name
    level3_name:           major discipline category
    prefs:                 student preferences
    prestige_within_tier:  0→1, normalised rank-selectivity within same tier
                           (1.0 = most selective in tier, 0.0 = least selective)
                           Pre-computed by recommend engine across candidate set.
    school_id:             used to apply specialty domain-leader bonus (SPECIALTY_SCHOOLS)
    """
    # Tier component: blend tier_score with within-tier prestige (20% weight)
    # Apply specialty bonus first (e.g. 西政 + 法学 query → treat as ~211 prestige)
    bonus = _specialty_bonus(school_id, prefs.target_major_keywords)
    boosted_tier = min(tier_score + bonus, 1.0)
    effective_tier = boosted_tier * (0.80 + 0.20 * prestige_within_tier)

    # City score
    in_province = (prefs.prefer_home_province
                   and school_province_id > 0
                   and school_province_id == student_province_id)
    city_match = bool(prefs.preferred_cities and any(c in city for c in prefs.preferred_cities))
    if prefs.preferred_cities or prefs.prefer_home_province:
        city_score = 1.0 if (city_match or in_province) else 0.2
    else:
        city_score = 0.5

    # Major score
    if prefs.target_major_keywords:
        major_match = any(kw in sp_name for kw in prefs.target_major_keywords)
        if major_match:
            major_score = 1.0 if _domain_leader_match(school_id, prefs.target_major_keywords) else 0.80
        else:
            major_score = 0.2
    else:
        if prefs.major_type_preference:
            mtype = classify_major(level3_name, sp_name)
            major_score = 1.0 if mtype == prefs.major_type_preference else 0.4
        else:
            major_score = 0.5

    score = (prefs.w_tier * effective_tier
             + prefs.w_city * city_score
             + prefs.w_major * major_score)
    return max(0.0, score - _low_path_clarity_penalty(sp_name, level3_name, prefs))
