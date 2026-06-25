# 高考志愿助手

这是一个本地运行的高考志愿填报辅助项目，核心流程按六步执行：

1. 查位次，准确定位
2. 换算等位分
3. 筛选院校范围
4. 确定冲稳保策略
5. 排序志愿
6. 检查招生章程

项目由两部分组成：

- `gaokao-volunteer-app/`：Web 页面和 API 服务。
- `data-pipeline/`：数据下载、清洗、合并脚本。

## 快速启动

```bash
chmod +x start.sh restore_data.sh
./start.sh
```

打开：

```text
http://localhost:8000
```

`start.sh` 会检查河北专项运行数据库是否存在；如果缺失，会自动从 `data-bundles/` 的压缩包恢复。

## 数据包

运行数据库压缩包提交在：

```text
data-bundles/hebei-runtime-data.tar.zst
```

恢复命令：

```bash
./restore_data.sh
```

恢复后会生成：

```text
data-pipeline/output/hebei_lnwc_loggedin.db
data-pipeline/output/hebei_score_segments.db
data-pipeline/output/hebei_2026_plan.db
data-pipeline/output/batch_control_lines.db
```

说明：

- 当前项目按“河北考生报全国院校”收敛，运行时只依赖河北一分一段和河北考试院历年录取库。
- 原始下载文件、OCR 切片、临时文件和本地 vendor 依赖不会提交。

## AI 配置

页面左侧提供 `AI 配置`：

- Base URL
- Model
- Key
- Timeout

这些配置只保存在当前浏览器 `localStorage`，点击第 3-6 步的“调用 AI 分析”按钮时临时发送给后端。后端不保存 Key、Base URL 或 Model。

AI 参与方式：

- 第 3 步：AI 只能从数据库召回的候选池中筛选，不能新增学校或专业。
- 第 4 步：分析冲稳保策略。
- 第 5 步：分析志愿排序。
- 第 6 步：生成招生章程核验重点。

AI 服务不可用时，会显示规则兜底分析，不影响数据库推荐流程。

## 依赖

运行 Web 服务只需要 Python 3 和标准库；如果要重建数据，需要安装 `data-pipeline/README.md` 中列出的解析依赖。

恢复数据需要 `zstd`：

```bash
# macOS
brew install zstd

# Ubuntu/Debian
sudo apt-get install zstd
```

## 主要接口

- `GET /api/health`
- `POST /api/recommend/plan`
- `POST /api/llm/step`
- `GET /api/charter/checks`

## 数据边界

当前主流程只服务河北考生：

- `hebei_score_segments.db`：河北一分一段，用于位次定位和等位分换算。
- `hebei_lnwc_loggedin.db`：河北考试院历年录取查询，用于全国院校在河北的本科/专科、物理/历史候选筛选。
- `hebei_2026_plan.db`：河北考试院 2026 招生计划库，覆盖本科批/专科批、物理/历史，提供计划数、学制、学费、再选科目要求等字段；原始抓取库单独保留在 `hebei_zsjh_loggedin.db`。

其他省份录取库、全量一分一段库、第三方聚合库和开源快照库暂不参与主推荐流程。最终填报前必须核对学校官方招生章程和 2026 招生计划。
