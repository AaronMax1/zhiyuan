"""
Student profile questionnaire for 高考志愿填报助手.

Collects student inputs via structured Q&A and converts to a Query object.

Usage:
    from engine.profile import collect_cli, profile_to_query
    from engine.data_loader import DataLoader

    dl = DataLoader()
    profile = collect_cli(dl)
    query   = profile_to_query(profile, dl)
"""

from dataclasses import dataclass, field
from typing import Optional, List

from engine.advisor import FamilyProfile, MajorAdvisor

YEAR = 2025

# ── Province registry ─────────────────────────────────────────────────────────
# (province_id, display_name, exam_mode, categories)
# exam_mode: '3+3', '3+1+2', '文理科'
_PROVINCE_DATA = [
    (11, '北京',   '3+3',   ['综合']),
    (12, '天津',   '3+3',   ['综合']),
    (13, '河北',   '3+1+2', ['物理类', '历史类']),
    (14, '山西',   '3+1+2', ['物理类', '历史类']),
    (15, '内蒙古', '3+1+2', ['物理类', '历史类']),
    (21, '辽宁',   '3+1+2', ['物理类', '历史类']),
    (22, '吉林',   '3+1+2', ['物理类', '历史类']),
    (23, '黑龙江', '3+1+2', ['物理类', '历史类']),
    (31, '上海',   '3+3',   ['综合']),
    (32, '江苏',   '3+1+2', ['物理类', '历史类']),
    (33, '浙江',   '3+3',   ['综合']),
    (34, '安徽',   '3+1+2', ['物理类', '历史类']),
    (35, '福建',   '3+1+2', ['物理类', '历史类']),
    (36, '江西',   '3+1+2', ['物理类', '历史类']),
    (37, '山东',   '3+3',   ['综合']),
    (41, '河南',   '3+1+2', ['物理类', '历史类']),
    (42, '湖北',   '3+1+2', ['物理类', '历史类']),
    (43, '湖南',   '3+1+2', ['物理类', '历史类']),
    (44, '广东',   '3+1+2', ['物理类', '历史类']),
    (45, '广西',   '3+1+2', ['物理类', '历史类']),
    (46, '海南',   '3+1+2', ['物理类', '历史类']),  # 2025 一分一段 uses 物理/历史 split
    (50, '重庆',   '3+1+2', ['物理类', '历史类']),
    (51, '四川',   '3+1+2', ['物理类', '历史类']),
    (52, '贵州',   '3+1+2', ['物理类', '历史类']),
    (53, '云南',   '3+1+2', ['物理类', '历史类']),
    (54, '西藏',   '3+1+2', ['物理类', '历史类']),
    (61, '陕西',   '3+1+2', ['物理类', '历史类']),
    (62, '甘肃',   '3+1+2', ['物理类', '历史类']),
    (63, '青海',   '3+1+2', ['物理类', '历史类']),
    (64, '宁夏',   '3+1+2', ['物理类', '历史类']),
    (65, '新疆',   '文理科', ['理科', '文科']),
]

PROVINCES: List[dict] = [
    {'id': p[0], 'name': p[1], 'mode': p[2], 'categories': p[3]}
    for p in _PROVINCE_DATA
]

_PROVINCE_BY_ID:   dict = {p['id']:   p for p in PROVINCES}
_PROVINCE_BY_NAME: dict = {p['name']: p for p in PROVINCES}


# ── City list ─────────────────────────────────────────────────────────────────
# Ordered by approximate student preference / population size
CITIES: List[str] = [
    '北京', '上海', '广州', '深圳', '成都', '杭州', '武汉', '西安',
    '南京', '天津', '重庆', '苏州', '长沙', '郑州', '青岛', '济南',
    '合肥', '厦门', '福州', '南昌', '哈尔滨', '沈阳', '长春', '昆明',
    '贵阳', '南宁', '石家庄', '太原', '兰州', '乌鲁木齐', '海口',
    '呼和浩特', '西宁', '银川', '拉萨',
]


