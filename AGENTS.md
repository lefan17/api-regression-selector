# AGENTS.md · api-regression-selector

## 这是什么

本仓 **fork** 自 `D:\DSH\test-projects\fullstack-test-framework`（起点 commit `dfc9fe6`，2026-10-31）。
**上游保持冻结只读**，本仓独立演进。

- 用途：在既有的 pytest 接口 / UI / 契约 / 混合链路框架上，新增 **「变更 → 测试选择」（TIA, Test Impact Analysis）** 能力
- 上游仓：`D:\DSH\test-projects\fullstack-test-framework` —— **不要在这里改任何东西**
- 本仓：`D:\DSH\test-projects\api-regression-selector`
- 两仓**共享同一个 venv**（本仓没有 `.venv`）

## 怎么跑

一律用上游 venv 的绝对路径调 `-m`（不必 activate）：

```bash
VENV=/d/DSH/test-projects/fullstack-test-framework/.venv/Scripts/python.exe
cd /d/DSH/test-projects/api-regression-selector

$VENV -m pytest -m "smoke and not ui"            # 冒烟 30 条，实测 11.84s
$VENV -m pytest -m "not ui"                      # 全量非 UI
$VENV -m pytest -m api -n 4 --dist loadfile      # 接口层
$VENV -m pytest -m platform                      # qaplat 平台层
$VENV -m mypy                                    # 配置全在 pyproject.toml，别加参数
$VENV -m ruff check . && $VENV -m ruff format --check .

# TIA（变更 → 测试选择）。入口是 -m qaplat.cli，不是 -m qaplat（这个包没有 __main__.py）
$VENV -m qaplat.cli tia select --base HEAD~1     # 只打印会影响哪些用例，不跑测试
$VENV -m qaplat.cli gate --layer pr --select tia --base origin/main   # 门禁内启用（默认关闭）
$VENV -m qaplat.cli tia capture --coverage-data .tia-coverage        # 采集后记下 HEAD
```

## 环境硬约束（本仓特有，必读）

### 1. Low 完整性级别 —— 新目录一律写不进去

DSH 派生的 Python 进程运行在 **Low 完整性级别**，只能写同样带 Low 标签的目录。
`git clone` / `mkdir` 出来的新目录继承父目录 `D:\DSH\test-projects` 的 **Medium** 标签，
于是 Python 写文件立刻 `PermissionError: [Errno 13] Permission denied`，
接着 `tempfile` 连锁报 `No usable temporary directory found in [...]`（候选里含当前 cwd）。

**症状**：`pytest --collect-only` 还没开始收集就崩在 `tempfile._get_default_tempdir`。

**修复**（在 PowerShell 里执行；**不要**用 git-bash 的 `icacls`，MSYS 会把 `/setintegritylevel`
当成 Unix 路径转换成 `D:/.../setintegritylevel`，报"无效参数"）：

```powershell
icacls 'D:\DSH\test-projects\api-regression-selector' /setintegritylevel '(OI)(CI)L' /T /C /Q
```

**排查时别被带偏**：`icacls` 列出的 DACL 是正常的（`lefan` 有 FullControl），
改 DACL 完全无效 —— 低完整性是 no-write-up 策略，与 DACL 无关。
脚本往 SID 上加 ACE 也不会有效果。

### 2. TMP 必须可写

`D:\study\AppCache\Temp` 若不存在或不可写，pytest 同样连锁失败。
Low 标签生效后 cwd 可直接当临时目录；否则显式指定：

```bash
export TMP=/可写路径 TEMP=/可写路径
$VENV -m pytest --basetemp=$TMP/pytest
```

### 3. 不要设 `PYTHONPATH=work\pylibs`

那份 pylibs 里是 schemathesis 3.39.16，会顶掉 venv 的 4.31.1，在
`testcases/contract/test_schemathesis_properties.py` 收集期报
`ImportError: cannot import name 'GenerationMode'`。

## 硬约束

- **上游仓只读**：一切改动只在本仓。要吸收上游新提交用 `git fetch upstream && git cherry-pick`，
  不要两边各改一份造成分叉。
- **冻结文件不许改**：`FROZEN_MESWEB.md`、`qaplat/FROZEN_CLI.md`、
  `traffic-replay/FROZEN_INTERFACES.md`、`traffic-replay/replay/model.py`
  （最后一个只允许**末尾追加带默认值**的字段）。
- **不加依赖**：`python-multipart`、`itsdangerous`、`jinja2` 都不要；`locust` 只进
  `requirements-perf.txt`。改依赖必须 `pip-compile` 重生成 `requirements.lock`。
  ⚠️ 比上游更严的一条：**两仓共享 venv，加依赖会同时改变上游的运行环境**。
- **LF 换行**：改文件用 `read_bytes/write_bytes` 或 `newline=""`，不要用
  `Path.read_text/write_text`（Windows 会写成 CRLF，pre-commit `mixed-line-ending` 判红）。

## 与上游的差异

| 项 | 上游 `fullstack-test-framework` | 本仓 |
|---|---|---|
| 用例数 | 636 collected | **697 collected**（+61 条 TIA 用例） |
| 冒烟 | 30 passed | 30 passed（实测 11.84s） |
| venv | 自带 330M | **复用上游** |
| 包名 | `fullstack-test-framework` | `api-regression-selector` |
| zentao schema | `fullstack-test-framework/zentao-payload@1` | `api-regression-selector/zentao-payload@1` |
| 新增能力 | — | **TIA（`qaplat/tia.py`，已实现并验收）** |

改名已同步到 8 处：`pyproject.toml`、`docker-compose.yml`、
`.github/ISSUE_TEMPLATE/config.yml`、`scripts/case_index.py`、
`scripts/report_to_zentao.py`、`scripts/test_report_to_zentao_stub.py`、
`testcases/platform/test_scripts_report_to_zentao.py`、`docs/evidence/summary.json`。

> `docs/wave*-handoff.md`、`docs/platform-*.md` 里的旧项目名与死路径
> （`C:\Users\lefan\DSH_project\DH_1\...`）是**历史记录，故意保留不改**。

## 已知坑

- 死路径 `...\DH_1\auto-test-demo\.venv\Scripts\python.exe` 已不存在，一律用上游 venv 绝对路径。
- 并行跑 pytest 偶发收集期崩（`FileNotFoundError [WinError 2]`、退出码 2、**零 nodeid**）：
  共用 `$env:TMP` 根被并发进程反复建删。手敲时给独立 `--basetemp`，
  且 `--confcutdir` 必须插在 `--` **之前**。
- `-m ui` 报 `Executable doesn't exist ... chrome-headless-shell.exe`：不是浏览器没装，
  是 Playwright 要**可写 TEMP**，先查 `TMP`/`TEMP`。
- 端口不固定：`mock_server`、`mes_web` 都用 `free_port()`，别写死 8000。
- 变量：`MAX_RESPONSE_MS=8000`（CI 放宽）、`CONTRACT_MAX_EXAMPLES=8`、`PERF_P95_LIMIT_MS`（只能收紧）。
