# TIA 交付报告

> 一页纸：做了什么、数字是多少、怎么用、哪里不靠谱。
> 想读实现细节看 [TIA 完全入门](TIA完全入门.md)；想读契约看 [tia-plan.md](tia-plan.md)。

## 一句话

把「这次改动会影响哪些用例」算出来，只跑那一批。**但它只对同进程执行的代码成立**：

- 改 `qaplat/registry.py:70`（平台层纯函数）→ 选中 **36 / 712 条（5.06%）**，跑完 **2.45 秒**；
  全量 `-m "not ui"` 是 **137.41 秒** → 约 **56 倍**。
- 改 `mock_server/` 任何文件 → **全量兜底**。原因见「已知限制」第 0 条：uvicorn 子进程的
  coverage context 在 spawn 那一刻就固定了，行级数据会失真到「整份文件只关联 10 个用例」。

早期版本写过「改 `mock_server/routers.py` 一行只跑 1 条、2.21 秒」——那个**测量本身是真的，
但它测的是失真的索引**，不是精确判定。现已作废，别再引用。

## 交付物

| 文件 | 行数 | 作用 |
|---|---|---|
| `qaplat/tia.py` | **956** | 选择器核心：v1 文件级 + v2 行级（coverage-context）+ 索引新鲜度 |
| `qaplat/cli.py` | **+235/−2**（文件 703 行） | `tia select` / `tia capture` 子命令；`gate --select tia`（默认关闭） |
| `qaplat/_dump.py` | **+11**（文件 168 行） | 采集插件加 `trylast=True`，修正分层分母与跨层选中 |
| `testcases/platform/test_tia.py` | **1063 行 / 76 条** | 覆盖 v1/v2 共 16 个分支 + 索引新鲜度 8 条 + 被测服务必全量 6 条 + docs 分流 3 条 + 陈旧度文案 2 条 |
| `.github/workflows/tia.yml` | **103** | 仅 `workflow_dispatch`：采集 → capture → 选择，不进 PR 门 |
| `docs/tia-plan.md` | **221** | 契约基准：接口、CLI、验收数字、不做什么 |
| `docs/TIA完全入门.md` | **2170** | 面向初学者的逐层讲解（数据从哪来、每行代码为什么这么写） |
| `docs/tia-review.md` | **206** | 独立核验记录：三轮判决、问题处理表、核验方明确没验的部分 |
| `docs/tia-report.md` | 本文 | 一页纸总账（行数随增删变化，不写死） |

> 行数是 `wc -l` 实测值。改代码后请重测再回填——手写行数正是这份表上一轮 6 行全错的来源。

## 核心数字

### 全量基线（一次真实采集）

```
pytest -m "not ui" -q --cov=. --cov-context=test --cov-report=
→ 3 failed, 664 passed, 34 deselected, 11 xfailed in 137.41s (0:02:17)
→ 收集总数 712；其中 3 条 failed 是上游基线就有的（见下文「不要背锅」）
```

> **这是当前口径（2026-10-31，含 76 条 TIA 平台自测）。** 历史上还有两个口径，都真实存在：
> 首采（`test_tia.py` 还不存在）是 `3 failed, 588 passed, ... in 196.55s`、收集 636；
> 补完 53 条自测后是 `3 failed, 640 passed, 1 skipped, ... in 190.00s`、收集 697。
> 三套数字都对，但**分母不同不能互相换算**：引用「省了多少」时，分子分母必须取自同一次运行。
> 下面凡是写「全量」的地方，都指 **712 条 / 137.41 秒** 这一次。

### 索引规模

| 项 | 值 | 说明 |
|---|---|---|
| `.tia-coverage` | 843,776 字节 | SQLite，每行被谁执行过（**2026-10-31 最新重采**；首采 757,760、上一次 811,008） |
| `measured_files` | 122 | coverage 记录到的文件数（首采 121，多出来的是 `test_tia.py` 自身） |
| 唯一 context 名 | 2035 | 678 条用例 × 3 个阶段（`run`/`setup`/`teardown`）+ 1 个空 context（模块级代码） |
| 行×context 条目 | 67,182 | 索引里「某用例某阶段执行过某行」的映射条数 |
| 建索引后可用文件 | 119 | 122 减去 3 个没有可用 context 的文件，没进索引的是 `mes_web/*` |

