# TIA（变更 → 测试选择）设计与实施计划

> 本文件是 `qaplat tia` 的**契约基准**：接口签名、算法判据、门禁口径、验收数字。
> 实现与测试都以本文件为准；本文件与代码不一致时，先改本文件再由 Lead 裁定。
>
> **状态（2026-10-31）：已实现并验收。** 交付清单、实测数字、漏检实验与已知限制见
> [tia-report.md](tia-report.md)；逐层实现讲解见 [TIA完全入门.md](TIA完全入门.md)。
> 本文件继续作为契约基准，实现细节以代码与那两份文档为准。

## 1. 要解决什么

用例库随 AI 生成 / 契约参数化膨胀（当前 636 条），**每次 PR 都跑全量**的代价线性上升：

- `qaplat gate --layer pr`（`smoke or api or platform`）实测 13:30 级别的墙钟里，
  大部分时间花在**与本次改动无关**的用例上。
- 副作用已经可见：`testcases/contract/test_schemathesis_properties.py` 一条函数生成 188 条用例，
  改一个登录字段也要把它们全跑一遍。

**TIA（Test Impact Analysis）**：从 git diff 出发，只跑「被本次改动触达」的用例，
并保证**漏跑风险可控**（宁可多跑，不可漏跑）。

### 叙事闭环（为什么这个项目值得做）

> AI 编码 Agent 让「写用例」变便宜 → 用例库膨胀 → 全量门禁变慢 →
> **TIA 只跑受影响的那部分** → 耗时回落。
>
> 提效（AI 生成）→ 副作用（用例膨胀）→ 治理副作用（TIA）。三段都能给出实测数字。

## 2. 已有基础（不重复造）

`qaplat/registry.py` 已经具备 TIA 需要的全部输入，**零新依赖**：

| 能力 | 函数 | 产出 |
|---|---|---|
| 全量用例 + 归属 | `collect_cases()` | `CaseInfo(node_id, markers, referenced_paths, covers)` |
| 用例 → 接口路径 | `CaseInfo.referenced_paths` | 该用例源码里的 `/api...` 字面量 |
| 源码 → 接口路径 | `read_referenced_paths(file)` / `extract_paths_from_source(text)` | 同一套正则，对被测源码同样有效 |
| 用例 → 所在文件 | `owner_files()` | `[(文件, 用例数)]` |
| 平台自测排除 | `is_platform_self_test()` | `testcases/platform/**` 不参与路径归属 |

`pytest-testmon` **未安装**，且本仓硬约束是「不加依赖」，所以自研。

## 3. 算法（三级保守策略）

输入：变更文件集 `changed`、全量用例 `cases`。

```
对每个 changed 文件 f：
  ① 关键文件（见 3.1）           → 立即判定「全量」
  ② f 在 testcases/ 下            → 选中该文件的所有 nodeid
  ③ 其他源码                      → paths = read_referenced_paths(f)
        paths 非空且命中用例        → 选中 referenced_paths ∩ paths ≠ ∅ 的用例
        paths 非空但命中 0 条       → 全量兜底（该路径有代码但没人测它）
        paths 为空                  → 全量兜底（公共模块，无法判定影响面）
```

命中「全量」即**立刻返回**，不再继续匹配（避免半量误判）。

### 3.1 关键文件清单（变更即全量）

| 路径模式 | 理由 |
|---|---|
| `conftest.py`（任意层级） | 夹具/钩子影响所有用例 |
| `pytest.ini`、`pyproject.toml`、`setup.cfg` | marker/testpaths/addopts 定义 |
| `requirements*.txt`、`requirements.lock` | 依赖面变化 |
| `qaplat/**` | 平台层自身，被 platform 层用例覆盖 |
| `data/**`、`schemas/**`、`docs/openapi.json` | 契约与数据源 |
| `traffic-replay/**` | 独立子项目 |
| `scripts/**` | 报表/导出脚本，与用例断言直接相关 |

### 3.2 已知限制（必须在输出里显式声明）

