# 六步志愿填报 Agent 架构

## 核心变化

当前项目不能只做“查数据库 -> 返回学校列表”。新的定位是：

用户自己完成第一步“查分数和位次”，系统从第二步开始接管，按志愿填报六步流程做一个可解释、可核验、可追问的 Agent。

整体目标：

- 用数据库做硬证据，不让 LLM 编分数线。
- 用 LLM 做画像采集、解释、排序理由和风险提示。
- 用联网搜索补最新招生章程、选科要求、单科限制、体检限制和学校公开信息。
- 用 xuefeng-agent 的咨询方法论做“问什么、怎么问、怎么解释”，但不直接复刻特定人物口吻或不可控表达。
- 用 gaokao-advisor 的概率模型、冲稳保策略、效用评分做可计算推荐。

## 六步流程映射

### 1. 查位次，准确定位

责任边界：

- 用户输入：省份、分数、位次、选科大类、层次。
- 系统提示：位次优先；如果用户只有分数，系统可以先按分数粗筛。
- 后续能力：接入各省一分一段表后，自动把分数换算成位次。

当前实现：

- 表单已有省份、分数、位次、层次、选科大类。
- 选科大类已按省份映射为物理/历史、理/文、综合。

待建设：

- `rank_service`: 分数转位次。
- `score_segment_records`: 各省各年一分一段表。
- `equivalent_score_service`: 位次转往年等位分。

### 2. 换算等位分

核心逻辑：

- 用今年位次，去往年一分一段表找同位次对应分数。
- 生成 2025、2024、2023 等位分。
- 后续筛学校时优先用等位分和位次，不直接拿今年裸分套往年分数线。

需要新增表：

- `score_segments`
  - `province`
  - `year`
  - `category`
  - `score`
  - `same_score_count`
  - `cumulative_rank`
  - `source_url`
  - `source_type`
  - `quality_flags`

需要新增服务：

- `EquivalentScoreService`
  - `score_to_rank(province, year, category, score)`
  - `rank_to_score(province, year, category, rank)`
  - `build_equivalent_scores(current_year, province, category, score, rank)`

### 3. 筛选院校范围

核心逻辑：

- 以等位分为基准，默认向上 20 分、向下 30 分。
- 同时结合位次区间。
- 只筛选近 3 年有招生记录的学校/专业。
- 按用户偏好过滤城市、专业、层次、办学性质、费用接受度。

当前可复用：

- `unified_admission.db`
  - `admission_best_records`
  - `normalized_admission_records`
  - `school_profiles`
- `RecommendationService`
- `xuefeng_data.py` 里的统一库查询适配。

需要新增：

- `CandidateFilterService`
  - 分数区间筛选
  - 位次区间筛选
  - 近三年稳定招生筛选
  - 城市/专业/学费/民办/中外合作过滤

### 4. 确定冲稳保策略

核心逻辑：

- 冲：录取位次略高于用户水平。
- 稳：录取位次与用户基本匹配。
- 保：录取位次明显低于用户水平。
- 策略要按省份志愿模式调整。

当前可复用：

- `gaokao-advisor/engine/probability.py`
- `gaokao-advisor/engine/recommend.py`
- `gaokao-advisor/engine/monte_carlo.py`
- 当前 `RecommendationService` 的冲稳保输出结构。

需要调整：

- 把现在简单的 rank/score 区间升级为策略配置：
  - 新高考一校一专业
  - 院校专业组
  - 老高考文理批次
  - 专科批
- 输出每条推荐为什么是冲/稳/保。

### 5. 排序志愿

核心逻辑：

- 不是按分数高低机械排序。
- 按用户真实偏好排序：学校、专业、城市、就业、深造、费用、家庭资源。
- LLM 参与排序解释，但不能改写数据库事实。

需要新增：

- `PreferenceProfile`
  - `city_preference`
  - `major_interest`
  - `major_dislike`
  - `career_goal`
  - `family_resources`
  - `budget`
  - `accept_private`
  - `accept_sino_foreign`
  - `risk_tolerance`

- `VolunteerRankingService`
  - 计算效用分
  - 生成排序理由
  - 输出可调整的志愿列表

可复用：

- `gaokao-advisor/engine/utility.py`
- `xuefeng-agent/knowledge_base.md` 中家庭资源、专业避坑、学校/专业/城市三角关系的方法论。

### 6. 检查核对

这是最终产品必须补上的关键步骤。

系统不能只告诉用户“能不能上”，还要提醒：

- 招生章程是否有单科成绩限制。
- 专业是否有选科要求。
- 是否有体检限制，比如色盲、色弱、身高、视力。
- 学费是否超预算。
- 专业组内是否存在不可接受专业。
- 是否需要服从调剂。

需要新增：

- `CharterCheckService`
  - 根据学校 + 专业 + 年份搜索招生章程。
  - 抽取选科、单科、体检、外语语种、学费、校区。
  - 输出“必须人工复核”的清单。

- `WebEvidenceService`
  - 搜索学校官网、招生网、省考试院。
  - 给每条信息保存来源链接、来源类型、抓取时间。

输出原则：

- 招生章程类信息必须标注来源。
- 搜不到就说搜不到，不能编。
- 最终结论必须提示“以省考试院投档表和学校招生章程为准”。

## Agent 分层

### 1. Conversation Agent

负责多轮对话和槽位采集。

槽位来自 xuefeng-agent 的方法论，但要结构化：

