# 接口回归选择器（API Regression Selector）

> 🍴 **本仓库派生自 [`lefan17/fullstack-test-framework`](https://github.com/lefan17/fullstack-test-framework)**
> （本地 fork 起点 `dfc9fe6`，2026-10-31）。派生目的：**在完全冻结原仓库代码的前提下**，
> 新增「变更 → 测试选择（TIA, Test Impact Analysis）」能力与 AI 编码 Agent 协作规范。
> **派生时上游代码与用例一字未改，至今也没有为了本仓改过一行测试代码。**
> 唯一的例外是 2026-11-02 在上游新增了 `AGENTS.md` 与 README 的「智能体协作」一节
> （纯文档，不动代码、不动用例），因为那 200 条提交的协作过程本来就记录在上游仓里，
> 写在别处反而失真。
>
> 下方 badge 与 GitHub Pages 链接**仍指向原仓库**的真实 CI 与报表——新仓库尚未建立
> 自己的流水线，指向新地址会 404。建立后应替换。

---

## 本仓新增：变更 → 测试选择（TIA）

上游那套框架回答的是「跑得对不对」，本仓补的是**「改了一行，到底该重跑哪几条」**。

做法：用 `--cov-context=test` 采集「每一行被哪条用例执行过」的索引，再拿 `git diff`
的新增行号反查受影响的用例。**它只对同进程执行的代码成立**——改 `qaplat/registry.py`
一行时：

| 指标 | 全量 `-m "not ui"` | TIA 选中集 |
|---|---|---|
| 用例数 | 712 | **36**（5.06%） |
| 耗时 | 137.41s | **2.45s** |

改 `mock_server/**`（被测服务）则**一律全量**：这些代码由测试拉起 uvicorn 子进程执行，
coverage 的 context 在子进程 spawn 那一刻就固定了，行级数据会失真到「整份文件只关联
10 个用例」，而实际有 423 条用例在打接口。宁可多跑，不可静默漏跑——细节见
[`docs/tia-report.md`](docs/tia-report.md) 的「已知限制」第 0 条。

正确性不靠「看起来对」：把 `normalize_path` 故意改坏后，7 条真红里**旧索引漏检 3 条**
（那 3 条来自采集之后才新增的测试文件），补采后复算 **0/7**。这个 3/7 也解释了为什么要有
`tia capture` 与「索引落后」警告——**索引旧了不会报错，它只会悄悄少选几条**。

```bash
VENV=/path/to/.venv/Scripts/python.exe

# 1. 建索引（改了测试代码或新增测试文件后必须重跑，140–190s）
COVERAGE_FILE=.tia-coverage "$VENV" -m pytest -m "not ui" --cov=. --cov-context=test --cov-report=
"$VENV" -m qaplat.cli tia capture --coverage-data .tia-coverage   # 记下采集时的 HEAD

# 2. 看这次改动会影响哪些用例（只打印，不跑测试）
"$VENV" -m qaplat.cli tia select --base HEAD~1
"$VENV" -m qaplat.cli tia select --base HEAD~1 --json

# 3. 在门禁里用（默认关闭，必须显式开）
"$VENV" -m qaplat.cli gate --layer pr --select tia --base origin/main
```

`--select tia` **默认关闭**：不给这个参数时，行为与改造前逐字一致。CI 上也只挂在
`workflow_dispatch`（[`.github/workflows/tia.yml`](.github/workflows/tia.yml)）——进 PR 流水门
之前要先攒真实 diff 分布，看兜底率与误选率；漏跑不会报错，它只会让门禁绿着放行一次回归。

| 想深入 | 看这里 |
|---|---|
| 一条改动怎么变成一串 nodeid（逐文件、逐调用链） | [`docs/TIA完全入门.md`](docs/TIA完全入门.md) |
| 一页纸交付报告：数字与复现命令 | [`docs/tia-report.md`](docs/tia-report.md) |
| 契约、CLI 定义、明确不做什么 | [`docs/tia-plan.md`](docs/tia-plan.md) |
| 两轮独立核验记录与遗留项 | [`docs/tia-review.md`](docs/tia-review.md) |

> 下面从「接口 + UI + 业务全链路自动化测试框架」开始的内容**原样继承自上游**，其中的用例数
> （636 条）与覆盖率数字都是**上游口径**；本仓当前是 **712 条收集**（上游 636 + TIA 相关 76 条）。
> 两套数字并存不是笔误——改上游段落会把「这是 fork、上游测试代码原样继承」这件事抹掉。

---

# 接口 + UI + 业务全链路自动化测试框架

[![Auto Test](https://github.com/lefan17/fullstack-test-framework/actions/workflows/test.yml/badge.svg)](https://github.com/lefan17/fullstack-test-framework/actions/workflows/test.yml)
[![在线报表](https://img.shields.io/badge/%E5%9C%A8%E7%BA%BF%E6%8A%A5%E8%A1%A8-GitHub%20Pages-2ea44f)](https://lefan17.github.io/fullstack-test-framework/)

> 📄 **在线测试报表（GitHub Pages）**：<https://lefan17.github.io/fullstack-test-framework/>
> qaplat 平台层生成的 5 页自包含 HTML（最近轮次、接口覆盖矩阵、失败归因、契约漂移），
> 由 CI 的 `pages` job 在每次 push 到 `main` / 手动触发 / 夜间回归后重新生成并发布。

> 📊 **数字从哪来**：[`docs/evidence/`](docs/evidence/) 固化了本仓库关键指标的来源、
> 生成时间与复核命令。正文里出现的用例数、分层数字，都能在那里找到对应产物和复现方式。

基于 Python + pytest 的自动化测试框架，覆盖 **接口自动化**、**UI 自动化**、
**带本地 Mock 后端的业务全链路测试**（状态机、乐观锁、幂等、并发竞态、Decimal 金额精度）
与 **契约/缺陷探针测试**（自带一个故意带缺陷的登录服务），
支持数据驱动、JSON Schema 契约校验、Allure 报告和 GitHub Actions 持续集成。

> 本仓库的前身是已归档的 [`lefan17/software-testing-practice`](https://github.com/lefan17/software-testing-practice)
> （契约驱动的接口自动化测试实践：Flask 被测系统 + OpenAPI 契约 + 27 条登录接口用例），
> 它的内容已并入本仓库，此后只在这里维护。

> 🧭 **在作品集中的位置**：本仓库是这条测试 / 质量线的**起点**——先把接口、UI 与带本地 Mock
> 后端的业务全链路自动化做扎实。往下的两段是
> [perf-baseline-quality-gate](https://github.com/lefan17/perf-baseline-quality-gate)（性能与
> 稳定性基线 + 12 条 CI 门禁规则，回答"变慢了没有、门禁拦不拦得住"）与
> [agent-test-hub](https://github.com/lefan17/agent-test-hub)（LangGraph 编排的 Agent 自动测试，
> 把执行与缺陷归因交给 LLM，失败降级为确定性 MockBrain）：**接口自动化 → 性能与质量门禁 → Agent 自动测试**。

### 目录

- [技术栈](#技术栈)
- [分层设计](#分层设计)
- [快速开始](#快速开始)
- [用例覆盖](#用例覆盖)
- [用例设计方法](#用例设计方法)
- [设计要点（面试会问）](#设计要点面试会问)
- [数据隔离（自动化能不能长期活下去的关键）](#数据隔离自动化能不能长期活下去的关键)
- [当前实测结果](#当前实测结果)
- [性能基线（P95 回归）](#性能基线p95-回归)
- [容量压测（perf/）](#容量压测perf)
- [流量回放（批次 2，独立子项目）](#流量回放批次-2独立子项目)
- [持续集成](#持续集成)
- [常见问题](#常见问题)

> 🚪 **第一次来，建议先读 [`docs/初学者完全入门.md`](docs/初学者完全入门.md)**
> —— 它不讲「怎么用」，而是讲「这些文件是怎么连起来的」，并把一条用例从收集到清理的
> 全过程拆给你看。本 README 是**地图**，那篇是**走路的过程**。

### 深入文档

`docs/` 下的 Markdown 分两类，读之前先分清，能省不少时间。

**活文档** —— 描述当前代码怎么用，随代码一起更新：

- [`docs/初学者完全入门.md`](docs/初学者完全入门.md)：**给零基础的人看的**。
  不教怎么用，而是讲**这些文件彼此之间是怎么连起来的** —— 一条用例从被 pytest 发现、
  发 HTTP 请求、验数据库、到清理数据，中间经过了哪些文件、哪些函数、为什么非要拆成这么多层。
  含「改坏它，看谁变红」的动手实验（每个实验都标了实测结果）与一份代码阅读顺序。
- [`docs/platform-usage.md`](docs/platform-usage.md)：qaplat 平台层的**唯一使用手册**。
  `gate --layer smoke|pr|nightly|release` 四层分别什么时候跑、度量库里存了什么、
  包名为什么叫 `qaplat` 而不是 `platform`。本 README 只说平台层是什么，这份说怎么用。
- [`docs/platform-metrics-report.md`](docs/platform-metrics-report.md)：平台层实测报告。
  **是时点快照，不是当前值** —— 引用里面任何一个数字前，先看它的采集时间与基线 commit。
- [`docs/mixed-flow-usage.md`](docs/mixed-flow-usage.md)：混合链路（接口造数 → UI 走流程 →
  接口校验）的使用方式、架构与边界。契约冻结在 `FROZEN_MESWEB.md`，这份只讲怎么用、做到哪为止。
- [`docs/sql_practice.md`](docs/sql_practice.md)：SQL 数据校验练习。「业务返回 200」不等于
  「数据正确」这句话的手工版对照练习 —— 先用 SQL 自己想一遍，再看用例是怎么断言的。
- [`docs/test_report_template.md`](docs/test_report_template.md)：测试报告模板。跑完只是跑完，
  能说清结果才是交付；这份给的是结构。

**跨平台** —— [`Linux环境排查手册.md`](docs/Linux环境排查手册.md)：把三个仓库搬进 Linux 容器跑一遍时踩到的六个坑
（其中一个是只在 Linux 暴露的真 bug：[issue #24](https://github.com/lefan17/fullstack-test-framework/issues/24)），
外加「同一份代码在 Windows 与 Linux 上数字方向相反」的实测对照。

**历史交接（`wave\*-handoff`）** —— 分批交付时写给整合者的交接记录，里面写死了当时的基线
commit 与工作副本路径。保留是为了追溯「当时为什么这么做」，**不是当前的使用文档**，
照着它敲命令会撞上已经不存在的路径：

- [`wave1-auth-handoff.md`](docs/wave1-auth-handoff.md)：密钥外置与 token 生命周期（P0-1 / P0-3）
- [`wave2-flaky-handoff.md`](docs/wave2-flaky-handoff.md)：flaky 治理（P1-1）
- [`wave2-ratelimit-handoff.md`](docs/wave2-ratelimit-handoff.md)：重试尊重 `Retry-After` 与有界退避（P1-2）
- [`wave3-data-lifecycle-handoff.md`](docs/wave3-data-lifecycle-handoff.md)：数据生命周期（P2-5）
- [`wave3-gate-policy-handoff.md`](docs/wave3-gate-policy-handoff.md)：分层门禁策略显式化（P2-4）
- [`wave3-perf-baseline-handoff.md`](docs/wave3-perf-baseline-handoff.md)：性能基线回归（P2-2）

另有 4 篇同性质的交接已在正文别处引用（`wave1-contract-handoff.md`、`wave2-coverage-handoff.md`、
`wave3-load-handoff.md`、`wave3-zentao-handoff.md`），同一性质，这里不再重复列。

## 30 秒速览

| 问题 | 答案 |
|---|---|
| **测什么** | 一个带本地 Mock 后端的物料管理系统（`mock_server/`）：接口契约、UI 流程、业务状态机，以及三者的混合链路 |
| **有多少用例** | **636** 条（`pytest --collect-only -q`）= 49 接口 + 34 UI + 64 MES + 29 登录 + 224 契约 + 236 平台层自测 |
| **怎么跑** | `pip install -r requirements.txt` → `pytest -m smoke -q`（核心链路）→ `pytest -q`（全量） |
| **看什么** | [在线报表](https://lefan17.github.io/fullstack-test-framework/)：最近轮次 / 接口覆盖矩阵 / 失败归因 / 契约漂移 |
| **凭什么信它不是摆设** | 契约缺陷用 `xfail(strict=True)` 钉住（DEF-01~04 共 **11** 个 nodeid）——修好即 XPASS 变红，缺陷单会自己找上门；登录缺陷反过来，用**绿用例断言「当前确实是这个行为」**：[`docs/defects.md`](docs/defects.md) |
| **数字哪来的** | [`docs/evidence/summary.json`](docs/evidence/summary.json)：每条指标都带来源、生成时间与复核命令 |
| **CI** | 9 个 job；其中 UI 组**不阻塞**（打公网 saucedemo，失败归因不可靠），理由与退出条件见 [`docs/质量门禁策略.md`](docs/质量门禁策略.md) |
| **换个系统还跑得动吗** | 能：容器里 `pytest -m "not ui"` → **591 passed / 11 xfailed**，与 Windows 逐条一致。过程中修掉一条只在 Linux 暴露的解释器误判（[`docs/Linux环境排查手册.md`](docs/Linux环境排查手册.md)） |

> 下面各节与 `docs/` 里是完整论证——设计取舍、踩坑记录、面试追问预案。
> 这一节只回答三件事：这是什么、怎么跑、凭什么信。

![在线报表：累计轮次、用例级记录 368、各层概况、接口覆盖 24/29、契约漂移比对](docs/images/report-dashboard.png)

## 技术栈

| 用途 | 选型 |
|---|---|
| 测试框架 | pytest 9.1 + pytest-xdist（并行）+ pytest-cov 6.3（覆盖率门禁，**卡在 6.x**） |
| 接口测试 | requests + jsonschema + PyYAML |
| UI 测试 | Playwright 1.48（sync API） |
| Mock 后端 | FastAPI + SQLite（WAL 模式），仅测试用 |
| 被测服务 | FastAPI（`login_app/`，故意带 6 个已知缺陷，用于契约与探针用例） |
| 报告 | Allure |
| 代码规范 | ruff（配置见 `ruff.toml`，CI 里强制执行） |
| 类型检查 | mypy（配置见 `pyproject.toml`，CI 里强制执行） |
| 提交规范 | Conventional Commits（`scripts/check_commit_msg.py`，不依赖 Node） |
| 依赖管理 | `requirements.txt`（直接依赖）+ `requirements.lock`（完整依赖树，pip-tools 生成） |
| 安全扫描 | Dependabot（pip + github-actions 每周）+ CI 里的 pip-audit |
| 容器化 | Dockerfile + docker-compose.yml（两个被测服务） |
| 环境分层 | `TEST_ENV` 选择 dev / staging / prod（`common/config.py`） |
| 持续集成 | GitHub Actions（9 个 job，含每周定时夜间回归、非阻塞容量压测与在线报表发布） |

## 分层设计

```
apis/         接口封装层   —— BaseApi 统一超时/重试/日志/Allure 步骤，
                            UserApi / PostApi / MesApi 定义业务接口
testcases/    用例层       —— 只写业务断言，不出现 requests 和 selector
  api/        接口用例（打公网练习站 jsonplaceholder）
  ui/         UI 用例（打 saucedemo）
  mes/        业务全链路用例（自己起本地 Mock 服务 + 独立 SQLite）
  login/      契约与缺陷探针用例（自己起本地 login_app，无需公网）
ui/pages/     Page Object  —— 元素定位与页面操作
mock_server/  Mock 后端    —— 工单状态机 / 库存 / 权限 / 幂等，仅测试用
login_app/    被测服务     —— 登录接口，故意带 6 个已知缺陷（见下）
data/         数据层       —— 配置档案（config.yaml / config.staging.yaml /
                            config.prod.yaml）、users.json 测试数据、schemas.py 契约
common/       工具层       —— YAML 读取、统一断言封装、环境分层加载（config.py）
scripts/      工程化脚本   —— 提交信息规范校验（check_commit_msg.py）
qaplat/       平台层       —— 契约驱动的测试平台自身（门禁分层 / 度量库 / 覆盖矩阵 / 报表）
mes_web/      本地 Demo Web —— UI 混合链路用的被测站点（故意简单，不依赖公网）
traffic-replay/ 流量回放（批次 2）—— 独立子项目，自带 pytest.ini 与 conftest.py
conftest.py   全局 fixture —— 配置、接口对象、报告钩子
```

> **为什么源码包叫 `apis/` 而不是 `api/`**：测试目录里有 `testcases/api/`。
> 如果源码包也叫 `api`，pytest 的路径插入规则会让 `testcases/api/__init__.py`
> 抢先注册 `api` 这个名字，于是 `from api import MesApi` 会**静默**解析到测试目录，
> 只报一句看不懂的 `ImportError: cannot import name 'MesApi' from 'api'`。
> 源码包与测试目录不同名，是避免这类事故最省事的办法。

**为什么这么分层**：接口地址变了只改 `data/config.yaml`；接口字段变了只改 `data/schemas.py`；
页面元素变了只改 `ui/pages/`；用例本身稳定不动。这是自动化框架能长期维护的前提。

### 一条链路看全仓

上面按目录讲了分层，下面这张图画的是**层与层之间真实的调用关系** —— 每组用例打的是谁、
qaplat 的几个模块谁依赖谁、CI 里哪两个 job 会碰它：

```mermaid
flowchart TB
    subgraph cases["① 用例层 testcases/（pytest）"]
        direction LR
        api["api/ 接口用例"]
        ui["ui/ UI 用例（含混合链路闭环）"]
        mes["mes/ 业务全链路"]
        contract["contract/ 契约属性"]
        login["login/ 登录契约与缺陷探针"]
        platform["platform/ 平台层自测"]
    end

    subgraph sut["② 被测对象"]
        direction LR
        pub["公网练习站 jsonplaceholder / saucedemo"]
        mock["mock_server 本地 Mock 后端 + 独立 SQLite"]
        web["mes_web 本地 Demo Web（服务端转发）"]
        lapp["login_app 本地被测服务（6 个已知缺陷）"]
    end

    subgraph qa["③ qaplat 平台层（纯标准库，无第三方依赖）"]
        direction LR
        dump["_dump.py 采集插件"]
        reg["registry 覆盖矩阵"]
        met["metrics 度量库 qaplat.db"]
        gatel["gate 分层门禁"]
        rep["report 5 页自包含 HTML"]
    end

    subgraph ci["④ GitHub Actions（9 个 job）"]
        direction LR
        others["lint / typecheck / security / pre-commit / commit-convention / perf"]
        tj["test 契约漂移 → 六组用例 → flaky 账本 → Allure"]
        pj["pages 生成报表 → 发布"]
    end

    gh["gh-pages 分支 · lefan17.github.io/fullstack-test-framework"]

    api --> pub
    ui --> pub
    ui --> web
    mes --> mock
    mes --> web
    web --> mock
    contract --> mock
    login --> lapp
    platform -.->|"被测对象就是平台自己"| qa

    tj -.->|"pytest 跑这些 marker"| cases
    dump -.->|"dump 文件"| reg
    dump -.->|"dump 文件"| gatel
    gatel -->|"每次 gate 落库"| met
    reg -->|"覆盖矩阵"| rep
    met -->|"轮次 / 归因 / 漂移历史"| rep

    pj -->|"registry build"| reg
    pj -->|"drift record"| met
    pj -->|"report --out"| rep
    rep -->|"force push 整目录"| gh
```

看这张图要看出四件事：

1. **只有两组用例打公网**：`api/` 与 `ui/` 指向 `data/config.yaml` 里的公网练习站；`mes/`、
   `login/`、`contract/` 各自拉起本地服务（`mock_server` / `login_app` / `mes_web`），
   所以除 api、ui 之外整套用例离线可跑 —— 这也是它们能当阻塞门禁、而 ui 只能标
   `continue-on-error` 的根本原因。
2. **`platform/` 的箭头指回 `qaplat/` 自己**：平台层的被测对象就是它本身，所以它单独占一个
   marker，坏了会在 `test` job 里直接拦下来。
3. **qaplat 内部是单向的**：`_dump.py` 是唯一采集入口（`-p qaplat._dump`），`registry` 与
   `gate` 都读它写出的 dump 文件；`gate` 把每轮结果落进 `metrics`，`report` 再从
   `registry` + `metrics` 两处读 —— 报表里的「覆盖」与「轮次」是两条独立的数据线。
4. **CI 里只有 `pages` job 调用平台层 CLI**（`registry build` / `drift record` / `report`），
   `test` job 只跑 `-m platform` 自测；所以 `gh-pages` 上的报表是每次 push 现场生成的，
   不是仓库里存着的历史产物。

## 快速开始

```powershell
# 1. 建虚拟环境并激活（Windows PowerShell）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 装依赖（国内建议加 -i 用清华源，见下方"国内环境"）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 装浏览器（UI 用例需要，只装 chromium 约 140MB）
playwright install chromium
```

> **想装出与 CI 完全一致的依赖树，用 `requirements.lock` 而不是 `requirements.txt`**：
>
> ```powershell
> pip install -r requirements.lock
> ```
>
> 两个文件的分工：`requirements.txt` 是**人维护的**直接依赖（16 个，全部 `==` 钉死版本），
> 它是唯一真值来源；`requirements.lock` 是 `pip-compile` 生成的**完整传递依赖树**
> （188 行 / 61 个 pin，含 anyio / certifi / urllib3 / greenlet 等几十个间接包）。
> 只锁直接依赖的话，传递依赖会随环境漂移——本地和 CI 装的不是同一棵树，
> 于是"本地绿、CI 红"就会以随机事件的形式出现。
> 改了 `requirements.txt` 之后要重新生成：
>
> ```powershell
> $env:PIP_TOOLS_CACHE_DIR = "$PWD\.piptools-cache"   # 受限环境下家目录不可写
> python -m piptools compile requirements.txt --output-file requirements.lock --quiet --strip-extras
> ```

> **一条命令搞定全部**：直接跑 `.\setup.ps1`，脚本会自动建环境、装依赖（含国内镜像）、
> 装浏览器并跑一遍接口用例验证。**但注意**：依赖必须先装好，
> **国内环境**下 PyPI 和 Playwright CDN 都需要换镜像，否则会"看起来卡死"：
>
> ```powershell
> # PyPI：清华源（实测同一份依赖：清华源 50 秒 / 官方源 20 分钟无进展）
> pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
> # 想永久生效：把 pip.ini.example 复制到 %APPDATA%\pip\pip.ini
>
> # Playwright 浏览器：用 npmmirror（官方 cdn.playwright.dev 在国内常被 TLS 阻断）
> $env:PLAYWRIGHT_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/playwright"
> playwright install chromium
> ```

> **第二条路（跨平台）**：`make install && make test`。仓库根的 `Makefile` 把本节这些命令
> 收拢成 target（`make help` 列全），CI 里跑的也是同样两条命令。两处差别要知道：
> `make install` 只装 Python 依赖、不代建 `.venv` 也不代下 chromium（只提示命令）；
> `make test` 跑的是 `-m "not ui"`，比上面 `setup.ps1` 那遍 `-m api` 更宽。
> Linux / macOS 上没有 `setup.ps1` 可跑，就走这条。


### 运行测试

```powershell
pytest                      # 全部用例
pytest -m api               # 只跑接口（快，日常开发用这个）
pytest -m mes               # 只跑 MES 业务链路（本地 Mock 后端 + SQLite）
pytest -m contract          # 只跑契约驱动用例（自动导出 spec 的漂移门禁 + 属性测试）
pytest -m ui                # 只跑 UI（需要浏览器）
pytest -m login             # 只跑登录契约用例（本地服务，1.3 秒，无需公网）
pytest -m smoke             # 只跑冒烟
pytest -m flaky             # 允许重跑吸收抖动的用例（当前 = testcases/api 打公网的 33 条）
pytest -m quarantine        # 隔离区：明知红/不稳定但暂不删除的用例（当前 0 条）
pytest -m platform          # 平台层自测（qaplat 自己：门禁分层/度量库/覆盖矩阵/报表 CLI）
pytest -n 4 --dist loadfile # 4 进程并行（见下方说明）
pytest -k "login"           # 按关键字筛选用例
pytest --lf                 # 只重跑上次失败的用例

# 流量回放（批次 2）是独立子项目：自带 pytest.ini，不在根 marker 体系里，要进它自己的目录跑
cd traffic-replay; pytest   # 173 条

# 带覆盖率门禁（CI 用的就是这套组合）
pytest -m api -n 4 --dist loadfile --cov=. --cov-report=term --cov-report=json:.coverage.json --cov-report= --cov-fail-under=54
python scripts/check_coverage.py --report .coverage.json   # 第二道：per-file 55%
```

> **关于 `flaky` 与 `quarantine` 两个标记（波 2）**：`testcases/api/` 那 33 条打公网
> `jsonplaceholder.typicode.com` 的用例全部挂了 `flaky`。它们的 429/5xx、连接失败、
> 超时、第三方返回非 JSON、响应过慢会被**重跑一次吸收**（`only_rerun` 收窄到 6 条抖动
> 指纹）；而**契约类断言红了会当场硬失败，一次都不重跑**——这是「重跑」与「掩盖」的分界。
> `addopts` 里刻意**不加** `--reruns`：全局重跑会把真回归一起吞掉。
>
> 重跑过的用例不会悄悄变绿：`-raR` 里的 `R` 让插件打印 `RERUN` 与
> `rerun test summary info`，根 `conftest.py` 另外把「重跑才通过 / 重跑仍失败 / 被隔离」
> 三类写进 `reports/flaky-governance.json`（junit-xml 本身不认 rerun，只能自己记）。
> `python scripts/flaky_report.py --markdown` 把账本转成两份周报清单——CI 的 step summary
> 里贴的就是它。`quarantine` 当前 0 条；主门禁里被隔离的用例是 **deselect 而不是判为通过**，
> 且清单会逐条打印（静默丢弃是另一种死亡方式）。
>
> 这两个标签的**完整处置策略**（谁在什么时候跑、红了找谁、退出条件）写在
> [`docs/质量门禁策略.md`](docs/质量门禁策略.md) 第五、六节；那份文档与 workflow 的一致性
> 由 `python scripts/check_gate_policy.py` 机器校验。

### 切换环境档案

配置不再只有一份。用 `TEST_ENV` 选档案，**不设置时行为与以前完全一致**（读 `data/config.yaml`）：

```powershell
pytest -m api                                # dev（默认）→ data/config.yaml
$env:TEST_ENV = "staging"; pytest -m api     # → data/config.staging.yaml
$env:TEST_ENV = "prod";    pytest -m api     # → data/config.prod.yaml
```

加载器是 `common/config.py` 的 `get_config()`（`conftest.py` 用它取配置）。
三份档案的差异只有 `base.api` / `base.ui` 两个 host：

| 档案 | `base.api` | `base.ui` |
|---|---|---|
| dev（默认） | `https://jsonplaceholder.typicode.com` | `https://www.saucedemo.com` |
| staging | `https://api.staging.invalid` | `https://web.staging.invalid` |
| prod | `https://api.prod.invalid` | `https://web.prod.invalid` |

> staging / prod 的 host 用 RFC 2606 保留后缀 `.invalid` 是**故意的**：
> 占位符如果写成看起来像真的域名，某天就会有人真的把测试流量打上去。
> `.invalid` 是保证解析失败的，宁可连不上、也不要静默打到真实站点。
> `data/config.prod.yaml` 里的测试账号也**故意留空**——
> 生产环境不该存在测试账号，应该用只读账号从 CI secret 注入。

> **关于 `-n` 并行**：加 `--dist loadfile`（按文件分发），不要用默认的按用例分发。
> 原因是默认分发会把同一个文件里的用例拆到不同进程，fixture 的 session 级复用
> 就失效了（UI 那边每个进程各启一个浏览器），而且接口用例分散到多个进程后会
> **同时**打公网接口，更容易触发限流——并行度越高越容易出假失败。
> 在 CI 或内网环境上跑 `-n auto` 是合理的；对着公共练习站跑并行收益有限。
>
> **但这里有一个不只是"收益有限"的问题——默认分发在本仓库曾经是坏的，实测会失败：**
> （改造前，30 条 MES 用例时）`pytest -m mes -n 4 --dist load` → **`30 passed, 1 error`，退出码 1**。
> 报错在 teardown 阶段会话级数据隔离断言（当时在 `testcases/mes/conftest.py:129`，
> 波 3 给 `factory` teardown 补兜底后移到 `testcases/mes/conftest.py:158`）：
> `AssertionError: 会话结束时数据没有回到基线`。
> 根因不是并发竞态，是**分发策略与 fixture 作用域错配**：
> 数据隔离基线（`baseline_counts`）与收尾回收断言都是 `scope="session"` 的，
> 按用例分发会把同一个文件的用例拆到不同 worker，
> 于是"A worker 的收尾快照"对不上"B worker 造的脏数据"。
> 按文件分发（`loadfile`）让一个文件整体落在同一个 worker，作用域闭合，因而干净。
>
> ⚠️ **订正一条旧登记**：早前这里写过「那个 1 error 在现在的用例集下复现不出来了」。
> 2026-10-17 复测是 **1/4 通过**（三次 `64 passed, 1 error`、退出码 1），
> 所以那句结论作废——不是消失了，是那四次运气好。
> 最小复现：`pytest -k test_permission_matrix -n 7 --dist load` →
> `ERROR at teardown of ...test_permission_matrix[生产领料]`，
> 断言原文是「库存可用量合计: 基线 3270.000 -> 现在 3268.000（差 -2.000）」
> 「已发料合计: 基线 0.000 -> 现在 2.000（差 +2.000）」。
> 根因也坐实了，而且正是上面那段的机制：`PERMISSION_MATRIX` 里「生产领料」扣 2.000、
> 「退料」补 2.000，两行**靠彼此抵消**才回到基线；`--dist load` 把它们分到两个 worker，
> 而 `mes_server` 是 session 作用域 = 每个 worker 各起一个独立 mock server、各建一份 SQLite，
> 于是两边都不自洽。注意行数一条不差，只有库存两个合计偏——所以别用"脏数据没清"去解释它。
>
> 所以 CI 里 `-m api` 那一步开 `-n 4 --dist loadfile`（三次不同批次实测：
> 串行 75.55–79.11s → 并行 38.00s（约 2.0–2.1×）、串行 60.44–61.85s → 并行 25.75s、
> 串行 71.70–74.03s → 并行 31.63s（约 2.3×）；**三轮的绝对值不可比**，机器负载与公网接口
> 都在变，能比的只有同一轮内的比值，它稳定在 2.0–2.4×），
> 而 `-m mes` / `-m login` / `-m ui` **刻意不开**，理由见"持续集成"一节。

### 口令与密钥

口令**不再硬编码在用例里**，全部走 `common/secrets.py` 这一个来源：

```powershell
# 1. 本地：复制模板后填值（.env 已被 .gitignore 忽略，不会误提交）
Copy-Item .env.example .env

# 2. 或者用环境变量（优先级高于 .env）
$env:MES_PASSWORD_ADMIN = "我的本地口令"
pytest -m mes
```

三条约定：

1. **种子口令与用例登录口令同源**。`mock_server/db.py` 建库时用哪个口令，
   用例就用哪个口令——两边取的都是 `common/secrets.py`。所以不会出现
   "种子入库 A、用例拿 B 登录"这种查半天的 401。
2. **配置文件里可以写 `${VAR}` 引用**，读取时由 `common/config.py` 的
   `expand_placeholders()` 展开（`data/config.yaml` 里的 `${UI_PASSWORD}` 就是这么来的）。
   真实环境的口令只从 `.env` 或 CI secret 注入，不写进入库的 yaml。
3. **已存在的环境变量永远优先**，`load_dotenv()` 不覆盖。所以 CI 上注入的 secret
   不会被仓库里的 `.env` 顶掉；而**没配 secret 时 GitHub 求值成空串**，
   `common/secrets.py` 判的是 `if value:`，空串会回落到"本地 Mock 默认口令"——
   即行为与完全不注入时逐字节一致。fork 的 PR 拿不到 secret，流水线照样跑得通。

不设任何变量时的默认口令与改造前**逐字节相同**，所以老文档里的登录示例都还有效。

### 生成报告

```powershell
# Allure 报告（需要先装 allure 命令行工具）
pytest --alluredir=allure-results
allure serve allure-results

# 轻量 HTML 报告，不需要额外装东西
pytest --html=report.html --self-contained-html
```

## 用例覆盖

| 模块 | 用例数 | 覆盖内容 |
|---|---|---|
| 接口-用户 | 23 | 列表/详情/创建/更新/过滤，状态码、Schema 契约、字段类型、唯一性、邮箱格式、404 异常分支（`test_user_create.py` 12 + `test_user_list.py` 11） |
| 接口-文章 | 8 | 创建回显、数据驱动、关联过滤、分页参数、更新 |
| 接口-响应时间（抖动类） | 2 | **单次**请求的响应时间阈值（`MAX_RESPONSE_MS`）、请求头校验。打的是**公网** `jsonplaceholder`，测的是链路而非被测服务，**不是性能基线**、与基线无关，仅作连通性冒烟。详见下方「性能基线」 |
| 接口-性能基线（P95 回归） | 2 | 波 3 新增：对**本地 `mock_server`** 的 6 个只读端点各采 100 次（丢弃前 10 次预热），用 nearest-rank 算 P50/P95/P99 落盘成 `testcases/api/perf_baseline.json`；回归时按 `基线 P95 × 3.0 + 10ms` 判红，显著变快则提示可更新基线。基线可用 `UPDATE_PERF_BASELINE=1` 一键重采并打印旧值→新值 |
| 接口-重试退避 | 9 | 波 2 新增：`Retry-After` 的 delta-seconds 与 HTTP-date 两种格式、超上限截断、无头回落指数退避，以及 429 + `Retry-After` 的端到端路径（起本地 `http.server`，请求真的过 socket） |
| flaky 治理元测试 | 5 | 波 2 新增：开真 pytest 子进程验证「标了 flaky 的用例首跑失败会被重跑」「真契约断言红了不被重跑吞掉」「隔离区用例退出主门禁且清单被打印」「隔离区能单独跑」 |
| UI-登录 | 6 | 登录成功、4 种失败场景（参数化）、退出登录 |
| UI-商品/购物车 | 8 | 列表渲染、加购角标、多件参数化、购物车内容一致性、标题 |
| 业务-MES 全链路 | 64 | 7 条冒烟（全链路 PENDING→SHIPPED、探针、登录、重复单号、小数精度、金额分位、额外字段）+ 23 条业务（状态机守卫分支、乐观锁并发、幂等键回放/冲突/失败释放/超长拒绝、并发领料不超卖、流水账实一致、权限矩阵双向断言、令牌校验）+ 24 条认证与密钥（9 条口令来源、15 条令牌生命周期）+ 1 条覆盖率采集元测试（波 2 新增）+ 9 条数据生命周期（波 3 新增：子进程故障注入 ×2、TTL 清理前后行数对照、白名单保护、幂等、模糊项只报告、命名判据、时间戳容错、`verify_clean` 残留复核） |
| 契约-自动导出 | 224 | 从 FastAPI 自动导出 `docs/openapi.json`（29 operation）并做漂移门禁；schemathesis 属性测试按 spec 生成请求 + 手写契约与自动导出的逐条对照。扫出 6 类真实契约缺陷：DEF-05/06 已修并由正常用例固化，DEF-01~04 共 11 条 `xfail(strict=True)` 钉住 |
| 契约-登录 | 29 | 用 5 种设计方法推出的 30 条用例（等价类/边界值/判定表/场景法/错误推测），钉住被测服务当前行为并标注 6 个已知缺陷（FIND-01~06） |
| UI-Demo Web / 混合链路 | 20 | 本地 Demo Web 的页面与接口断言 15 条（`test_mes_web_app.py`，不起浏览器）+ 页面操作与接口校验的混合链路 5 条（`test_mes_mixed_flow.py`，用浏览器） |
| 平台层（qaplat 自测） | 236 | 门禁分层表达式、度量库、覆盖矩阵 registry、报表 CLI、采集插件分类、跨盘 nodeid 补前缀、per-file 覆盖率脚本契约等平台自身的用例（`testcases/platform/`） |
| 数据隔离 | 会话级 1 项 | 会话结束时各表行数与关键数值列合计（库存可用/占用/已发料、流水数量与账面余额）必须回到基线，且流水余额链逐笔自洽（见下方"数据隔离"一节） |

业务全链路那些用例**不依赖公网**：`testcases/mes/` 的 session 级 fixture 会自己
拉起一个本地 FastAPI + 独立 SQLite（端口由系统分配、库在 pytest 临时目录里），
跑完自动 kill。所以它既能离线跑，也不会污染开发者手工起的那个实例。

登录契约用例同样**不依赖公网**：`testcases/login/` 的 session 级 fixture 起一个
本地 `login_app`（端口由系统分配），跑完自动 kill。

契约用例里有两条**方向相反**的路子，它们测的不是同一件事，所以都在：

- **手写契约**（`契约-登录` 那 29 条）：人读契约文档、用等价类/边界值/判定表推出用例。
  强在"知道业务该怎么用"，能测语义（比如"密码错了要提示具体文案"）。
- **自动导出 + 属性驱动**（`契约-自动导出` 那 224 条）：`docs/openapi.json` 由
  `scripts/export_openapi.py` 从 FastAPI 应用**自动导出**，再用 schemathesis 按 spec
  生成请求、断言"响应符合 spec 声明"。强在**不知道业务也能扫**——
  它靠的是"spec 说什么就该是什么"。

  这不是理论优势。本仓库的 **DEF-05**（`{"qty": "INFINITY"}` 让服务回 500 而不是 422）
  就是它扫出来的：那句请求手写用例基本不可能想到，因为它既不是合法数量也不是典型非法值，
  而是"语法上是个字符串、语义上是无穷大"的**畸形输入**。
  同一类还扫出 **DEF-06**（`page=461168601842738792` 让 `(page-1)*page_size` 溢出 int64，
  绑进 SQLite 抛 `OverflowError` → 500）——spec 里 `page` 声明的类型就是 integer 且**没有上界**，
  所以这几个请求完全合法，是服务自己没兜住。

  **这两条已经修好，不再是 `xfail`**：`mock_server/quantity.py` 三个解析函数把 `quantize`
  收进 `try` 并显式挡掉非有限值（`INFINITY` / `NaN` / `1e999999` → 422），
  `mock_server/deps.py` 在拼 OFFSET 之前用 `MAX_SQLITE_INTEGER` 挡住越界页码（→ 400
  `PAGINATION_OUT_OF_RANGE`）。现在由正常用例断言正确行为，而不是钉着旧行为。

  其余契约缺陷（DEF-01~04，共 11 条）**不修，用 `xfail(strict=True)` 钉住**：
  一旦有人真修好了，用例会从 xfail 变成 XPASS，strict 模式下立刻红——缺陷单会自己找上门。

  > 完整台账（12 条缺陷的编号、级别、钉住/回归用例 nodeid、与禅道字段的映射）见
  > [`docs/defects.md`](defects.md)。

  它扫出的还有一类"文档与实现不一致"：`POST /api/v1/auth/login` 的 description 写着
  "同时接受 json 与 x-www-form-urlencoded"，实际发正确凭据的表单会 422、空表单会 500
  （服务端日志：`The python-multipart library must be installed to use form parsing.`）。
  契约声称的能力从来没被验证过——**这是只有"拿 spec 当输入去发请求"才会暴露的问题**。

  > **属性测试的绿灯是"这么多样本没找到反例"，不是证明。** 而且它必须**确定性**才有门禁价值：
  > 默认 8 个样例/operation，`CONTRACT_MAX_EXAMPLES=50` 放大约 2.5 倍耗时（19s → 49s）。
  > 早期版本正因为把 hypothesis 样例数据库（`.hypothesis/`）当环境噪声，出现过
  > "清空后绿、留着样本就红"——同一份代码红在不同端点上（`materials` / `work-orders` 的
  > `page=461168601842738792`，以及 `inventory/{issue,reverse}` 的 404）。
  > 现在 `-m contract` 在任何样本量下都恒定绿，`.hypothesis/` 也已显式加进 `.gitignore`。

## 用例设计方法

`testcases/login/` 的 30 条用例不是"想一条写一条"，而是用 5 种设计方法推出来的，
每条用例在 `docs/login-test-cases.md` 里都标了来源方法：

| 方法 | 一句话解释 | 覆盖编号 |
|---|---|---|
| 等价类划分 | 输入分为有效/无效类，每类取代表值 | LC-01~LC-13 |
| 边界值分析 | 测边界上/内/外的值 | LC-15~LC-21 |
| 判定表 | 多条件组合，覆盖所有分支 | LC-22~LC-25 |
| 场景法 | 按业务流程/协议层走 | LC-26~LC-29 |
| 错误推测 | 凭经验猜容易出错的地方 | LC-20/21/30 |

**等价类和边界值必须一起用**：等价类管"每类取一个代表"，但管不住边界——
密码 `123456` 和 `1234567` 属于同一个等价类（都错），却是不同的边界。
只做等价类会漏边界，只做边界值覆盖不了类别。

上面五种方法管的是**单点输入怎么选**。跨层的链路用例靠的是**场景法**——
按业务流程把整条路走完，而不是只验一个接口的返回码。下面这条就是典型：

![混合链路用例截图：API 造数 → UI 审批 → API 校验](docs/images/mixed-flow-closed-loop.png)

`testcases/ui/test_mes_mixed_flow.py` 里的
`test_api_seed_ui_approve_api_verify_should_close_the_loop` 做的是一个三段闭环：
**接口造数 → UI 操作 → 接口校验**。先用接口建一条 `PENDING` 工单，在真实浏览器里从登录页
进去点「审批」，最后**回到接口**确认这条工单真的变成了 `APPROVED`。
中间那次 UI 操作不来自证——截图里那一行 `WO-MIX-…` 的 `APPROVED` 状态是接口侧独立确认过的，
连"点完审批按钮后它该消失"都是分别断言的。外网练习站只有别人家写死的商品列表，
UI 与接口操作不到同一份数据、闭环是断的，所以这条打的是自带 Mock 后端的 Demo Web。

### 缺陷清单与用例的分工

![缺陷台账：12 条缺陷的严重级、钉住用例与禅道字段映射（docs/defects.md 渲染）](docs/images/defects-ledger.jpg)

被测服务 `login_app/` 是**故意带 6 个缺陷**的，而用例**断言的是"当前行为就是这样"**
（`assert r.status_code == 200`，注释标 `FIND-01`），**不是**"期望它被修好"
（`assert r.status_code == 400`）。理由：

> 期望式的断言会常年红着，团队很快学会"这几条是已知失败，忽略即可"，
> 然后**真的失败也没人看了**。钉住当前行为则用例是绿的，一旦有人修好、
> 或者改坏了，它立刻报警。缺陷本身由缺陷清单跟踪，用例当"行为变化探测器"。

| 编号 | 级别 | 发现点 |
|---|---|---|
| FIND-01 | 中 | 无用户存在性校验：任意用户名 + 正确密码都能登录 |
| FIND-02 | 低 | 契约偏差（宽松）：额外接受表单提交，契约只定义了 JSON |
| FIND-03 | 低 | username trim 而 password 不 trim（处理不对称） |
| FIND-04 | 低 | 无长度限制：1000 字符的用户名/密码均可提交 |
| FIND-05 | 中 | 用户名原样回显：前端未转义则存在 XSS 风险 |
| FIND-06 | 中 | token 为固定值 `demo-token-123`：无签发时间、无过期 |

设计与契约细节见 `docs/login-test-cases.md`、`docs/login-contract.md`。

### 用例编号与禅道上报的起点

**编号目前只有 login 一个套件有，不是全仓都有。** `LC-01`~`LC-30` 与
`docs/login-test-cases.md` 的 30 行表格一一对应；其余套件（`api`/`contract`/`mes`/`ui`）
一条编号都没有——这个缺口本身就是"编号体系成熟度"的证据，所以它被显式统计出来
而不是被含糊掉。`python scripts/case_index.py` 实跑（产物 `reports/case-index.json`）：

| 口径 | 实测数字 |
|---|---|
| 已登记为用例编号的前缀 | `LC`（30 个编号 / 17 个函数 / 27 个 nodeid） |
| 一条 nodeid 承载多个编号 | `test_lc22_to_lc25_decision_table` → LC-22、LC-23、LC-24、LC-25 |
| 未解析（抽到编号却找不到 nodeid） | 0 |
| 无编号 nodeid（装全依赖 + 两条线全部合入） | **609**：contract 224、platform **236**、mes 64、api 49、ui 34、login 2 |

> **无编号那一行的数字随收集范围变，写死会过期——三组数字都对，差的是"跑在哪个树"。**
> 同一台机器上：依赖没装齐时（`test_schemathesis_properties.py` 收集报
> `ModuleNotFoundError: No module named 'schemathesis'`）是 181 条收集 / **154** 条无编号
> （contract 36、mes 55、api 47、ui 14、login 2）；
> ~~在本波工作副本里设 `PYTHONPATH` 指向 `pylibs` 后是 369 条收集 / **342** 条无编号；~~
> （**2026-10 订正**：`pylibs` 这条前置已随 schemathesis 3.39.16 → 4.31.1 迁移消失——它现在
> 是 `requirements.txt` 里的普通一行、装进 `.venv`，`PYTHONPATH` 不再需要。这一组数字只作历史记录。）
> 合入波 3 全部改动（多出 `test_data_lifecycle.py`、`test_perf_baseline.py`）后是
> 380 条收集 / **353** 条无编号（contract 224、mes 64、api 49、ui 14、login 2）；
> 再合入 qaplat 与 mes_web 两条线后是 499 条收集 / **472** 条无编号；
> 本轮再给 qaplat 补 6 条自测（error 预算 3 条 + 覆盖矩阵 3 条）后是 **505** 条收集 / **478** 条无编号；
> 其后又给 `collect_cases` 补 2 条采集失败判据自测，是 **507** 条收集 / **480** 条无编号；
> 再往后合入 CI 加固（`windows` job 与 `gate --layer pr` 的真跑用例）后是 **554** 条收集 / **527** 条无编号；
> 最后给 `qaplat/_dump.py` 的采集分类分支补 17 条自测（新增 `test_dump_plugin.py`，一个分支一个用例）后是
> **571** 条收集 / **544** 条无编号（contract 224、platform **171**、mes 64、api 49、ui 34、login 2）；
> 再给两条 pytest 采集通道补 4 条 `--confcutdir` 自测（含一条真开 4 线程反复建删临时目录的用例）后是
> **575** 条收集 / **548** 条无编号（contract 224、platform **175**、mes 64、api 49、ui 34、login 2）；
> 最后补 2 条钉住「`--confcutdir` 必须落在 `--` 之前」的用例（那是一条真会被 `--` 透传踩到的回归）后是
> **577** 条收集 / **550** 条无编号（contract 224、platform **177**、mes 64、api 49、ui 34、login 2）；
> 再给跨盘 nodeid 补前缀这处修复补 4 条自测后是
> **581** 条收集 / **554** 条无编号（contract 224、platform **181**、mes 64、api 49、ui 34、login 2）；
> 再给 per-file 覆盖率脚本补 37 条契约用例（新增 `test_check_coverage_script.py`，一个分支一个用例）后是
> **618** 条收集 / **591** 条无编号（contract 224、platform **218**、mes 64、api 49、ui 34、login 2）；
> 最后给 `perf/run_load.py` 的两处传输层缺陷补 12 条平台用例（连接失败要如实返回、
> 代理不能劫持本机请求）后是
> **636** 条收集 / **609** 条无编号（contract 224、platform **236**、mes 64、api 49、ui 34、login 2）
> ——上表那一行就是最后这组。**脚本的原始输出才是权威**：
> README 只钉"有编号的 30 / 27"这一组，它与装了哪些依赖、树里有哪些文件都无关，不会漂。
> 复现：`python scripts/case_index.py`（它自己调 `python -m pytest --collect-only` 取 nodeid；
> 收集不到的只如实报出，不猜）。

两处容易误读的地方：

- **编号埋在两种位置**，索引脚本必须同时处理：函数 docstring 首行
  （`"""LC-01 正确用户名 + 正确密码 -> 200，返回 token 与 user。"""`）与
  `parametrize` 的 id 字符串（`({}, "LC-06 缺全部字段")`）。LC-06~LC-13 八条挤在一个函数里、
  LC-22~LC-25 四条挤在一个**没有 parametrize** 的函数里，所以"30 条编号 ↔ 29 个 nodeid
  ↔ 27 个带编号 nodeid"三个数各不相同，差在哪条一行行解释得清（见 `docs/wave3-zentao-handoff.md` §4.1）。
  判定表那 4 条共用 1 个 nodeid，意味着**缺陷定位精度到这一条为止**。
- `DEF-xx` 与 `FIND-xx` 是**缺陷编号不是用例编号**（分别见 `docs/wave1-contract-handoff.md`
  的真实缺陷清单与本节的 FIND 表），脚本不会把它们算进用例编号，只记为文本引用。

「LC 编号 → 禅道用例 ID」的登记表是 `docs/zentao-case-map.json`（入库，因为它是需要评审的配置）。
它的 `cases` 目前**为空，这是正确状态**——禅道用例要先在真实实例里建出来，才有 ID 可填，
脚本对没登记的编号只跳过、不猜。

要清楚口径：这是**上报与追溯的起点**，不是"已经双向追溯"。
链路是「编号 → nodeid → junit/账本里的结局 → 禅道用例 → 缺陷」，其中禅道侧尚未接真实例，
`scripts/report_to_zentao.py` 默认 `--dry-run` 只打印将要发送的 payload，真实上报要显式 `--send`
且凭据只从 `ZENTAO_URL` / `ZENTAO_TOKEN` 环境变量读。逐条置信度与未验证项见
`docs/wave3-zentao-handoff.md` §2 与 §6。

### 为什么把被测服务也放进仓库

一般项目「测的是别人的服务」，这里把被测服务（`login_app/`）一起放进来，是为了让
**缺陷、用例、契约三者的对应关系**能被完整看到：用例为什么这么写、契约在哪一行
被实现悄悄放宽了，都能查证。这也是面试讲"你怎么设计用例"时最缺的一环——
只讲用例本身，讲不出它是**针对什么**设计的。

### 提交历史里的 `dispatcher` / `integ` / `agent` 是什么

点开提交历史会看到 **219 条提交里，100 条的作者是 AI 编码代理会话**（其余是人工 111 条
＋ dependabot 8 条；口径写死在这里——它是本仓自己的历史，不是上游那份）：

| 作者 | 邮箱 | 条数 | 是什么 |
|---|---|---|---|
| `Lefan Ma` | `128964809+lefan17@users.noreply.github.com` | 84 | GitHub 账号，人工 review 与亲自改的那部分 |
| `lefan` | `lefan@local` | 19 | 同一个人的第三个 git 身份：fork 之后 TIA 那 19 条全在这里（本机未配 user 时提交） |
| `dispatcher` | `dispatcher@local` | 76 | 本机未配置 git user 时，AI 编码代理会话提交的 |
| `integ` | `i@x` | 17 | 集成／收口会话（跨仓库搬运、数字核对、口径统一） |
| `dependabot[bot]` | — | 8 | 依赖升级 |
| `lefan17` | `lefan17@users.noreply.github.com` | 8 | 同一个人的另一个 git 身份（仓库搬家后统一成 `Lefan Ma`） |
| `agent` | `agent@local` | 7 | 早期 Agent 会话（波 1 的认证改造与 schemathesis 契约套件） |

复现：`git log --format='%an|%ae' | sort | uniq -c | sort -rn`。

**它们不是外部贡献者，是这个仓库的开发过程本身。** 逐条核对过：`dispatcher` 那 76 条是
`fix(qaplat):` / `ci:` / `test:` 一类的小步修复，`integ` 那 17 条是 `docs:` 的数字重算与口径统一，
`agent` 那 7 条是 `feat(apis):` / `feat(secrets):` 的功能实现——全都在本仓库的演进线里，
没有一条是别人写进来的。

写在这里不是辩解，是因为它恰好是本仓库最值得说的一件事：**这个仓库的大部分代码是
AI 编码代理写的，而它现在是 636 条用例、87% 覆盖、9 个 job 全绿的状态。**
身份没统一是工程疏漏（后续提交已改用同一身份），但「AI 写的代码要过什么闸才敢上线」
这个问题的答案不在这里——在 [`设计要点`](#设计要点面试会问)（幂等重试、代理回退、耗时口径）、
`qaplat/` 的四层门禁，以及 [`docs/质量门禁策略.md`](docs/质量门禁策略.md) 那份被
`check_gate_policy.py` 机器校验的文档里。

## 设计要点（面试会问）

- **数据驱动**：用例逻辑与测试数据分离，`@pytest.mark.parametrize` 从 `data/` 取数据，加数据不改代码。
- **契约测试**：用 JSON Schema 校验响应结构，接口"删字段/改类型"能被立刻发现。
- **异常分支覆盖**：不存在的资源返回 404、错误密码提示文案，比正向用例更容易抓到 bug。
- **显式等待**：UI 用例全程使用 Playwright 自动等待和 `expect` 断言，没有一处 `sleep`。
- **fixture 作用域**：`browser` 用 session（启动浏览器最贵，整个会话只启一次），
  **`browser_context` 必须用 function**——浏览器上下文是 cookie/localStorage 的隔离边界。
  saucedemo 的购物车就存在 localStorage 里，context 一共用，上一条用例加的商品就会串到下一条，
  表现为「加了 1 个，角标显示 2」这种假失败。创建 context 只要几十毫秒，省这个钱不值。
- **失败留证**：UI 用例失败自动截图到 `testcases/ui/artifacts/`，CI 里作为 artifact 上传。
- **失败重试**：接口层对连接失败、读超时、429 和 5xx 自动重试（退避 0.5s/1s），
  避免公网或测试环境抖动产生"假失败"——没有重试的自动化，团队很快就不再信任它。
  但**只重试幂等请求**：GET/PUT/DELETE 可以重放，PATCH 与 POST 不重放
  （PATCH 在 RFC 意义上不保证幂等），POST 只在 429（请求没被处理）时重试，
  因为创建接口在 5xx 后重放可能造出重复订单、重复用户这类脏数据。
- **重试等待尊重 `Retry-After`**（波 2）：服务端说"2 秒后再来"就等 2 秒，
  不再按自己的指数退避硬撞。两种格式都认——增量秒（`Retry-After: 2`）与
  HTTP-date（`Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`，日期已过去则不等）；
  头缺失或值非法时退回指数退避。指数退避带**有界随机抖动**（多线程重试不会挤在
  同一毫秒惊群），且所有等待都被 `API_MAX_RETRY_WAIT`（默认 30 秒）截断——
  否则一个 `Retry-After: 3600` 就能把整个用例会话挂死。
  这条路径由 `testcases/api/test_retry_after.py` 起本地 `http.server` 真实返回
  429 + `Retry-After` 验证（请求真的过 socket），`tools/check_retry.py` 另有 23 条离线断言
  （桩对象、不发真请求，CI 上由 `test` job 里那一步执行）。
- **代理自动回退**：传输失败后的重试会临时绕过系统代理再试一次
  （`proxies={"http": None, "https": None}`）。本机开着 Clash/v2ray 时，`requests`
  会自动走它，而这类代理对某些域名会直接掐断 TLS，报
  `SSLError: UNEXPECTED_EOF_WHILE_READING`——直连反而是通的。
  这条回退让"代理抽风"不至于被误判成"用例写错了"。设 `API_TRUST_ENV=0` 可关闭。
- **耗时口径**：`response.elapsed_ms` 只统计**产出响应那一次请求**的耗时，
  不含之前失败重试的等待。否则一次成功的重试会因为把上回超时的 30 秒也累加进来，
  被判成"接口变慢"——恢复成功反而报失败。

## 数据隔离（自动化能不能长期活下去的关键）

测试用例造数据不收尾，是自动化项目最常见的死因：第一次跑全绿，第二次开始出现
莫名其妙的失败，三个月后没人敢删用例、也没人敢信它的结论。这套用例把"干净"
做成了**会红的断言**，而不是一句口头约定：

- **会话级基线断言**：`testcases/mes/conftest.py` 的 `data_isolation_check`（autouse，session 级）
  在会话开始抓一次快照、结束时再抓一次，两者必须完全相等。挂在 session 级而不是每条用例结束，
  是因为工厂造完数据、下一条用例还没跑时行数本来就是"临时脏"的；逐条查会把正常流程判成污染。
- **三个指标，缺一不可**：`testcases/mes/data_isolation.py` 同时统计**各表行数**、
  **关键数值列合计**（库存可用/占用/已发料、流水数量与账面余额）和**流水余额链**。
  只断行数会漏掉最阴的一类泄漏——领料只 `UPDATE` 一行、行数完全不变，但账实已经不符；
  而"流水金额被写错"连列合计也抓不到，因为流水行会被回收逻辑删掉、收尾时已经是 0 行，
  所以余额链必须**在流水行还活着的时候**逐笔重放（见 `docs/测试有效性说明.md`）。
- **清理不依赖用例正文走完**：`factory` fixture 在 teardown 里回收，`take_material` fixture
  在 teardown 里自动反向退料。断言失败的用例也会把数据还回去，否则一条失败的用例会留下脏数据、
  把后面几条一起带红，最后没人分得清哪个才是真正的失败点。

  teardown 里**回收一定执行**这件事是显式写出来的，不是靠代码顺序碰巧成立：
  早先是「先断言流水链、再 `cleanup()`」的直线代码，断言一红回收那行就永远走不到，
  数据留在库里、会话收尾再红一次，真因被第二层噪声盖住。现在两件事互不阻断，
  回收失败**先打印再原样抛回**（不吞掉），回收完再按登记过的单号/编码回查一遍残留
  （`DataFactory.verify_clean()`），残留显式报出。
  > 「兜底真的存在」不靠声明，靠 `testcases/mes/_fault_probe.py` 这条**故意失败**的探针：
  > 它把流水余额改错、精确命中那条断言，再由 `test_data_lifecycle.py` 用真子进程验
  > 「回收照样跑了、且没有连带基线漂移」。把它跑在修复前的 conftest 上会**没有**
  > `[数据回收]` 且出现 `表 work_orders: 基线 0 行 -> 现在 1 行（差 +1）` —— 承重性有红证。

- **跨会话残留靠 TTL 扫**：上面的兜底都只在**进程还活着**时有效。会话被 Ctrl-C / 崩溃 /
  CI 超时打断，teardown 根本没机会跑；而下次跑的是**另一个库**（session fixture 每次都
  `mktemp`），于是没人会发现。`scripts/mes_data_ttl.py` 就是给这种残留准备的扫帚：

  ```powershell
  python scripts/mes_data_ttl.py                      # 干跑，看会删什么（TTL 24h）
  python scripts/mes_data_ttl.py -n 1                 # 换个 TTL：超过 1 小时
  python scripts/mes_data_ttl.py --apply              # 真的删
  python scripts/mes_data_ttl.py --fail-on-lingering  # 清完仍有残留则退出码 3
  ```

  判据两级，**基线白名单是硬否决**：种子用户 / 种子物料 / `(物料, 仓库)` 键命中即
  **永不删除，哪怕它已经"过期"**。这一条不是额外保险，而是让"按时间清理"成立的前提 ——
  种子行的 `created_at` 就是建库那一刻，本来就老于任何 TTL，**只看时间判定，
  第一次跑清理就会把 5 个种子物料和 5 行种子库存全删掉**。另一级是「认得出是测试数据」：
  工厂标记正则（`WO-T-XXXXXX-1` 形状）**加上**用例手写编码的显式清单 —— 仓库里有一批
  用例手写 `FG-TEST-01` / `FG-X` / `MAT-ACL` / `WO-IDEM-*` 这类不含随机标记的编码，
  纯正则认不出来。**认不出来的过期行不删、只报告**（宁可留着被人看到，也不猜着删）。
  清理是幂等的：第二次跑命中 0 项、行数不变、不报错；报告里还有一个 `!! 基线行数减少了（误删）`
  自报字段，它一旦出现就是 bug，报告不许替它遮掩。

- **登记式回收**：用例里直接发请求造的数据（绕开工厂方法的那些）要显式登记，
  漏登记的表现是"用例全绿、会话收尾报差异"。这类差异信息被刻意写成排查方向而不是结论：

  ```text
  会话结束时数据没有回到基线 —— 有用例造了数据没清理，或改了库存没退回去。
    表 materials: 基线 5 行 -> 现在 7 行（差 +2）
    库存可用量合计: 基线 3270.000 -> 现在 3280.000（差 +10.000）
  排查方向：用了 factory 却绕开它直接发请求造数据（没登记回收）；
  或者领料用例忘了反向退料（见 test_mes_business.py 的 take_material fixture）。
  ```

- **只增不改的表怎么回收**：`stock_ledger` 作为审计真相来源没有删除接口，
  所以它只能由测试在收尾时直连数据库回收；判据是 `X-Work-Order-No` 请求头声明的
  **归属标识**，而不是服务端签发的单号——那个字符串在流水里可能是 NULL、空串或用例随手写的标记。
  这里踩过两个坑，都留在代码注释里：`WHERE work_order_no = ?` 对 NULL 永远匹配不到
  （SQL 里 `NULL = NULL` 是 UNKNOWN，得写 `IS NULL ?`）；以及工单已被别的用例删掉时，
  早期代码直接 `continue` 跳过了整条回收流程，连带它关联的库存行也永远清不掉。

## 当前实测结果

> 采集时间 **2026-10-18**（Windows 本机，Python 3.12.10，公网接口用例受网络影响会有波动——
> 同一套用例在两天里 `-m api` 分别量到 60.44–61.85s 与 71.70–74.03s，**下面这一栏的耗时读量级**，
> 计数与覆盖率才是定值）。
> **一个容易看错的点**：`api` 与 `mes`、`login` 三个标记有**重叠** ——
> `testcases/mes/` 的用例同时打了 `mes` 和 `api`，`testcases/login/` 的同时打了
> `login` 和 `api`。所以 `-m api` 选到 132 条（49 条接口目录 + 54 条 MES + 29 条登录），
> **这些数字不能相加**。全量共 636 条 = 49 接口 + 34 UI + 64 MES + 29 登录 + 224 契约 + 236 平台层自测。
> 另有 2 条性能基线用例（`-m perf`）挂在接口目录里，`-m 'api and not perf'` 是 130 条。
> 契约那 224 条是**独立标记**（`-m contract`），不并入 `api` ——
> 它们要起本地服务、按 spec 生成请求，属于另一档耗时（约 20 秒），
> 不适合塞进"日常开发随手跑"的那一组。

> ⚠️ 下面这块是 **2026-10-19/20 的一次实测记录**，原始输出保持原样，不作改写。
> 2026-10-25 新增 `testcases/platform/test_launcher_python.py` 之后，全量变为 **636**、
> `-m platform` 变为 **236**（其余各行的数字未变，`-m contract` 仍是 213 passed + 11 xfailed）。

```text
pytest --collect-only -q                 630 tests collected

pytest -m api                            132 passed, 486 deselected in 72.51–80.99s
pytest -m mes                             64 passed, 554 deselected in 41.81–42.80s
pytest -m login                           29 passed, 589 deselected in  3.44s
pytest -m contract                       213 passed, 394 deselected, 11 xfailed in 20.47s
pytest -m perf                             2 passed, 628 deselected in  5.21s
pytest -m platform                       230 passed, 400 deselected in 49.58s
pytest -m ui                             34 passed, 596 deselected in 38.31s（浏览器已装好，全绿，见下）

cd traffic-replay; pytest                173 passed in 17.57s（批次 2，自带 pytest.ini）

pytest -m api -n 4 --dist loadfile       132 passed in 38.45–38.63s（同轮串行 72.51–80.99s，约 1.99×）
pytest -m mes -n 4 --dist loadfile        64 passed in 21.50–21.80s（同轮串行 41.81–42.80s，约 1.96×）
CONTRACT_MAX_EXAMPLES=50 pytest -m contract
                                         213 passed, 11 xfailed in 53.77s  # 放大 6.25 倍样例，耗时 ×2.5

pytest -m "not ui" --cov=. --cov-report=term               TOTAL 4634 722 84%
pytest -m api -n 4 ... --cov-fail-under=54                 TOTAL 4310 1928 55%（55.27%）
                                        Required test coverage of 54% reached（退出码 0）
pytest -m mes --cov=mock_server --cov-report=term          TOTAL 1374 235 83%
pytest -m login --cov=login_app --cov-report=term          TOTAL   88  13 85%
python scripts/export_openapi.py --check 两份 spec [一致]（退出码 0）
python -m mypy                          Success: no issues found in 118 source files
python scripts/check_gate_policy.py     策略文档、test.yml、pytest.ini 三者一致（退出码 0）
python tools/check_retry.py             23/23 通过，退出码 0（原 12 条 + 波 2 新增 10 条 + 第 23 条）；
                                        第 23 条钉的是「代理回退判据」那个曾经的缺陷；
                                        CI 上由 `test` job 里那一步执行，并由门禁脚本钉住
```

> **上面这一块都是 2026-10-18 重跑的实测**，没有一行是从旧值推算的。基准树是**含跨盘 nodeid 修复
> 及其 4 条自测、再加 per-file 覆盖率脚本 37 条契约用例与 perf 传输层 12 条**的当前树（636 条收集）；覆盖率与各层计数都在同一棵树上测。
> 一个例外要说清：`passed` / `deselected` / 计数都是**可复现的定值**（串行重跑逐位相同），
> 但**耗时是单次观测**——`-m api` 与 `-m ui` 打公网，批次间能差十几秒（同一天这轮两轮 `-m api`
> 65.73s ↔ 73.09s，差了 11%；上一轮是 71.70s ↔ 74.03s，两天合起来跨度近 20%，
> 所以耗时与并行那两行都写了区间）。**提速比值必须读同一轮内的对**：
> `-m api` 是 68.02s→28.80s（2.36×）与 73.09s→30.04s（2.43×），`-m mes` 是 36.89s→18.33s（2.01×）
> 与 36.95s→17.97s（2.06×）——**不要拿不同批次的两个数凑比**，那会算出根本不存在的"提速"。
> 前提：`$env:TMP` 必须指向一个**确实存在**的可写目录——那里不可写时 pytest 的 basetemp 会回落到仓根，
> 它生成的 `.py` 会被 `--cov=.` 算进分母，分母就跟着漂。
> **跨盘那个坑已经修了（2026-10-18）**：`$env:TMP` 与仓库**不在同一个盘**时，pytest 对
> rootdir 之外的跨盘路径算不出相对路径（`bestrelpath` 的 `commonpath` 抛 `ValueError`），
> nodeid 的前缀会变成空串（dump 里是 `::test_probe_a`），`registry` 靠前缀读源码那一步跟着失效。
> 现在由采集插件 `qaplat/_dump.py::_qualified_nodeid` 在收集期用 `item.path` 把前缀补回来，
> 被改过的 nodeid 记在 dump 的 `renamed` 字段里（正常收集时为空）。**修前**跨盘 `-m platform`
> 挂 1 条、同盘 177 全绿；**修后**两种盘符下都是全绿（当时 **181 passed**，多出的 4 条就是这次
> 补的跨盘自测）。**pytest 9.1.1 上仍有效**：本轮升级后重跑 `-m platform` 是 230 passed，
> 且 `$TMP` 恰好在 C 盘、仓库在 D 盘（跨盘状态），说明补前缀逻辑没有随 pytest 内部调整而失效。
> 这个修复不是为了「让某台机器变绿」——空前缀会让 `referenced_paths` 整条来源静默归零，
> 报表上表现为「这条用例没覆盖任何接口」，而它其实覆盖了；那是个会算错覆盖率的缺陷。
> **marker 真的重叠，别做加法**：`-m api` 的 132 条 = `api and mes` 54 条 + `api and login` 29 条
> + 只属接口目录的 49 条（`-m perf` 的 2 条就在这 49 条里）；另有 `mes and not api` 10 条。
> 所以 132 = 49 + 54 + 29 这个加法成立，但 "49 + 54 + 29" 里的 54、29 已经分别被 `-m mes`、`-m login` 数过一遍。
> 全量 636 = 49 接口 + 34 UI + 64 MES + 29 登录 + 224 契约 + 236 平台层自测（这六组互不重叠，加法成立）。
> **覆盖率两行同一个道理**：`--cov=.` 的分母里含 `qaplat/` 与 `mes_web/` 整目录
> （`testcases/` 自 2026-11-02 起已排除，见下面「分母口径变了」那段），
> 所以 `not ui` 是 4634 分母 / 未覆盖 722 / **84%**，
> 而只跑 `-m api` 的分母是 4310 / 未覆盖 1922–1928 / **55%（55.27%）**。
> 两组的分母差 324 条，差的正是 `-m api` 摸不到的那些文件——所以两个百分比不可比，
> 门禁挂在 `-m api` 上只能拿 55% 这一栏说事。
> `-m api` 不 import `testcases/` 里的平台层用例，分母比 `not ui` 少 324 条，
> 但那 324 条里有相当一部分是被 `-m api` 覆盖不到的模块，所以未覆盖行数反而更多。
> **未覆盖那一栏是区间，这是判据不是美化**：10-17、10-18 两轮复测累计跑了 9 次
> （`not ui` 串行 4 次、并行 `-n 4` 3 次，10-18 收官这轮又串行、并行各 1 次；
> `-m api` 串行 3 次、并行 `-n 4` 3 次），
> `not ui` 的未覆盖数落在 1177–1183（旧分母口径），
> 而**分母 8916 / 8597 每一次都逐位相同**——跳的只是子进程里 `mock_server` 收尾落盘那几行
> （原因见下面 `mock_server` 那一节）。
> 百分比这几次全是 87% 与 55%，所以百分比写单值、未覆盖数写区间。
> 一旦某次它们逐位相同，就改回单值——判据是「重跑是否逐位相同」，不是「哪个好看」。
> 批次 2 的 28 个文件**不进**这个分母：它自带 rootdir、`--cov=.` 只覆盖根仓库，
> 且它的 `analyze/` `recorder/` `replay/` 都没有 `__init__.py`（靠 `conftest.py` 插 `sys.path` 导入）。
>
> **分母口径变了（2026-11-02）**：`.coveragerc` 的 `[run] omit` 现在同时排除
> `perf/*` 与 `testcases/*`。理由与 per-file 门禁段写下的那句是同一句：**测试代码的
> 覆盖率是循环定义** —— 跑测试当然会执行测试文件本身。它留在分母里的后果是
> 「新增一个测试文件」这个纯增测动作会把全局覆盖率往**下**拽：`testcases/platform/test_tia.py`
> （452 语句）以 `-m api` 跑不到、自身只有 18%，单这一个文件就把 TOTAL 压掉约 1.3pp，
> 直接把 CI 压成红（`51.78% < 54%`）。排除后 `-m api` 从 4595/9529（51.78%）变成
> 1922–1928/4310（55.27%），`not ui` 从 8916/1181（87%）变成 4634/722（84%）——
> 两栏跌的点数正好就是测试代码那一块，`apis/ common/ mock_server/ login_app/` 一行没少
> （对齐后单算这四块是 86.27%）。门槛 54 因此仍然有效（余量约 1.3pp）。

> **`-m ui` 那一行是本次真的重跑过的**：命令是 `pytest -m ui -o addopts=`，
> 结果是 `34 passed, 596 deselected in 33.36s`，退出码 0。收集数 34 条
> （`pytest -m ui --collect-only -q -o addopts=` → `34/636 tests collected`）。
> 这 34 条里，15 条（`testcases/ui/test_mes_web_app.py`）走 HTTP 层、**不申请 `page`
> fixture、不起浏览器**；另外 19 条（`test_inventory.py` 8 条 / `test_login.py` 6 条 /
> `test_mes_mixed_flow.py` 5 条）需要真实浏览器，打的是公共 demo 站
> `https://www.saucedemo.com`（配在 `data/config.yaml`）。
> **浏览器没装时，正是这 19 条在 setup 阶段全 error**（那 15 条 HTTP 层的照样过），
> 报错是 `playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at
> ...\ms-playwright\chromium_headless_shell-1243\chrome-headless-shell-win64\chrome-headless-shell.exe`。
> 这是**运行环境属性（缺浏览器），不是用例缺陷、也不是被测代码的缺陷**：本机装浏览器之前
> 观测到的就是 `15 passed, 19 errors`，而那 19 条恰好就是上面 8 + 6 + 5 这 19 条（分布完全对齐）；
> 装上对应 rev 的浏览器后就是 34 条全绿（也就是上面那一行）。
> 早期本文件把同一批 error 归因成"沙箱禁止 asyncio 建命名管道（`_winapi.CreateNamedPipe`）"，
> 那个归因不成立：那 19 条 error 逐条都能用"缺浏览器"复现，没有一条是那个原因。
>
> **覆盖率门禁挂在 `-m api` 上，而它现在有 55%（55.27%），不是"全量"的 84%**：
> 这两组的选取集**不再相同**（改造前它们恰好都是 92 条，所以旧文档里
> "选取集完全相同"那句当时是对的，现在错了）。
> 门禁定在 54：它要拦的是**退化**，不是卡住当前值（当前 55.27%，留约 1.3pp 余量）。
>
> **当前值（2026-11-02，排除 testcases/ 这棵树）**：`not ui` 分母 4634、未覆盖 722、**84%**；
> `api` 分母 4310、未覆盖 1922–1928、**55%（55.27%）**。
> 上一版（2026-10-19，分母含 `testcases/`）是 `not ui` 8916/1177–1183/87% 与
> `api` 8597/3847/55.25%；两栏的分母各少掉 5219 条测试代码语句，百分比的下移纯粹来自这里。
>
> **以下为历史记录，分母已作废。** 它记的是"当时在某棵树上串行测了几次"，
> 把里面的分母与未覆盖数换成新值就等于伪造记录，所以下面的数字原样保留，只作溯源用：
>
> **那未覆盖语句数不是个稳定值**：合流前同一台机器连跑五次是
> 560 / 562 / 551 / 558 / 556，对应覆盖率 88.89% / 88.85% / 89.07% / 88.93% / 88.97%
> —— 真正在动的是这一栏数字，百分比只挪了 0.2pp。合流后分母变成 7095，
> 这一栏在 1177 ~ 1183 之间跳（三次实测 1179 / 1177 / 1183，百分比一直是 83%）；
> 本轮补完 qaplat 两处判据后分母是 7158；随后契约测试适配 schemathesis 4.31，
> `testcases/contract/test_schemathesis_properties.py` 的可执行语句从 171 条变成 172 条，
> 分母跟着变成 7159（这一条是量出来的：`coverage.parser.PythonParser` 在两个提交上分别解析该文件）。
> 本轮在 7159 这个分母上先后测了 16 次（前提是 `$env:TMP` 指向一个**确实存在**的可写目录——那里不可写时 pytest 的 basetemp 会回落到仓根，它生成的 .py 会被 `--cov=.` 算进分母），这一栏落在 1181–1191 之间，百分比稳定在 83%（其中两次显示 84%）。
> 再往后，`qaplat/registry.py` 补采集失败判据（+15 条可执行语句）、`test_registry_coverage.py` 加 2 条自测（+13 条），分母 7159 → 7187（同样是 `coverage.parser.PythonParser` 量出来的）；在 7187 这个分母上串行测了 5 次（每次前清掉分片数据 `.coverage` 与 `.coverage.*`，避免两次运行互相污染；**不要**写成 `.coverage*` —— 那个通配符会连 `.coveragerc` 一起删掉），这一栏落在 1179–1188，百分比 83%（5 次里 3 次显示 84%）。`-m api` 那一组在 7095 分母上串行测了 8 次，未覆盖恒为 2748、百分比恒为 61.27%。
> 逐文件比对两次运行，差异**全部**来自
> `mock_server/services.py`（合流前 `359/74` ↔ `359/80`，合流后 `359/76` ↔ `359/78`）：
> 该服务由 fixture 以常驻子进程拉起、收尾走 `terminate()`，落盘那一刻的行数
> 取决于被测服务有没有被动到那几行。
> 所以这一栏请读**百分比**和量级，别把它当成固定值。
>
> **`mock_server/*` 的覆盖率现在是真行为覆盖**（波 2 · P1-3，见 `docs/wave2-coverage-handoff.md`）。
>
> 之前那 0% / 14% 是**采集失败**，不是"没人跑过"：被测服务由 fixture 以常驻子进程拉起
> （`testcases/mes/mock_server_ctl.py`），收尾走 `terminate()`，而 Windows 上是
> `TerminateProcess` 强杀 —— coverage 靠 `atexit` 落盘，进程被强杀就什么都不剩。
>
> 现在服务经 `scripts/service_launcher.py` 启动：launcher 先在进程内装一个"吸收信号"的
> handler，uvicorn 关闭时会 `signal.raise_signal(原信号)` 把信号重发给自己
> （默认处置 = 终结进程、不跑 `atexit`），这个 handler 把它接住，于是 `serve()` 正常返回、
> 进程走到 `atexit`、分片落盘；`stop()` 同时改成先礼后兵（优雅信号 → `terminate()` → `kill()`）。
> 分片（`.coveragerc` 的 `[run] parallel = true`）由 pytest-cov 在 session 结束时**自动 combine**，
> 不需要手工 `coverage combine`，也不需要 `-m coverage run --parallel-mode`。
>
> **pytest-cov 必须停在 6.x（升到 7.x 这条链路整片归零）**：上面这套子进程采集全靠
> 7.0.0 之前才有的两个文件——`pytest_cov/embed.py` 与 `site-packages/pytest-cov.pth`。
> 后者在每个解释器启动时读 `COV_CORE_SOURCE`，命中就调 `embed.init()` 起一个后台采集线程、
> 退出时写分片。7.0.0 起两个文件都被移除（实测 7.1.0 的 wheel 只剩 `__init__`/`engine`/`plugin`），
> 于是子进程里的覆盖**一条都收不到**，表现为 `mock_server/` 11 个文件全 0%。
> 实测对照：装上 7.1.0 后 `pytest -m mes --cov=mock_server` 从 **83% → 0%**，
> `testcases/mes/test_coverage_collection.py` 直接红。所以 `.github/dependabot.yml` 里
> 对 `pytest-cov >= 7.0.0` 加了 ignore——这不是「暂时不想升」，是「升上去就丢能力」。
> 而 `pytest_cov 6.3.0` 声明 `pytest>=6.2.5`（无上限），与 pytest 9.1.1 兼容。
>
> 实测（本机）：`-m mes --cov=mock_server` → TOTAL **9% → 83%**
> （`routers.py` 0% → 84%、`services.py` 0% → 70%、`deps.py` 0% → 82%、`app.py` 0% → 90%）；
> `login_app` 同构：`-m login --cov=login_app` → TOTAL **31% → 85%**（`run.py` 0% → 100%）。
>
> **`-m contract` 的 xfailed 恒为 11**：DEF-01、DEF-02a、DEF-02b(×6)、DEF-03、DEF-04(×2)。
> 它们都是 `strict=True`，所以**修好一条，xpass 会立刻把门禁打红**——
> 这不是"允许失败的用例"，是"用用例钉住缺陷单"。
> 早期版本的 DEF-05/DEF-06 也在这 11 条里，现已修好并转成正常用例。

> **登录用例是这套用例里"最划算"的一组**：29 条跑完只要 1.34 秒，
> 而接口用例 33 条要 3 分多钟——差别在登录用例打的是**本地进程**，
> 接口用例打的是公网站（受网络和限流影响）。这也是为什么能落地的自动化
> 大多把关键校验放在**本地可控的服务**上，公网练习站只作补充。

> **这里曾经短暂红过一次，值得留着当例子**：第 23 条用例
> 「传输失败后的状态码重试不绕过代理」钉的是 `apis/base.py` 的代理回退判据缺陷 ——
> 原判据 `last_error is not None` 记的是「这个请求曾经出过传输失败」，
> 而不是「上一次尝试就是传输失败」，于是一次传输失败之后，**由 5xx 触发的重试也会绕过用户显式配置的代理**。
> 落地的顺序是：**先加用例、看它红（22/23，退出码 1）；再改判据、看它绿（23/23，退出码 0）** ——
> 顺序倒了就没有证据链。当时红的细节是三次请求带的 `proxies` 依次为
> `[用户配置, {'http': None, 'https': None}, {'http': None, 'https': None}]`，第三次本该还原成用户配置。
> 修法是把「上一次尝试是不是卡在传输层」单独记一个变量（拿到响应就复位），
> 而不是让它和循环末尾 re-raise 用的 `last_error` 共用一个变量 —— 后者一旦被赋过值就再也不复位。
>
> 完整步骤：该自检脚本是 `tools/check_retry.py`（每条自检都有编号注释，波 1 收工时有 12 条，
> 波 2 之后是 22 条，这条接在末尾成第 23 条）；
> 它钉的分支在 `apis/base.py` 的 `request()` / `_should_retry()`。
>
> 可复现的反证：把判据改回 `last_error is not None`，第 23 条立刻变红（22/23、退出码 1）。

> 接口用例跑的是公网 `jsonplaceholder.typicode.com`。这个站会**限流**：
> 短时间内连续跑多轮全量用例，会开始返回 429 或直接读超时。
> 传输层参数可用环境变量调，不用改代码：
>
> ```powershell
> $env:API_TIMEOUT = "15"        # 单次请求超时秒数（默认 15）
> $env:API_RETRIES = "2"         # 重试次数（默认 2）
> $env:MAX_RESPONSE_MS = "5000"  # 响应时间断言阈值（默认 5000，公网接口要放宽）
> $env:API_TRUST_ENV = "0"       # 关掉"失败后绕过代理重试"
> $env:API_MAX_RETRY_WAIT = "30" # 单次重试等待的硬上限秒数（默认 30）
> ```
>
> 换成自己公司的内网接口后，`MAX_RESPONSE_MS` 应收到 200-500ms 才有性能门禁的意义。

## 性能基线（P95 回归）

上一条的 `MAX_RESPONSE_MS` 是「**单次**请求 < 阈值」，跑的是公网：一次请求的耗时是纯噪声，
换个时段、换条链路数字全变。它是一条连通性冒烟，**不是性能基线**。

波 3 起另有一套真的基线，打在**本地 `mock_server`** 上（同一份代码、同一份种子数据、同一个 SQLite，
端口系统分配、库在 pytest 临时目录里），因此「P95 变差了」指向被测代码而不是网络：

```powershell
python -m pytest -m perf           # 只跑性能采样（独占机器，数字最干净）
python -m pytest -m api            # 接口总门禁；性能基线也带 api 标记，所以会被一起选中
```

- **基线文件**：`testcases/api/perf_baseline.json`（入仓）。6 个只读端点各采 100 次、丢弃前 10 次预热，
  用 **nearest-rank** 分位算 P50/P95/P99/max/mean，并记录采集环境指纹（平台/CPU/Python/采样数/分位方法/阈值公式）。
- **判定**：`基线 P95 × 3.0 + 10.0ms`。倍数吸收跨机器的系统性差异，10ms 下限保证基线只有几毫秒时
  倍数放大不出分辨率。它拦的是**量级劣化**（漏索引、N+1 查询、同步阻塞变大），不是几毫秒的漂移。
  实测同一梯度重复采集的轮间波动最大 1.25x（把本轮八次同口径采集一并计入 1.96x），阈值对实测最慢 P95
  仍留 4 倍以上余量 —— 门禁不会随机红。
- **显著变快不静默**：实测 P95 < 基线的 0.5 倍时以 **warning** 提示「可更新基线」，而不是悄悄通过
  （用 warning 而非 print，是因为 `pytest` 默认捕获 stdout，print 等于没提示）。
- **不判红的对比对象**：`testcases/api/test_response_time.py` 那 2 条打公网的用例保留原样，
  属抖动类，与基线无关。

传输与阈值参数全部走环境变量，不用改代码：

```powershell
$env:PERF_SAMPLES = "100"        # 每个端点采样次数（默认 100）
$env:PERF_WARMUP = "10"          # 丢弃的预热次数（默认 10）
$env:PERF_P95_FACTOR = "3.0"     # 阈值倍数（默认 3.0，调小=更严）
$env:PERF_P95_FLOOR_MS = "10"    # 阈值绝对下限毫秒（默认 10）
$env:PERF_P95_LIMIT_MS = "50"    # 与基线无关的绝对上限；设了取更严的一侧（min）
$env:PERF_IMPROVE_RATIO = "0.5"  # 低于基线的多少倍时提示可重采
$env:PERF_BASELINE_FILE = "..."  # 换一个基线文件路径（默认仓库内那个）
```

**更新基线**必须显式开开关，且只能由一次真实采集写文件（代码里没有任何手编数字的路径）：

```powershell
$env:UPDATE_PERF_BASELINE = "1"
python -m pytest -m perf -s
```

它会打印 `旧值 → 新值` 的 diff，新增/移除端点单独标注。**基线放宽必须留痕**。
只想看当前基线内容（不采样）：`python testcases/api/perf_baseline.py`（加 `--json` 打全文）。
## 容量压测（perf/）

**`testcases/mes/concurrency.py` 不是压测。** 它用 `threading.Barrier` 对齐启动、用 `spans`
时间线是否交叠来判**竞态会不会发生**，回答的是"会不会超卖/丢更新"，2 个并发就能红。
它不回答"能扛多少并发"。两件事各管各的：

| | `testcases/mes/concurrency.py` | `perf/`（locust） |
|---|---|---|
| 问什么 | 会不会竞态（超卖 / 丢更新） | 能扛多少并发，RPS 何时到顶、P95 何时恶化 |
| 负载 | 一次性脉冲，Barrier 对齐 | 持续负载，按用户数分梯度，20s/轮 |
| 判据 | 时间线交叠 + 业务不变量 | RPS / P50 / P95 / P99 / 错误率 / 5xx 次数 |
| 产出 | 布尔结论 + 时间线证据 | 容量数字表 + 拐点档位 |
| 跑在 | `pytest -m mes`（CI 每次提交，阻塞门禁） | `perf/run_load.ps1`（手动 / 夜间，**不阻塞**） |

压测不能替代竞态判定，反过来也一样。混合场景里曾经带上 `POST /inventory/reverse`，
10 并发就稳定出 400——那是 `mock_server/services.py:636` 的 check-then-act 竞态被踩中，
不是容量问题；留在压测里只会让"错误率"变成两件事的混合物，所以默认关掉
（要它的延迟数字就 `LOAD_INCLUDE_REVERSE=1` 单独跑）。

### 怎么跑

```powershell
# 一键：起服务 → 4 个梯度 × 2 次发压 → 落结果 → 回收服务进程
pwsh -File perf/run_load.ps1

# 快速验证链路（约 20s）
pwsh -File perf/run_load.ps1 -Users "5,10" -RunTime 10s -Repeat 1 -Label quick

# 只看计划不发压 / 压登录接口（PBKDF2 120k rounds 的 CPU 天花板）
pwsh -File perf/run_load.ps1 -ListOnly
pwsh -File perf/run_load.ps1 -UserClasses LoginOnlyUser -Label login
```

非 Windows 或要自己指定解释器时，直接用 runner（它自己会拼好 PYTHONPATH 与 PYTHONUTF8）：

```bash
python perf/run_load.py --users 5,10,20,50 --run-time 20s --repeat 2 --label my-run
```

locust **故意不写进 `requirements.txt`**（见 `requirements-perf.txt` 文件头）：它是压测工具
而不是测试依赖，会拖进 gevent 并 monkey-patch 标准库，并入 CI 每个 job 都要装的依赖集
风险大于收益。要跑压测的人显式装：

```powershell
& "<python>" -m pip install -r requirements-perf.txt   # 仅 locust==2.46.7
```

产出落在 `reports/perf/<时间戳>-<label>/`（`reports/` 已被 `.gitignore` 忽略）：
`summary.json` 是梯度聚合 + 拐点判定 + 服务回收证据，`samples.jsonl` 是逐轮明细，
`u<并发>-r<第几次>_stats.csv` 是 locust 原始统计（`Aggregated` 行在最后一行）。

### 本机实测（**实测口径，不是通用结论**）

ADMIN 角色、读写混合（read:write = 8:1）、`wait_time = 0`（用户数≈在途请求数，量的是
服务端吞吐上限）、`--concurrency-window-ms 0`、单轮 20s、4 梯度 × 2 次取中位数：

| 并发 | RPS | P50 | P95 | P99 | 错误率 | 服务端 5xx |
|---|---|---|---|---|---|---|
| 5 | 340.6 ~ 419.1 | 11~14ms | 22~24ms | 30~31ms | 0.00% | 0 |
| 10 | 298.6 ~ 377.4 | 23~29ms | 54~65ms | 67~79ms | 0.00% | 0 |
| 20 | 335.4 ~ 371.4 | 45~51ms | 104~115ms | 125~145ms | 0.00% | 0 |
| 50 | 338.9 ~ 357.9 | 104~105ms | 205~230ms | 695~970ms | 0.02~0.06% | 2~6 |

**拐点（两批一致，判据与结论一起写在 `summary.json` 的 `knee` 里）**：

- **吞吐在 10 并发就饱和**：5u→10u 的 RPS 不是变平而是**掉**了 10%~12%，之后 20u/50u 也再没
  超过 5u（判据：相邻梯度 RPS 增幅 < 20% 即算饱和）。
- **延迟拐点在 10 并发**：P95 从 22~24ms 跳到 54~65ms，越过"最低档 × 2"的判据线。
  50u 时 P99 到 695~970ms，P50 也涨到 104~105ms。
- **稳定性拐点在 50 并发**：5/10/20 档服务端故障为 0，50 档出现 2~6 次 500 或连接重置，
  服务日志对应 `sqlite3.OperationalError: database is locked`（栈顶 `mock_server/db.py:296`
  的 `BEGIN IMMEDIATE`）。**总错误率只有 0.02%~0.06%，只看错误率会把这一档误判成健康** ——
  所以 5xx 是单独数的，阈值取 0。
- 三个拐点不在同一档（10 / 10 / 50），所以分三行报，不合成一个"最大并发"。

同梯度跑两次的批次内波动 ≤ 11.7%（P95 与 RPS 都记在 `summary.json` 的
`p95_spread_ratio` / `rps_spread_ratio`），拐点档位在两批之间稳定复现；
但**批次之间**同梯度能差到 20%+，所以**读趋势与拐点档位，不要读绝对数字**。

### 两条必须知道的局限

1. **压测机与被测服务同机（`127.0.0.1`）**。方向相反的偏差同时存在：走 loopback 省掉了
   真实网络 RTT 与网卡开销（→ 数字偏**乐观**），但 locust 的 gevent 与 uvicorn 抢同样的
   CPU、库文件同盘（→ 偏**悲观**）。净结论：绝对 RPS 与拐点档位都应当读作**乐观上界**，
   真实跨网部署只会更差。要拿生产规划的容量数，得换独立压测机 + 真实网络 +
   `LOAD_THINK_TIME_MS` 模拟思考时间 + 单轮 ≥5 分钟。
2. **服务端是 SQLite（WAL + `BEGIN IMMEDIATE`）**，写操作天然串行。50 档的 500 来自写锁，
   不是应用逻辑错。换库（Postgres 等）才能看到应用层的真实容量。

完整论证（含每个结论的原始 `Aggregated` 行、失败明细、未验证项）在
[`docs/wave3-load-handoff.md`](docs/wave3-load-handoff.md)。

## 流量回放（批次 2，独立子项目）

`traffic-replay/` 是一条**独立交付物**，不是根仓库的一层：它有自己的 `pytest.ini`
（`testpaths = tests`、marker 是 `recorder` / `replay` / `analyze`，与根仓库那 12 个
**零重叠**）和自己的 `conftest.py`（只把本目录插进 `sys.path`）。因此：

- **根 `pytest` 收集不到它**（根 `pytest.ini` 是 `testpaths = testcases`），要单独进目录跑：

  ```powershell
  cd traffic-replay; pytest        # 173 passed
  ```

- **它不受根 `ruff.toml` 管辖**：`replay/model.py` 用了 `type JsonValue = ...`
  这个 py312 才有的类型别名语句，而根配置声明 `target-version = "py310"`。
  根 `ruff.toml` 用 `extend-exclude`（不是 `exclude`——后者是**替换**默认排除表，
  会把 `.tox` / `node_modules` 一起顶掉）把它排除掉，`force-exclude = true`
  让 pre-commit 那种"显式传文件名"的调用形式也一并跳过。
- **它不进覆盖率分母**：`--cov=.` 只统计根仓库（且它的 `analyze/` `recorder/` `replay/`
  都没有 `__init__.py`，靠 `conftest.py` 插 `sys.path` 导入）。
- CI 上有独立一步 `working-directory: traffic-replay`，**不设 `continue-on-error`**。

设计文档在 `traffic-replay/docs/recorder-design.md` 与 `replay-design.md`，
共享数据模型被冻结在 `traffic-replay/FROZEN_INTERFACES.md`。

## 持续集成

推送到 `main`、开 PR、手动点按钮，以及**每周一凌晨的定时回归**都会触发。共 **9 个 job**：

**每个 job 的阻塞性、每个测试层的触发时机与预算时长，以 [`docs/质量门禁策略.md`](docs/质量门禁策略.md) 为准**——
那份文档不是说明文，是门禁的一部分：`python scripts/check_gate_policy.py` 会拿本文档里的 job 表、
`continue-on-error` 放行清单、marker 归属表、自检脚本表，与 `.github/workflows/test.yml` 和 `pytest.ini` 逐条对照，
分叉就红（这一步在 `pre-commit` job 里跑）。下表是速览：

| job | 做什么 | 失败会拦流水线吗 |
|---|---|---|
| `lint` | `ruff check .` + `ruff format --check .`（规则全部来自仓库根的 `ruff.toml`） | ✅ 拦 |
| `typecheck` | `python -m mypy`（配置来自 `pyproject.toml`） | ✅ 拦 |
| `windows` | `ruff check .` + `ruff format --check .` + `python -m mypy` + `pytest -m api`，在 `windows-latest` 上再跑一遍；不装浏览器、不跑 UI | ✅ 拦 |
| `test` | 先做契约漂移门禁 → 契约属性测试 → 再跑六组用例（接口含覆盖率门禁 → UI → 业务全链路 → 登录契约 → 流量回放子项目 → 平台层自测）→ qaplat 门禁（pr 层）→ 禅道上报桩服务 → 用例编号索引自检 → 重试与代理回退离线自检 → 依赖清单一致性自检 → 汇总 flaky 治理账本 + 生成 Allure 报告 | ✅ 拦（**UI 那一步除外**） |
| `security` | `pip-audit --requirement requirements.lock --strict` | ❌ 不拦（见下） |
| `pre-commit` | `pre-commit run --all-files`，验证 hook 配置本身没腐烂；再跑 `scripts/check_gate_policy.py` 校验门禁策略没腐烂 | ✅ 拦 |
| `commit-convention` | 拿最近 30 条真实提交跑 `scripts/check_commit_msg.py` | ✅ 拦（逐条警告，不聚合） |
| `perf` | 容量压测 4 梯度 × 2 次 × 20s，上传 `reports/perf/` 并在 Step Summary 里贴拐点 JSON。**仅 `workflow_dispatch` / `schedule` 触发，push 与 PR 不跑** | ❌ 不拦（见下） |
| `pages` | 现场跑 `qaplat.cli` 三步生成报表，再把整目录 force push 到 `gh-pages`，更新上面的在线报表。**PR 上只生成、不发布**（PR 未必进 main，提前发布等于发错版本） | ❌ 不拦（见下） |

报告在 Actions 页面的 Artifacts 里下载（`allure-report`），失败截图在 `ui-screenshots`。
qaplat 平台的 5 页 HTML 报表除了进 Artifacts，还会被 `pages` job 发到
<https://lefan17.github.io/fullstack-test-framework/>。

### 几个刻意的设计

- **`lint` / `typecheck` 单独开 job**：规范检查几秒钟就出结果，没必要等 playwright 装浏览器、
  等接口用例跑完才告诉你 import 顺序错了。乱序的 import 和没格式化的代码在 review 时
  是纯噪声，让机器去管，人只讨论逻辑。
  这些 job 是**必须过**的（没有 `continue-on-error`）：`ruff.toml` 立在仓库里很久了，
  但在此之前没有任何地方执行它——**写在配置里的规矩，不执行就等于没有规矩。**
- **UI 用例标了 `continue-on-error`**：它们打的是公共 demo 站，外部因素多。
  但失败不阻断流水线 ≠ 可以不管——报告和截图照样产出，需要人去看。
  **这层豁免与 `qaplat gate --layer pr` 不是一回事**：那条门禁的表达式是
  `smoke or api or platform`，其中 `smoke` **含 6 条** `testcases/ui/test_login.py` 的
  公网用例，所以这 6 条在 gate 里**没有豁免**——UI 那一步是 `continue-on-error`，gate 这一步不是。
  这是当前有意的取舍：gate 回答的是「这一层能不能放行」，不是「pytest 有没有报错」。
  **完整的理由、风险与退出条件写在 [`docs/质量门禁策略.md`](docs/质量门禁策略.md) 第六节**：
  不是"因为外部因素多"就完了，那里列了三类具体风险（静默腐烂、覆盖停止生长、报告靠自觉）
  和三条可判定的退出条件（改为本地站点 / 近 20 次 CI 失败率 ≤ 5% 且可归因 / 站点改版表现为确定的契约失败），
  以及改成阻塞时要同步改的四处。真实项目里这个开关要慎用，它很容易变成"红灯没人管"的起点。
- **`security` 非阻塞**：pip-audit 查的是**公共漏洞库的当前状态**，而不是这份代码的质量。
  今天全绿、明天可能因为某个传递依赖被披露了 CVE 而变红，那与本次提交毫无关系。
  设成阻塞等于让"上游披露漏洞"变成"任何人的 PR 都过不去"，最后一定是有人把它删掉。
  正确做法是让它红并**通知到人**，由人决定升级还是接受风险。
  扫的是 `requirements.lock` 而不是 `requirements.txt`：锁文件里是**实际会被安装的完整传递依赖树**，
  而绝大多数 CVE 出现在传递依赖上（urllib3 / certifi / anyio 之类），只扫直接依赖等于漏掉大半攻击面。
- **`perf` job 非阻塞、且 push/PR 不触发**：一批压测 4 梯度 × 2 次 × 20s 要 ~200 秒，而数字
  天然带 10% 量级的批次间噪声（见上文）。拿它当 PR 门禁等于给每个 PR 加一个"随机红灯"，
  结局一定是被人加 `continue-on-error` 或者干脆删掉。它的产出是**给人读的容量结论**
  （`summary.json` 的 `knee` 会贴进 Step Summary），不是通过/失败信号。
  要跑就手动点按钮，或跟周一夜间回归一起跑。
- **`pages` job 非阻塞、且 PR 上不发布**：它既不在测代码也不产出测试结论，只把 qaplat 的
  HTML 报表推到 `gh-pages` 分支。发布成功与否取决于外部状态（分支、Pages 服务、token 权限），
  这些不该获得把流水线弄红的权力——否则结果和上面 `security` 一样，被人一删了事。
  报表在 CI 上**现场生成**而不是从仓库里读：`qaplat/.cache/` 的语义是「本机跑过什么」的历史状态，
  属过程产物，不进 `main` 的历史。PR 上只生成不发布：PR 的内容未必会进 `main`，
  往 `gh-pages` 推等于提前发布一个可能被否决的版本。
- **Allure 用官方命令行生成，不用第三方 Docker action**。原来用的
  `simple-elf/allure-report-action@v1.9` 依赖镜像 `openjdk:8-jre-alpine`，
  该镜像已被 Docker Hub 下架，报 `failed to resolve source metadata`，
  结果整个 job 在第一步就失败、连测试都没跑到。第三方 action 的这种腐烂
  是真实工程里常见的一类风险：**引用别人的东西，就要承担它某天消失的代价。**
- **覆盖率门禁有两道，仓库级 54% + per-file 55%**：仓库级那道按语句数加权
  平均，**结构上就看不出单点失守**——一个模块整体掉到 0%，只要总量还高于
  54，它就是绿的。本仓库的真实形状是 `qaplat/cli.py`（253 语句）在 `-m api`
  口径下只有 13.8%，当时没有任何门禁会对此提出异议。第二道读**同一份**
  `--cov-report=json` 产物（不重跑，否则要么多花 100 秒，要么用一个更小的
  子集算出另一套数字，那时候"全局 55%、per-file 87%"会同时出现在两份报告里，
  谁也说不清哪个才算数），配置和排除理由都住在 `pyproject.toml` 的
  `[tool.coverage.perfile]` 旁边。阈值是**棘轮不是目标**：看到红灯该做的是
  补测试，不是调低它——本仓的 `ruff format` 曾经长期挂在 `continue-on-error`
  下，就是这么废掉的。
  脚本还会**验证每条 `exclude` 路径真实存在**：写错的排除等于没写，但它
  看起来写了，这是最坏的一种假安全感。

### 并行、超时、定时与通知

- **`-m api` 开 `-n 4 --dist loadfile`**（同一轮内实测约 2.0–2.4×，绝对耗时随机器负载与公网波动；
  三轮的原始数字见上方"关于 `-n` 并行"）。
  必须是 `loadfile`：默认的 `--dist load` 在改造前实测 `-m mes` → `30 passed, 1 error`（退出码 1），
  根因见上方"关于 `-n` 并行"。**注意那个失败并没有消失**——2026-10-17 复测是 1/4 通过，理由同上。
- **`-m mes` / `-m login` / `-m ui` 刻意不开并行**，理由都基于实测：
  ①`-m mes -n 4 --dist loadfile` 只快约 1.55 倍（24.24s vs 串行 37.43s），收益太小——
  实测只摊到 6 个测试文件，而每个 worker 都要付一次"造一个独立 SQLite + 起一个 mock_server 子进程"
  的启动成本，把能省的都吃掉了；
  ②这组用例里有**真实线程交叠**的竞态断言（`testcases/mes/concurrency.py` 的
  `assert_requests_overlapped`，靠 `threading.Barrier` 让请求同时在途），
  把机器塞满并行 worker 会拉长每个请求的墙钟、让交叠判据的边界变窄——
  虽然实测在 loadfile 下仍绿（`-n 8` 连跑 3 次 64 passed，23.19~25.67s），但在它自己该守的那一步上，
  用 20 秒换一份不确定性，不划算。
  **一句话：能不省的地方就不省——性能门禁和并发断言的价值全在于它说了实话。**
- **每个 job 都有 `timeout-minutes`**：一个卡死的 job 不设上限会占满默认的 360 分钟，
  把并发名额全部堵住。现在是 lint 10 / typecheck 15 / windows 20 / test 45 / security 15 / pre-commit 15 / commit-convention 10 / perf 20 / pages 20。
- **`concurrency` 只取消 PR 上的旧任务**：连续推 3 个提交修同一处时，
  前两次的流水线已经没有意义，让它们继续跑既占名额又会在 Actions 页面刷出一片过期的红叉。
  但 **`main` 上的 push 必须全部跑完**——每次合并到 main 都可能引入问题，漏掉任何一次都是"没测过就进主干"。
- **每周一凌晨的定时回归**（`cron: "0 18 * * 1"`，UTC）= 北京时间**周二 02:00**，
  在工作日开始前拿到结果，且跑的是没人在改代码的时间点。
  为什么需要它：push/PR 触发只能发现"这次改动弄坏了什么"，
  **发现不了时间带来的腐烂**——某个依赖发了新版、公网站点改了响应、
  runner 镜像换了 Python 补丁版本、Allure 的 npm 包发了破坏性更新。
  没有定时跑，这类问题会在某次无关的 PR 上突然爆出来，然后被当成"那个 PR 的锅"。
- **失败通知不需要任何外部 secret**，走两条路：
  ①`if: failure()` 把诊断信息（触发方式 / 分支 / 提交 / 执行者 / run 链接 + 排查顺序）写进 step summary。
  ②仅在 `push` 到 main 与定时回归时，用**内置的 `GITHUB_TOKEN`** 自动开或更新一个 issue
  （靠固定 label `ci-failure` 去重，同一时间只留一个未关闭的告警）。
  排除 PR 是因为 fork 来的 PR token 是只读的，开 issue 必然 403，那种失败会盖住真正的测试失败。
  > 想接企业微信 / 飞书 / Slack 的话，需要配一个 webhook secret 再在 workflow 里引用它。
  > 这里**没有**写任何引用不存在 secret 的步骤——那会让 workflow 直接报错，
  > 而且报错信息还指不到真正的地方。要加的时候请先配好 secret 再加步骤。


生成报告这步特意**没有**用 `allure generate --clean`：

```bash
rm -rf allure-report
allure generate allure-results -o allure-report
```

`--clean` 只存在于 Allure **2.x**（CI 装的 `allure-commandline`）。本地若装的是
**3.x**（npm 包名是 `allure`，是 Node 程序而非 Java 程序），同一句会直接报
`Unknown Syntax Error: Command not found`，且 3.x 的 `generate` **默认不清理输出目录**。
「先删目录」是 2.x / 3.x 都合法的写法，效果与 `--clean` 等价，
这样本地和 CI 可以用同一条命令，不会出现单边失败。

## 常见问题

**1. `pip install` 卡住不动（CPU 跑满但没输出）**
国内直连 PyPI 会导致依赖解析极慢。用清华源，或把 `pip.ini.example` 复制到 `%APPDATA%\pip\pip.ini` 永久生效。

**2. `playwright install` 报 TLS/证书错误**
官方 CDN 在国内常被阻断，设置 `PLAYWRIGHT_DOWNLOAD_HOST` 指向 npmmirror（见上方安装步骤）。
实测（playwright 1.63.0）：设上它之后 `python -m playwright install chromium --dry-run` 给出的
下载地址就会变成 `https://cdn.npmmirror.com/binaries/playwright/builds/cft/<版本>/win64/...zip`，
HTTP 200 可直接下载。

**3. 浏览器启动报 `EPERM: operation not permitted, mkdtemp ...\Temp\playwright-artifacts`**
系统临时目录受限（企业电脑、沙箱环境常见）。把临时目录指到项目内：

```powershell
New-Item -ItemType Directory -Force -Path .\.tmp | Out-Null
$env:TEMP = (Resolve-Path .\.tmp).Path
$env:TMP = $env:TEMP
```

**4. 中文输出乱码**
PowerShell 默认 GBK。设置 `$env:PYTHONIOENCODING = "utf-8"`，或在 VS Code 里用已配置好的终端（`.vscode/settings.json` 已设）。

**5. UI 用例想看着浏览器跑**
把 `testcases/ui/conftest.py` 里 `p.chromium.launch(headless=True)` 改成 `headless=False`。

注意这需要**完整版** Chromium，headless shell 不够：Playwright 1.63 起
`headless=True` 用的是独立的 `chromium_headless_shell-<rev>`，`headless=False` 要的是完整的
`chromium-<rev>`，只装了前者会报
`Executable doesn't exist at ...\ms-playwright\chromium-1243\chrome-win64\chrome.exe`（本机实测）。
先 `python -m playwright install chromium` 把两者都装上（`--dry-run` 会把它们分别列出来）。

**6. `playwright install chromium` 完全没输出、永远不动（0 下载）**
少数机器上 Playwright 自带的下载器会静默卡死。判断方法：看 `%USERPROFILE%\AppData\Local\ms-playwright`
下有没有新建目录、有没有增长。绕开办法是**先问真值、再手工下载 + 解压**——
版本号和下载地址只有一个可靠来源，就是 `--dry-run` 的输出，**不要照抄本文档里写死的 rev**：

```powershell
# 1. 先问清楚它期望放到哪、从哪下（这一步就是真值来源，换版本时重跑它）
python -m playwright install chromium --dry-run

# 2. 照上面输出里的 "Download url" 原样下载（下面的 $url 用第 1 步的输出替换）
#    国内可以先设镜像，让第 1 步直接给出 npmmirror 的地址：
$env:PLAYWRIGHT_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/playwright"
$url = '<第 1 步输出的 Download url 原样>'
curl.exe -L -o .\.tmp\chrome-headless-shell-win64.zip $url

# 3. 解压到第 1 步输出里对应的 "Install location"（zip 顶层目录名就是 Playwright 期望的子目录名）
$dest = "$env:USERPROFILE\AppData\Local\ms-playwright\chromium_headless_shell-<rev>"
Expand-Archive -Path .\.tmp\chrome-headless-shell-win64.zip -DestinationPath $dest -Force

# 4. 验证
python -c "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(); print('OK', b.version); b.close(); p.stop()"
```

> 注意几件事：
> - **`--dry-run` 会列出多个浏览器**。以本机 playwright 1.63.0 为例，它是
>   `Chrome for Testing 153.0.8010.12 (playwright chromium v1243)`（完整版，`headless=False` 用它）
>   与 `Chrome Headless Shell 153.0.8010.12 (playwright chromium-headless-shell v1243)`
>   （`headless=True` 用的就是它）。**默认的 headless 跑法要的是 headless shell**，
>   只装完整版并不能让 headless 用例跑起来（本机实测：只放完整版时 headless 启动仍报
>   找不到 `chromium_headless_shell-1243`）；想两种模式都能跑，两个 zip 都要装。
> - 版本号 `1140` 是 Playwright 1.48 时代的编号，与本机 1.63.0 期望的 `1243` 对不上；
>   照旧编号下载出来的目录名 Playwright 根本不认，只会继续报
>   `Executable doesn't exist at ...`。同理，旧的 `builds/chromium/<rev>/chromium-win64.zip`
>   老路径只对老 rev 有效（本机实测 1140 那个 URL 仍 200，但 `builds/chromium/1243/...` 是 404）。
> - **标记文件不是必需的**：本机 `chromium_headless_shell-1243` 目录里**本来就没有**
>   `INSTALLATION_COMPLETE` / `DEPENDENCIES_VALIDATED`，它照样能启动（1.63.0 只查可执行文件
>   在不在）。补上无害，但别把它当必要条件。
> - **这一步要用 PowerShell 做，不要用 Python 脚本来写这个目录**：受限环境下 Python 进程写
>   工作区外会被直接拒绝（本机实测 `PermissionError: [Errno 13] Permission denied`，
>   报错原文随环境不同，也有环境报 `[WinError 5]`），而 PowerShell 写同一路径可以。
> - 不想手工做这一步，也可以用 `tools/install_chromium.py`：它自己跑 `--dry-run` 读真值。

**7. 用 Docker 跑这套用例**

```powershell
# 起两个被测服务（Mock 后端 8000、登录服务 5001，分别映射到宿主机 18000 / 15001）
docker compose up -d mock-server login-app

# 跑用例（第三个 service 挂在 test profile 下，不会跟着 up 一起起）
docker compose run --rm tests

# 不跑容器，只检查 compose 文件本身写得对不对
docker compose -f docker-compose.yml config --quiet
```

> ⚠️ **本仓库的容器化只做过静态校验，从未在本机构建成功过。**
> 本机 Docker 客户端 29.7.2 与 Compose v5.5.0 都在，但 **daemon 起不来**
> （WSL 报 `E_ACCESSDENIED`；`docker compose config` 不需要 daemon 所以能过，
> 而 `docker build` 会失败于 `failed to connect to the docker API at
> npipe:////./pipe/dockerDesktopLinuxEngine; ... The system cannot find the file specified.`）。
> 所以：`docker compose -f docker-compose.yml config --quiet` 退出码 0 是**已验证**的；
> **"镜像能构建""容器能跑通"是未验证的**，需要在 daemon 可用的机器上补测。
> 这份文档里任何地方都不声称构建成功。
>
> 另外两条与容器相关的实际约束：镜像基于 `python:3.12-slim`，**里面没有 curl**，
> 所以 compose 的 healthcheck 用 `python -c "import urllib.request; ..."` 而不是 curl；
> `Dockerfile` 里是**精确 COPY 各个目录**而不是 `COPY . .`：
> 构建上下文的体积由根目录的 `.dockerignore` 负责（它已排除 `.venv/`、`.tmp/`、`.git/` 等），
> 而镜像里有哪些文件由这份精确 COPY 清单负责 —— 两者解决的不是同一件事，所以两者都保留。

**8. `pre-commit install` 卡住 / 报 `ensurepip` 权限错误**

部分受限环境（企业电脑、沙箱）禁止创建虚拟环境，`ensurepip` 会以
`PermissionError: [WinError 5]` 失败——**pre-commit 装不上**。
`.pre-commit-config.yaml` 仍然是有意义的，它**只在 CI 里验证**
（workflow 的 `pre-commit` job 会跑 `pre-commit run --all-files`）。
装不上的机器上，提交前手工跑一遍等价命令即可：

```powershell
python -m ruff check .
python -m ruff format --check .
```

**9. `pip-audit` 在本地跑不起来**

同样的 `ensurepip` 限制。依赖安全扫描**只在 CI 的 `security` job 里跑**，
而且是**非阻塞**的（理由见"持续集成"一节）。
想在本地查，可以 `pip install pip-audit` 后手工跑，但注意它会创建虚拟环境。

**10. `python -m mypy` 报 `PermissionError` 指向 `pip-build-tracker-*`**

如果之前用 `TEMP` 指到项目内的方式装过依赖，仓库根目录会留下
`pip-build-tracker-*` / `pip-ephem-wheel-cache-*` / `pip-install-*` / `pip-unpack-*`
这些临时目录，**且权限不可读**。`mypy .`（遍历整个仓库）撞上它们会直接崩溃。
`pyproject.toml` 里已经用 `files = [...]` 显式列出要检查的目录来绕开这个问题，
不依赖"仓库根目录干净"。这些目录也已在 `.gitignore` 里。

**11. 想确认 `TEST_ENV` 到底选中了哪份配置**

```powershell
python -c "from common.config import get_config; import json; print(json.dumps(get_config(), indent=2, ensure_ascii=False))"
```

不设 `TEST_ENV` 时它的输出与旧实现（`common.yaml_util.get_config()` 直接读 `config.yaml`）
逐字节一致——这是硬约束，改动配置加载器时必须保持。
`TEST_ENV` 给了不认识的值会直接抛 `ValueError` 并列出可用档案，**不会静默回落到默认档**：
静默回落意味着"以为在测 staging，实际打的是 dev"。
