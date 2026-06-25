# 高考数据清洗流水线

这个目录用于窗口 B：把多个开源/公开数据源清洗成统一的 `gaokao_clean.db`。

第一版接入两个本地来源：

- `source-snapshots/xuefeng-agent/admission_clean.db.gz`
- `source-snapshots/qiming-zhiyuan/admission_clean.db.gz`

核心原则：

- 不把来源库的行数当作可信行数。
- 每条记录保留来源、原始 id、源文件和质量标记。
- 明显不是录取数据的来源直接排除。
- 可疑年份、可疑学校名、可疑专业名、可疑位次不删除，但打 `quality_flags`。
- 推荐系统只能使用 `is_usable = 1` 的记录；位次推荐还必须满足 `rank_reliable = 1`。

## 运行

```bash
python3 data-pipeline/build_clean_db.py
```

默认输出：

```text
data-pipeline/output/gaokao_clean.db
data-pipeline/output/data_quality_report.md
data-pipeline/output/data_quality_report.json
```

## 构建统一推荐库

`build_unified_admission_db.py` 会把三个已清洗来源合并到一个独立库，不覆盖原始库：

- `official_admission.db`: 官方导入数据，最高优先级。
- `fallback_admission.db`: 掌上高考 API 补充数据，中等优先级，并标记为第三方聚合源。
- `gaokao_clean.db`: xuefeng/qiming 等开源快照清洗结果，低信任补充源，默认只导入 `is_usable = 1`。

```bash
python3 data-pipeline/build_unified_admission_db.py
```

默认输出：

```text
data-pipeline/output/unified_admission.db
data-pipeline/output/unified_admission_report.md
data-pipeline/output/unified_admission_report.json
```

统一库核心表：

- `normalized_admission_records`: 保留所有来源记录、来源优先级、清洗字段、质量标记和去重键。
- `admission_best_records`: 对严格去重键保留一条优先记录，排序为官方 > 第三方聚合 > 开源快照。
- `school_profiles`: 院校画像，优先使用聚合源补充学校省市、类型、层次、办学性质。
- `data_quality_summary`: 来源、层次、质量标记等统计。

查询推荐时优先使用 `admission_best_records`；需要审计来源、解释数据可信度或排查异常时查 `normalized_admission_records`。

## 河北登录查询数据

河北考试院信息查询系统的“历年录取情况”需要考生登录态。脚本只从环境变量读取浏览器 cookie，不把 cookie、账号或密码写入代码、数据库或文档。

从浏览器开发者工具复制请求里的 `Cookie`，再运行：

```bash
HEBEEA_COOKIE='从浏览器复制的 Cookie' \
python3 data-pipeline/download_hebei_lnwc_loggedin.py \
  --form-id '从表单数据复制的 id' \
  --sleep 0.8 \
  --progress-every 100
```

默认下载：

- 本科批：物理科目组合、历史科目组合
- 专科批：物理科目组合、历史科目组合

默认输出：

```text
data-pipeline/raw/hebei_lnwc_loggedin/
data-pipeline/output/hebei_lnwc_loggedin.csv
data-pipeline/output/hebei_lnwc_loggedin.db
data-pipeline/output/hebei_lnwc_loggedin_report.md
```

这部分数据单独存放，不默认并入 `unified_admission.db`。后续应用使用时应作为“河北考试院登录查询数据源”独立读取，并在界面或日志中保留独立来源标识。

如果登录态过期，脚本会停止并提示重新复制 `HEBEEA_COOKIE`；已缓存的分页 HTML 会保留，重新运行会复用本地缓存继续处理。

## 河北 2026 招生计划数据

河北考试院信息查询系统的“招生计划”同样需要考生登录态。脚本只抓普通本科批和普通专科批，明确排除提前批；数据单独保存，不并入历年录取库。

从浏览器开发者工具复制 `zsjhIframe` 请求里的 `Cookie`，再运行：

```bash
HEBEEA_COOKIE='从浏览器复制的 Cookie' \
python3 data-pipeline/download_hebei_zsjh_loggedin.py \
  --sleep 0.25 \
  --progress-every 50
```

默认下载：

- 本科批：历史科目组合、物理科目组合
- 专科批：历史科目组合、物理科目组合

默认输出：

```text
data-pipeline/raw/hebei_zsjh_loggedin/
data-pipeline/output/hebei_zsjh_loggedin.csv
data-pipeline/output/hebei_zsjh_loggedin.db
data-pipeline/output/hebei_2026_plan.db
```

