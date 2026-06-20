# 高考志愿助手

当前版本以 `data-pipeline/output/unified_admission.db` 作为主数据源，保留 `admission_clean.db` 作为备用兼容库，并保留 `gaokao-advisor` 的确定性推荐引擎作为可选高级引擎。

## 目录

```text
app.py                         API 服务
engine/                        从 gaokao-advisor 复用的可选高级推荐引擎
services/recommendation_service.py 统一推荐入口
services/xuefeng_data.py       统一录取库/旧 admission_clean.db 适配器
services/orchestrator.py       gaokao-advisor 编排层
services/school_life.py        学校生活质量查询
static/                 前端页面
../data-pipeline/output/unified_admission.db 统一录取库，默认主数据源
data/admission_clean.db xuefeng-agent 历史录取库，备用兼容源
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

只要存在 `../data-pipeline/output/unified_admission.db`，`/api/recommend` 就会使用统一库生成历史区间推荐；没有统一库时会回退到 `data/admission_clean.db` 或 `data/admission_clean.db.gz`。`/api/rank` 和 gaokao-advisor 高级概率模型需要额外提供 `data/gaokao.db`。

## 可选 LLM 顾问层

LLM 只负责解释、追问和总结，不负责生成学校名单。学校、分数、位次、冲稳保仍由本地数据库和规则服务生成。

默认不在服务端保存 key。不配置时会自动使用规则总结。

前端页面左侧有 LLM 配置区：

- Base URL
- Model
- Key
- Timeout

点击“保存到浏览器”后，配置只保存在当前浏览器 `localStorage`。生成方案时，前端把配置随本次请求临时发送给后端，后端不写入代码、数据库或配置文件。

健康检查 `/api/health` 只返回服务端默认 LLM 状态；页面配置以浏览器本地为准。

## 当前状态

- 已接入 `unified_admission.db` 作为主数据源，官方数据优先，第三方聚合和开源快照作为补充。
- 保留 xuefeng-agent `admission_clean.db` 作为备用兼容源。
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

`unified_admission.db` 已合并官方导入、掌上高考 fallback 和开源快照清洗数据。推荐默认查询 `admission_best_records`，每条结果仍保留来源、可信等级和质量标记；第三方/低信任数据不会伪装成官方数据。
