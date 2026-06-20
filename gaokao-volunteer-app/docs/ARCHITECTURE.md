# 高考志愿助手架构说明

## 目标

本项目采用“统一推荐服务 + 主数据源 + 可选高级引擎 + 学校生活质量信息”的架构。

新的产品主线见 `SIX_STEP_AGENT_ARCHITECTURE.md`：系统不再只是查库返回学校列表，而是按“查位次、算等位分、筛院校、分冲稳保、排志愿、查章程”的六步流程组织 Agent、数据库和联网核验。

核心原则：

- 录取推荐由可计算引擎完成，LLM 不直接编造学校清单。
- LLM 负责追问、解释、整理和风险提示。
- 每条结论尽量标注来源：录取数据库、学校生活质量网站、联网搜索、模型推理。
- 数据缺失必须显式提示，不用话术掩盖。
- `unified_admission.db` 是当前主数据源，官方数据优先，第三方聚合和开源快照作为补充。

## 模块分层

```text
用户
  ↓
Web UI / Chat UI
  ↓
API 层 app.py
  ↓
RecommendationService 统一推荐服务
  ↓
主数据源 unified_admission.db
  ↓
可选高级引擎 gaokao-advisor RecommendationEngine
  ↓
SQLite 数据库 / 学校生活质量信息 / 联网搜索
```

## 已复用模块

从 `gaokao-advisor` 复用：

- `engine/data_loader.py`：SQLite 数据加载、分数转位次、学校录取统计。
- `engine/probability.py`：录取概率模型和冲稳保标签。
- `engine/recommend.py`：候选收集、过滤、效用评分、冲稳保选槽。
- `engine/utility.py`：学校层次、城市、专业偏好的效用评分。
- `engine/profile.py`：用户画像到推荐查询的转换。
- `engine/advisor.py`：家庭背景和职业目标的专业建议规则。
- `engine/monte_carlo.py`：平行志愿模拟。
- `engine/drift.py`：2025 首年换制省份位次修正。

从 `xuefeng-agent` 借鉴：

- 聊天式交互和多轮追问思路。
- OpenAI 兼容模型配置思路。
- Tavily/联网搜索作为补充信息，而不是主推荐依据。
- 本地优先、隐私优先的产品方向。
- `admission_clean.db` 作为备用兼容数据源。

## 数据目录

主数据源：

```text
../data-pipeline/output/unified_admission.db
data/unified_admission.db
```

该库用于历史分数/位次区间推荐，应用默认查询 `admission_best_records`，并通过 `school_profiles` 补充院校省市、类型、层次、办学性质。每条记录保留来源类型、可信等级和质量标记。

备用兼容源：

```text
data/admission_clean.db
data/admission_clean.db.gz
```

可选高级引擎数据库位置：

```text
data/gaokao.db
```

gaokao-advisor 高级引擎需要至少包含：

- `score_segments`
- `admission_scores`
- `major_scores`
- `enrollment_plans`
- 学校元数据相关表

学校生活质量数据：

```text
data/school_life_quality.json
```

当前第一版只做轻量结构，后续可以从 `cn.colleges.chat` 对应内容定期同步并结构化。

## API 设计

### `GET /api/health`

返回服务状态、数据库是否存在、推荐引擎是否可用。
同时返回：

- `primary_data_source`：主数据源状态和覆盖概况
- `optional_engines.gaokao_advisor`：高级引擎状态
- `quality_warnings`：数据质量提示

### `GET /api/rank`

参数：

- `province_id`
- `category`
- `score`
- `year`

用途：分数转省内位次。

### `POST /api/recommend`

输入用户画像，返回：

- `student_rank`
- 冲稳保数量
- `coverage`
- `advisor_note`
- `advisor_top_majors`
- `simulation`
- `recommendations`

每条 recommendation 附带：

- 学校、城市、层次、专业
- 录取概率
- 冲稳保标签
- 效用分
- 专业路径提示
- 学校生活质量摘要
- 数据来源
- 来源年份
- 历史分数
- 历史位次
- 可信度

### `GET /api/school-life`

参数：

- `school_name`

用途：查询学校生活质量摘要。

## Agent 编排策略

第一版 `RecommendationService` 是 API 的唯一推荐入口：

1. 默认调用统一录取库主数据源。
2. 当请求 `engine_mode=advisor` 且 `data/gaokao.db` 可用时，调用 gaokao-advisor 高级引擎。
3. 统一两种结果的字段结构。
4. 补齐学校生活质量信息。
5. 附加数据源状态、证据字段和数据质量提示。

`AdvisorOrchestrator` 只负责 gaokao-advisor 高级引擎编排：

1. 校验输入。
2. 调用 `profile_to_query()` 转换画像。
3. 调用推荐引擎生成结构化结果。
4. 调用 `simulate()` 生成平行志愿风险分布。
5. 为推荐结果补充学校生活质量信息。
6. 生成一个简短的可读摘要。

后续接入 LLM 时，LLM 只应访问这些结构化工具：

- `rank_tool`
- `recommend_tool`
- `school_life_tool`
- `web_search_tool`

LLM 输出必须引用工具返回结果，不允许凭空补学校信息。

## 学校生活质量接入边界

`cn.colleges.chat` 是学校信息网站，不是推荐引擎。它适合补充：

- 宿舍条件
- 空调/独卫/洗浴
- 门禁/查寝/断电断网
- 外卖/快递/食堂
- 校区交通
- 校园网

许可注意：

- 其背后内容采用 CC BY-NC-SA 4.0。
- 非商业内部使用可做摘要缓存，但需要署名和来源链接。
- 商业项目建议只做外链、获取授权或自建问卷数据。

## 下一步

1. 继续扩充或替换主数据源，并在 `/api/health` 展示覆盖范围。
2. 补 `school_life_quality.json` 同步脚本和别名匹配表。
3. 接入 LLM 槽位提取和追问。
4. 增加前端推荐表格、风险面板和导出功能。