> 这几个数**随用例数变化，不是常数**。首采时 `test_tia.py` 还不存在，连 `qaplat/tia.py`
> 自己都没进索引（121 / 1807 / 117，差 4 个）；补上 53 条平台自测后变成 122 / 1966 / 119，
> 差 3 个。引用时把采集时间一起引，别把两个口径的数混着算。

### 行级选择效果（真实可用 vs 必须全量）

| 改动 | 选中 | 比例 | 说明 |
|---|---|---|---|
| `qaplat/registry.py:70` | **36 条** | 5.06% | 平台层同进程执行，行级数据真实可用；跑完 **2.45s** vs 全量 137.41s |
| `apis/user_api.py` 某行 | 按索引 | — | HTTP 客户端同进程 import，可精确（`apis/` 不在必跑清单） |
| `mock_server/routers.py:65` | **全量 712** | 100% | 子进程执行 → context 失真 → 必跑清单（**旧文档说的「1 条」是失真读数**） |
| `mock_server/*` 任意行 | 全量 | 100% | 同上；该文件 444 行只关联 10 个用例，而实际有 423 条在打接口 |
| `qaplat/gate.py` 模块级行 | 全量 | 100% | import 期执行，空 context，不归属任何用例 |
| `README.md` / `docs/*.md` | **0 条** | 0% | `strategy=docs`：没有任何用例读它们 |

**倍数必须连分母一起报**：2.45s（36 条，pytest 内部计时）对 137.41s（712 条全量）≈ **56 倍**；
按墙钟 4s 对 139s ≈ 35 倍。少量用例里约 2 秒是 pytest 固定启动开销（起 `mock_server`、
加载插件、收集），条数越少固定开销占比越高，倍数不是线性可推的——**对外只写「量级为数十倍」**。

### 漏检实验（这是"可不可信"的唯一硬证据）

把 `qaplat/registry.py:70`（`normalize_path` 的核心行）临时改坏，让所有路径归一化成同一串：

| 项 | 数值 |
|---|---|
| 全量 `-m platform` | `10 failed, 286 passed, 400 deselected in 43.81s` |
| 其中基线失败（改动前就有） | 3 条 |
| **这次改动造成的真红** | **7 条** |
| TIA 对第 70 行的选中集 | 11 条 |
| **漏检（旧索引，按定义直算）** | **3 条 / 7 条 ≈ 43%** —— 4 条在选中集内，3 条在 `testcases/platform/test_tia.py`（采集之后才新增，索引里没有），见限制 3 |
| **漏检（补采后复算）** | **0 条 / 7 条** —— 重采后同一行选中 34 条（当时口径），那 3 条全部命中（已实测） |

实验后已原样恢复，`git diff qaplat/registry.py` 为空，platform 层回到 `3 failed, 286 passed`。

### 分层分母（`_dump.py` 修 `trylast` 前后）

| `pytest_args` | 对应入口 | 修前 | 修后 |
|---|---|---|---|
| `[]` | `tia select` 独立预览 | 697 | **697** |
| `['-m','smoke']` | `--layer smoke` | 697（错） | **36** |
| `['-m','api']` | `--layer api` | 697（错） | **132** |

两个交叉印证：`36` 与 pytest 自报的 `36/697 tests collected (661 deselected)` 一致；`132` 与仓库 `AGENTS.md` 记载的「接口层 132 条」一致。

## 怎么用

### 1. 建索引（改了测试代码或新增测试文件后必须重跑）

```bash
cd D:/DSH/test-projects/api-regression-selector
VENV=/d/DSH/test-projects/fullstack-test-framework/.venv/Scripts/python.exe
COVERAGE_FILE=.tia-coverage "$VENV" -m pytest -m "not ui" \
  --cov=. --cov-context=test --cov-report=
```

这是**建索引**，不是跑测试——它顺带把结果跑一遍，产出 `.tia-coverage`。

采完**立刻**记一次 HEAD，否则后面判断不了新鲜度：

```bash
"$VENV" -m qaplat.cli tia capture --coverage-data .tia-coverage
# → [qaplat] tia capture: HEAD=875e14a90fdc → .tia-coverage.head
```

