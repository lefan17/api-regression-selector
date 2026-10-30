# TIA 交付报告

> 一页纸：做了什么、数字是多少、怎么用、哪里不靠谱。
> 想读实现细节看 [TIA 完全入门](TIA完全入门.md)；想读契约看 [tia-plan.md](tia-plan.md)。

## 一句话

把「这次改动会影响哪些用例」算出来，只跑那一批。实测：**改 `mock_server/routers.py` 一行 → 选中 1 条用例（1/689，0.15%），跑完 2.21 秒**；而全量 `-m "not ui"` 是 **196.55 秒**。

## 交付物

| 文件 | 行数 | 作用 |
|---|---|---|
| `qaplat/tia.py` | 766 | 选择器核心：v1 文件级 + v2 行级（coverage-context） |
| `qaplat/cli.py` | +159/−1 | `tia select` 子命令；`gate --select tia`（默认关闭） |
| `qaplat/_dump.py` | +11 | 采集插件加 `trylast=True`，修正分层分母与跨层选中 |
| `testcases/platform/test_tia.py` | 741 / 53 条 | 覆盖 v1/v2 共 16 个分支，`qaplat/tia.py` 行覆盖 90% |
| `docs/tia-plan.md` | 199 | 契约基准：接口、CLI、验收数字、不做什么 |
| `docs/TIA完全入门.md` | 1992 | 面向初学者的逐层讲解（数据从哪来、每行代码为什么这么写） |

## 核心数字

### 全量基线（一次真实采集）

```
pytest -m "not ui" -q --cov=. --cov-context=test --cov-report=
→ 3 failed, 588 passed, 34 deselected, 11 xfailed in 196.55s (0:03:16)
→ 收集总数 636；其中 3 条 failed 是上游基线就有的（见下文「不要背锅」）
```

### 索引规模

| 项 | 值 | 说明 |
|---|---|---|
| `.coverage.tia` | 757,760 字节 | SQLite，每行被谁执行过 |
| `measured_files` | 121 | coverage 记录到的文件数 |
| context 总数 | 1807 | 「某用例某阶段执行过某行」的条目数 |
| 建索引后可用文件 | 117 | 121 减去 4 个没有可用 context 的文件 |

### 行级选择效果（四个真实场景）

| 改动 | 选中 | 比例 | 说明 |
|---|---|---|---|
| `mock_server/routers.py:65`（`readyz` 里一次查询） | **1 条** | 0.15% | 实测跑完 **2.21s**（墙钟）/ 2.83s（pytest 内部计时） |
| `mock_server/services.py` 某行 | 1 条 | 0.15% | 该文件 486 行被覆盖，最小影响面就是 1 条 |
| `mock_server/quantity.py` / `idempotency.py` 某行 | 1 条 | 0.15% | 同上，59 / 82 行被覆盖 |
| `qaplat/registry.py:70` | 11 条 | 1.7% | 平台层纯函数，被 11 个单测覆盖 |

**69–89 倍**这个量级要谨慎表述：全量 196.55s 是 602 条用例的墙钟，单条 2.21s 里约 2 秒是 pytest 固定启动开销（起 mock_server、加载插件、收集）。用例越少，固定开销占比越高，倍数不是线性可推的。

### 漏检实验（这是"可不可信"的唯一硬证据）

把 `qaplat/registry.py:70`（`normalize_path` 的核心行）临时改坏，让所有路径归一化成同一串：

| 项 | 数值 |
|---|---|
| 全量 `-m platform` | `10 failed, 286 passed, 400 deselected in 43.81s` |
| 其中基线失败（改动前就有） | 3 条 |
| **这次改动造成的真红** | **7 条** |
| TIA 对第 70 行的选中集 | 11 条 |
| **漏检** | **0 条**（7 条真红全部落在选中集内；另 3 条是索引陈旧，见限制 3） |

实验后已原样恢复，`git diff qaplat/registry.py` 为空，platform 层回到 `3 failed, 286 passed`。

