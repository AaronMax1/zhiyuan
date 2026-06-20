"""
Major selection advisor — 张雪峰-style family background profiling.

Given a FamilyProfile, produces:
  - Major category recommendations with concrete reasoning
  - Utility weight adjustments (reflecting career priorities)
  - Slot ratio adjustments (reflecting risk tolerance)
  - A narrative advisory note (Chinese, direct & practical)

Data sources:
  - 麦可思(MyCOS) 中国大学生就业报告 2022–2024
  - 教育部 就业质量报告 2023
  - Standard knowledge of Chinese professional licensing requirements
"""

from dataclasses import dataclass, field
from typing import List, NamedTuple


# ── FamilyProfile ──────────────────────────────────────────────────────────────

@dataclass
class FamilyProfile:
    """
    Student's family and personal background for major selection advice.
    Used by MajorAdvisor to personalise recommendations.
    All fields optional — partial profiles are fully supported.
    """
    # Economic standing
    economic_tier: str = '工薪'
    # '贫困' — rural/poverty-line household
    # '工薪' — ordinary salaried family (most common)
    # '中产' — middle class (dual income, owns housing, college-educated parents)
    # '富裕' — affluent (business owners, senior professionals)

    # Parents' occupational background (list; multiple values allowed)
    parental_background: List[str] = field(default_factory=list)
    # 'government' — civil servant / CCP/state apparatus
    # 'medical'    — doctor, nurse, or pharmacist in household
    # 'education'  — teacher or university faculty
    # 'technical'  — engineer, IT, or R&D professional
    # 'business'   — private business owner or entrepreneur
    # 'legal'      — lawyer, judge, notary, or compliance officer
    # 'finance'    — banker, fund manager, accountant, or analyst
    # 'military'   — military or police

    # Career priority (single most important goal)
    career_priority: str = '未确定'
    # '就业稳定'   — stable employment; willing to trade income for security
    # '收入最大化' — maximise expected lifetime income
    # '个人发展'   — career advancement / promotion ceiling
    # '个人兴趣'   — follow passion regardless of ROI
    # '社会地位'   — prestige and social recognition
    # '未确定'     — unclear; needs guidance

    # Risk tolerance → maps to 冲/稳/保 ratio
    risk_tolerance: str = '稳健'
    # '保守' — safety-first
    # '稳健' — balanced (default)
    # '激进' — reach-heavy

    # Years family can financially support without student income
    family_support_years: int = 6
    # 临床医学 needs ~8 yr (5-yr undergrad + 3-yr 规培)
    # Pure-research path needs 10 yr+

    # First-generation college student (第一代大学生)
    first_gen_college: bool = False
    # Less family guidance → benefits from structured, clear-path careers

    # Plans for postgraduate study
    postgrad_willing: str = '视情况'
    # '考研'   — planning master's degree (硕士, ~3 yr after undergrad)
    # '考博'   — planning PhD / 直博 / 硕博连读 (~7-8 yr after undergrad)
    # '否'     — want to work directly after undergrad
    # '视情况' — open to it if warranted

    # Overseas study plans
    overseas_plan: str = '无计划'
    # '本科出国' — planning undergraduate study abroad; gaokao is safety net only
    # '研究生出国' — finish undergrad in China, pursue master's/PhD abroad
    # '无计划'   — domestic path only


# ── Major data table ───────────────────────────────────────────────────────────
# Indices 0–14 correspond to MAJOR_CATEGORIES order in engine/profile.py

class _MD(NamedTuple):
    name:       str    # display name matching MAJOR_CATEGORIES[i][0]
    employ:     float  # 6-month initial employment rate  (MyCOS 2022–2024 avg)
    income:     float  # 5-year relative income index: 1.0 = 计算机 (highest)
    years:      float  # years until first *stable* employment
    connx:      float  # connection-benefit weight: 0 = pure merit, 1 = all connections
    tier:       float  # school-tier premium: 0 = tier irrelevant, 1 = huge tier gap
    long_train: bool   # True if stable career requires >6 yr commitment