### 2. 看这次改动会影响哪些用例（不跑测试）

```bash
"$VENV" -m qaplat.cli tia select --base HEAD~1
"$VENV" -m qaplat.cli tia select --base HEAD~1 --json
```

`--base` 是 git 比对基准，默认 `HEAD~1`；`--base HEAD` 比的是「工作区相对 HEAD」。

### 3. 在门禁里用（默认关闭，必须显式开）

```bash
"$VENV" -m qaplat.cli gate --layer smoke --select tia --coverage-data .tia-coverage
```

输出里会有一行：

```
[qaplat] tia: selected=X/Y (ratio=Z%) full=BOOL strategy=lines|files|full|docs reason=...
```

**不给 `--select` 时行为与改造前逐字一致。**

## 索引新鲜度：漏跑的唯一来源

行级 TIA 的准确率上限由**索引新鲜度**决定，不是由选择算法决定。漏检实验里旧索引下漏检 3/7，
那 3 条全部来自采集之后才新增的 `test_tia.py`——索引里根本没有这份测试的实测数据。

这个失效**不报错**：选择器照常返回一个更小的集合，只是悄悄少了几条。所以做了两件事：

1. **`tia capture`**：采集后把 HEAD 记进 `.tia-coverage.head`（JSON：`head` / `captured_at` / `coverage_file`）。
   不塞进 SQLite，因为那是 coverage 自己的 schema，`combine` 时可能被重写。
2. **`tia select` / `gate --select tia` 自动比对**：落后就在 stderr 警告，并给出「落后 N 个提交」，
   但**不改变退出码、也不改选择结果**——新鲜度是旁路提示，它自己坏掉不该让一次选择失败。

实测三种状态：

| 状态 | 行为 |
|---|---|
| 与 HEAD 同步 | 沉默（不打扰） |
| 索引落后 3 个提交 | 警告：`索引落后：采集于 c62af9d74103，当前 HEAD 875e14a90fdc，中间有 3 个提交未被覆盖 —— 这些提交里新增的测试不会被选中` |
| 没有 `.head` 标记 | 沉默（「不知道」与「同步」必须分开：每次都喊「不知道」会把人训练成无视警告） |

**为什么不自动重采**：重采要 140–190s，且只有「改了测试代码」时才必要。自动重采会让一次
`tia select` 从 6 秒变成 3 分钟，结果是没人再用它。正确做法是让人看到警告后自己决定。

`.tia-coverage.head` 必须被 `.gitignore` 忽略（现在是 `.tia-coverage*`）：它一旦被
`git ls-files --others` 当成新增文件，每次选择都会判「影响面未知 → 全量」，行级选择直接失效。

## 什么时候会退回全量（这是设计，不是失败）

| 情形 | 判据 | 为什么保守 |
|---|---|---|
| 改了 `pyproject.toml` / `conftest.py` / `pytest.ini` | `ALWAYS_FULL_NAMES` | 影响所有用例的收集与执行 |
| 改了 `qaplat/gate.py` / `data/*.json` | `ALWAYS_FULL_PREFIXES` | 门禁本体与共享数据 |
| 改了 `mock_server/` / `login_app/` / `mes_web/` / `ui/` | `ALWAYS_FULL_PREFIXES` | 子进程执行，行级 context 失真（见「已知限制」第 0 条） |
| 改了某个「没有任何用例执行过」的行 | 行级索引查不到 | 影响面未知，宁可全跑 |
| 改了模块级代码（import 期执行，空 context） | 该行 context 为空 | 不归属任何用例 |
| 变更文件不在覆盖索引里 | 索引里没有 | 可能是新文件或采集漏了 |
| 提取不到接口路径 | 路径交集命中 0 条 | 动态拼接路径提取不到 |
| **`git diff` 为空** | 本次没有任何变更 | 避免「静默跳过全部门禁」 |

## 已知限制（必须知道）