# ── Major categories ──────────────────────────────────────────────────────────
# Each entry: (display_name, [keywords matching sp_name in DB])
MAJOR_CATEGORIES: List[tuple] = [
    ('计算机 / AI / 数据',
        ['计算机', '软件', '人工智能', '数据', '网络安全', '物联网', '信息安全', '密码']),
    ('电子 / 通信 / 集成电路',
        ['电子', '通信', '集成电路', '微电子', '半导体', '信号']),
    ('机械 / 自动化 / 机器人',
        ['机械', '自动化', '机器人', '智能制造', '车辆工程', '工业工程']),
    ('土木 / 建筑 / 规划',
        ['土木', '建筑学', '城乡规划', '工程管理', '道路', '桥梁', '工程造价']),
    ('经济 / 金融 / 会计',
        ['经济', '金融', '财务', '会计', '审计', '税务', '保险', '投资']),
    ('工商 / 管理 / 市场',
        ['工商', '管理', '市场营销', '人力资源', '电子商务', '物流', '供应链']),
    ('法学',
        ['法学', '法律', '知识产权', '国际法']),
    ('医学 / 护理 / 药学',
        ['临床', '口腔', '护理', '药学', '医学', '中医', '公共卫生', '生物医学', '助产']),
    ('师范 / 教育',
        ['师范', '教育', '学前教育', '体育', '思想政治']),
    ('文史哲 / 中文',
        ['中文', '汉语', '文学', '历史学', '哲学', '语言学', '秘书']),
    ('外语',
        ['英语', '翻译', '日语', '法语', '德语', '韩语', '西班牙', '阿拉伯', '俄语']),
    ('理学（数理化生统）',
        ['数学', '统计', '物理学', '化学', '生物科学', '地理科学', '大气科学']),
    ('新闻 / 传媒 / 传播',
        ['新闻', '传媒', '广播', '传播学', '影视', '广告', '出版']),
    ('艺术 / 设计',
        ['艺术', '设计', '美术', '动画', '音乐', '舞蹈', '戏剧', '摄影']),
    ('农林 / 环境 / 生态',
        ['农学', '林学', '园艺', '农业', '环境', '生态', '水产', '动物']),
]


# ── Weight presets ────────────────────────────────────────────────────────────
WEIGHT_PRESETS: List[dict] = [
    {
        'key':    '学校优先',
        'desc':   '以学校名气/层次为主，专业和城市为辅',
        'w_tier': 0.70, 'w_city': 0.20, 'w_major': 0.10,
    },
    {
        'key':    '专业优先',
        'desc':   '以意向专业为主，兼顾学校层次',
        'w_tier': 0.30, 'w_city': 0.20, 'w_major': 0.50,
    },
    {
        'key':    '城市优先',
        'desc':   '以意向城市为主，希望留在特定地区发展',
        'w_tier': 0.30, 'w_city': 0.55, 'w_major': 0.15,
    },
    {
        'key':    '均衡',
        'desc':   '学校、专业、城市三方均衡考虑',
        'w_tier': 0.45, 'w_city': 0.30, 'w_major': 0.25,
    },
]


# ── Minimum tier options ──────────────────────────────────────────────────────
TIER_OPTIONS: List[tuple] = [
    ('985院校及以上',  '985'),
    ('211院校及以上',  '211'),
    ('双一流院校',     '双一流'),
    ('本科院校即可',   '本科'),
    ('不限（含专科）', '专科'),
]


# ── StudentProfile ────────────────────────────────────────────────────────────

@dataclass
class StudentProfile:
    """
    All student inputs collected from the questionnaire.
    Convert to engine.recommend.Query via profile_to_query().
    """
    province_id:      int
    year:             int        = YEAR
    category:         str        = ''       # e.g. '物理类', '历史类', '综合', '理科', '文科'
    score:            int        = 0        # raw gaokao total score
    min_tier:         str        = '本科'
    preferred_cities: List[str]  = field(default_factory=list)
    major_keywords:   List[str]  = field(default_factory=list)   # passed as target_major_keywords
    w_tier:           float      = 0.45
    w_city:           float      = 0.30
    w_major:          float      = 0.25
    max_slots:        int        = 30
    prefer_home_province: bool  = False    # True = boost all schools within student's province
    xuanke_codes:     List[str] = field(default_factory=list)  # e.g. ['70000','70001','70002']
    # Optional family background (enables 张雪峰-style major advice)
    family:           Optional[FamilyProfile] = None

    @property
    def province_name(self) -> str:
        return _PROVINCE_BY_ID.get(self.province_id, {}).get('name', str(self.province_id))

    def summary(self) -> str:
        cities = '、'.join(self.preferred_cities) if self.preferred_cities else '不限'
        majors = ('、'.join(self.major_keywords[:4]) + ('…' if len(self.major_keywords) > 4 else '')) \
                 if self.major_keywords else '不限'
        return (
            f"{self.province_name} {self.category}  {self.score}分  "
            f"层次≥{self.min_tier}  城市:{cities}  专业:{majors}"
        )