# Employment rates from MyCOS 就业绿皮书 2024 (2023届本科毕业生)
# Income index normalised so 计算机 = 1.00
_MAJOR_DATA: dict = {
    0:  _MD('计算机/AI/数据',      0.958, 1.00, 4.0, 0.08, 0.65, False),
    1:  _MD('电子/通信/集成电路',   0.952, 0.87, 4.0, 0.08, 0.60, False),
    2:  _MD('机械/自动化/机器人',   0.931, 0.73, 4.0, 0.10, 0.45, False),
    3:  _MD('土木/建筑/规划',      0.885, 0.65, 4.0, 0.18, 0.40, False),
    4:  _MD('经济/金融/会计',      0.903, 0.83, 4.5, 0.45, 0.80, False),
    5:  _MD('工商/管理/市场',      0.862, 0.66, 4.0, 0.42, 0.70, False),
    6:  _MD('法学',               0.834, 0.63, 5.5, 0.60, 0.75, False),
    7:  _MD('医学/护理/药学',      0.908, 0.75, 7.0, 0.28, 0.55, True),
    8:  _MD('师范/教育',           0.897, 0.56, 4.0, 0.20, 0.35, False),
    9:  _MD('文史哲/中文',         0.792, 0.44, 4.0, 0.48, 0.70, False),
    10: _MD('外语',               0.873, 0.55, 4.0, 0.30, 0.55, False),
    11: _MD('理学（数理化生统）',   0.784, 0.58, 6.0, 0.12, 0.65, False),
    12: _MD('新闻/传媒/传播',      0.841, 0.52, 4.0, 0.55, 0.60, False),
    13: _MD('艺术/设计',           0.786, 0.54, 4.0, 0.50, 0.40, False),
    14: _MD('农林/环境/生态',      0.870, 0.50, 4.0, 0.10, 0.35, False),
}


# ── Output types ───────────────────────────────────────────────────────────────

@dataclass
class MajorAdvice:
    """Advisor assessment for one major category."""
    major_idx:      int
    major_name:     str
    fit_score:      float      # 0–1 overall fit for this family/goal combination
    recommendation: str        # '推荐' | '可考虑' | '谨慎' | '不建议'
    reasons:        List[str]  # concrete, specific reasons (Chinese)


@dataclass
class AdvisorOutput:
    """Complete advisor output derived from one FamilyProfile."""
    # Major category indices (into MAJOR_CATEGORIES in profile.py), sorted best-first
    recommended_indices: List[int]   # fit_score ≥ 0.65
    cautioned_indices:   List[int]   # fit_score < 0.35

    # Full scored list for all 15 categories, sorted by advisor rank.
    # fit_score remains the base fit; ordering also considers family/career priority.
    major_advice: List[MajorAdvice]

    # Suggested utility function weights
    w_tier:  float
    w_city:  float
    w_major: float

    # Slot distribution targets (passed to Query)
    chong_target: float   # fraction for 冲 (reach) bucket
    bao_target:   float   # fraction for 保 (safety) bucket

    # Natural-language advisory note (Chinese)
    narrative: str

    # Priority-ordered top-3 major indices (same order as narrative; used for keyword injection)
    # Sorted by advisor rank, filtered to 推荐/可考虑
    narrative_top3_indices: List[int]

    # Machine-readable flags for downstream logic
    key_flags: List[str]
    # 'long_training_risk'  — high-training major likely but support_years too short
    # 'connection_heavy'    — top picks all depend heavily on parental connections
    # 'first_gen_caution'   — first-gen + unclear direction
    # 'interest_vs_reality' — '个人兴趣' priority but interest major has poor ROI
    # 'missing_keywords'    — no major keywords set; advisor injects defaults


# ── MajorAdvisor ──────────────────────────────────────────────────────────────