### 分层分母（`_dump.py` 修 `trylast` 前后）

| `pytest_args` | 对应入口 | 修前 | 修后 |
|---|---|---|---|
| `[]` | `tia select` 独立预览 | 689 | **689** |
| `['-m','smoke']` | `--layer smoke` | 689（错） | **36** |
| `['-m','api']` | `--layer api` | 689（错） | **132** |

两个交叉印证：`36` 与 pytest 自报的 `36/689 tests collected (653 deselected)` 一致；`132` 与仓库 `AGENTS.md` 记载的「接口层 132 条」一致。

## 怎么用

### 1. 建索引（改了测试代码或新增测试文件后必须重跑）

```bash
cd D:/DSH/test-projects/api-regression-selector
VENV=/d/DSH/test-projects/fullstack-test-framework/.venv/Scripts/python.exe
COVERAGE_FILE=.coverage.tia "$VENV" -m pytest -m "not ui" \
  --cov=. --cov-context=test --cov-report=
```

这是**建索引**，不是跑测试——它顺带把结果跑一遍，产出 `.coverage.tia`。

### 2. 看这次改动会影响哪些用例（不跑测试）

```bash
"$VENV" -m qaplat.cli tia select --base HEAD~1
"$VENV" -m qaplat.cli tia select --base HEAD~1 --json
```

`--base` 是 git 比对基准，默认 `HEAD~1`；`--base HEAD` 比的是「工作区相对 HEAD」。

### 3. 在门禁里用（默认关闭，必须显式开）

```bash
"$VENV" -m qaplat.cli gate --layer smoke --select tia --coverage-data .coverage.tia
```

输出里会有一行：

```
[qaplat] tia: selected=X/Y (ratio=Z%) full=BOOL strategy=lines|files|full reason=...
```

**不给 `--select` 时行为与改造前逐字一致。**

## 什么时候会退回全量（这是设计，不是失败）

| 情形 | 判据 | 为什么保守 |
|---|---|---|
| 改了 `pyproject.toml` / `conftest.py` / `pytest.ini` | `ALWAYS_FULL_NAMES` | 影响所有用例的收集与执行 |
| 改了 `qaplat/gate.py` / `data/*.json` | `ALWAYS_FULL_PREFIXES` | 门禁本体与共享数据 |
| 改了某个「没有任何用例执行过」的行 | 行级索引查不到 | 影响面未知，宁可全跑 |
| 改了模块级代码（import 期执行，空 context） | 该行 context 为空 | 不归属任何用例 |
| 变更文件不在覆盖索引里 | 索引里没有 | 可能是新文件或采集漏了 |
| 提取不到接口路径 | 路径交集命中 0 条 | 动态拼接路径提取不到 |
| **`git diff` 为空** | 本次没有任何变更 | 避免「静默跳过全部门禁」 |

## 已知限制（必须知道）

1. **行级粒度的固有取舍**：同一文件里部分行命中时按命中行选，不因「另有几行没人执行」而升全量。收紧要加显式开关（v3）。
2. **路径字面量方案对 `mock_server/**` 失效**：路由写 `@router.get("/readyz")`，完整前缀在 `mock_server/app.py:50` 的 `include_router(prefix=API_PREFIX)` 里拼。实测 `registry.read_referenced_paths("mock_server/routers.py")` 返回 **0 条**，所以 v1 对被测源码必然退化全量——这正是 v2 改用 coverage-context 的原因。
3. **索引陈旧**：新增测试文件不在旧索引里，永远不会被选中。必须重采（见「建索引」）。
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
| `testcases/platform/test_tia.py` | 53 passed |
| `-m platform` | 3 failed（基线）, 286 passed, 400 deselected |
| `qaplat/tia.py` 行覆盖 | 90%（267 stmts / 27 miss，未覆盖的是 git 与 CLI 接线） |
| `ruff check` / `format --check` | 通过 |
| 全仓 `mypy` | 122 文件 0 issue |
| 上游仓零改动 | `dfc9fe6` dirty 0 |
