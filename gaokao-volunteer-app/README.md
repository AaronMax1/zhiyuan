# 高考志愿助手

当前版本以河北考生为核心：主数据源是 `data-pipeline/output/hebei_lnwc_loggedin.db`，一分一段使用 `data-pipeline/output/hebei_score_segments.db`。`gaokao-advisor` 的确定性推荐引擎仍保留为可选高级引擎。

## 目录

```text
app.py                         API 服务
engine/                        从 gaokao-advisor 复用的可选高级推荐引擎
services/recommendation_service.py 推荐入口
services/xuefeng_data.py       河北历年录取库适配器
services/orchestrator.py       gaokao-advisor 编排层
services/school_life.py        学校生活质量查询
static/                 前端页面
../data-pipeline/output/hebei_lnwc_loggedin.db 河北考试院历年录取库，默认主数据源
../data-pipeline/output/hebei_score_segments.db 河北一分一段，等位分/位次换算
data/gaokao.db          gaokao-advisor 高级引擎数据库，可选
data/school_life_quality.json 学校生活质量摘要缓存
docs/ARCHITECTURE.md    整体架构
docs/SIX_STEP_AGENT_ARCHITECTURE.md 六步志愿填报 Agent 架构
```

## 启动

```bash
python3 app.py
```

打开：

```text
http://localhost:8000
```

只要存在 `../data-pipeline/output/hebei_lnwc_loggedin.db`，`/api/recommend` 就会使用河北考试院历年录取库生成历史区间推荐。`/api/rank` 使用 `hebei_score_segments.db`，不再依赖 `data/gaokao.db`。gaokao-advisor 高级概率模型仍需要额外提供 `data/gaokao.db`。

## 可选 LLM 顾问层

LLM 只负责解释、追问和总结，不负责生成学校名单。学校、分数、位次、冲稳保仍由本地数据库和规则服务生成。

默认不在服务端保存 key。不配置时会自动使用规则总结。

第 3-6 步支持手动点击“调用 AI 分析”，并且每一步都有独立的“继续和 AI 沟通”对话框。AI 对话会带入当前步骤、候选池、冲稳保、排序、章程核验等上下文，并使用河北考生志愿顾问 skill：就业结果、城市产业、学校层次、专业壁垒、家庭资源、预算、调剂风险和章程核验都会作为分析约束。

前端页面左侧有 LLM 配置区：

- Base URL
- Model
- Key
- Timeout

点击“保存到浏览器”后，配置只保存在当前浏览器 `localStorage`。生成方案时，前端把配置随本次请求临时发送给后端，后端不写入代码、数据库或配置文件。

健康检查 `/api/health` 只返回服务端默认 LLM 状态；页面配置以浏览器本地为准。

## 当前状态

- 已接入 `hebei_lnwc_loggedin.db` 作为主数据源，覆盖全国院校在河北 2023-2025 本科/专科、物理/历史录取分数和位次。
- 已接入 `hebei_score_segments.db`，用于河北一分一段和等位分换算。
- 已接入统一 `RecommendationService`。
- 已保留 gaokao-advisor engine 作为可选高级引擎。
- 已提供 `/api/health`、`/api/rank`、`/api/recommend`、`/api/school-life`。
- 已提供 `/api/recommend/plan` 六步志愿方案和 `/api/charter/checks` 章程核验任务查询。
- 已提供轻量前端，并显示当前主数据源、可选高级引擎、数据质量提示。
- 每条推荐会标注来源、年份、分数、位次和可信度。
- 每条推荐会附加 A-E 证据等级；章程核验项会持久化到 `data-pipeline/output/charter_checks.db`。
- 学校生活质量先使用本地 JSON 缓存，后续再补同步脚本和别名表。
- 后续主流程按“查位次、算等位分、筛院校、分冲稳保、排志愿、查章程”六步 Agent 架构演进。

## 数据质量边界

当前主流程只服务河北考生。推荐默认查询 `hebei_lnwc_loggedin.db`，每条结果保留年份、分数、位次、院校/专业代码和来源标识；同时读取 `hebei_2026_plan.db` 补充 2026 招生计划数、学费、学制和再选科目要求。由于历年录取代码和当年招生计划代码可能换号，服务会先按代码精确匹配，失败后按同批次/科类下的院校名 + 专业代码/专业名兜底匹配。最终填报前仍必须核对学校官方招生章程和当年招生计划。

当前已加入填报硬条件过滤：

- 再选科目与 2026 招生计划要求不匹配时剔除。
- 明确“不接受民办/只要公办”时剔除民办。
- 明确“不接受/不考虑中外合作”时剔除中外合作、合作办学等高风险项。
- 填写“学费8000以内”等预算时剔除超预算专业。
- 第 4 步会检查保底厚度、保底学校分散度、官方计划匹配情况和小计划风险。