class MajorAdvisor:
    """
    Generates major-selection advice from a FamilyProfile.
    Stateless — call advise() directly.
    """

    _SLOT_RATIOS: dict = {
        '保守': (0.15, 0.40),
        '稳健': (0.20, 0.30),
        '激进': (0.30, 0.20),
    }

    _PATH_CLARITY: dict = {
        0: 0.90, 1: 0.82, 2: 0.80, 3: 0.70, 4: 0.72,
        5: 0.58, 6: 0.70, 7: 0.78, 8: 0.82, 9: 0.50,
        10: 0.55, 11: 0.55, 12: 0.48, 13: 0.45, 14: 0.62,
    }

    _CATEGORY_SCORE_BONUS: dict = {
        '历史类': {
            0: -0.08, 1: -0.24, 2: -0.22, 11: -0.14,
            4: 0.05, 5: 0.08, 6: 0.08, 8: 0.08, 9: 0.08,
            10: 0.06, 12: 0.06, 14: 0.04,
        },
        '文科': {
            0: -0.08, 1: -0.24, 2: -0.22, 11: -0.14,
            4: 0.05, 5: 0.08, 6: 0.08, 8: 0.08, 9: 0.08,
            10: 0.06, 12: 0.06, 14: 0.04,
        },
    }

    _BG_SCORE_BONUS: dict = {
        'medical':    {7: 0.28},
        'legal':      {6: 0.24},
        'finance':    {4: 0.22},
        'education':  {8: 0.24},
        'technical':  {0: 0.14, 1: 0.14},
        'business':   {4: 0.22, 5: 0.22},
        'government': {6: 0.22, 5: 0.22, 8: 0.12},
        'military':   {8: 0.18, 5: 0.14, 6: 0.14},
    }

    _BG_REASON: dict = {
        'medical': '家有医疗背景，对培养周期、实习和执业路径更熟悉',
        'legal': '父母有法律背景，法学入行信息和实习资源更清楚',
        'finance': '家有金融背景，实习和入行路径更清楚',
        'education': '教育家庭对教师职业和培养路径有直接了解',
        'technical': '家有技术背景，学习路线和求职判断有直接参考',
        'business': '商业家庭背景，与经济、管理类路径更匹配',
        'government': '父母有体制内背景，公共事务和管理路径信息更清楚',
        'military': '军警家庭背景，纪律性和公共服务路径匹配度较高',
    }

    def advise(self, fp: FamilyProfile, has_major_keywords: bool = False,
               category: str = '') -> AdvisorOutput:
        """
        Generate advice for a given FamilyProfile.

        fp:                  family/personal background
        has_major_keywords:  True if student already specified major preferences
        category:            student's exam category, e.g. '物理类' / '历史类'
        """
        advice_list = sorted(
            [self._score_major(i, fp, category) for i in range(15)],
            key=lambda a: -self._advisor_rank(a, fp),
        )

        recommended = [a.major_idx for a in advice_list if a.fit_score >= 0.65]
        cautioned   = [a.major_idx for a in advice_list if a.fit_score < 0.35]

        w_tier, w_city, w_major = self._suggest_weights(fp)

        # 本科出国 → gaokao is safety net → all 保
        if fp.overseas_plan == '本科出国':
            chong_target, bao_target = 0.05, 0.70
        else:
            chong_target, bao_target = self._SLOT_RATIOS.get(fp.risk_tolerance, (0.20, 0.30))
        key_flags = self._extract_flags(fp, advice_list, has_major_keywords)
        narrative = self._build_narrative(fp, advice_list, key_flags)

        # Priority-sorted top-3 (matches narrative display order)
        narrative_top3 = sorted(
            [a for a in advice_list if a.recommendation in ('推荐', '可考虑')],
            key=lambda a: -self._advisor_rank(a, fp)
        )[:3]
        narrative_top3_indices = [a.major_idx for a in narrative_top3]

        return AdvisorOutput(
            recommended_indices=recommended,
            cautioned_indices=cautioned,
            major_advice=advice_list,
            w_tier=w_tier,
            w_city=w_city,
            w_major=w_major,
            chong_target=chong_target,
            bao_target=bao_target,
            narrative=narrative,
            narrative_top3_indices=narrative_top3_indices,
            key_flags=key_flags,
        )

    # ── Private: score one major category ─────────────────────────────────────

    def _score_major(self, idx: int, fp: FamilyProfile,
                     category: str = '') -> MajorAdvice:
        md = _MAJOR_DATA[idx]
        reasons: List[str] = []

        # Base quality score: weighted blend of employ, income, and time-to-job
        time_factor = max(0.0, 1.0 - (md.years - 4.0) / 8.0)  # 4yr→1.0, 12yr→0.0
        path_clarity = self._PATH_CLARITY.get(idx, 0.55)
        score = (
            0.40 * md.employ
            + 0.25 * md.income
            + 0.20 * time_factor
            + 0.15 * path_clarity
        )

        category_bonus = self._CATEGORY_SCORE_BONUS.get(category, {}).get(idx, 0.0)
        if category_bonus:
            score += category_bonus
            if category_bonus < 0:
                reasons.append(f'{category}报考该方向存在科类/选科适配压力')
            else:
                reasons.append(f'{category}与该方向适配度较高')

        # ── Economic tier adjustments ──────────────────────────────────────────
        if fp.economic_tier in ('贫困', '工薪'):
            if md.connx <= 0.15 and md.income >= 0.70:
                score += 0.08
                reasons.append('就业不依赖人脉，收入较高，适合普通家庭')
            if path_clarity >= 0.80:
                score += 0.05
                reasons.append('职业路径较清晰，试错成本相对可控')
            if md.connx >= 0.45:
                score -= 0.15
                reasons.append('高度依赖人脉资源，普通家庭积累有限')
            if md.long_train and fp.family_support_years < 8:
                score -= 0.18
                reasons.append(f'培养周期长（约{md.years:.0f}年），家庭经济支撑压力大')

        elif fp.economic_tier == '中产':
            if md.connx >= 0.55:
                score -= 0.08
                reasons.append('较依赖人脉，中产家庭资源有一定但不充裕')
            if md.long_train and fp.family_support_years < 8:
                score -= 0.10
                reasons.append(f'培养周期约{md.years:.0f}年，需确认家庭支撑能力')

        # '富裕': minimal penalisation; connections and long training manageable

        # ── Parental background boosts ─────────────────────────────────────────
        pb = fp.parental_background
        for bg in pb:
            bg_bonus = self._BG_SCORE_BONUS.get(bg, {}).get(idx, 0.0)
            if not bg_bonus:
                continue
            score += bg_bonus
            reasons.append(self._BG_REASON.get(bg, '家庭背景与该方向匹配'))
            if bg == 'medical' and idx == 7 and md.long_train and fp.family_support_years < 8:
                score += 0.12
                reasons = [r for r in reasons if '培养周期' not in r]
                reasons.append('医疗家庭背景可部分抵消长培训周期风险')

        # ── Career priority alignment ──────────────────────────────────────────
        cp = fp.career_priority

        if cp == '就业稳定':
            if md.employ >= 0.90:
                score += 0.08
                reasons.append(f'就业率{md.employ*100:.0f}%，符合稳定就业目标')
            if idx in (8, 7, 2, 14):   # 师范, 医学, 机械(国企), 农林(基层选调)
                score += 0.08
                reasons.append('存在稳定编制或规范化职业路径')

        elif cp == '收入最大化':
            if md.income >= 0.80:
                score += 0.10
                reasons.append('5年薪资预期高，收入天花板较高')
            elif md.income < 0.58:
                score -= 0.12
                reasons.append('长期收入上限偏低，不符合收入最大化目标')

        elif cp == '个人发展':
            if md.tier >= 0.65:
                score += 0.06
                reasons.append('名校光环在晋升路径中有明显加成')

        elif cp == '社会地位':
            if md.tier >= 0.70:
                score += 0.08
                reasons.append('名校＋此专业社会认可度高')
            if idx in (7, 6):
                score += 0.12
                reasons.append('职业本身社会认可度高（医生/律师）')

        elif cp == '未确定':
            if path_clarity >= 0.80:
                score += 0.04
                reasons.append('职业路径清晰，适合目标尚未明确时优先比较')

        # '个人兴趣': no priority adjustment; other factors dominate

        # ── First-generation college student ──────────────────────────────────
        if fp.first_gen_college:
            if md.connx >= 0.45:
                score -= 0.10
                reasons.append('第一代大学生缺乏行业人脉，此专业人脉依赖度高')
            if path_clarity >= 0.80:
                score += 0.04
                reasons.append('职业路径清晰，适合第一代大学生规划')

        # ── Postgrad willingness ───────────────────────────────────────────────
        if fp.postgrad_willing == '否':
            if idx == 11:    # 理学 — most need 考研 for good jobs
                score -= 0.12
                reasons.append('理学本科直就业竞争力弱，通常需要考研')
            if md.years >= 6 and not md.long_train:
                score -= 0.08
                reasons.append('本科直就业竞争力偏弱，读研后优势更明显')

        elif fp.postgrad_willing == '考研':
            if idx == 11:
                score += 0.10
                reasons.append('有考研意愿，理学硕士竞争力大幅提升')
            if idx in (4, 5):   # 金融/管理 — MBA/专硕路径加成
                score += 0.05
                reasons.append('硕士学位对金融/管理晋升有加成')

        elif fp.postgrad_willing == '考博':
            # PhD path = undergrad(4) + master(3) + PhD(4) ≈ 11yr commitment
            if idx == 11:
                score += 0.20
                reasons.append('有读博意愿，理学学术路径完全打开')
            if idx in (0, 1):   # CS/电子 PhD — 高校/研究院/大厂研究院
                score += 0.10
                reasons.append('工科博士在高校/研究院/头部企业研究院有高竞争力')
            if fp.economic_tier in ('贫困', '工薪') and fp.family_support_years < 11:
                score -= 0.15
                reasons.append('读博全程约11年，普通家庭长期支撑压力大')

        # ── Overseas study plan ────────────────────────────────────────────────────
        if fp.overseas_plan == '研究生出国':
            if idx in (0, 1, 11):   # 计算机, 电子, 理学 — strong overseas grad market
                score += 0.08
                reasons.append('理工科背景有利于海外研究生申请')
            if md.tier >= 0.65:
                score += 0.05
                reasons.append('名校GPA和背景对海外申请有加成')

        elif fp.overseas_plan == '本科出国':
            # Gaokao is safety net only — compress toward neutral
            score *= 0.5
            reasons.append('高考作为保底，专业/学校偏好权重大幅降低')

        # ── Long training hard floor ───────────────────────────────────────────
        if md.long_train and fp.family_support_years < 6:
            score -= 0.25
            reasons.append(
                f'家庭可支撑{fp.family_support_years}年，不足以覆盖{md.years:.0f}年培训期'
            )

        score = round(max(0.0, min(1.0, score)), 3)

        if score >= 0.70:
            rec = '推荐'
        elif score >= 0.55:
            rec = '可考虑'
        elif score >= 0.35:
            rec = '谨慎'
        else:
            rec = '不建议'

        if not reasons:
            reasons.append(f'就业率{md.employ*100:.0f}%，5年相对薪资指数{md.income:.2f}')

        return MajorAdvice(
            major_idx=idx,
            major_name=md.name,
            fit_score=score,
            recommendation=rec,
            reasons=reasons,
        )

    # ── Private: narrative tie-breaking ───────────────────────────────────────

    # Which major indices directly correspond to each parental background
    _BG_MAJORS: dict = {
        'medical':    {7},
        'legal':      {6},
        'finance':    {4},
        'government': {6, 5, 8},
        'technical':  {0, 1},
        'business':   {4, 5},
        'education':  {8},
        'military':   {8, 5, 6},    # 师范/公共服务路径
    }

    # Which major indices align with each career priority
    _CP_MAJORS: dict = {
        '就业稳定':   {8, 7, 2, 14},
        '收入最大化': {0, 1, 4},
        '社会地位':   {7, 6},
        '个人发展':   {0, 4, 5, 11},
        '个人兴趣':   set(),   # no override — let background dominate
        '未确定':     {0, 2, 8},
    }

    def _narrative_priority(self, major_idx: int, fp: FamilyProfile) -> int:
        """
        Tie-breaking weight for narrative display order.
        Higher = show earlier. Applied after fit_score descending sort.
        """
        p = 0
        for bg, indices in self._BG_MAJORS.items():
            if bg in fp.parental_background and major_idx in indices:
                p += 3   # direct family resource match — strongest signal
        cp_indices = self._CP_MAJORS.get(fp.career_priority, set())
        if major_idx in cp_indices:
            p += 2       # career goal alignment
        return p

    def _advisor_rank(self, advice: MajorAdvice, fp: FamilyProfile) -> float:
        """Final ordering value. fit_score is not changed; rank includes pathway priority."""
        return advice.fit_score + self._narrative_priority(advice.major_idx, fp) * 0.22

    # ── Private: weight suggestions ────────────────────────────────────────────

    def _suggest_weights(self, fp: FamilyProfile):
        # Overseas plans override career priority for weight selection
        if fp.overseas_plan == '本科出国':
            return 0.30, 0.40, 0.30   # gaokao is safety net; city matters most for backup
        if fp.overseas_plan == '研究生出国':
            return 0.55, 0.20, 0.25   # name school matters for overseas MS/PhD apps

        cp = fp.career_priority
        if cp == '就业稳定':
            return 0.35, 0.30, 0.35
        elif cp == '收入最大化':
            return 0.40, 0.20, 0.40
        elif cp == '社会地位':
            return 0.65, 0.20, 0.15
        elif cp == '个人兴趣':
            return 0.30, 0.30, 0.40
        elif cp == '个人发展':
            return 0.45, 0.25, 0.30
        else:   # '未确定'
            return 0.40, 0.30, 0.30

    # ── Private: flag extraction ───────────────────────────────────────────────

    def _extract_flags(self, fp: FamilyProfile, advice_list: List[MajorAdvice],
                       has_keywords: bool) -> List[str]:
        flags: List[str] = []
        top5_idx = {a.major_idx for a in advice_list[:5]}
        if 7 in top5_idx and _MAJOR_DATA[7].long_train and fp.family_support_years < 8:
            flags.append('long_training_risk')
        high_connx = [a for a in advice_list[:5] if _MAJOR_DATA[a.major_idx].connx >= 0.45]
        if len(high_connx) >= 3:
            flags.append('connection_heavy')
        if fp.first_gen_college and fp.career_priority == '未确定':
            flags.append('first_gen_caution')
        if fp.career_priority == '个人兴趣' and fp.economic_tier in ('贫困', '工薪'):
            flags.append('interest_vs_reality')
        if fp.overseas_plan == '本科出国' and fp.economic_tier in ('贫困', '工薪'):
            flags.append('overseas_economic_mismatch')
        if fp.overseas_plan == '本科出国':
            flags.append('gaokao_safety_net_only')
        if not has_keywords:
            flags.append('missing_keywords')
        return flags

    # ── Private: narrative generation ─────────────────────────────────────────

    def _build_narrative(self, fp: FamilyProfile,
                         advice_list: List[MajorAdvice],
                         flags: List[str]) -> str:
        lines: List[str] = []

        eco_map = {'贫困': '农村/贫困家庭', '工薪': '普通工薪家庭',
                   '中产': '中产家庭', '富裕': '富裕家庭'}
        cp_map  = {'就业稳定': '就业稳定优先', '收入最大化': '收入最大化优先',
                   '个人发展': '个人发展优先', '个人兴趣': '兴趣优先',
                   '社会地位': '社会地位优先', '未确定': '职业目标待明确'}
        rt_map  = {'保守': '保守风险偏好', '稳健': '稳健风险偏好', '激进': '激进风险偏好'}

        ctx = [eco_map.get(fp.economic_tier, fp.economic_tier)]
        if fp.career_priority in cp_map:
            ctx.append(cp_map[fp.career_priority])
        if fp.risk_tolerance in rt_map:
            ctx.append(rt_map[fp.risk_tolerance])
        if fp.first_gen_college:
            ctx.append('第一代大学生')
        lines.append(f"【家庭背景分析】（{'，'.join(ctx)}）")

        # Top recommendations — sort by fit_score, then by background/career relevance
        candidates = [a for a in advice_list if a.recommendation in ('推荐', '可考虑')]
        # Advisor rank keeps the display order aligned with keyword injection.
        top = sorted(
            candidates,
            key=lambda a: -self._advisor_rank(a, fp)
        )[:3]
        if top:
            lines.append('【推荐方向】')
            for i, adv in enumerate(top, 1):
                r = adv.reasons[0] if adv.reasons else ''
                lines.append(f'  {i}. {adv.major_name}  — {r}')

        # Cautions (only show for working/poor families — the ones who need explicit warnings)
        if fp.economic_tier in ('贫困', '工薪'):
            bad = [a for a in advice_list if a.recommendation == '不建议']
            if bad:
                names = '、'.join(a.major_name for a in bad[:3])
                lines.append(f'【需谨慎的方向】{names}')
                lines.append('  上述专业就业率偏低或高度依赖资源，普通家庭起步阻力较大。')

        # Flag-specific paragraphs
        if 'long_training_risk' in flags:
            lines.append('【特别提示】临床医学培训周期约8年（本科5年＋规培3年），'
                         '请确认家庭经济可持续支撑后再填报。')

        if 'interest_vs_reality' in flags:
            lines.append('【兴趣与现实】选择兴趣专业没有问题，但请做好就业预期管理——'
                         '文科/艺术本科阶段就业天花板有限，建议同步规划考研或考公路径。')

        if 'first_gen_caution' in flags:
            lines.append('【第一代大学生建议】优先选择职业路径清晰的专业（计算机、机械、师范），'
                         '避免强人脉依赖型专业（新闻传媒、部分人文社科）。')

        if 'overseas_economic_mismatch' in flags:
            lines.append('【⚠️ 出国计划与经济条件矛盾】本科留学年费通常30-80万，'
                         '与您目前的家庭经济条件差距较大。建议明确资金来源（奖学金/助学金/贷款）再确认该计划。')

        if 'gaokao_safety_net_only' in flags:
            lines.append('【出国保底策略】高考志愿仅作为保底，建议绝大多数志愿填保守选项（约70%保底），'
                         '少量冲刺可省略，专注海外申请主线。')
        elif fp.overseas_plan == '研究生出国':
            lines.append('【研究生出国策略】本科名校背景对海外MS/PhD申请有显著加成——'
                         '优先争取985/211院校，GPA和科研经历比专业本身更关键。')
        elif fp.risk_tolerance == '保守':
            lines.append('【志愿策略】保守风险偏好：保底志愿约40%，冲刺约15%。')
        elif fp.risk_tolerance == '激进':
            lines.append('【志愿策略】积极风险偏好：冲刺志愿约30%，保底不低于20%。')

        return '\n'.join(lines)