# ── profile_to_query ──────────────────────────────────────────────────────────

def profile_to_query(profile: StudentProfile, dl) -> tuple:
    """
    Converts a StudentProfile to a (Query, AdvisorOutput | None) tuple.

    Returns:
      (query, advisor_output)
        query          — ready for RecommendationEngine.recommend()
        advisor_output — AdvisorOutput if profile.family was set, else None.
                         Callers should reuse this instead of calling
                         MajorAdvisor().advise() a second time.

    When profile.family is set:
      - Utility weights are replaced by advisor-suggested values
        (reflecting career priorities)
      - Slot ratios (chong/bao targets) are adjusted for risk tolerance
      - If major_keywords is empty, advisor-recommended major keywords are injected
        (with strict_major_match=False so non-matching majors still appear as fallback)

    Uses DataLoader to convert score → province rank.
    Falls back to pool total (last rank) when score is below the table minimum.
    """
    from engine.recommend import Query
    from engine.utility import Preferences

    rank = dl.score_to_rank(profile.province_id, profile.year, profile.category, profile.score)
    if rank is None:
        rank = dl.get_pool_total(profile.province_id, profile.year, profile.category) or 999999

    # Resolve weights and slot ratios — advisor overrides when family is provided
    w_tier  = profile.w_tier
    w_city  = profile.w_city
    w_major = profile.w_major
    chong_target = 0.20
    bao_target   = 0.30
    keywords     = list(profile.major_keywords)
    strict_match = True
    path_unclear_major_penalty = 0.0

    advisor_output = None
    if profile.family is not None:
        family = profile.family
        advisor_output = MajorAdvisor().advise(
            family,
            has_major_keywords=bool(profile.major_keywords),
            category=profile.category,
        )
        # Broad/path-dependent majors are still allowed, but ordinary families,
        # first-gen students, and direct-employment plans should not see them
        # silently promoted above clearer-path alternatives.
        if family.economic_tier in ('贫困', '工薪'):
            path_unclear_major_penalty += 0.04
        if family.first_gen_college:
            path_unclear_major_penalty += 0.04
        if family.postgrad_willing == '否':
            path_unclear_major_penalty += 0.04
        if family.career_priority in ('就业稳定', '未确定'):
            path_unclear_major_penalty += 0.03
        if any(bg in family.parental_background for bg in ('business', 'government')):
            path_unclear_major_penalty = max(0.0, path_unclear_major_penalty - 0.04)
        path_unclear_major_penalty = min(0.12, path_unclear_major_penalty)

        # Apply weight adjustments
        w_tier  = advisor_output.w_tier
        w_city  = advisor_output.w_city
        w_major = advisor_output.w_major
        # Apply slot ratio adjustments
        chong_target = advisor_output.chong_target
        bao_target   = advisor_output.bao_target
        # Inject keywords if student has none; use narrative ordering (priority-aware)
        # so background-relevant majors (医学, 法学 etc.) are injected ahead of raw-score leaders
        inject_src = (advisor_output.narrative_top3_indices
                      if advisor_output.narrative_top3_indices
                      else advisor_output.recommended_indices[:3])
        if not keywords and inject_src:
            injected_cats = [
                MAJOR_CATEGORIES[i][1]
                for i in inject_src
                if i < len(MAJOR_CATEGORIES)
            ]
            for kw_list in injected_cats:
                keywords.extend(kw_list)
            # De-duplicate while preserving order
            keywords = list(dict.fromkeys(keywords))   # deduplicate, preserve order
            strict_match = False   # advisor keywords are suggestions, not hard filter

    prefs = Preferences(
        min_tier=profile.min_tier,
        preferred_cities=profile.preferred_cities,
        target_major_keywords=keywords,
        prefer_home_province=profile.prefer_home_province,
        w_tier=w_tier,
        w_city=w_city,
        w_major=w_major,
        path_unclear_major_penalty=path_unclear_major_penalty,
    )
    query = Query(
        province_id=profile.province_id,
        student_rank=rank,
        category=profile.category,
        xuanke_codes=profile.xuanke_codes,
        prefs=prefs,
        max_slots=profile.max_slots,
        strict_major_match=strict_match,
        chong_target=chong_target,
        bao_target=bao_target,
    )
    return query, advisor_output