`hebei_zsjh_loggedin.db` 保留考试院原始招生计划字段；`hebei_2026_plan.db` 是应用运行库，字段对齐服务里的计划信息展示，`is_mock=0` 表示来自考试院官方查询。当前接口科类编码为历史 `0`、物理 `B`。

## 河北专项运行数据

当前应用主流程按“河北考生报全国院校”收敛，运行时只需要两份核心数据库：

```text
data-pipeline/output/hebei_score_segments.db
data-pipeline/output/hebei_lnwc_loggedin.db
data-pipeline/output/hebei_2026_plan.db
```

- `hebei_score_segments.db`：河北一分一段，用于位次定位和 2025/2024/2023 等位分换算。
- `hebei_lnwc_loggedin.db`：河北考试院历年录取查询，用于本科/专科、物理/历史的候选院校和专业筛选。
- `hebei_2026_plan.db`：河北考试院 2026 招生计划库，用于展示计划数、学制、学费、再选科目要求等字段；原始抓取结果保留在 `hebei_zsjh_loggedin.db`。

其他省份录取库、全量一分一段库、第三方聚合库和开源快照库暂不参与河北专项推荐主流程。

## 下载官方附件

官方源放在 `source_registry.json`，下载脚本会把附件保存到 `raw/official/` 并生成 manifest。

```bash
python3 data-pipeline/download_official_sources.py --only-province 山东 --only-year 2025 --no-discover
```

说明：

- `attachment_urls` 已知时建议加 `--no-discover`，避免官网页面访问慢导致阻塞。
- Python TLS 访问部分考试院附件会失败，脚本会自动 fallback 到 `curl`。
- 下载产物不会进 git，见 `.gitignore`。

下载后生成本地清单和摘要：

```bash
python3 data-pipeline/inventory_official_sources.py
python3 data-pipeline/summarize_official_sources.py
```

ZIP 附件先解压到 `raw/official_extracted/`：

```bash
python3 data-pipeline/extract_official_archives.py
```

## 导入官方数据

官方导入脚本会读取 `raw/official/local_inventory.json` 和已解压文件 manifest，输出独立库：

```bash
python3 data-pipeline/import_official_sources.py
```

默认输出：

```text
data-pipeline/output/official_admission.db
data-pipeline/output/official_import_report.md
data-pipeline/output/official_import_report.json
```

当前支持：

- 标准 OOXML `.xlsx`
- 静态 HTML 表格
- 旧版 Excel `.xls`
- 部分机器文字 PDF：
  - 广东普通类投档 PDF
  - 江苏普通类投档 PDF
  - 上海普通批/专科投档 PDF

`.xls` 和 PDF 文本解析需要本地依赖，建议装到项目内的 `.vendor`，避免污染系统 Python：

```bash
python3 -m pip install --target data-pipeline/.vendor -r data-pipeline/requirements-official.txt
```

暂未覆盖的 PDF、图片、动态网页和非标准 `.xlsx` 会进入 `parse_queue`，供后续 PDF 表格解析、OCR 或人工复核处理。广东 PDF 目前只导入正文包含“普通类”的文件，艺术类/体育类投档表先保留在队列中。

PDF 文本会缓存到 `raw/pdf_text/`。第一次导入会较慢，后续重建会复用缓存。

新增省份/年份时，只需要往 `source_registry.json` 添加：

```json
{
  "id": "province-year-batch",
  "province": "山东",
  "year": 2025,
  "category": "综合",
  "batch": "普通类常规批第1次志愿",
  "source_type": "official_admission",
  "publisher": "山东省教育招生考试院",
  "page_url": "https://example.edu.cn/news",
  "attachment_urls": ["https://example.edu.cn/file.xls"]
}
```

## 目标 schema

主表：`admission_records`

关键字段：

- `source_dataset`: `xuefeng-agent` 或 `qiming-zhiyuan`
- `source_id`: 来源库原始 id
- `province`, `year`, `category`, `batch`
- `school_name`, `major_name`
- `score`, `rank`, `quota`
- `source_file`
- `source_type`: `official_admission`, `major_score`, `plan`, `ranking`, `employment`, `unknown`
- `trust_level`: `official`, `third_party`, `mixed`, `bad`
- `is_usable`: 是否可用于推荐候选
- `score_reliable`: 分数是否可用于推荐
- `rank_reliable`: 位次是否可用于推荐
- `quality_flags`: JSON 数组，记录清洗判断