- 硬分：省份、分数、位次、选科大类、层次。
- 兴趣：想学什么、不想学什么。
- 地域：想去哪、不去哪、距离限制。
- 家庭：父母行业、亲友资源、预算。
- 诉求：就业、考公、深造、稳定、城市、学校层次。
- 限制：体检、单科、外语、民办/中外合作接受度。

规则：

- 硬分 + 诉求必须有。
- 至少填满 4 类槽位才进入正式推荐。
- 用户缺位次时，不能卡死；先用分数粗筛，并提示位次更准。

### 2. Planning Agent

负责把用户目标转换成工具调用计划。

典型计划：

- 查当前位次或等位分。
- 查近三年录取记录。
- 生成候选池。
- 分冲稳保。
- 查学校画像。
- 查招生章程。
- 生成排序和风险提示。

### 3. Tool Layer

LLM 只能调用工具，不能自己编学校名单。

工具清单：

- `rank_tool`: 分数和位次换算。
- `equivalent_score_tool`: 等位分计算。
- `recommend_tool`: 数据库推荐。
- `school_profile_tool`: 院校画像。
- `major_knowledge_tool`: 专业解释和避坑。
- `web_search_tool`: 最新政策、招生章程、学校官网信息。
- `charter_check_tool`: 招生章程核对。
- `compare_tool`: 多个志愿方案对比。

### 4. Evidence Layer

所有结论必须带证据等级。

证据等级：

- A：省考试院官方投档表、学校招生章程。
- B：学校官网招生网、官方本科招生公众号。
- C：权威教育媒体、阳光高考、中国教育在线。
- D：第三方聚合数据。
- E：开源快照或模型推理。

推荐时展示：

- 来源类型。
- 年份。
- 分数。
- 位次。
- 质量标记。
- 是否经过章程核验。

## 数据架构

当前已有：

- `unified_admission.db`
  - `admission_best_records`
  - `normalized_admission_records`
  - `school_profiles`
  - `data_quality_summary`

需要新增：

- `score_segments.db` 或并入统一库
  - 一分一段表。

- `school_charters`
  - 招生章程链接、年份、学校、摘要、限制条件。

- `major_profiles`
  - 专业介绍、就业方向、适合人群、避坑提示。

- `agent_sessions`
  - 用户槽位、推荐过程、工具调用记录。

- `recommendation_plans`
  - 候选池、冲稳保分档、排序、证据快照。

## 可复用资产

### xuefeng-agent

可复用：

- 槽位采集思路。
- 家庭资源判断矩阵。
- 就业倒推法。
- 学校/专业/城市三角关系。
- 具体数据必须先查库、再搜索、再提示用户核对的流程。

不建议直接复用：

- 直接模仿特定人物的娱乐化 persona。
- 过强的绝对化表达。
- 无证据的“稳了”“能上”式结论。

落地方式：

- 把 `knowledge_base.md` 清洗成 `advisor_knowledge_rules.json`。
- 把 `system_prompt.md` 中的方法论提炼成项目自己的 Agent prompt。
- 保留直白、务实、少废话风格，但不绑定真人身份。

### gaokao-advisor

可复用：

- 概率模型。
- 冲稳保标签。
- 效用评分。
- 蒙特卡洛风险模拟。
- 家长解释文档中的风险表达。

落地方式：

- 把它作为高级推荐引擎，而不是主数据源。
- 与 `unified_admission.db` 逐步适配。

## API 设计

### `POST /api/agent/message`

聊天入口。

输入：

- 用户自然语言。
- 当前 session_id。

输出：

- 已识别槽位。
- 缺失槽位。
- 下一步追问。
- 是否可以生成方案。

### `POST /api/recommend/plan`

生成完整志愿方案。

输入：

- 结构化画像。

输出：

- 等位分。
- 候选池摘要。
- 冲稳保列表。
- 排序理由。
- 章程核验清单。
- 风险提示。

### `POST /api/charter/check`

招生章程核验。

输入：

- 学校。
- 专业。
- 省份。
- 年份。

输出：

- 选科要求。
- 单科要求。
- 体检限制。
- 学费。
- 校区。
- 来源链接。
- 可信度。

## 前端改造

当前表单保留，但改成两种模式：

- 快速推荐：用户已知道分数、位次，直接生成冲稳保。
- Agent 填报：聊天式引导，按六步流程走。

页面结构：

- 左侧：用户画像和六步进度。
- 中间：对话与追问。
- 右侧：候选池、冲稳保、证据和核验清单。

六步进度：

- 查位次。
- 算等位分。
- 筛院校。
- 分冲稳保。
- 排志愿。
- 查章程。

## 落地优先级

### P0：把产品主流程改对

- 前端展示六步流程。
- 保留当前推荐接口。
- 增加用户画像槽位。
- 推荐输出改成“候选池 + 冲稳保 + 证据 + 待核验”。

### P1：一分一段表和等位分

- 下载/导入河北等重点省份一分一档表。
- 实现分数转位次、位次转等位分。
- 推荐从裸分逻辑升级到等位分逻辑。

### P2：Agent 对话

- 新增 session。
- 自动抽取槽位。
- 缺什么问什么。
- 工具调用推荐。

### P3：招生章程联网核验

- 学校官网/招生网搜索。
- 抽取限制条件。
- 输出核验清单。

### P4：高级模型

- 适配 gaokao-advisor 到统一库。
- 加概率模型和模拟滑档风险。

## 一句话原则

LLM 负责像顾问一样问清楚、讲明白；数据库负责给硬证据；联网负责查最新规则；最终方案必须能解释、能追溯、能核验。
