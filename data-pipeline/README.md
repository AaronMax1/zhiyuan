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
