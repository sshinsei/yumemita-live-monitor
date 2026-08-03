# X_Schedule_Discovery

精简版 **YouTube 直播发现 + 同接采样** 监控，并可选接入 X 官方账号（默认 `@BDP_yumemita`）的配信日程，作为发现调度的辅助信息源。

本仓库用 **[uv](https://docs.astral.sh/uv/)** 管理虚拟环境与依赖（`.venv` + `pyproject.toml` + `uv.lock`）。

---

## 功能一览

### 监控与采集

- 频道配置驱动（`channels.csv`）
- YouTube 自动发现 live / upcoming（uploads 等）
- **按成员** discovery due；近窗（开播前 5 分钟 + 开播后宽限）可 `near_probe`（默认 30s）
- 批量同接采样；失败不写假 `0`
- 可选 **X 日程**：官方 API 增量拉帖 → 解析 `ScheduleHint` → 辅助发现

### X 日程要点

- X **只做日程提示**；无 `video_id` 时不造假直播记录
- **YouTube 为权威**（状态、开播/下播、同接）
- **发现与采样分离**：X / 近窗只影响 discovery；采样仍走 time_bands
- X推文解析：去 emoji、`明日`→日程日+1、メン限标记可剥除
- `x_schedule_member_only_enabled`：开关控制是否写入 `member_only` 元数据

### 周报

- 定时生成上一完整 ISO 周的个人 HTML + `summary.json`
- 支持手工 `report --week`

---

## 环境要求

- **[uv](https://docs.astral.sh/uv/)**（推荐）
- Python 3.9+（`uv sync` 会选用本机解释器，必要时可下载）
- 网络可访问 **YouTube Data API v3**
- 有效的 **YouTube Data API Key**
- （可选）启用 X 时：X 开发者 **Bearer Token**

依赖以 `pyproject.toml` / `uv.lock` 为准：

| 依赖         | 用途                                    |
| ------------ | --------------------------------------- |
| `requests` | YouTube / X HTTP                        |
| `tzdata`   | Windows 时区（`Asia/Tokyo` 等）       |
| `pytest`   | 开发测试（`uv sync` 默认安装 dev 组） |

`requirements.txt` 由 `uv export` 生成，**仅作兼容**；请以 uv 为主。

---

## 用 uv 管理环境

### 安装 uv（仅首次）

Windows PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

必要时把 uv 加入当前 PATH：

```powershell
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
uv --version
```

### 创建 / 同步环境

```powershell
cd E:\yumemita_stream_data\X_Schedule_Discovery

# 按 pyproject.toml + uv.lock 创建 .venv 并安装依赖（含 pytest）
uv sync
```

| 命令                 | 说明                                              |
| -------------------- | ------------------------------------------------- |
| `uv sync`          | 创建/更新`.venv`，安装锁定依赖（含 dev）        |
| `uv sync --no-dev` | 仅生产依赖                                        |
| `uv add <包>`      | 添加依赖并更新锁文件                              |
| `uv remove <包>`   | 移除依赖                                          |
| `uv lock`          | 仅刷新锁文件                                      |
| `uv run <命令>`    | 在项目环境中执行（**推荐，无需 activate**） |
| `uv python list`   | 查看可用 Python                                   |

虚拟环境目录：**`.venv/`**（已在 `.gitignore`，勿提交）。

可选手动激活：

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

推荐始终：

```powershell
uv run python main.py
uv run pytest -q
```

### 不用 uv 时（不推荐）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 快速开始

```powershell
cd E:\yumemita_stream_data\X_Schedule_Discovery
uv sync

copy config.example.json config.json
# 编辑 config.json：填写 youtube_api_key
```

### 常驻监控

```powershell
uv run python main.py run
# 或
uv run python main.py
```

### 离线解析 X 日程（不调 X API）

```powershell
uv run python main.py refresh-x --text-file fixtures/schedule_posts/standard_multi.txt -c config.example.json
```

### 周报

```powershell
uv run python main.py report --week 2026-W31 -c config.json
```

输出默认：`data/weekly_reports/<YYYY-Www>/`（各成员 HTML + `summary.json`）。

| 字段                   | 默认                    | 说明            |
| ---------------------- | ----------------------- | --------------- |
| `report_timezone`    | `Asia/Tokyo`          | 周界时区        |
| `weekly_report_day`  | `1`（周一）           | 触发星期（ISO） |
| `weekly_report_time` | `09:00`               | 触发时刻        |
| `weekly_reports_dir` | `data/weekly_reports` | 输出根目录      |

---

## 启用 X 日程

### 需要什么凭据？

需要 **Bearer Token**。

| 项       | 说明                                                     |
| -------- | -------------------------------------------------------- |
| 用途     | `Authorization: Bearer …` 调用 X API v2 Recent Search |
| 配置键   | `x_bearer_token_env`（默认名 `X_BEARER_TOKEN`）      |
| 存放位置 | **环境变量**，禁止写入 `config.json` / 提交 Git  |

### 步骤

1. 在 [X Developer Portal](https://developer.x.com/) 申请 API 访问，取得 **Bearer Token**。
2. 设置环境变量（Windows 示例）：

```powershell
# 当前终端
$env:X_BEARER_TOKEN = "你的令牌"

# 或持久写入用户环境（新开终端生效）
setx X_BEARER_TOKEN "你的令牌"
```

3. `config.json`：

```json
"x_schedule_enabled": true,
"x_bearer_token_env": "X_BEARER_TOKEN",
"x_schedule_username": "BDP_yumemita",
"x_schedule_refresh_interval_seconds": 7200
```

4. 启动：`uv run python main.py run`
   - 启动时会立即刷新一次 X
   - 之后按 `x_schedule_refresh_interval_seconds` 周期拉取X 推文

建议先 **只读观察** 若干天，再依赖 near_probe 提频。

### 代码如何拉帖（候选列表）

```text
官方 X API v2
  GET /2/tweets/search/recent
  query: from:{username} -is:retweet -is:reply
  since_id: runtime_state.last_x_since_id（增量）
  → 得到候选帖列表
  → 解析日程 → schedule_hints.json
  → 更新 last_x_since_id
```

- 第三方接口未接入生产路径
- 未过滤「仅日程」——账号近期原创帖都会进候选，再由解析器判断是否日程
- Recent Search 通常约 **近 7 天** 窗口；`max_results` 默认约 20
- `x_schedule_enabled=false` 或无 Token：**不请求 X**，仅 YouTube 路径

---

## 主要配置说明

### X / 近窗

| 字段                                                 | 默认                         | 说明                                                                  |
| ---------------------------------------------------- | ---------------------------- | --------------------------------------------------------------------- |
| `x_schedule_enabled`                               | `false`                    | 是否启用 X                                                            |
| `x_schedule_username`                              | `BDP_yumemita`             | 拉取账号                                                              |
| `x_bearer_token_env`                               | `X_BEARER_TOKEN`           | Bearer 所在环境变量名                                                 |
| `x_schedule_refresh_interval_seconds`              | `3600`                     | X 刷新间隔（秒）                                                      |
| `x_schedule_hints_file`                            | `data/schedule_hints.json` | 日程提示存储                                                          |
| `x_schedule_member_only_enabled`                   | `false`                    | `true`：メン限行写 `member_only`；`false`：仍剥标记但当普通日程 |
| `discovery_near_pre_start_window_seconds`          | `300`                      | 开播前近窗                                                            |
| `discovery_near_post_start_grace_seconds`          | `1800`                     | 开播后宽限                                                            |
| `discovery_near_probe_interval_seconds`            | `30`                       | 近窗发现间隔（硬下限 ≥30）                                           |
| `discovery_known_schedule_interval_seconds`        | `10800`                    | 有 YT/X 预约且未进近窗时的 discovery 间隔（3h）                       |
| `discovery_no_schedule_off_band_interval_seconds`  | `7200`                     | 无预约 + 非 peak 的 discovery 间隔（2h）                              |
| `discovery_active_band_youtube_interval_seconds`   | `300`                      | peak 无预约时的 discovery 间隔（5min）                                |
| `discovery_active_band_x_refresh_interval_seconds` | `1800`                     | peak 无预约时 X 刷新目标间隔（30min）                                 |

### 其它常用

| 字段                          | 说明                                                                                                       |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `youtube_api_key`           | YouTube Data API Key（必填，勿用占位符）                                                                   |
| `channels_file`             | 成员频道表                                                                                                 |
| `schedule_timezone`         | 时段 band / 日程解读时区（默认`Asia/Tokyo`）                                                             |
| `time_bands` / `off_peak` | **仅**定义重点时段窗口 + `sampling_seconds`（同接采样）；**不再**含 idle/scheduled discovery |
| `sampling_interval_seconds` | 同接采样基准（`schedule_enabled=false` 时用；开启 band 时由 band/off_peak 覆盖）                         |

完整示例见 `config.example.json`。

---

## CLI 一览

```powershell
uv run python main.py run                          # 常驻监控（默认）
uv run python main.py report --week 2026-W31       # 手工周报
uv run python main.py refresh-x                    # 单次 X 刷新（需启用+Token）
uv run python main.py refresh-x --text-file path   # 离线解析文本
uv run python main.py version
```

`-c` / `--config` 可放在子命令前后，例如：

```powershell
uv run python main.py -c config.json run
uv run python main.py report --week 2026-W31 -c config.json
```

---

## 测试

```powershell
cd E:\yumemita_stream_data\X_Schedule_Discovery
uv sync
uv run pytest -q
```

---

## 数据与日志目录

| 路径                         | 内容                                |
| ---------------------------- | ----------------------------------- |
| `data/streams.csv`         | 直播元数据                          |
| `data/viewer_samples/`     | 同接采样长表                        |
| `data/runtime_state.json`  | 运行时状态（含`last_x_since_id`） |
| `data/schedule_hints.json` | X 日程提示                          |
| `data/weekly_reports/`     | 周报                                |
| `logs/`                    | 日志                                |

`config.json`、`data/`、`logs/`、`.venv/` 已忽略提交。

---

## 启用 X 后的观察清单

- 日程帖获取 / 解析成功率
- 直接得到 `video_id` 的比例
- 靠 X 定向加速提前发现的场次
- 首次发现相对 `actualStartTime` 的延迟
- YouTube API 与 X API 用量
- X 失败时是否仍按 YouTube + time_bands 正常运行

---