- **粒度是文件**：`mock_server/routers.py` 里所有路由定义在一个文件，
  改任何一个路由都会提取出全部 `/api/*`，退化为大部分用例。
  行级影响分析（coverage `--cov-context=test`）是 v2，**已实现**：`select_by_lines` 按
  「这一行被谁执行过」选用例，文件级 v1 退化为兜底。实测把 `routers.py` 的粒度从
  「636 条全跑」压到「1 条」（7.5 节）。
- **不识别动态路径**：`f"/api/{x}"` 这类拼接无法被字面量正则提取 → 落入「paths 为空」→ 全量兜底（安全）。
- **不处理删除的用例**：nodeid 已不存在时归入「匹配不到」→ 全量兜底。

## 4. 接口契约

### 4.1 `qaplat/tia.py`（新增）

```python
ALWAYS_FULL_PREFIXES: tuple[str, ...]   # 3.1 清单，稳定排序
ALWAYS_FULL_NAMES: frozenset[str]       # {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg"}

@dataclass(frozen=True)
class Selection:
    node_ids: tuple[str, ...]      # 选中的 nodeid，稳定排序
    full: bool                     # 是否退化为全量
    reason: str                    # 命中原因（中文，一句话，进 JSON 与文本输出）
    changed_files: tuple[str, ...] # 本次判定用到的变更文件
    total_cases: int               # 全量用例数
    matched_files: tuple[str, ...] # 步骤②命中的测试文件
    matched_paths: tuple[str, ...] # 步骤③命中的接口路径

    @property
    def ratio(self) -> float:      # len(node_ids) / total_cases；total_cases==0 → 0.0


def changed_files(base: str = "HEAD~1", *, include_worktree: bool = True,
                  cwd: Path | None = None) -> tuple[str, ...]:
    """git diff --name-only base...HEAD（committed）+ 工作区/未跟踪（可选）。"""


def select(changed: Sequence[str], cases: Sequence[registry.CaseInfo], *,
           path_reader: PathReader | None = None) -> Selection:
    """v1 文件级选择：不做 git、不起子进程；path_reader 是唯一可能读盘的地方。"""


def select_by_lines(hunks: Sequence[ChangedHunk], index: ContextIndex,
                    cases: Sequence[registry.CaseInfo]) -> Selection:
    """v2 行级选择（已实现）：变更行 → 执行过该行的 nodeid 集合。"""


def select_from_git(base: str = "HEAD~1", *, pytest_args: Sequence[str] = (),
                    include_worktree: bool = True,
                    cwd: Path | None = None,
                    data_path: Path | None = None) -> Selection:
    """changed_files + registry.collect_cases → select；给了 data_path 就走 v2 行级。"""


def render_text(sel: Selection) -> str
def render_json(sel: Selection) -> str   # 固定字段，供 CI 解析
```

`render_json` 输出结构（字段名固定，不许改）：

```json
{
  "full": false,
  "strategy": "files",
  "reason": "命中 3 个测试文件、12 条接口路径",
  "total_cases": 636,
  "selected": 87,
  "effective_count": 87,
  "ratio": 0.1368,
  "changed_files": ["testcases/login/test_login_contract.py"],
  "matched_files": ["testcases/login/test_login_contract.py"],
  "matched_paths": [],
  "node_ids": ["testcases/login/test_login_contract.py::test_lc01_login_success"]
}
```

`strategy` 取值：`full`（全量兜底）／`files`（v1 文件级）／`lines`（v2 行级）／
`docs`（仅文档变更：`full=false` 且 `selected=0`，不必跑任何用例）。

`selected` 是「显式选中的 nodeid 个数」，全量时恒为 0（执行集不是清单）。为了让 CI 不必
自己拼 `full ? total_cases : selected`，另给 `effective_count` = 这一轮实际要跑多少条。
它**是纯增量字段**：老的十个字段一个没改名、没删。

### 4.2 `qaplat/cli.py`

```
qaplat tia select [--base HEAD~1] [--json] [--no-worktree] [--pytest-args "..."]
qaplat gate --layer pr --select tia [--base HEAD~1]
```