# ── CLI helpers ───────────────────────────────────────────────────────────────

def _ask_single(prompt: str, options: list, display_fn=None) -> int:
    """Print numbered options, return 0-based index of user's choice."""
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        label = display_fn(opt) if display_fn else str(opt)
        print(f"  {i:>2}. {label}")
    while True:
        raw = input("请输入编号: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"  ✗ 无效输入，请输入 1–{len(options)} 之间的数字")


def _ask_multi(prompt: str, options: list, display_fn=None, cols: int = 4) -> List[int]:
    """
    Print options in a grid layout.
    User enters comma-separated numbers; 0 = skip/不限.
    Returns list of selected 0-based indices (empty = 不限).
    """
    print(f"\n{prompt}")
    print("  0. 不限 / 跳过")
    per_row = cols
    for i, opt in enumerate(options, 1):
        label = display_fn(opt) if display_fn else str(opt)
        sep = '\n' if i % per_row == 0 else ''
        end = sep if sep else ''
        # Print items with padding; flush newline at end of row
        print(f"  {i:>2}. {label:<28}", end=end or ('  ' if i < len(options) else ''))
    print()  # final newline

    while True:
        raw = input("输入编号（多选用逗号，如 1,3；0=不限）: ").strip()
        if raw == '0' or raw == '':
            return []
        parts = [p.strip() for p in raw.split(',') if p.strip()]
        indices, ok = [], True
        for p in parts:
            if p.isdigit() and 1 <= int(p) <= len(options):
                idx = int(p) - 1
                if idx not in indices:
                    indices.append(idx)
            else:
                print(f"  ✗ 无效编号 '{p}'，请重新输入")
                ok = False
                break
        if ok and indices:
            return indices
        if ok and not indices:
            print("  ✗ 请至少输入一个编号，或输入 0 跳过")


def _ask_score(province_id: int, category: str, dl=None) -> int:
    """Prompt for gaokao score with optional rank preview."""
    if dl:
        pool = dl.get_pool_total(province_id, YEAR, category)
        hint = f"（{_PROVINCE_BY_ID[province_id]['name']} {category} 共约 {pool:,} 人参考）" if pool else ''
    else:
        hint = ''
    print(f"\n【第3题】您的高考总分（{YEAR}年，整数）{hint}：")
    while True:
        raw = input("请输入分数: ").strip()
        if raw.isdigit() and 100 <= int(raw) <= 900:
            score = int(raw)
            if dl:
                rank = dl.score_to_rank(province_id, YEAR, category, score)
                if rank:
                    print(f"  → {score}分 ≈ 全省第 {rank:,} 名")
                else:
                    pool = dl.get_pool_total(province_id, YEAR, category) or 0
                    print(f"  → 分数超出表格范围，按末位约 {pool:,} 名处理")
            return score
        print("  ✗ 请输入 100–900 之间的整数")


# ── collect_cli ───────────────────────────────────────────────────────────────

def collect_cli(dl=None) -> StudentProfile:
    """
    Interactive CLI questionnaire.  Returns a StudentProfile.
    Pass a DataLoader instance to enable score→rank preview and validation.
    """
    print("\n" + "=" * 62)
    print("   高考志愿填报助手 — 学生档案填写（共7题）")
    print("=" * 62)

    # Q1: Province
    idx = _ask_single(
        "【第1题】您所在的省份 / 直辖市 / 自治区：",
        PROVINCES,
        display_fn=lambda p: p['name'],
    )
    province = PROVINCES[idx]
    province_id = province['id']
    print(f"  → 已选：{province['name']}  (考试制度: {province['mode']})")

    # Q2: Category — auto-select if only one option
    cats = province['categories']
    if len(cats) == 1:
        category = cats[0]
        print(f"\n【第2题】考试类别：{category}（{province['name']}仅此一类，自动选定）")
    else:
        cat_idx = _ask_single(
            "【第2题】您参加的考试类别：",
            cats,
            display_fn=lambda c: c,
        )
        category = cats[cat_idx]
        print(f"  → 已选：{category}")

    # Q3: Score
    score = _ask_score(province_id, category, dl)

    # Q4: Minimum tier
    tier_idx = _ask_single(
        "【第4题】您可接受的最低学校层次：",
        TIER_OPTIONS,
        display_fn=lambda t: t[0],
    )
    min_tier = TIER_OPTIONS[tier_idx][1]
    print(f"  → 已选：{TIER_OPTIONS[tier_idx][0]}")

    # Q5: Preferred cities (multi-select)
    city_indices = _ask_multi(
        "【第5题】意向就读城市（可多选；0=不限）：",
        CITIES,
        display_fn=lambda c: c,
        cols=5,
    )
    preferred_cities = [CITIES[i] for i in city_indices]
    print(f"  → {'、'.join(preferred_cities) if preferred_cities else '不限城市'}")

    # Q6: Major categories (multi-select)
    major_indices = _ask_multi(
        "【第6题】意向专业方向（可多选；0=不限）：",
        MAJOR_CATEGORIES,
        display_fn=lambda m: m[0],
        cols=2,
    )
    major_keywords: List[str] = []
    selected_major_names = []
    for i in major_indices:
        major_keywords.extend(MAJOR_CATEGORIES[i][1])
        selected_major_names.append(MAJOR_CATEGORIES[i][0])
    # De-duplicate while preserving order
    major_keywords = list(dict.fromkeys(major_keywords))   # deduplicate, preserve order
    print(f"  → {'、'.join(selected_major_names) if selected_major_names else '不限专业'}")

    # Q7: Weight preset (skipped when family profile is provided — weights come from advisor)
    print("\n【第7题】是否填写家庭背景（可获得更个性化的专业建议，类似张雪峰分析）？")
    print("  1. 是，填写家庭背景（推荐）")
    print("  2. 否，手动设置填报权重")
    use_family_raw = input("请选择 (1/2): ").strip()
    use_family = (use_family_raw != '2')

    family: Optional[FamilyProfile] = None
    preset = WEIGHT_PRESETS[3]   # default: 均衡

    if use_family:
        family = _collect_family_profile()
        print(f"\n  已收集家庭背景 — 权重和风险偏好将由顾问自动优化")
    else:
        weight_idx = _ask_single(
            "【第7题】填志愿时您最看重什么？",
            WEIGHT_PRESETS,
            display_fn=lambda w: f"{w['key']}  —  {w['desc']}",
        )
        preset = WEIGHT_PRESETS[weight_idx]
        print(f"  → 已选：{preset['key']}")

    print("\n" + "=" * 62)
    print("  档案填写完成 ✓")
    print("=" * 62)

    return StudentProfile(
        province_id=province_id,
        year=YEAR,
        category=category,
        score=score,
        min_tier=min_tier,
        preferred_cities=preferred_cities,
        major_keywords=major_keywords,
        w_tier=preset['w_tier'],
        w_city=preset['w_city'],
        w_major=preset['w_major'],
        family=family,
    )


# ── Family background questionnaire ──────────────────────────────────────────

_ECONOMIC_TIERS: List[tuple] = [
    ('贫困/农村',     '贫困'),
    ('普通工薪家庭',  '工薪'),
    ('中产（双职工，有房）', '中产'),
    ('富裕（企业主/高收入）', '富裕'),
]

_PARENTAL_BG_OPTIONS: List[tuple] = [
    ('政府/公务员/事业单位', 'government'),
    ('医疗（医生/护士/药剂师）', 'medical'),
    ('教育（教师/高校）', 'education'),
    ('技术/工程/IT', 'technical'),
    ('私营企业主/创业', 'business'),
    ('法律/律师/法官', 'legal'),
    ('金融/银行/会计', 'finance'),
    ('军警', 'military'),
]

_CAREER_OPTIONS: List[tuple] = [
    ('就业稳定（铁饭碗优先）', '就业稳定'),
    ('收入最大化（商业/技术路径）', '收入最大化'),
    ('个人发展（晋升空间）', '个人发展'),
    ('个人兴趣（热爱优先）', '个人兴趣'),
    ('社会地位（声望/面子）', '社会地位'),
    ('还不确定', '未确定'),
]

_RISK_OPTIONS: List[tuple] = [
    ('保守 — 多填保底，确保有学上', '保守'),
    ('稳健 — 均衡配置（推荐）', '稳健'),
    ('激进 — 多冲好学校，有风险也无所谓', '激进'),
]

_POSTGRAD_OPTIONS: List[tuple] = [
    ('计划考研（硕士）', '考研'),
    ('计划直博/硕博连读（约11年）', '考博'),
    ('本科毕业直接工作', '否'),
    ('视情况而定', '视情况'),
]

_OVERSEAS_OPTIONS: List[tuple] = [
    ('计划本科就出国（高考是备选）', '本科出国'),
    ('本科读国内，研究生考虑出国', '研究生出国'),
    ('暂无出国打算', '无计划'),
]


def _collect_family_profile() -> FamilyProfile:
    print("\n" + "─" * 62)
    print("  家庭背景问卷（共6题，影响专业推荐）")
    print("─" * 62)

    # Q-F1: Economic tier
    idx = _ask_single("【背景1】家庭经济条件：", _ECONOMIC_TIERS,
                      display_fn=lambda t: t[0])
    economic_tier = _ECONOMIC_TIERS[idx][1]

    # Q-F2: Parental background (multi-select)
    bg_indices = _ask_multi("【背景2】父母职业背景（多选；0=其他/不清楚）：",
                            _PARENTAL_BG_OPTIONS,
                            display_fn=lambda t: t[0], cols=2)
    parental_background = [_PARENTAL_BG_OPTIONS[i][1] for i in bg_indices]

    # Q-F3: Career priority
    idx = _ask_single("【背景3】毕业后最重要的目标是什么？", _CAREER_OPTIONS,
                      display_fn=lambda t: t[0])
    career_priority = _CAREER_OPTIONS[idx][1]

    # Q-F4: Risk tolerance
    idx = _ask_single("【背景4】您的志愿风险偏好：", _RISK_OPTIONS,
                      display_fn=lambda t: t[0])
    risk_tolerance = _RISK_OPTIONS[idx][1]

    # Q-F5: Family support years
    print("\n【背景5】家庭经济大约可以支撑您读书多少年（不需要您赚钱的年数）？")
    while True:
        raw = input("请输入年数（如 6）: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= 20:
            support_years = int(raw)
            break
        print("  ✗ 请输入 1–20 之间的整数")

    # Q-F6: First-gen + postgrad
    print("\n【背景6】请回答两个小问题：")
    print("  (a) 您是家里第一个上大学的人吗？(y/n): ", end='')
    first_gen = input().strip().lower() in ('y', 'yes', '是', '对')

    idx = _ask_single("  (b) 是否计划读研/考研？", _POSTGRAD_OPTIONS,
                      display_fn=lambda t: t[0])
    postgrad_willing = _POSTGRAD_OPTIONS[idx][1]

    idx = _ask_single("  (c) 出国留学打算？", _OVERSEAS_OPTIONS,
                      display_fn=lambda t: t[0])
    overseas_plan = _OVERSEAS_OPTIONS[idx][1]

    print("─" * 62)

    return FamilyProfile(
        economic_tier=economic_tier,
        parental_background=parental_background,
        career_priority=career_priority,
        risk_tolerance=risk_tolerance,
        family_support_years=support_years,
        first_gen_college=first_gen,
        postgrad_willing=postgrad_willing,
        overseas_plan=overseas_plan,
    )