0. **⚠️ 被测服务代码（`mock_server/`、`login_app/`、`mes_web/`、`ui/`）不在行级判定的适用范围。**
   `--cov-context=test` 的 context 在**子进程 spawn 的那一刻**就由 `COV_CORE_CONTEXT` 固定了：
   uvicorn 进程里执行的所有行，全部算到「启动它的那条用例」头上。实测 `mock_server/routers.py`
   的 444 行只关联 **10 个用例**，而实际有 423 条用例在打接口——若让行级策略判这个文件，
   会**不报错地**漏掉约 92% 的用例。所以这几个目录留在 `ALWAYS_FULL_PREFIXES` 里，改它们一律全量。
   同理「模块级行 → 全量」那道安全网在这里也失效（`app.py` 63 行每行都有非空 context）。

   真正能精确判定的是**同进程执行**的代码：

   | 目录 | 执行方式 | 行级判定 |
   |---|---|---|
   | `qaplat/**` 平台层 | 测试直接 import | ✅ 精确 |
   | `apis/**` HTTP 客户端 | 测试直接 import | ✅ 精确 |
   | `mock_server/**`、`login_app/**`、`mes_web/**` | 测试起 uvicorn 子进程 | ❌ 失真 → 全量 |
   | `ui/**` | playwright 浏览器 | ❌ 无覆盖 → 全量 |

   副作用要说明白：**「改一行路由只跑 1 条」这个卖点不成立**；能成立的是「改一行 `qaplat`
   平台代码只跑几十条」。把前者写进简历，面试官只要问一句「那改路由参数呢」就会拆穿。

1. **行级粒度的固有取舍**：同一文件里部分行命中时按命中行选，不因「另有几行没人执行」而升全量。收紧要加显式开关（v3）。
2. **路径字面量方案对 `mock_server/**` 失效**：路由写 `@router.get("/readyz")`，完整前缀在 `mock_server/app.py:50` 的 `include_router(prefix=API_PREFIX)` 里拼。实测 `registry.read_referenced_paths("mock_server/routers.py")` 返回 **0 条**，所以 v1 对被测源码必然退化全量——这正是 v2 改用 coverage-context 的原因。
3. **索引陈旧**：新增测试文件不在旧索引里，永远不会被选中。必须重采（见「建索引」）。
   采集后跑一次 `python -m qaplat.cli tia capture --coverage-data .tia-coverage` 会把当前 HEAD
   记进 `.tia-coverage.head`；之后 `tia select` 若发现索引落后，会直接在 stderr 警告并给出
   「落后 N 个提交」。**它只警告、不自动重采**——重采要 140–190s，该不该跑得由人决定。
4. **不挂覆盖率门**：`qaplat gate` 的 pytest 命令行本来就不带 `--cov`，所以 tia 模式不会撞 `--cov-fail-under=54`。覆盖率门在 CI 的独立 job 里跑全量。
5. **临时目录污染**（已修）：pytest 的 `tmpdir` 会在仓库根留 `tmp<随机>`，被 `git ls-files --others` 当成变更 → 全量兜底。`.gitignore` 已加 `/tmp*` 与 `/qaplat-collect-*/`。

## 不要背锅：3 条基线失败

下面三条在**上游仓 `dfc9fe6` 同样失败**，与 TIA 无关：

- `testcases/platform/test_registry_coverage.py::test_confcutdir_only_added_for_targets_outside_the_repo`
- `testcases/platform/test_registry_coverage.py::test_collect_cases_survives_a_churning_temp_root`
- `testcases/platform/test_registry_coverage.py::test_confcutdir_option_is_spliced_before_the_double_dash`

## 验收清单

| 项 | 结果 |
|---|---|
| `testcases/platform/test_tia.py` | **72 passed**（含索引新鲜度 8 条、被测服务必全量 6 条、docs 跳过整层 1 条） |
| `-m platform` | 3 failed（基线）, 294 passed, 400 deselected |
| `qaplat/tia.py` 行覆盖 | 90%（267 stmts / 27 miss；未覆盖的主要是 `_run_git` 的真 git 调用、CLI 接线，以及 `select` / `select_by_lines` 里「未命中任何用例 → 全量」的两条算法兜底分支，`qaplat/tia.py:554` 与 `:671`） |
| `ruff check` / `format --check` | 通过（163 files formatted） |
| 全仓 `mypy` | 122 文件 0 issue |
| 上游仓零改动 | `dfc9fe6` dirty 0 |
| 索引新鲜度 | 实测三种状态：同步=沉默、落后 3 个提交=警告、无标记=沉默 |