- `tia select` 只打印选择结果，退出码 0；`--json` 输出 4.1 的 JSON。
- `gate --select tia`：先 `select_from_git`，把 `sel.node_ids` 作为 `extra` 传给
  `gate.run_layer(spec, node_ids)`；`sel.full` 为真时等价于不带 `--select`。
- `--select` 的取值只有 `tia`（未来可扩展 `none`），非法值退出码 2。
- **默认关闭**：不带 `--select` 时行为与今天完全一致。

### 4.3 覆盖率门禁口径（关键）

`gate.evaluate()` 只有 5 条判据（总数为 0 / decisive 为 0 / 通过率 / error 预算 / 耗时），
**不含覆盖率**。覆盖率门禁在别处（`--cov-fail-under=54` 与 `scripts/check_coverage.py`），
`qaplat gate` 的 pytest 命令行不带 `--cov`。

因此：

- **tia 模式天然不挂覆盖率门**，不需要额外加开关。
- 但 CI 里如果先跑 `tia` 再跑 `coverage`，两步必须**分开**，不能把 `--cov` 拼进 tia 那一步。
- `docs/` 与 `README` 必须写明这一点，否则会有人以为「tia 让覆盖率掉了」。

## 5. 验收数字（实测填写，不接受估算）

| 指标 | 口径 | 期望 |
|---|---|---|
| 用例总数 | `pytest --collect-only -q -q` | 636（基线） |
| 场景 A：只改 `testcases/login/test_login_contract.py` | 选中数 / 比例 | 应只含该文件用例，比例 < 10% |
| 场景 B：只改 `mock_server/services.py` | 应触发全量 | `full=true` |
| 场景 C：改 `qaplat/gate.py` | 应触发全量 | `full=true` |
| 场景 D：改 `mock_server/routers.py` | 提取路径数 / 选中数 | 路径非空，比例显著 > 场景 A |
| 全量耗时 vs 场景 A 选中耗时 | 墙钟，同一机器 | 选中耗时显著更低 |
| 漏检率 | 全量失败集 − 选中失败集 | 0（对场景 A/B/C/D 各跑一次） |

**漏检率定义**：`全量跑的 failed nodeid 集合` 中，**不在** TIA 选中集里的数量。
必须为 0 —— 否则说明兜底策略失效。

## 6. 分工与写域（幕府）

| 角色 | 负责文件（写域） | 交付 |
|---|---|---|
| **中书** | `qaplat/tia.py`（新增，唯一写入者） | 第 4.1 节全部函数 + docstring 讲清每级判据 |
| **部·CLI** | `qaplat/cli.py` | 第 4.2 节两个入口；不动其他命令 |
| **部·测试** | `testcases/platform/test_tia.py`（新增） | 纯函数分支覆盖 + 真实仓库集成用例 |
| **部·CI** | `.github/workflows/tia.yml`（**已落地**：仅 `workflow_dispatch`，不进 PR 门） | 采集 → `tia capture` → 选择/门禁 |
| **尚书** | `docs/tia-report.md`（新增） | 汇总实测数字与漏检率 |
| **门下**（fresh subagent） | 只读 | 按五条判据逐项核验 |

**写域互不重叠**，可并行；测试与 CI 依赖中书接口 → 用 `blocked_by` 排序。

## 7. 里程碑

1. **M1 核心**：`qaplat/tia.py` + 纯函数测试通过（`pytest testcases/platform/test_tia.py`）
2. **M2 接线**：`qaplat tia select` 能打印真实仓库的选择结果
3. **M3 门禁**：`qaplat gate --layer pr --select tia` 跑通，退出码语义正确
4. **M4 实测**：第 5 节 6 个场景全部有数字，漏检率 0
5. **M5 留痕**：`docs/tia-report.md` + CI 手动 workflow

## 8. 不做什么（明确排除）

- 不引入 `pytest-testmon`、`coverage` 之外的新依赖
- 不做比「行」更细的影响分析（函数级 / 表达式级 / 分支级）；行级（v2）**已实现**
- 不改 `gate.evaluate()` 的判据与阈值
- 不改 `qaplat/FROZEN_CLI.md` 与任何 `FROZEN_*` 文件
- 不默认开启 `--select tia`，不把它塞进 PR 流水门（先手动跑一周）
