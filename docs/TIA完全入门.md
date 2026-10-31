# TIA 完全入门：从一行改动到一条用例

> **写给谁**：已经会写 pytest、也跑过 `qaplat gate`，但没做过「变更 → 测试选择」（TIA，Test Impact Analysis）的人。你最好先读完 [初学者完全入门](初学者完全入门.md) 的第 0 章和第 7 章——知道 `qaplat` 是平台层、`gate` 是门禁、`nodeid` 长什么样，这篇才接得上。
> **读完你会知道**：改了一行代码之后，`qaplat tia select` 到底经过哪些文件、读了哪些数据、按什么顺序做判断，最后凭什么「只跑 1 条」而不是「跑 636 条」；以及这套判据**在哪里会失效**、失效时为什么必须退回全量。不是「tia 有哪些函数」，而是「一条改动怎么变成一串 nodeid」。
>
> 这份文档不教你「怎么用 `qaplat tia select`」（那是 CLI 帮助的事），也不重复讲基础框架（看 [初学者完全入门](初学者完全入门.md)）。它只回答一个问题：**从 git diff 的一行，到 pytest 命令行的那个 nodeid，中间的数据是怎么流动、怎么被判断的。**

---

## 目录

**第一部分：先建立地图**
- [第 0 章 全局地图：一次 `qaplat tia select` 都发生了什么](#第-0-章-全局地图一次-qaplat-tia-select-都发生了什么)
- [第 1 章 为什么需要 TIA：636 条用例与 196 秒的账](#第-1-章-为什么需要-tia636-条用例与-196-秒的账)

**第二部分：数据从哪来**
- [第 2 章 coverage-context：每一行代码被哪个用例执行过](#第-2-章-coverage-context每一行代码被哪个用例执行过)
- [第 3 章 变更行从哪来：git diff -U0 与 hunk](#第-3-章-变更行从哪来git-diff--u0-与-hunk)

**第三部分：怎么算出来**
- [第 4 章 从行号到用例：v1 的尸体与 v2 的做法](#第-4-章-从行号到用例v1-的尸体与-v2-的做法)
- [第 5 章 兜底判据：什么时候必须跑全量](#第-5-章-兜底判据什么时候必须跑全量)
- [第 6 章 接线：cli.py 到 gate.py 到 pytest](#第-6-章-接线clipy-到-gatepy-到-pytest)

**第四部分：实测与边界**
- [第 7 章 一次完整实测：四个场景的真实数字](#第-7-章-一次完整实测四个场景的真实数字)
- [第 8 章 与门禁的关系：为什么 tia 不挂覆盖率门](#第-8-章-与门禁的关系为什么-tia-不挂覆盖率门)
- [第 9 章 已知限制与 v2 路线](#第-9-章-已知限制与-v2-路线)

**附录**
- [附录 A 文件速查](#附录-a-文件速查)
- [附录 B 三条调用链](#附录-b-三条调用链)
- [附录 C 术语表](#附录-c-术语表)
- [附录 D FAQ](#附录-d-faq)

---

## 第 0 章 全局地图：一次 `qaplat tia select` 都发生了什么

### 0.1 先看总数

先把这篇文档要说到的所有规模记住。数字分三种来源，标在表里：**Lead 实跑**（这台机器上跑出来的）、**本文实测**（写文档时用仓库里的数据复核过的）、**代码常量**（读代码就知道、不会变）。

| 数字 | 值 | 来源 | 它说明什么 |
|---|---|---|---|
| 全量收集用例数 | **636** | Lead 实跑 `pytest --collect-only` | 基线采集口径；本文场景 A–D 的 ratio 都除以它（分层后的分母口径见 6.5） |
| `-m "not ui"` 实际执行数 | **602** | Lead 实跑：588 passed + 3 failed + 11 xfailed | 分母 636 里有 34 条 UI 用例被 deselect |
| 全量墙钟 | **196.55s（0:03:16）** | Lead 实跑 | 「全跑」的代价；TIA 要把它压下来 |
| 只跑 1 条用例的墙钟 | **2.83s** | Lead 实跑 | 场景 D 的选中集耗时，约 69 倍差距 |
| `.tia-coverage` 文件大小 | **757,760 字节** | Lead 实测 | SQLite 二进制；里面是「每行被谁执行过」 |
| `measured_files` | **121** | Lead 实测；本文复核 | coverage 记录到的文件数 |
| 原始 context 数 | **1807** | Lead 实测；本文复核 | 覆盖数据的「行级归属」总量 |
| 建索引后能用的文件 | **117** | 本文实测 | 121 减去 4 个「没有任何可用 context」的文件 |
| `qaplat/tia.py` 行数 | **766** | 本文实测 `wc -l` | 这篇文档要拆的全部代码量（含 Lead 修复 `core.quotepath` 时加的 5 行，见 3.8） |
| 公开入口函数 | **4 个** | 代码常量 | `changed_files` / `select` / `select_by_lines` / `select_from_git`，另有 `changed_hunks`、`load_context_index` 两个 v2 数据入口 |

一个必须先记住的结论：**TIA 不是「让测试变快」的魔法，它是一次「影响面分析」**。它算出的是「哪些用例可能受这次改动影响」，然后把这个集合交给 pytest 的路径参数。它不改变 pytest 怎么跑、也不改变门禁怎么判。

### 0.2 一次 `qaplat tia select` 的完整生命周期

命令入口在 `qaplat/cli.py:261`（`cmd_tia_select`）。本仓 `qaplat` 包**没有 `__main__.py`**，所以正确的入口是 `python -m qaplat.cli`；写成 `python -m qaplat` 会得到 `No module named qaplat.__main__`。下文所有命令示例都用前者。

从你按下回车的瞬间到屏幕上出现那段文本，一共经过下面这些步骤。每一步都标了「哪个文件、哪一行、读什么、写什么」：

```
你敲下： python -m qaplat.cli tia select
   │
   ├─① cli.main() 解析子命令
   │     qaplat/cli.py:614  main()  →  qaplat/cli.py:553  注册了 tia select 子命令
   │     ↓
   ├─② cmd_tia_select(args)                           qaplat/cli.py:240
   │     先决定「用哪份覆盖率数据」：                     qaplat/cli.py:175
   │       --coverage-data 显式给了就用它（不存在 → 退出码 2，不静默退回 v1）
   │       没给就按约定找：.tia-coverage → .coverage → 都没有则 None（退回 v1）
   │     ↓
   ├─③ _pytest_args_from("--pytest-args ...")           qaplat/cli.py:199
   │     把 "--pytest-args '-m smoke'" 这一整串用 shlex 切成 argv
   │     ↓
   ├─④ tia.select_from_git(base, pytest_args, include_worktree, data_path)
   │                                                  qaplat/tia.py:686
   │     ↓（这是唯一把几种 IO 串起来的函数）
   ├─⑤ registry.collect_cases(pytest_args)             qaplat/registry.py:202
   │     起一个 pytest 子进程：pytest --collect-only -q -q --no-header -p qaplat._dump
   │     产出 list[CaseInfo]：nodeid + markers + referenced_paths + covers
   │     ↓
   ├─⑥ tia.changed_hunks(base, include_worktree, cwd)  qaplat/tia.py:334
   │     ├─ changed_files(...)                         qaplat/tia.py:298
   │     │    三条 git 命令：diff --name-only base...HEAD / diff --name-only HEAD / ls-files --others
   │     └─ 逐文件 git diff -U0 → _new_lines_from_diff → ChangedHunk(path, lines)
   │     ↓
   ├─⑦ tia.load_context_index(data_path, root)         qaplat/tia.py:387
   │     coverage.CoverageData 读 SQLite
   │     → {仓库相对路径: {行号: (nodeid, ...)}}
   │     ↓
   ├─⑧ tia.select_by_lines(hunks, index, cases)        qaplat/tia.py:567
   │     纯函数：不读盘、不起子进程、不调 git
   │     → Selection(node_ids, full, reason, strategy, ...)
   │     ↓
   ├─⑨ tia.render_json(sel) 或 tia.render_text(sel)     qaplat/tia.py:748 / 715
   │     ↓
   └─⑩ 打印结果，退出码 0（选择器自身失败才是 2）
```

再看 `gate` 侧。`python -m qaplat.cli gate --layer pr --select tia` 走的是同一批函数，只是最后一步不同——它不打印，而是把选中的 nodeid 塞进 pytest 的命令行：

```
python -m qaplat.cli gate --layer pr --select tia
   │
   ├─ cmd_gate(args)                                  qaplat/cli.py:118
   │    data_path = _resolve_coverage_data(args.coverage_data)   qaplat/cli.py:175
   │    ↓
   ├─ _tia_extra(args, spec, data_path)                qaplat/cli.py:219
   │    sel = tia.select_from_git(...)                 ← 与 tia select 完全同一条路径
   │    if sel.full: return args.passthrough           ← 全量兜底 = 不加 nodeid
   │    return list(sel.node_ids) + list(args.passthrough)
   │    ↓
   ├─ gate.run_layer(spec, extra)                      qaplat/gate.py:187
   │    argv = [sys.executable, "-m", "pytest", *gate.build_pytest_args(spec, extra)]
   │    ↓
   └─ pytest 子进程：-m "smoke or api or platform" -p qaplat._dump ... <nodeid...>
```

**注意第 ⑧ 步的「纯函数」三个字**：`select_by_lines`（以及 v1 的 `select`）不碰任何 IO。测试可以直接喂构造数据断言它的判断；真实运行时，IO 全部集中在 ⑤⑥⑦ 三个函数里。这条分界线是整份实现最重要的架构决定，第 4 章会反复回到它。

### 0.3 三个文件在链上的位置

| 文件 | 在链上的位置 | 读什么 | 写什么 / 产出什么 |
|---|---|---|---|
| `.tia-coverage`（SQLite） | 链的最上游，离线产物 | —— | `file` 表 121 行、`context` 表 1807 行、`line_bits` 位图 |
| `qaplat/tia.py` | 唯一的判断中枢 | 上面那份 SQLite、git 的输出、`registry.collect_cases()` 的用例表 | `Selection`（含 node_ids / full / reason / strategy） |
| `qaplat/cli.py` | 入口与出口 | 命令行参数、`Selection` | 终端文本、JSON、或 `extra` 参数列表 |
| `qaplat/gate.py` | 执行层 | `LayerSpec`、`extra` | pytest 子进程与其产出 `PytestRun` |
| `pytest` 子进程 | 链的最下游 | argv 里的 `-m` 与 nodeid 路径参数 | 用例结论（passed/failed/...） |

用一句话概括它们的关系：**`.tia-coverage` 回答「谁执行过哪一行」，git 回答「哪几行变了」，`tia.py` 把两个答案求交集，`cli.py` 把交集变成文字或命令行，`gate.py` 把命令行变成一次真实的 pytest 运行。**

### 0.4 数据流全景

```
                  一次「全量 + 带 context」的 pytest 运行
                  （产出物，离线存在）
                            │
                            ▼
                  .tia-coverage（SQLite）
                  ├─ file     121 行：被测文件绝对路径
                  ├─ context 1807 行：nodeid|run / nodeid|setup / nodeid|teardown / 空串
                  └─ line_bits       ：每个文件每行的执行位图（按 context 分组）
                            │
                            │  load_context_index()            qaplat/tia.py:387
                            ▼
                  dict[仓库相对路径 -> dict[行号 -> (nodeid, ...)]]
                            │
   git diff -U0  ───────────┤
   changed_hunks()          │  select_by_lines()              qaplat/tia.py:567
   qaplat/tia.py:334        ▼
                  Selection(node_ids, full, reason, strategy)
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
   render_text / render_json          _tia_extra()            qaplat/cli.py:219
   （人看 / CI 解析）                  ↓
                             list[nodeid] 作为 extra
                                      ↓
                             build_pytest_args(spec, extra)   qaplat/gate.py:139
                                      ↓
                             pytest -m "..." <nodeid...>
                                      ↓
                             只跑受影响的那些用例
```

### 0.5 为什么先读 `tia.py` 的 766 行就够

`qaplat/tia.py` 虽然只新增了一个文件，但它把整条链的「形状」固定下来了：

- **上界**：`Selection` 这个 dataclass 是所有下游（CLI、gate、报表）唯一认识的数据结构；
- **下界**：`changed_files` / `changed_hunks` / `collect_cases` 三个函数是它唯一允许碰的外部世界；
- **中间**：`select` / `select_by_lines` 是两个纯函数，判断逻辑全在这里。

读代码时建议按这个顺序：先看常量区（`qaplat/tia.py:51-98`）搞清「哪些文件一改就全量」；再看 `changed_files`（`qaplat/tia.py:298`）和 `changed_hunks`（`qaplat/tia.py:334`）搞清「变更行怎么来」；然后看 `select`（v1，`qaplat/tia.py:466`）——**它虽然已经不是默认路径，但它是理解「为什么会失败」的最好教材**；最后看 `load_context_index`（`qaplat/tia.py:387`）和 `select_by_lines`（`qaplat/tia.py:567`）。

---

## 第 1 章 为什么需要 TIA：636 条用例与 196 秒的账

### 1.1 先看总数：用例库是怎么膨胀的

| 数字 | 值 | 说明 |
|---|---|---|
| 收集到的用例总数 | **636** | `pytest --collect-only` 的真实产出 |
| 其中「一个函数展开出来」的 | **188 条** | `testcases/contract/test_schemathesis_properties.py` 单文件，hypothesis 按 OpenAPI 生成 |
| UI 用例（要浏览器） | **34 条** | `-m "not ui"` 时被 deselect |
| 全量非 UI 一次运行 | **602 条** | 588 passed + 3 failed + 11 xfailed |
| 全量墙钟 | **196.55s** | 同一台机器、同一份代码 |
| 平均每条用例 | **≈0.33s** | 196.55 / 602，只是均摊，不代表单条成本 |

这 188 条是理解「为什么必须有 TIA」的钥匙：**它们不是 188 段不同的断言，而是同一段属性测试被参数化展开的 188 个实例**。改一个登录字段的校验逻辑，这 188 条理论上都该跑；但改的是 `mock_server/services.py` 里一个和登录字段无关的函数，它们一条都不该跑——而全量门禁分不清这两种情况，只能全跑。

这个副作用有一个很清楚的因果链（`docs/tia-plan.md` §1 的叙事）：

> AI 编码 Agent 让「写用例」变便宜 → 用例库膨胀 → 全量门禁变慢 → **TIA 只跑受影响的那部分** → 耗时回落。

### 1.2 196 秒里花在哪

把一次全量运行拆开看：

```
196.55s
 ├─ 会话级准备（pytest 启动、conftest 导入、fixture 定义)
 ├─ 602 条用例执行（含 34 条被 deselect 的 UI 用例的收集期开销）
 └─ 报告与收尾
```

我们没法在不改变语义的前提下「让单条用例变快」，所以唯一能压缩的就是**条数**。场景 D 的实测把这件事说得很直白：同一台机器、同一个仓，

- 跑全量：**196.55s**；
- 只跑 TIA 选中的 1 条：**2.83s**；
- 差距 **约 69 倍**。

注意 2.83s 里仍然包含 pytest 启动、conftest 导入、`mock_server` 起服务的固定开销——也就是说**选得再准，也压不到 0**。这决定了 TIA 的设计取向：**先把「明显无关」的一大堆剔除掉，比追求「精确到唯一一条」重要得多**。

### 1.3 「选」而不是「裁」：漏跑比多跑贵得多

TIA 有一个必须写死的价值判断：**宁可多跑，不可漏跑**。

原因不抽象。假设一次改动真的会让某条用例红，而 TIA 把它漏了：

- 你看到的是「门禁绿了」；
- 实际发生的是「回归被放过去了」；
- 而且**没有任何信号**告诉你「这次有东西没跑」。

漏跑是**静默**的，多跑只是**慢**。慢可以在任何一天优化，静默的错误会在合并之后才炸。所以这套实现的每一条兜底判据都是同一个方向：**只要「影响面判定不了」，就退全量（`full=True`），而不是猜一个较小的集合**。

`docs/tia-plan.md` §5 定义了验收口径：

> **漏检率定义**：`全量跑的 failed nodeid 集合` 中，**不在** TIA 选中集里的数量。必须为 0。

第 7.8 节给了实测：把 `normalize_path` 一行故意改坏，platform 层跑出 7 条真红，TIA 只选 11 条（该层实际执行 296 条，约 3.7%），**7 条真红一条都没漏在算法之外**（其中 3 条未选中是索引陈旧，补采即覆盖）。

### 1.4 为什么「按目录」「按 marker」不够

最容易想到的三种「土办法」，都在真实仓库上不成立：

| 土办法 | 为什么不成立 |
|---|---|
| 按目录：改了 `mock_server/` 就跑 `testcases/api/` | `mock_server` 被 4 个测试目录同时依赖（api / login / mes / contract），改哪个文件影响哪一层完全看不出来 |
| 按 marker：改了接口逻辑就跑 `-m api` | marker 是**用例的标签**，不是**源码的属性**；`test_schemathesis_properties.py` 一条函数 188 个实例，全在 `-m contract` 里 |
| 按文件名猜：`routers.py` 对 `test_routers.py` | 这个仓库里没有这种一一对应；而且改 `services.py` 会同时影响 `mes`、`contract`、`login` 三处 |

**TIA 换了一个问题**：不问「这个文件属于哪一层」，而问「这行代码被哪些用例执行过」。这个问题的答案是**实测出来的**，不是配置出来的——这是它比前三种办法准的根本原因。

### 1.5 两个必须先认清的边界

1. **TIA 不保证「一条不漏」**。它的保证是「判定不了就全量」。只要你的改动落在判据覆盖不到的地方（动态路径、跨语言、配置外的东西），它会退回全量——慢，但不会静默漏。
2. **TIA 不是「测试加速器」**。它不改变 pytest 的并发、不缓存结果、不跳过 fixture。它只做一件事：**决定这次传给 pytest 的 nodeid 列表**。

---

## 第 2 章 coverage-context：每一行代码被哪个用例执行过

### 2.1 先看总数

| 指标 | 值 | 来源 |
|---|---|---|
| `.tia-coverage` 字节数 | **757,760** | Lead 实测 |
| SQLite 里的 `file` 行数（= `measured_files`） | **121** | Lead 实测；本文复核 |
| SQLite 里的 `context` 行数 | **1807** | Lead 实测；本文复核 |
| 建索引后实际可用的文件 | **117** | 本文实测 |
| 索引里不同的「值」 | **603** = 602 个 nodeid + 1 个空串哨兵 | 本文实测 |
| coverage 版本（写在 `meta` 表里） | **7.16.2** | 本文实测 |

这里有个容易读错的地方：**`measured_files` 是 121，但建完索引只剩 117**。差的 4 个文件不是 bug，第 2.7 节会解释。

### 2.2 context 是什么

普通覆盖率只回答「这一行执行过没有」。`coverage.py` 有一个更细的模式：`--cov-context=test`，它会给每一次测量打一个**标签**（context），标签内容由 pytest-cov 填成「当前正在跑的用例的 nodeid」：

```
testcases/login/test_login_contract.py::test_lc01_login_success|run
└──────────────────────── nodeid ────────────────────────┘ └ 阶段 ┘
```

于是同一行代码的「执行记录」不再是布尔值，而是**一串 nodeid**：哪些用例跑过这一行，一目了然。

`qaplat/tia.py` 的整个 v2 策略就建立在这个事实上：**如果一行代码只被 1 个用例执行过，而这次改动正好落在这行，那么「需要重跑的用例」至少有这 1 个**——这不需要理解代码语义，是实测出来的执行关系。

### 2.3 四种形态：`|run`、`|setup`、`|teardown`、空串

平台层采集的 161 个 context 样本，分布是这样的（Lead 实测）：

| 形态 | 数量 | 含义 | TIA 怎么处理 |
|---|---|---|---|
| `<nodeid>\|run` | **130** | 用例**函数体**执行了这一行 | 命中该 nodeid |
| `<nodeid>\|setup` | **15** | 该用例的 **fixture / setup** 执行了这一行 | 同样命中该 nodeid |
| `<nodeid>\|teardown` | **15** | 该用例的 **teardown** 执行了这一行 | 同样命中该 nodeid |
| 空串 `""` | **1** | 这一行在**模块导入期**执行，当时没有任何用例在跑 | 记为「模块级」，触发全量兜底 |

**为什么 `setup` / `teardown` 也算命中**：如果某条用例的 fixture 里执行了这一行，说明这条用例**依赖**这行代码的行为。改了它，这条用例的准备工作就可能变，当然要重跑。只有 `run` 才算命中，会漏掉一批「靠 fixture 建立前置状态」的用例。

**为什么空串危险**：模块导入期的代码（`import`、常量、装饰器、函数定义本身）不属于任何用例。如果把它当成「没有任何用例执行过」，那么「改了 import 行」会被判成「影响 0 条用例」——这恰好是最需要全量的情况。所以索引里用一个空串哨兵把它标出来，交给 `select_by_lines` 判全量（第 4 章）。

**为什么不能无条件按 `|` 切**：参数化 id 里可能自带竖线，例如 `test_x[a|b]`。`context.split("|")[0]` 会把 id 切成 `test_x[a`，永远匹配不上任何 `CaseInfo`。实现里只在**后缀确实属于 `{run, setup, teardown}`** 时才切（`qaplat/tia.py:370` 的 `_context_node_id`）：

```python
# qaplat/tia.py:370
def _context_node_id(context: str) -> str | None:
    if not context.strip():
        return _MODULE_LEVEL  # 空串 → 模块级哨兵，不是 None：必须留下痕迹
    head, separator, suffix = context.rpartition("|")  # 只切最后一个 |
    text = head if separator and suffix in _CONTEXT_STAGES and head.strip() else context
    if "::" not in text:
        return None  # 不是 nodeid（如 --cov-context=dynamic 写的自定义标签）→ 丢弃
    return text.strip()
```

### 2.4 空 context 为什么必须留下来

实测数字（`.tia-coverage`，本文复核）：

| 文件 | 被覆盖行数 | 其中模块级（含空 context）行数 | 占比 |
|---|---|---|---|
| `qaplat/registry.py` | **218** | **57** | 26% |
| `qaplat/gate.py` | **233** | **109** | 47% |
| `mock_server/routers.py` | **444** | **0** | 0% |
| `mock_server/services.py` | **486** | **0** | 0% |
| `mock_server/quantity.py` | **59** | **0** | 0% |
| `mock_server/idempotency.py` | **82** | **0** | 0% |

这个分布非常说明问题：

- **被测服务（`mock_server/*`）几乎没有模块级行**——它的行几乎全部在请求处理过程中执行，天然带用例 context。这解释了为什么行级 TIA 在它身上效果最好。
- **平台层（`qaplat/*`）近一半是模块级**——`gate.py` 233 行里 109 行是模块级（函数定义、常量、`LAYERS` 字典）。这意味着**改平台层的「定义行」会频繁触发全量**，这是 v2 的已知成本，不是缺陷：改一个函数的**签名**确实影响所有调用它的用例。

### 2.5 SQLite 里到底存了什么

`.tia-coverage` 不是文本，是一份 SQLite 数据库。实测的表结构（本文实测）：

```
sqlite> .tables
coverage_schema   meta   file   context   line_bits   arc   tracer
```

| 表 | 行数 | 内容 |
|---|---|---|
| `meta` | 2 | `version=7.16.2`、`has_arcs=0` |
| `file` | **121** | 每个被测文件的绝对路径 |
| `context` | **1807** | 每个 context 字符串一行（`nodeid|run` 等） |
| `line_bits` | 多行 | 「某个文件的某段行号 → 某个 context 的执行位图」 |
| `arc` | 空 | 分支覆盖率数据；本仓没开 `--cov-branch`，所以是空的 |

`coverage.CoverageData.contexts_by_lineno(file)` 做的事情，就是把 `line_bits` 和 `context` 两张表 join 起来，还原成「这个文件的第 123 行，被哪些 context 执行过」。`load_context_index`（`qaplat/tia.py:387`）在此基础上再做一层：把 context 还原成 nodeid、把绝对路径还原成仓库相对路径。

**一个数字上的巧合，值得记一下**：1807 = 602 × 3 + 1。

- 602 是本次运行真正执行的用例数（`-m "not ui"`）；
- 每个用例贡献 3 条 context（run / setup / teardown）；
- 再加 1 条空串（模块级）。

所以「1807 个 context」听起来很多，实际信息量是「602 条用例 × 3 个阶段 + 模块级」——这也解释了为什么索引里不同的值只有 603 个。

### 2.6 怎么采集这份数据

采集和 TIA 是两件事，分两次跑。标准命令（Lead 用的口径）：

```bash
# 唯一目的是采集 per-test context；这一次的用例结果不用来看门禁
COVERAGE_FILE=.tia-coverage \
  <VENV>/python.exe -m pytest -m "not ui" --cov=. --cov-context=test
```

为什么写 `COVERAGE_FILE=.tia-coverage` 而不是默认的 `.coverage`：

- `qaplat/cli.py:176` 的 `_resolve_coverage_data` 优先找 `.tia-coverage`，再找 `.coverage`；
- 用独立文件名，不会把「平时看报告用的那份覆盖率」覆盖掉；
- `.coverage` / `.coverage.*` 已经在 `.gitignore` 里，不会被误提交。

为什么必须开 `--cov-context=test`：没有这个开关，`contexts_by_lineno` 返回的是空集或只有空串，索引建出来一片「模块级」，行级策略会疯狂退全量。`load_context_index` 在索引为空时**直接抛 RuntimeError** 而不是返回空字典，就是为了让这种「白采集了」的情况立刻暴露。

为什么 `.coveragerc` 里 `parallel = true` 很重要（`.coveragerc` 第 17 行）：

```
[run]
parallel = true
```

- 被测服务跑在子进程里（`mock_server` 由 fixture 起）；子进程的覆盖率要落盘，就必须让每个进程各写一份分片，最后由父进程 `combine`；
- 本仓的 `pytest-cov` 固定在 6.x（7.0 移除了 `COV_CORE_*` 子进程通道），所以 `--cov` 下子进程数据也能进来；
- 最终 `.tia-coverage` 是 combine 后的单份文件——TIA 只读这一份。

### 2.7 121 与 117 的差：4 个文件没有「可用的 context」

`measured_files()` 返回 121，但建完索引只有 117。少的 4 个是（本文实测）：

| 文件 | 为什么没有可用 context |
|---|---|
| `mes_web/__init__.py` | UI 层服务代码；本次 `-m "not ui"` 没有跑 UI 用例，它没有被任何用例执行过 |
| `mes_web/app.py` | 同上 |
| `mes_web/run.py` | 同上 |
| `qaplat/tia.py` | 收集期不会 import 它；`select_from_git` 里 `from qaplat import tia` 只在 CLI 里发生，而采集时没跑 CLI |

这带来两个直接影响，第 9 章会展开：

- **改 `mes_web/*` 的任何一行 → 全量**（文件不在索引里，`select_by_lines` 判「不在覆盖索引里」）；
- **改 `qaplat/tia.py` 自己的任何一行 → 全量**（同理）。这不是 bug：一个从未被执行过的文件，没有任何证据说明「改动它会影响谁」，全量是唯一诚实的答案。

---

## 第 3 章 变更行从哪来：git diff -U0 与 hunk

### 3.1 先看总数

一次行级选择要跑多少条 git 命令？取决于变更文件数 N：

| 命令 | 次数 | 作用 |
|---|---|---|
| `git diff --name-only <base>...HEAD` | 1 | 已提交的变更文件 |
| `git diff --name-only HEAD` | 1（`include_worktree=True`） | 工作区 + 暂存区相对 HEAD 的变更 |
| `git ls-files --others --exclude-standard` | 1（`include_worktree=True`） | 未跟踪但没被 `.gitignore` 忽略的新文件 |
| `git diff -U0 <base>...HEAD -- <file>` | N | 每个变更文件的新增行号 |
| `git diff -U0 HEAD -- <file>` | N（`include_worktree=True`） | 每个变更文件的工作区新增行号 |

为什么「逐文件再 diff 一次」，而不是解析一次全量 diff 的 `+++ b/...` 头——第 3.5 节讲。

### 3.2 三条 `--name-only` 各自回答什么

`changed_files`（`qaplat/tia.py:298`）固定执行三条命令，缺一条都会漏掉某一类改动：

```python
# qaplat/tia.py:298（节选）
found.update(line.strip() for line in _run_git(["diff", "--name-only", f"{base}...HEAD"], workdir).splitlines())
if include_worktree:
    found.update(line.strip() for line in _run_git(["diff", "--name-only", "HEAD"], workdir).splitlines())
    found.update(
        line.strip() for line in _run_git(["ls-files", "--others", "--exclude-standard"], workdir).splitlines()
    )
return tuple(sorted(found))  # 稳定排序：同一份 diff 永远产出同一份 tuple
```

| 命令 | 回答的问题 | 漏了它会怎样 |
|---|---|---|
| `base...HEAD` | 「本分支相对 merge-base 改了哪些已提交文件」 | 提交过的改动被当成本次没改 → 漏跑 |
| `diff --name-only HEAD` | 「工作区里还有哪些没提交的改动」 | 本地调试时最常见的场景失效 |
| `ls-files --others` | 「新增了哪些还没 add 的文件」 | 新建的测试文件/源码不会被选中 |

三点 `...` 而不是两点 `..`（`qaplat/tia.py:298` 的 docstring）：

> 用**三点** `...` 而不是两点 `..`：PR 门禁关心的是「本分支相对 merge-base 改了什么」；`..` 会把 base 分支自己走远的那部分也算进来，变更集于是随 base 的推进而抖动。

`include_worktree` 默认 `True`，因为本地手动跑 TIA 时最常看的正是「还没提交的改动」。CI 里应该用 `--no-worktree` 关掉——构建机上残留的生成物（测试报告、临时文件）会被当成业务变更。

### 3.3 hunk 是什么

`git diff -U0` 的输出被切成一段段「hunk」，每段以 `@@` 开头：

```
@@ -12,3 +12,4 @@ def some_function():
```

意思是：旧文件从第 12 行起 3 行，被替换成新文件从第 12 行起 4 行。`-U0` 表示**不要上下文**——默认的 `-U3` 会把改动行前后各 3 行也打印出来，那些行**没有变**，但会被笨解析器当成变更行。

为什么必须 `-U0`（`qaplat/tia.py:334` 的 docstring）：

> 为什么必须 `-U0`：默认 3 行上下文会把「没改的行」也算成变更行，命中面被无谓放大，行级精度直接退化；行级策略要的恰恰是**真正新增/修改的那几行**。

### 3.4 解析：`_new_lines_from_diff`

`qaplat/tia.py:262` 的正则和取数逻辑：

```python
# qaplat/tia.py:89
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


# qaplat/tia.py:262
def _new_lines_from_diff(patch: str) -> set[int]:
    lines: set[int] = set()
    for match in _HUNK_RE.finditer(patch):
        start = int(match.group(3))  # 新文件起始行
        count = int(match.group(4) or 1)  # 行数省略时是 1
        if count <= 0:
            continue  # 纯删除：d=0，没有目标行，跳过
        lines.update(range(start, start + count))
    return lines
```

三个必须记住的细节：

1. **只取新文件侧（`+c,d`）**。TIA 要的是「新代码的哪几行变了」，旧行号在新文件里可能已经不存在。
2. **`count` 省略时是 1**。`@@ -5 +5 @@` 和 `@@ -5,1 +5,1 @@` 等价。
3. **`d == 0` 是纯删除**。例如删除最后一行会产生 `@@ -10,1 +9,0 @@`。删掉的行在新文件里没有对应行号，硬编一个只会误命中。这类改动交给「命中为空 → 全量」兜住。

一次实测（临时 git 仓库，本文实测）：`a.txt` 原来 3 行，改成 4 行并把第 2 行改掉；同时新增未跟踪的 `b.txt`。解析结果：

```python
(ChangedHunk(path="a.txt", lines=(2, 4)), ChangedHunk(path="b.txt", lines=()))
```

注意两个信号：

- `a.txt` 的 `(2, 4)` 正好是「改掉的第 2 行」与「新增的第 4 行」；
- `b.txt` 的 `lines=()` 是**空元组**，不是「没变化」——它是未跟踪文件，`git diff HEAD` 对它返回空，所以拿不到目标行。`select_by_lines` 会把空元组判成「影响面未知 → 全量」。

`lines=()` 的四种来源（`qaplat/tia.py:103` 的 `ChangedHunk` docstring）：

> 纯删除、二进制、纯重命名、未跟踪文件都落在这里。行级策略必须把它当成「影响面未知」全量兜底——没有目标行不等于没有影响。

### 3.5 为什么逐文件再跑一次 `git diff`

`changed_hunks`（`qaplat/tia.py:334`）不是解析一次全量 diff，而是对每个变更文件单独跑 `git diff -U0 -- <raw>`。理由写在 docstring 里：

> 为什么逐文件再调一次 `git diff`，而不是解析全量 diff 的 `+++ b/...` 头：路径里带空格、非 ASCII 或引号时 git 会做 C 风格转义，解析头部要写一套反转义且容易错；这层多起几个子进程是毫秒级成本，换来的是「路径永远来自 `--name-only`，与 `changed_files` 逐字一致」。

换成白话：全量 diff 的文件头长这样——

```
diff --git a/路径 带空格.txt b/路径 带空格.txt
```

路径里有空格、中文、引号时，git 会输出 `"a/\346\226\207\344\273\266.txt"` 这种 C 风格转义。你要么写一个反转义器（容易出错、边界多），要么**让 git 自己把路径当参数**——`git diff -- <path>` 直接用 `--name-only` 给的原始路径，不需要解析任何东西。多敲几个子进程（毫秒级）换掉一整类解析 bug，非常值。

### 3.6 git 失败为什么必须抛错

`_run_git`（`qaplat/tia.py:203`）对任何非 0 退出码都抛 `RuntimeError`，并把命令、cwd、退出码、stdout / stderr 的尾部一起带出来。实测（本文）：

```
RuntimeError: git 读取变更失败，TIA 不能在这种状态下判断影响面：
  命令: git diff --name-only no-such-ref-xyz...HEAD
  cwd: D:\DSH\test-projects\api-regression-selector
  returncode: 128
  stdout 尾部:
（空）
  stderr 尾部:
fatal: ambiguous argument 'no-such-ref-xyz...HEAD': unknown revision or path not in the working tree.
```

为什么不能「失败就返回空元组」：`changed_files` 返回 `()` 的语义是**「本次没有任何变更」**，下游会据此认为「一条用例都不用跑」。git 不在 PATH、base 写错、不在仓库里、权限问题——任何一个原因都会变成「静默跳过全部门禁」。这正是 TIA 最危险的失效模式，所以这里宁可让整条命令失败退出（CLI 会把它折成退出码 2）。

### 3.7 小结：从 diff 到 `ChangedHunk`

```
git diff --name-only base...HEAD      ┐
git diff --name-only HEAD             ├─→ changed_files() → tuple[str, ...]（稳定排序）
git ls-files --others                 ┘              │
                                                     ▼
                             对每个文件：git diff -U0 ... -- <file>
                                                     │
                                                     ▼
                              _new_lines_from_diff() → set[int]
                                                     │
                                                     ▼
                          tuple[ChangedHunk(path, lines)]（按 path 排序）
```

到这里，「哪几行变了」已经有了。下一步要回答的是：「这几行，被哪些用例执行过？」——那需要第 2 章那份 SQLite 数据。

---

### 3.8 一个看起来无害的默认值：`core.quotepath` 让整个方案静默失效

前面 3.2 ~ 3.6 讲的都是「解析」的正确性。这一节讲一个更阴的坑：**解析没错，是 git 交给你的字符串已经被改写过一遍了。**

#### 3.8.1 现象

本次写文档期间，`tia select` 对「只改了一个中文文件名」的工作区，给出了这么一行原因：

```
原因: "docs/TIA/345/256/214/345/205/250/..." 没有可用的新增行（纯删除/重命名/二进制/未跟踪），按全量兜底
```

`docs/TIA/345/256/214/...` 根本不是任何真实路径——真实文件是 `docs/TIA完全入门.md`。于是：

1. 这个假路径在 `.tia-coverage` 里当然找不到；
2. 所有用例的 `referenced_paths` 也不会引用它；
3. 判据「不在覆盖索引里 / 没有被任何用例执行过」依次成立；
4. **结果：全量兜底（636 条）**，而正确答案是「新增的是一份文档，跟任何用例都无关」。

#### 3.8.2 原因：git 默认把非 ASCII 路径转义

git 有一个默认配置 `core.quotepath=true`：当路径里含非 ASCII 字符时，输出会被转成**八进制转义 + 双引号**的形式：

```bash
$ git ls-files --others --exclude-standard
"docs/TIA\345\256\214\345\205\250\345\205\245\351\227\250.md"
#   å® = 「完」的 UTF-8 字节 e5 ae 8c，其余同理
```

`_normalize_path`（`qaplat/tia.py:148`）挡不住它：那个函数做的是「反斜杠→斜杠、去 `./`、剥 ROOT 前缀」——它把八进制转义里的 `\` 当成**分隔符**处理，结果就是上面那串 `docs/TIA/345/256/214/...` 的假路径。

**更麻烦的是触发条件**：只有**非 ASCII 文件名**才会被转义。纯英文仓库里永远看不到这个问题——如果你在英文项目上验收通过，就以为它没问题，中文项目上就会静默失效。

#### 3.8.3 修复：在源头关掉转义

修在 `_run_git`（`qaplat/tia.py:203`）里，一处改动覆盖所有 git 调用：

```python
# qaplat/tia.py:212-216（修复后的样子）
    # ``-c core.quotepath=false``：git 默认把非 ASCII 路径转义成八进制（``"\345\256\214..."``），
    # 那样拿到的是一串假路径：匹配不上覆盖索引与用例，于是被判「影响面未知」而误触发全量兜底。
    # 关掉转义后输出的是原样 UTF-8 路径，与 .coverage / 用例里的路径可以直接比。
    argv = ["git", "-c", "core.quotepath=false", *args]
```

为什么不在 `_normalize_path` 里「把八进制转回 UTF-8」：那是给**症状**打补丁（还要处理双引号、C 风格转义、不同 git 版本差异），而且只在「路径经过 `_normalize_path`」的地方有效；`-c core.quotepath=false` 是在**产出侧**统一口径，所有调用 `_run_git` 的地方一起受益。

#### 3.8.4 修复前后的实测对比

同一条命令（`python -m qaplat.cli tia select --base HEAD~1 --coverage-data .tia-coverage`）：

| | 变更文件列表 | 原因 |
|---|---|---|
| 修复前 | `"docs/TIA\345\256\214..."`（八进制假路径） | 「没有可用的新增行…按全量兜底」 |
| 修复后 | `docs/TIA完全入门.md`（真实路径） | 正常参与判定；文档类变更不影响用例时可以选中 0 条或按策略兜底 |

修复后的回归证据（Lead 实跑）：

- `testcases/platform/test_tia.py`：**50 passed**；
- `mypy qaplat/tia.py`：0 issue；
- `ruff check`：通过。

#### 3.8.5 这条教训为什么值得单独占一节

它和 4.3 节「路径字面量方案失效」是**同一类**问题：

> **判据没有错，输入被工具悄悄改写了。**

程序自己写的正则、自己做的字符串比较都是对的；错在「以为自己拿到的是原始路径」。这类 bug 尤其危险，因为：

- 它**不报错**（假路径也是合法字符串，下游只会「匹配不到」）；
- 它**取决于数据**（只有中文/日文/韩文文件名才触发，英文夹具测不出来）；
- 它的**表现是「更安全」**（全量兜底），所以没人会去查它——直到有人注意到「TIA 好像从来没生效过」。

所以对外部工具的输出，要养成的习惯是：**先确认它的「输出口径」**（编码、转义、路径分隔符、是否带引号），再写解析。`git -c core.quotepath=false` 就是把这个口径钉死；同类还有 `git config --global core.quotepath false`（但那依赖每台机器的配置，不如命令行参数可靠）。

---

## 第 4 章 从行号到用例：v1 的尸体与 v2 的做法

这一章是全文的核心。先说结论：**这个仓库里跑着两套选择策略，v1 是文件级、v2 是行级；v1 不是「被淘汰的旧代码」，它是一份活着的教科书——它证明了「看起来可行的方案」为什么会在真实仓库上失效。**

### 4.1 先看 v1：三级策略

v1 的入口是 `select`（`qaplat/tia.py:466`）。它接收两个东西：

```python
# qaplat/tia.py:462（签名）
def select(
    changed: Sequence[str],              # 变更文件（仓库相对路径）
    cases: Sequence[registry.CaseInfo],  # 全量用例（含每个用例引用了哪些 /api 路径）
    *,
    path_reader: PathReader | None = None,  # 把「变更源码」变成「接口路径」的提取器
) -> Selection:
```

然后对每个变更文件逐级判断，**任何一级命中「全量」就立刻返回**：

```
对每个 changed 文件 f：
  ① 关键文件（ALWAYS_FULL_NAMES / ALWAYS_FULL_PREFIXES 命中）
       → full=True，立刻返回
  ② f 在 testcases/ 下
       → 选中该文件的所有 nodeid；一条都没有 → full=True
  ③ 其他源码
       paths = path_reader(f)（默认 read_referenced_paths，读文件里的 /api 字面量）
       ├─ paths 为空                → full=True（公共模块/动态拼接/非源码）
       ├─ paths 非空但命中 0 条用例  → full=True（这段代码没人测）
       └─ 命中                      → 选中 referenced_paths ∩ paths ≠ ∅ 的用例
```

代码里的三层判据分别在：

| 判据 | 实现位置 | 关键常量 |
|---|---|---|
| ① 关键文件 | `_is_always_full`（`qaplat/tia.py:180`） | `ALWAYS_FULL_PREFIXES`（`qaplat/tia.py:51`）、`ALWAYS_FULL_NAMES`（`qaplat/tia.py:68`） |
| ② 测试文件 | `select` 里的 `path.startswith(_TESTCASES_PREFIX)` | `_TESTCASES_PREFIX = "testcases/"`（`qaplat/tia.py:71`） |
| ③ 其他源码 | `_extract_paths`（`qaplat/tia.py:453`）+ 交集 | `registry.normalize_path`（`qaplat/registry.py:63`） |

`ALWAYS_FULL_PREFIXES` 的内容是**稳定排序**的 7 个前缀（`qaplat/tia.py:51`）：

```python
ALWAYS_FULL_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        {
            "data/",  # 数据源：用例的断言基准
            "docs/openapi.json",  # 契约基准
            "qaplat/",  # 平台层自身
            "requirements",  # 依赖面（requirements.txt / -perf.txt / .lock 全被这个前缀覆盖）
            "scripts/",  # 报表与导出脚本
            "schemas/",  # 数据 schema
            "traffic-replay/",  # 独立子项目
        }
    )
)
```

注意 `requirements` 用的是「前缀」而不是文件名：一个前缀同时覆盖 `requirements.txt`、`requirements-perf.txt`、`requirements.lock` 三种写法。代价是会顺带覆盖 `requirements-notes.md` 这种罕见文件名——方向是「多跑」，不是漏跑，可以接受。

`ALWAYS_FULL_NAMES`（`qaplat/tia.py:68`）是 4 个**任意层级**都算关键的文件名：

```python
ALWAYS_FULL_NAMES: frozenset[str] = frozenset({"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg"})
```

为什么按 basename 而不是路径前缀：`conftest.py` 可以出现在任意目录（`testcases/mes/conftest.py` 就有），它改变的是那一层所有用例的运行条件（夹具、钩子），与它落在哪个目录无关。

### 4.2 一个真实的成功场景：改测试文件

假设本次只改了 `testcases/login/test_login_contract.py`（整文件）。这个文件实测有 **29 条**用例（`pytest --collect-only` 实测）。v1 的判断过程：

1. 不是关键文件（`testcases/` 不在 `ALWAYS_FULL_PREFIXES` 里）；
2. 在 `testcases/` 下 → 直接用 `by_file` 取出该文件的 29 个 nodeid；
3. 不进入第③级，不需要读任何源码。

结果：`full=False`，`node_ids` 恰好 29 条，`ratio = 29/636 ≈ 0.0456`。`docs/tia-plan.md` §5 对场景 A 的期望是「比例 < 10%」——满足。

**这是 TIA 收益最稳的一档**：改用例的人只关心这个用例文件，影响面天然就是它自己。v2 也保留了这一档（`select_by_lines` 的判据 2），原因在第 4.4 节。

### 4.3 关键教训：路径字面量方案为什么在 `mock_server` 上失效

这是整篇文档最有价值的一段。它不是「某个函数写错了」，而是**方案本身的假设在真实仓库上不成立**。

#### 4.3.1 先看实测

```python
>>> from qaplat.registry import read_referenced_paths
>>> read_referenced_paths("mock_server/routers.py")
()          # ← 0 条
>>> read_referenced_paths("mock_server/services.py")
()
>>> read_referenced_paths("mock_server/app.py")
('/api/v1',)   # 只有这一条，而且是前缀常量，不是任何路由
```

第③级的前提是「变更源码里能提取出 `/api...` 路径字面量」。`routers.py` 是被测服务**所有路由定义**所在文件，提取结果是 **0 条**。于是 v1 对它的判断必然是：

```
paths 为空 → 无法判定影响面 → full=True → 636 条全跑
```

改 `routers.py` 里任何一行，TIA 都等于没做。

#### 4.3.2 为什么提不到

看 `routers.py` 怎么定义路由（`mock_server/routers.py:56`）：

```python
@router.get("/healthz", tags=["system"], summary="存活探针")
def healthz(): ...


@router.get("/readyz", tags=["system"], summary="就绪探针")
def readyz():
    with read_tx() as conn:
        conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()  # ← 第 64 行，场景 D 改的就是这里
    return {"status": "READY"}
```

装饰器里写的是 `"/readyz"`——**没有 `/api` 前缀**。前缀是在别处拼上去的（`mock_server/app.py:29` 与 `mock_server/app.py:50`）：

```python
# mock_server/app.py:29
API_PREFIX = os.getenv("MOCK_API_PREFIX", "/api/v1")

# mock_server/app.py:50
app.include_router(router, prefix=API_PREFIX)
```

而提取器只认 `/api` 开头的字面量（`qaplat/registry.py:33`）：

```python
_PATH_LITERAL_RE = re.compile(r"(?<![\w.])(/api[A-Za-z0-9_\-./{}]*)")
```

所以 `routers.py` 里每个路由都是 `"/readyz"`、`"/materials"` 这种**相对路径**，正则一条都匹配不上。整条链的信息（前缀 + 各路由）被拆在两个文件、两种语法位置（常量 + 装饰器）里——字面量提取看不到这层关系。

#### 4.3.3 三种「补救」为什么都不该做

发现这个问题后，最自然的三个想法，以及它们各自为什么不行：

| 想法 | 会撞上什么 |
|---|---|
| **放宽正则**：`"/readyz"` 这种任意字符串路径也提取 | 会命中大量**不是接口**的字符串：fixture 里的文件路径、错误消息里的 `"/foo"`、docstring 里的示例、URL 片段。噪声命中会让「命中 0 条 → 全量」的判据失效（因为总能命中几个错误的），比全量更糟 |
| **拼接前缀**：解析 `include_router(router, prefix=API_PREFIX)` 与 `API_PREFIX = os.getenv(...)` 的默认值 | 要写一个小型 Python 解析器（AST + 常量传播 + 跨文件调用图）。而且它天然打不过动态形态：`prefix=f"/api/{version}"`、循环里 `add_api_route`、条件注册、插件式挂载 |
| **维护一张「路由 → 文件 → 用例」映射表** | 手工维护的映射会腐烂；它和「测试选择」要解决的「别让人肉同步」正好背道而驰 |

更根本的问题是：**即使把前缀拼对了，改动的影响面仍然不由 URL 决定**。改 `services.py` 里的库存计算函数，没有一行 `/api` 字面量，但它影响所有调用库存接口的用例。这条路走到底也只能覆盖「路由定义」这一小类改动。

#### 4.3.4 v1 因此退化成什么

| 改动 | v1 的判断 | 实际跑的用例 |
|---|---|---|
| `testcases/login/test_login_contract.py` | 29 条 | 29 |
| `mock_server/routers.py` 任意一行 | full=True | **636** |
| `mock_server/services.py` 任意一行 | full=True | **636** |
| `qaplat/gate.py` | full=True（关键文件） | 636（这个判断是对的） |

对**被测服务**（`mock_server/*`）几乎全部退化成全量。这个失效方式特别坏，因为：

- 它**不报错**；
- 它看起来「很安全」（全量当然不会漏）；
- 于是没有人会去修它——直到有人发现「TIA 从来没生效过」。

#### 4.3.5 换问题，而不是修答案

真正的转折点是换一个提问方式：

> ❌ v1 问：「这行代码涉及哪个 URL？哪个用例引用了这个 URL？」
> ✅ v2 问：「**这行代码被哪些用例执行过？**」

第二个问题的答案不需要理解代码语义，也不需要解析路由——它可以直接从 coverage 的 per-test context 里**实测**出来。这就是第 2 章那份 SQLite 数据的用途，也是 `--cov-context=test` 开关存在的意义。

#### 4.3.6 v1 还留下了什么

`select` 没有被删除，它有两处仍然在用：

1. **没有覆盖率数据时的降级路径**：`select_from_git` 的 `data_path=None` 分支——函数定义在 `qaplat/tia.py:686`，`data_path` 为 `None` 时真正走 `select` 的代码在 **`qaplat/tia.py:706`–`712`**；CLI 找不到 `.tia-coverage`/`.coverage` 时也会走到这里（`qaplat/cli.py:176` 的 `_resolve_coverage_data`）。
2. **教学与测试价值**：`testcases/platform/test_tia.py` 可以对着纯数据断言三级判据，不需要真跑 coverage；`select` 也是理解「为什么需要 v2」的最短路径。

---

### 4.4 `load_context_index`：把 SQLite 变成倒排索引

入口（`qaplat/tia.py:387`）：

```python
def load_context_index(
    data_path: Path,
    *,
    root: Path | None = None,        # 仓库根；默认 qaplat.paths.ROOT
) -> dict[str, dict[int, tuple[str, ...]]]:
```

返回的数据形状（真实示例，取自 `qaplat/registry.py`）：

```python
{
    "qaplat/registry.py": {
        1: ("",),  # 第 1 行：模块级（import 期执行，空 context 哨兵）
        70: (
            "testcases/platform/test_registry_coverage.py::test_both_sources_merge_without_duplicating_a_case",
            "testcases/platform/test_registry_coverage.py::test_case_with_neither_source_is_uncovered",
            ...,
        ),  # 第 70 行：11 个用例执行过
        71: (...),
    },
    "mock_server/routers.py": {...},
}
```

逐段拆开看它做了什么、为什么这么做：

**① 数据文件不存在 → 抛错，不返回空字典**

```python
# qaplat/tia.py:417
path = Path(data_path)
if not path.is_file():
    raise RuntimeError(
        f"coverage 数据文件不存在或不是文件：{path}。行级 TIA 需要先跑一次 "
        "`pytest --cov=<被测包> --cov-context=test`，再用 --data 指到那份 .coverage。"
    )
```

为什么不返回 `{}`：空索引会让所有判断退化成全量（看似安全），但真正的成因（路径写错、忘了 `--cov-context=test`、在别的仓库采集的）被完全藏起来。**静默退化是 TIA 最不该有的失效方式**——这条原则在全文件里反复出现。

**② 读 SQLite：`CoverageData` 而不是自己解析**

```python
# qaplat/tia.py:423（节选）
from coverage import CoverageData  # 函数内 import：tia 平时不依赖 coverage，只有 v2 才需要

data = CoverageData(str(path))
data.read()
```

`from coverage import CoverageData` 放在函数内部，是为了让「只跑 v1」的路径（以及纯函数单测）不依赖 coverage 已安装。coverage 本身是 pytest-cov 的依赖，本项目**没有新增任何依赖**。

**③ 路径归一化：绝对路径 → 仓库相对路径**

```python
# qaplat/tia.py:245
def _relative_to_root(measured: str, root: Path) -> str | None:
    try:
        relative = Path(measured).resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    return relative.as_posix()
```

coverage 存的是绝对路径（`D:\DSH\test-projects\api-regression-selector\mock_server\routers.py`），git 与 nodeid 用仓库相对路径（`mock_server/routers.py`）。两边不对齐就永远查不到。用 `Path.resolve().relative_to(root)` 先落成绝对路径再相减，避免分隔符（`\` vs `/`）与大小写写法差异。

`relative_to` 失败（仓库外的文件，例如 site-packages）返回 `None` 由调用方跳过——那是**正常情况**，不是错误。

**④ 每行 → 一组 nodeid**

```python
# qaplat/tia.py:434（节选）
for line, contexts in (data.contexts_by_lineno(measured) or {}).items():
    node_ids: set[str] = set()
    for context in contexts:
        node_id = _context_node_id(context)  # 剥 |run / |setup / |teardown；空串→哨兵
        if node_id is not None:
            node_ids.add(node_id)
    if node_ids:
        per_line[int(line)] = tuple(sorted(node_ids))
```

三个细节：

- `contexts_by_lineno` 可能返回 `None`（文件没有被记录），用 `or {}` 兜住；
- `node_ids` 用 `set` 去重——同一行的 run/setup/teardown 会归到同一个 nodeid；
- `tuple(sorted(...))` 保证**稳定排序**，同一份数据永远产出同一份索引（报表与断言可以 diff）。

**⑤ 索引为空 → 抛错**

```python
# qaplat/tia.py:444
if not index:
    raise RuntimeError(
        "coverage 数据里没有可用的「用例 context」：行级 TIA 只能用 "
        "`--cov-context=test` 采集的数据，且被测文件必须落在 root 之下。"
        f"（data_path={path}，root={root_path}，measured_files={len(data.measured_files())}）"
    )
```

这是「白采集了」的报警器：如果覆盖数据是普通 `--cov` 采的（不带 context），`contexts_by_lineno` 里全是空串或空集，索引建不出来。直接抛错，让人立刻回去看采集命令。

### 4.5 `select_by_lines`：七条判据，每层都朝「多跑」倒

入口（`qaplat/tia.py:567`）：

```python
def select_by_lines(
    hunks: Sequence[ChangedHunk],                        # 变更行（第 3 章产出）
    index: Mapping[str, Mapping[int, Sequence[str]]],    # 行 → nodeid 索引（上面产出）
    cases: Sequence[registry.CaseInfo],                  # 全量用例
) -> Selection:
```

**它是纯函数**：不读盘、不起子进程、不调 git。所有输入都从参数来。测试可以直接构造 `hunks` / `index` / `cases` 三份数据断言判断结果——不需要真的改代码、真的采覆盖率。

#### 4.5.1 第一步：把 hunks 合并成「文件 → 行集合」

```python
# qaplat/tia.py:597（节选）
merged: dict[str, set[int]] = {}
for hunk in hunks:
    path = _normalize_path(hunk.path)  # 反斜杠→斜杠、去 ./ 前缀
    if not path:
        continue
    merged.setdefault(path, set()).update(int(line) for line in hunk.lines)
changed = tuple(sorted(merged))
```

同一个文件可能有多个 hunk（改了两处、相隔 100 行）；合并成集合后统一处理，避免重复判断。排序是为了输出稳定。

#### 4.5.2 第二步：空输入 → 全量

```python
# qaplat/tia.py:605
if not changed:
    return _full(changed, total, "没有可用的变更行（diff 为空），无法判定影响面，按全量兜底", "full")
```

注意：`changed` 为空有两种来源——`hunks` 本身为空（没有变更），或所有 hunk 的 path 都是空串（不可能，防御性）。两种都按「判定不了」处理。

#### 4.5.3 第三步：判据链（7 条）

对每个变更文件，依次判断（**任何一层命中全量就立刻返回**，不再看后面的文件）：

| 层 | 条件 | 结果 | 代码位置 |
|---|---|---|---|
| ① | 关键文件（`_LINES_FULL_PREFIXES`，即 v1 清单**去掉 `qaplat/`**） | `full=True` | `qaplat/tia.py:618` |
| ② | 在 `testcases/` 下 | 该文件全部 nodeid；无则 `full=True` | `qaplat/tia.py:620` |
| ③ | `lines` 为空（纯删除/二进制/重命名/未跟踪） | `full=True` | `qaplat/tia.py:633` |
| ④ | 文件不在 `index` 里 | `full=True` | `qaplat/tia.py:641` |
| ⑤ | 变更行里含模块级哨兵 `""` | `full=True` | `qaplat/tia.py:649` |
| ⑥ | 变更行没被任何已收集用例执行过 | `full=True` | `qaplat/tia.py:662` |
| ⑦ | 有命中 | 选中命中 nodeid；`strategy="lines"` | `qaplat/tia.py:674` |

两个必须解释的设计点：

**为什么 v2 的清单要去掉 `qaplat/`**（`qaplat/tia.py:78`）：

```python
# v1 把 qaplat/** 判全量，是因为它只能靠路径字面量猜影响面，而平台层代码里根本没有 /api 字面量；
# v2 走 coverage 行数据，平台层正是映射最准的部分。如果继续判全量，改一行报表代码就要跑全仓。
_LINES_FULL_PREFIXES: tuple[str, ...] = tuple(prefix for prefix in ALWAYS_FULL_PREFIXES if prefix != "qaplat/")
```

也就是说：改 `qaplat/report.py` 里一行报表逻辑，v2 会走行级、可能只选 1~2 条；改 `qaplat/gate.py` 的**函数定义行**（模块级），v2 会全量。这是同一个文件里两种完全不同的结果——**判据不仅看文件，还看改的是哪一行**。

**为什么第 ⑤ 层（模块级行）必须全量**：

```python
# qaplat/tia.py:648（节选）
for line in lines:
    if _MODULE_LEVEL in (line_index.get(line) or ()):
        return _full(
            changed,
            total,
            f"{path} 第 {line} 行属于模块级代码（import 期执行、不归属任何用例），"
            "改动会影响所有 import 它的用例，退化为全量",
            "full",
        )
```

模块级行（`import`、常量、函数定义本身）没有归属用例，但它一改，所有 import 这个模块的用例的运行条件都变了。按「没有用例命中 → 全量」处理既安全，又不需要知道「谁 import 它」——那正好是 coverage 不知道的信息。

**为什么第 ⑥ 层要「按文件严格兜底」而不是「按行兜底」**：

```python
# qaplat/tia.py:662（节选）
file_hits: set[str] = set()
for line in lines:
    for node_id in line_index.get(line) or ():
        if node_id in known:
            file_hits.add(node_id)
if not file_hits:
    return _full(changed, total, f"{path} 的 {len(lines)} 条变更行没有被任何已收集用例执行过，退化为全量", "full")
```

只要这个文件**至少有一行**被用例执行过，就按命中的那些行选；只有**所有变更行都没人执行**时才全量。这个取舍写在 docstring 的「已知限制」里：

> 同一文件里既有命中行又有未命中行时，只要命中不为空就按命中行选。未执行过的行按定义不影响既有用例的**当前**行为，但**改动它**可能改变控制流、让原本不执行的代码开始执行。v3 若要做全，应当补「未命中行出现即全量」的开关。

#### 4.5.4 最后：产出 `Selection`

```python
# qaplat/tia.py:674（节选）
selected_files = tuple(sorted({_file_of(node_id) for node_id in selected}))
return Selection(
    node_ids=tuple(sorted(selected)),
    full=False,
    reason=f"命中 {len(selected)} 条用例（分布在 {len(selected_files)} 个测试文件、覆盖 {hit_line_count} 条变更行）",
    changed_files=changed,
    total_cases=total,
    matched_files=selected_files,
    matched_paths=(),  # 行级策略不做接口路径匹配，恒为空
    strategy="lines",
)
```

`matched_files` 在 v1 里是「步骤②命中的测试文件」，在 v2 里换成「**选中用例所在的测试文件**」——对读日志的人更有用：知道该去哪个文件核对那几个 nodeid。

### 4.6 `Selection`、`_full` 与 ratio

`Selection`（`qaplat/tia.py:116`）是所有下游唯一认识的数据结构。它的 `ratio` 属性（`qaplat/tia.py:137`）：

```python
@property
def ratio(self) -> float:
    if self.total_cases <= 0:
        return 0.0
    return len(self.node_ids) / self.total_cases
```

为什么 `total_cases == 0` 返回 0.0 而不是抛异常：空用例库是**已经发生的状态**（例如收集失败后上层仍要打印一份结果），让报表层崩掉只会把「用例库空了」伪装成「tia 代码坏了」。

`_full`（`qaplat/tia.py:279`）统一构造全量结果，`node_ids` **恒为空**：

```python
# qaplat/tia.py:279（节选）
return Selection(
    node_ids=(),          # 全量 = 不选任何具体 nodeid，由调用方跑整层
    full=True,
    ...
)
```

为什么不把 636 个 nodeid 抄进 `node_ids`：契约里「全量」的语义是「等价于不带 `--select`」；抄一份进去只会让 JSON 变大，还会诱导调用方把 636 个 nodeid 拼进命令行（Windows 上有命令行长度上限）。

`strategy` 四个取值（`qaplat/tia.py:134`）：

| 值 | 含义 | 由谁产出 |
|---|---|---|
| `full` | 全量兜底 | `_full()`，两套策略都会走到 |
| `files` | v1 文件级选择 | `select()` |
| `lines` | v2 行级选择 | `select_by_lines()` |
| `docs` | 仅文档变更，不必跑任何用例 | `select_by_lines()` 在查索引之前分流 |

`docs` 这一档值得单说：`.md` / `.rst` / `.txt` 这类文件不在覆盖索引里（没有用例执行过
它们），若按「查不到索引 → 影响面未知 → 全量」的默认路径走，改一份 README 就要跑满一整层。
而仓库里**没有任何测试读这些文件**，所以它们被单独摘出来判「0 条」。这条白名单成立的前提
写在 `qaplat/tia.py` 的 `_DOC_ONLY_SUFFIXES` 注释里：哪天有人加了「文档链接检查」这类测试，
必须先把对应后缀从清单里摘掉。`docs/openapi.json` 是契约不是文档，由
`ALWAYS_FULL_PREFIXES` 先判全量，到不了这一档。

### 4.7 一条改动在代码里的完整旅程

以场景 D 为例（改 `mock_server/routers.py` 第 64 行，即 `readyz` 里那句 `conn.execute(...)`）：

```
你改了 routers.py:64
   │
   ├─ changed_files("HEAD~1", include_worktree=True)
   │     → ("mock_server/routers.py",)
   │
   ├─ changed_hunks("HEAD~1", include_worktree=True)
   │     → (ChangedHunk(path='mock_server/routers.py', lines=(64,)),)
   │
   ├─ load_context_index(Path(".tia-coverage"), root=ROOT)
   │     → index["mock_server/routers.py"][64]
   │       == ("testcases/contract/test_contract_export.py::test_live_error_status_codes_are_reachable",)
   │       （实测：只有 1 条用例执行过这一行）
   │
   ├─ select_by_lines(...)
   │     第 64 行不是模块级（无 "" 哨兵）
   │     file_hits = {test_live_error_status_codes_are_reachable}
   │     → Selection(full=False, strategy="lines",
   │                  node_ids=(那 1 条,), total_cases=636, ratio≈0.0016)
   │
   └─ pytest <那 1 条 nodeid>
         实测 2.83s（全量 196.55s）
```

---

## 第 5 章 兜底判据：什么时候必须跑全量

### 5.1 先看总数

两套策略加起来，一共有 **10 条**会触发 `full=True` 的判据（v1 5 条 + v2 7 条，其中「关键文件」「testcases 匹配不到」「未命中」三类是共用的）。看表：

| # | 策略 | 触发条件 | `reason` 里的关键词 | 为什么必须全量 |
|---|---|---|---|---|
| 1 | v1/v2 | 没有任何变更文件 / 变更行 | 「没有…变更」 | 空输入无法判断「是真没改」还是「采集/解析失败」 |
| 2 | v1/v2 | 变更文件命中 `ALWAYS_FULL_PREFIXES` / `ALWAYS_FULL_NAMES` | 「关键文件…命中必跑清单」 | 改的是运行条件（夹具、依赖、契约、marker），不是某段业务逻辑 |
| 3 | v1/v2 | 变更文件在 `testcases/` 下但匹配不到任何用例 | 「可能已删除或改名」 | 用例被删/改名后，目标状态未知；静默跳过等于放过回归 |
| 4 | v1 | 源码提取不到 `/api` 字面量 | 「未提取到 /api 路径字面量」 | 公共模块/动态拼接/非源码，影响面不可判定（第 4.3 节的血泪教训） |
| 5 | v1 | 提取到了路径但 0 条用例引用 | 「没有任何用例引用它们」 | 这段代码可能没人测，也可能用例写的是另一种路径写法 |
| 6 | v1/v2 | 一个用例都没选中 | 「未命中任何用例」 | 0 条 ≠ 安全；可能是判据全部落空 |
| 7 | v2 | hunk 的 `lines` 为空（纯删除/二进制/重命名/未跟踪） | 「没有可用的新增行」 | 没有目标行 ≠ 没有影响 |
| 8 | v2 | 变更文件不在覆盖索引里 | 「不在覆盖索引里」 | 没有任何用例执行过它，或采集没包含它 |
| 9 | v2 | 变更行含空 context（模块级） | 「属于模块级代码」 | import 期执行；改它影响所有 import 该模块的用例 |
| 10 | v2 | 变更行没有任何已收集用例执行过 | 「没有被任何已收集用例执行过」 | 这段代码可能真没人测，也可能是采集漏了 |

### 5.2 为什么每一条都朝「多跑」

把上面 10 条抽象一下，它们回答的是同一个问题：**「我现在有足够证据说『影响面是这几个用例』吗？」**

- 有证据 → 返回较小的集合（`full=False`）；
- 没证据 → 返回全量（`full=True`），并把「为什么没证据」写进 `reason`。

这里的成本账是**不对称**的：

| 选择 | 代价 | 可发现性 |
|---|---|---|
| 多跑 500 条无关用例 | 多花几分钟墙钟 | 立刻可见（耗时变长） |
| 漏跑 1 条回归 | 缺陷进主干 | **不可见**（门禁显示绿） |

所以所有含糊情形都倒向全量。这也是为什么 `select_by_lines` 的 docstring 要写「判据顺序（每一步都朝『多跑』的方向倒）」——顺序本身也是判据的一部分：先判最粗的（关键文件），再判次粗的（testcases），最后才到行级。

### 5.3 三条红线：不许「静默成功」

除了上面的业务判据，还有三条「程序级」的红线。它们的共同点是：**宁可让整条命令失败，也不许返回一个看起来正常的结果**。

| 红线 | 代码 | 失败形态 | 为什么 |
|---|---|---|---|
| git 失败 | `_run_git`（`qaplat/tia.py:203`） | `RuntimeError`，带命令/cwd/退出码/stdout/stderr 尾部 | 返回空元组会被读成「没有变更 → 全都不用跑」 |
| coverage 数据缺失/不可用 | `load_context_index`（`qaplat/tia.py:419`、`:443`） | `RuntimeError`，带路径与 `measured_files` 数量 | 返回空字典会让所有判定退化成全量，真正成因被藏起来 |
| 用例收集失败 | `registry.collect_cases` → `CaseCollectionError`（`qaplat/registry.py:202` 起的实现） | 非 exit 5 的失败一律抛，带退出码与两路输出尾部 | 零收集被当成「没有用例覆盖」是最危险的一种错误 |

CLI 层把这些异常统一折成**退出码 2**（`qaplat/cli.py:261` 的 `cmd_tia_select` 与 `qaplat/cli.py:118` 的 `cmd_gate` 都捕获 `Exception` 后返回 `EXIT_USAGE`）。这样 CI 里看到 2，就知道「不是选择结果为空，而是选择器自己坏了」。

### 5.4 「全量」不等于「什么都不做」

`full=True` 时下游怎么处理，决定了兜底是否真的安全：

```python
# qaplat/cli.py:219（节选）
if sel.full:
    return list(args.passthrough)  # 不加 nodeid，等价于原命令
return list(sel.node_ids) + list(args.passthrough)
```

`gate --select tia` 在 `full=True` 时**只**返回调用方原有的透传参数——也就是退化成「不带 `--select` 的原行为」。反过来，如果此时还叠一层 nodeid 过滤，就等于「先声称判定不了，又偷偷把范围收窄回去」，违背兜底语义。

### 5.5 一个需要解释的取舍：空变更集

`select` 与 `select_by_lines` 在「没有任何变更」时都返回 `full=True`（判据 1）。这是一个**刻意的选择**，值得单独说：

- 如果返回 `full=False, node_ids=()`，CLI 会打印「选中 0 条」；`gate --select tia` 会拿到一个空的 extra —— 而空 extra 在 pytest 里等价于**跑全量**。于是「声称 0 条」与「实际全跑」出现矛盾，日志会骗人。
- 返回 `full=True` 则语义一致：**判定不了（没有输入），所以按全量跑，并且明确说明原因**。

代价是：真的一次改动都没有时，你看到的是「全量兜底」而不是「0 条」。在 TIA 的语境里，这比撒谎好。

---

## 第 6 章 接线：cli.py 到 gate.py 到 pytest

前面五章讲的是「怎么算出选中集」。这一章讲**算出来之后怎么真的跑起来**——也就是 `.tia-coverage` → `tia.py` → `cli.py` → `gate.py` → pytest 子进程这条链的最后三段。

先把入口说明白：本仓 `qaplat` 包**没有 `__main__.py`**，所以命令行入口是 `python -m qaplat.cli`（`qaplat/cli.py:670` 的 `main`）。写成 `python -m qaplat` 会得到 `No module named qaplat.__main__`——这是本仓的既有事实，不是这次改造引入的。下文所有命令都用前者。

### 6.1 先看总数：两个入口，一条数据流

| 入口 | 是否真的跑 pytest | 退出码 | 用途 |
|---|---|---|---|
| `python -m qaplat.cli tia select …` | **否**，只打印 | 0（选择器自身失败才是 2） | 预览「这次改动会选哪些」，不占时间 |
| `python -m qaplat.cli gate --layer X --select tia …` | **是** | 0 达标 / 1 未达标 / 2 用法错误 | 门禁里真的只跑选中的那些 |
| `python -m qaplat.cli gate --layer X`（不带 `--select`） | 是 | 同上 | 与改造前**逐字一致**的全量跑法 |

两者共用同一条选择路径，只在最后一步分叉：

```
              .tia-coverage（行级）或 None（文件级）
                          │
git diff ──→ tia.select_from_git() ──→ Selection(full, strategy, node_ids, reason)
                          │
        ┌─────────────────┴──────────────────┐
        │                                    │
   tia select                          gate --select tia
   render_text / render_json            _tia_extra() → extra
   打印，退出 0                          build_pytest_args(spec, extra)
        │                                    │
        └────────── 同一个 Selection ─────── pytest 子进程
                                             │
                                        PytestRun → evaluate
                                             │
                                        0 / 1 退出码
```

**为什么要有「只打印」的入口**：选择结果本身是信息（选中几条、为什么全量、命中了哪些文件）。想调判据的人不该为了看一眼结果就跑一遍测试；而 `gate` 那边跑一次是几分钟的事。分开之后，调试循环从分钟级降到秒级。

### 6.2 `tia select`：五个参数，看清会选谁

参数定义在 `qaplat/cli.py:553-573`：

| 参数 | 默认 | 作用 | 注意点 |
|---|---|---|---|
| `--base REF` | `HEAD~1` | 变更基准：`git diff REF...HEAD`（三点，只算本分支带来的改动） | 与 `gate` 的 `--base` 同名同义 |
| `--json` | 关 | 输出固定字段的 JSON（`render_json`，见 4.6 节） | 字段名是对外契约，CI 靠它解析 |
| `--no-worktree` | 关（**默认包含**工作区） | 只看已提交改动，不把工作区与未跟踪文件算进来 | 默认包含是刻意的：本地改完就能预览 |
| `--pytest-args ARGS` | `""` | 收集用例时附带的 pytest 参数，整串用引号包住 | 用 `shlex` 切分（`qaplat/cli.py:198-206`），所以 `-m "login and not slow"` 不会被拆散；Windows 路径请写正斜杠 |
| `--coverage-data PATH` | `None` | per-test 覆盖率数据；给了走行级（v2），不给按 6.3 的顺序找 | 显式给了但文件不存在 → 退出码 2 |

为什么 `--pytest-args` 是「一个字符串」而不是 `nargs=REMAINDER`：`tia select` 后面没有位置参数，REMAINDER 会把拼错的选项也一并吞进 pytest 参数——于是「写错了参数」表现成「跑了错的范围」，而不是报用法错误（`qaplat/cli.py:199-204` 的注释就是讲这个取舍）。

一次真实运行（当前工作区，改了 16 个文件，包括本文件与 `qaplat/tia.py`）：

```console
$ python -m qaplat.cli tia select --base HEAD~1 --coverage-data .tia-coverage
模式: 全量兜底（full）
原因: .github/ISSUE_TEMPLATE/config.yml 不在覆盖索引里（没有任何用例执行过它，或采集未包含该文件），退化为全量
变更文件: 16 个
  - .github/ISSUE_TEMPLATE/config.yml
  - docs/TIA完全入门.md
  - qaplat/tia.py
  ...
选中: 0 / 689 条用例（0.00%）
已知限制: 影响面按文件/行粒度判定；动态拼接路径提取不到会退化为全量；已删除用例匹配不到同样退化为全量
```

这条输出本身就是一次「兜底判据生效」的演示：`.github/ISSUE_TEMPLATE/config.yml` 这类从未被任何用例执行过的文件不在覆盖索引里 → 判据④（4.5.3 表）→ 全量。**注意它不是报错**：退出码是 0，结论是「这次判不了，按全量」。

`cmd_tia_select` 有一条容易忽略的约定（`qaplat/cli.py:261-284`）：**恒返回 0**。「一条都没选中」也是有效结论（例如改动全落在没被任何用例引用的源码上），选择器会用 `full` 与 `reason` 表达它。只有选择器**自身**失败（git 不可用、用例收集崩了）才返回 2——那种情况下的「0 条」是假结论，不能当查询答案返回。

### 6.3 `_resolve_coverage_data`：三态决定走 v1 还是 v2

这是整条链上**唯一**决定「用哪份覆盖率数据」的地方（`qaplat/cli.py:176-196`），两个入口都调它：`cmd_gate` 在 `--select tia` 时调（`qaplat/cli.py:128`），`cmd_tia_select` 无条件调（`qaplat/cli.py:270`）。

```python
# qaplat/cli.py:176-195（节选）
def _resolve_coverage_data(arg: str | None) -> Path | None:
    if arg:
        path = Path(arg)
        if not path.exists():
            print(f"覆盖率数据不存在：{arg}", file=sys.stderr)
            raise SystemExit(EXIT_USAGE)  # ← 退出码 2，不静默退回 v1
        return path
    for candidate in (ROOT / ".tia-coverage", ROOT / ".coverage"):
        if candidate.exists():
            return candidate
    print("[qaplat] tia: 未找到覆盖率数据，退化为文件级策略", flush=True)
    return None
```

三态表格化：

| 情况 | 行为 | 结果 |
|---|---|---|
| 显式给了 `--coverage-data X`，且 `X` 存在 | 直接用 `X` | 行级（v2） |
| 显式给了，但文件不存在 | stderr 打印「覆盖率数据不存在：X」→ `SystemExit(2)` | **退出码 2**，不跑任何测试 |
| 没给 | 按 `.tia-coverage` → `.coverage` 顺序找 | 找到即行级（v2） |
| 没给且两份都没有 | 打印「未找到覆盖率数据，退化为文件级策略」→ 返回 `None` | 文件级（v1） |

为什么「显式路径不存在」必须退出 2（`qaplat/cli.py:179-180` 的注释）：调用方既然**点名**了这份数据，就说明他想要行级判据；悄悄换一套判据只会让人以为行级生效了，然后拿着「文件级选中集」去解释「行级效果」——这比直接报错难查得多。

为什么「都没找到」要打印一行日志：退化本身是合理的（没有覆盖率数据时 v1 仍可用），但**必须让人看得见**。没有这行日志，你无法从输出区分「这次是行级选出来的」还是「退化成了文件级」。

还有一个顺序细节：`cmd_gate` 在 `_open_store` **之前**解析覆盖率路径（`qaplat/cli.py:128` 在 `:129` 之前）。注释（`:126-127`）写得很清楚——显式路径写错时必须在开库之前就以用法错误退出，否则会留下一个「已建表但没跑」的空库，让人误以为这轮跑过了。

### 6.4 `gate --select tia`：`_tia_extra` 把选中集变成 `extra`

`cmd_gate` 里只多了一段（`qaplat/cli.py:135-145`）：

```python
# qaplat/cli.py:138-145（节选）
extra: Sequence[str] = args.passthrough  # 默认：与改造前完全一样
if args.select == "tia":
    try:
        extra = _tia_extra(args, spec, data_path)
    except Exception as exc:  # git / pytest 子进程的失败模式是开放的
        print(f"[qaplat] tia 选择失败：{exc}", file=sys.stderr)
        store.close()
        return EXIT_USAGE
```

`_tia_extra`（`qaplat/cli.py:219-237`）负责算出最终的 `extra`：

```python
# qaplat/cli.py:227-237（节选）
from qaplat import tia  # 函数内 import，见 6.8

sel = tia.select_from_git(base=args.base, pytest_args=_layer_collect_args(spec), data_path=data_path)
print(
    f"[qaplat] tia: selected={len(sel.node_ids)}/{sel.total_cases} "
    f"(ratio={sel.ratio:.1%}) full={sel.full} strategy={sel.strategy} reason={sel.reason}",
    flush=True,
)
if sel.full:
    return list(args.passthrough)  # 全量兜底 = 不加 nodeid
return list(sel.node_ids) + list(args.passthrough)
```

两个关键点：

**① 摘要行格式固定**：

```
[qaplat] tia: selected=X/Y (ratio=Z%) full=BOOL strategy=lines|files|full|docs reason=...
```

`selected=X/Y` 里 X 是 `len(sel.node_ids)`、Y 是 `sel.total_cases`；`strategy` 四值见 4.6 节；`reason` 是判据给出的中文解释。这行是排查 TIA 行为的**第一现场**：看到 `full=True` 就别去翻 nodeid 了，先看 `reason` 说哪条判据兜底。

**② `sel.full` 时只返回 `passthrough`**（`qaplat/cli.py:236-237`）：全量兜底的含义是「影响面判定不了」，这时再叠一层 nodeid 过滤，等于先声称判不了、又偷偷把范围收窄回去。所以 `full=True` 的产物就是「不带 `--select` 的原命令」，只是多了一行说明。

**`extra` 的顺序**：`node_ids` 在前、调用方的 `passthrough` 在后。两者在 pytest 里都是「位置参数/选项」的混合体，顺序不影响语义；保持在后面是为了让调用方追加的 `--` 参数仍然排在最后（与 `build_pytest_args` 里「`extra` 放到最后」的约定一致）。

### 6.5 一个必须说清的口径：分母 Y 是「收集到的用例数」

第 4 章的 `Selection.ratio = len(node_ids) / total_cases`——分母从哪来，直接决定 ratio 的含义。

设计意图写在 `_layer_collect_args`（`qaplat/cli.py:209-216`）：

```python
# qaplat/cli.py:209-216（节选）
def _layer_collect_args(spec: LayerSpec) -> list[str]:
    """层 → 与 gate 同口径的收集参数（只有 -m 表达式，不带 dump 插件）。"""
    return ["-m", spec.marker_expr] if spec.marker_expr else []
```

意图是：`gate --layer smoke` 只跑 `-m smoke` 那 36 条，那么收集时也只该收集这 36 条，这样「选中的 / 跑得了的」是同一批，ratio 才有意义；如果按全仓收集，ratio 的分母会虚高，选中集里还可能混进该层根本不会执行的用例。

> **这一条已经修好（Lead 实测，2026-10-31）。** 修复前：`collect_cases([])` 与 `collect_cases(["-m", "smoke"])` 都返回 **689**，而 pytest 自己的汇总行写的是 `36/689 tests collected (653 deselected)`。根因是 `qaplat/_dump.py` 的 `pytest_collection_modifyitems` 没加 `trylast=True`——`-m` 的 deselect 也在同一个 hook 里做（`_pytest/mark/__init__.py`），而 `-p` 加载的插件注册得晚、默认**先**执行，于是 dump 的 `markers` / `files` 记下的是 deselect **之前**的全量 items；`collect_cases` 的用例清单又取自 dump，所以 `-m` 对清单完全无效。
>
> 修法是给这个 hook 加 `@pytest.hookimpl(trylast=True)`（`qaplat/_dump.py:82`，同时 `import pytest` 在 `qaplat/_dump.py:23`），让记录发生在 deselect **之后**；`qaplat/_dump.py:84-91` 的注释把「这不是性能优化，是正确性要求」写清楚了。修后实测：

| `pytest_args` | 对应入口 | `total_cases` | 与什么互相印证 |
|---|---|---|---|
| `[]` | `tia select` 独立预览（不按层） | **689** | 全仓收集数 |
| `['-m', 'smoke']` | `--layer smoke` | **36** | pytest 自报的 `36/689 tests collected (653 deselected)` |
| `['-m', 'api']` | `--layer api` | **132** | 仓库 `AGENTS.md` 记载的「接口层 132 条」 |

两列「交叉印证」来自**两个独立来源**（pytest 自己的汇总行、仓库文档里长期记载的层规模），都对上了，说明收窄逻辑是对的，不是凑出来的数。

修复前的危害不只是「ratio 显示难看」。分母虚高的同时，`selected` 的 nodeid 来自**全量**索引、不按层过滤——`gate --layer smoke --select tia` 理论上可能把一条只会被 `-m api` 命中的用例塞进 smoke 层的 pytest 命令行，撞上「这层根本没准备跑它」的环境问题。加 `trylast` 之后 `collect_cases` 拿到的就是该层真实会跑的清单，**分母与串层两个问题一起消失**。

所以读 ratio 时要先看分母属于哪一档：`--layer smoke` 是 X/36、`--layer api` 是 X/132、`tia select` 独立预览是 X/689（全仓）。**跨层比较 ratio 没有意义**（分母不同），要比就比 `node_ids` 的绝对条数，或者固定同一个 layer。

回归（同一次修复）：`-m platform` → `3 failed, 286 passed, 400 deselected`（仍是那三条基线失败，无新增）；`ruff check` / `ruff format --check` 通过；全仓 `mypy` **122 文件 0 issue**。

### 6.6 `build_pytest_args`：`extra` 为什么排在最后

`qaplat/gate.py:139-157`：

```python
# qaplat/gate.py:152-157（节选）
args: list[str] = []
if spec.marker_expr:
    args += ["-m", spec.marker_expr]  # ① 层表达式
args += ["-p", DUMP_PLUGIN, "--qaplat-dump", str(_dump_path_for(spec.layer.value))]  # ② 采集插件
args += with_confcutdir([str(item) for item in extra])  # ③ 调用方附加（选中 nodeid 在这）
return args
```

顺序固定为「**表达式 → 采集插件 → `extra`**」，`extra` 放最后有三个理由：

1. **pytest 的位置参数限定收集范围**：`pytest <nodeid>` 只收集这个 nodeid；`-m 表达式` 在其上再做过滤——两者是**交集**。所以把 nodeid 放进 `extra`，效果就是「在这层会跑的用例里，只跑选中的这些」。
2. **`extra` 允许覆盖前面的同名选项**（`qaplat/gate.py:142-143` 的注释）：pytest 里后出现的同名选项生效，调用方的 `--` 透传因此可以改配置。
3. **`--confcutdir` 必须插在 `--` 之前**（`qaplat/gate.py:147-150`）：`--` 之后的一切都被当成路径参数，追加在末尾会让 `--confcutdir` 自己变成「找不到的路径」。`with_confcutdir` 负责按需插入。

还有一个细节：空表达式（release 层）**不产生** `-m ""`。pytest 对空 `-m` 的语义是「全部匹配」，多写一条只会让人误解成「有表达式」。

### 6.7 `run_layer`：`extra` 真正变成 pytest 进程

`qaplat/gate.py:187-233`：

```python
# qaplat/gate.py:198-219（节选）
workdir = cwd or ROOT  # cwd 默认仓库根：pytest.ini / conftest.py 按 cwd 解析
dump = _dump_path_for(spec.layer.value)
dump.parent.mkdir(parents=True, exist_ok=True)
dump.unlink(missing_ok=True)  # 先删旧 dump，避免拿到上一轮的残留

argv = [sys.executable, "-m", "pytest", *build_pytest_args(spec, extra)]
child_env = dict(os.environ if env is None else env)
child_env.setdefault("PYTHONIOENCODING", "utf-8")  # 中文用例名不因 cp936 变替换字符

proc = subprocess.run(
    argv,
    cwd=str(workdir),
    env=child_env,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=spec.timeout_seconds,
    check=False,
)
```

四个值得记住的点：

- **先删旧 dump**（`:201`）：否则这一轮超时/崩溃时，读到的可能是上一轮的 `collect.json`，结论会「穿越」。
- **cwd 默认仓库根**（`:198`）：`pytest.ini` 的 `testpaths`、根 `conftest.py` 都按 cwd 解析，跑偏了就等于换了套配置。
- **超时是层阈值的一部分**：`subprocess.TimeoutExpired` 折成 `EXIT_TIMEOUT` 的 `PytestRun`（`:223-232`），不是抛异常——门禁要的是「这一层超预算」这条结论，不是崩栈。
- **命令行里没有 `--cov`**（`:203`）：这是第 8 章的前提——tia 模式天然不撞覆盖率门，`--cov-fail-under=54` 与 `scripts/check_coverage.py` 只属于 CI 的覆盖率 job。

### 6.8 默认关闭与三条退出码

**默认关闭**：`gate --select` 的默认值是 `None`（`qaplat/cli.py:535-541`），不给时 `extra` 原样等于 `args.passthrough`（`qaplat/cli.py:138`）——与改造前逐字一致。这条是本轮的**回归红线**（bu-cli 专门验过），也是为什么 `qaplat/tia.py` 在 `cli.py` 里是**函数内 import**（`qaplat/cli.py:171-173` 的注释）：

> `tia` 刻意在函数内部 import：它是后加的模块，写在模块顶部会让 run / gate / metrics / report 全都依赖它的存在，而「不带 `--select` 时行为逐字不变」是这一轮的回归红线。

**三条退出码**：

| 码 | 含义 | 触发点 |
|---|---|---|
| 0 | 达标（或 `tia select` 正常打印完） | `gate.exit_code_for`（`qaplat/gate.py:359`）/ `cmd_tia_select` 返回 0 |
| 1 | 门禁未达标（违规非空） | `gate.exit_code_for`（`qaplat/gate.py:359`） |
| 2 | 用法错误 | argparse 解析失败；`--select bogus`（`choices=["tia"]`）；显式 `--coverage-data` 不存在（`qaplat/cli.py:189`）；选择器抛异常（`qaplat/cli.py:142-145`、`:257-259`） |

`--select bogus` 不需要 `cmd_gate` 自己校验：`choices=["tia"]`（`qaplat/cli.py:537`）让 argparse 在解析阶段就以 `SystemExit(2)` 退出，错误信息里会列出可选值。这就是「用参数表管住取值」的好处——非法值进不了业务函数。

### 6.9 一条命令的完整旅程（收束）

```
python -m qaplat.cli gate --layer pr --select tia --coverage-data .tia-coverage
   │
   ├─ cli.main()                                qaplat/cli.py:614
   ├─ argparse：--select ∈ {tia, None}          qaplat/cli.py:490-496
   ├─ cmd_gate(args)                            qaplat/cli.py:118
   │    ├─ _resolve_coverage_data(...)           qaplat/cli.py:176 → Path / None / 退出 2
   │    ├─ tia = 函数内 import                    qaplat/cli.py:227
   │    ├─ _tia_extra(...)                      qaplat/cli.py:219
   │    │    ├─ _layer_collect_args(spec)        qaplat/cli.py:209  → ["-m", "…"]
   │    │    ├─ tia.select_from_git(...)         qaplat/tia.py:686
   │    │    │    ├─ registry.collect_cases      qaplat/registry.py:202 → 子进程
   │    │    │    ├─ tia.changed_hunks           qaplat/tia.py:334
   │    │    │    ├─ tia.load_context_index      qaplat/tia.py:387
   │    │    │    └─ tia.select_by_lines         qaplat/tia.py:567
   │    │    └─ 打印摘要行 + 返回 extra
   │    ├─ gate.run_layer(spec, extra)          qaplat/gate.py:187
   │    │    ├─ gate.build_pytest_args           qaplat/gate.py:139
   │    │    └─ pytest 子进程（-m … -p qaplat._dump … <nodeid…>）
   │    ├─ _record_layer_run(store, …)          qaplat/cli.py:110  ← 落库在比对之前
   │    └─ gate.evaluate(spec, record)          qaplat/gate.py:312  → 5 条判据
   └─ return gate.exit_code_for(result)         qaplat/gate.py:359  → 0 / 1
```

这条链里「选得对不对」由 `tia.py` 负责，「跑得对不对」由 `gate.py` 负责，「两者接得对不对」由本节这几段代码负责。三者中任何一环静默失败，表现都是「门禁绿了」——所以第 5 章的三条红线（git/coverage/收集失败一律抛错、CLI 折成 2）必须同时成立。

---

## 第 7 章 一次完整实测：四个场景的真实数字

### 7.1 先看总数：这次实测的规模

| 项目 | 值 | 来源 |
|---|---|---|
| 仓库 | `D:\DSH\test-projects\api-regression-selector` | 派活 |
| 解释器 | `D:\DSH\test-projects\fullstack-test-framework\.venv\Scripts\python.exe` | 派活（本仓无 .venv） |
| 全量命令 | `pytest -m "not ui" -q` | Lead 实跑 |
| 全量结果 | **`3 failed, 588 passed, 34 deselected, 11 xfailed in 196.55s (0:03:16)`** | Lead 实跑 |
| 收集总数 | **636** | Lead 实跑 |
| 覆盖率数据 | `.tia-coverage`，**757,760 字节** | Lead 实测 |

先把 636 / 602 / 34 / 11 / 3 / 588 这几个数对齐，后面所有比例才有意义：

```
收集总数 636
 ├─ UI 用例 34（-m "not ui" 时 deselect，不产生 context）
 └─ 实际执行 602 = 588 passed + 3 failed + 11 xfailed
```

**那 3 条 failed 是上游基线就有的**，不是 TIA 引入的：

- `test_confcutdir_only_added_for_targets_outside_the_repo`
- `test_collect_cases_survives_a_churning_temp_root`
- `test_confcutdir_option_is_spliced_before_the_double_dash`

它们在上游仓 `dfc9fe6`（本仓 fork 的父提交）上同样失败。这 3 条属于**平台层 confcutdir 相关用例**，与本次改动无关——第 7.7 节讨论漏检率时，这个事实很关键。

> **636 与 689 是两个口径，别混用。** 636 是采集 `.tia-coverage` 那一刻的收集总数（`-m "not ui"` 口径）。现在在工作区直接跑 `python -m qaplat.cli tia select ...` 会看到 `total_cases: 689`，因为部·测试新增的 `testcases/platform/test_tia.py`（53 条）已经被收集进来：636 + 53 = 689。本文所有比例一律按 **636** 计算（那是覆盖率数据与 Lead 基线实测的口径）；提到「当前工作区实况」时才用 689。另外：`gate --select tia` 的分母按层收窄已修复并实测（`--layer smoke` 36、`--layer api` 132，见 6.5）；但**本文场景 A–D 的 ratio 全部是 636 全仓采集口径**（采集那一刻 `-m "not ui"` 的收集数），不是分层分母——别拿 36 去反算 0.16%，两者分母不同。

### 7.2 场景 A：只改一个测试文件

| 改动 | v1 结果 | v2 结果 |
|---|---|---|
| `testcases/login/test_login_contract.py`（整文件） | 该文件 **29 条** | 同一文件 **29 条** |
| 全量用例数 | 636 | 636 |
| `ratio` | **29/636 ≈ 0.0456** | 同左 |

判定路径：

- v1：`testcases/` 前缀命中第②级，`by_file` 直接取出该文件的 nodeid（`qaplat/tia.py:466`）；
- v2：`select_by_lines` 的第②级同样按文件命中（`qaplat/tia.py:617`）。

**为什么这一档两套策略结果相同**：改测试文件的影响面本来就是这个文件自己，不需要行级信息。这也是 v2 不把 testcases 文件丢给行级处理的原因——测试代码的行归属（fixture、参数化、import 期）比业务代码更不可靠，按文件命中更稳。

### 7.3 场景 B：只改被测服务的一行（`services.py`）

| 改动 | v1 结果 | v2 结果 |
|---|---|---|
| `mock_server/services.py` 第 **128** 行 | `full=True`（**636** 条） | `full=False`，选中 **1 条** |
| 具体条目 | —— | `testcases/mes/test_auth_secrets.py::test_role_passwords_come_from_environment` |
| `ratio` | 1.0 | **1/636 ≈ 0.0016** |

数据取自 `.tia-coverage`（本文实测复核）：

```
index["mock_server/services.py"][128] → 1 个 nodeid
index["mock_server/services.py"][129] → 1 个 nodeid
index["mock_server/services.py"][130] → 1 个 nodeid
```

这个文件实测 **486 行被覆盖、0 行模块级**——它的代码几乎全部在请求处理过程中执行，天然带用例 context。同类的还有：

| 文件 | 被覆盖行数 | 模块级行 | 影响面最小的行命中数 |
|---|---|---|---|
| `mock_server/services.py` | **486** | 0 | **1**（行 128/129/130 等） |
| `mock_server/quantity.py` | **59** | 0 | **1**（行 61/78/80） |
| `mock_server/idempotency.py` | **82** | 0 | **1**（行 42/43/45） |
| `mock_server/routers.py` | **444** | 0 | **1**（行 64） |

**这一列「1」是 v2 的全部意义**：v1 面对同一个文件只会说「全量 636 条」，v2 能说出「这一行只有 1 条用例执行过」。同样是「改被测服务」，两种判据的成本差 636 倍。

### 7.4 场景 C：改平台层（`qaplat/gate.py`）

这个场景最有趣，因为它能同时演示 v2 的「准」和 v2 的「兜底」。

| 改动 | v1 结果 | v2 结果 |
|---|---|---|
| `qaplat/gate.py` 第 **155** 行（`build_pytest_args` 函数体里拼 `-p qaplat._dump` 的地方） | `full=True`（关键文件） | `full=False`，选中 **7 条** |
| `qaplat/gate.py` 第 **139** 行（`def build_pytest_args(...)` 定义行） | `full=True` | `full=True`（**模块级行**，reason 含「属于模块级代码」） |
| `ratio`（第 155 行） | 1.0 | **7/636 ≈ 0.0110** |

为什么同一个文件、相隔 16 行，结论天差地别：

- 第 139 行是**函数定义**，在 import 期执行 → 索引里带空 context 哨兵（`""`）→ 判据⑤触发全量；
- 第 155 行在**函数体**里，只有真正调用过 `build_pytest_args` 的用例才执行它 → 7 条用例命中。

这正是第 2.4 节那个表的意义：`qaplat/gate.py` 233 行被覆盖、**109 行是模块级**（47%）。平台层的定义行很多，所以「改平台层」在 v2 里有一半概率仍然全量——这是设计上的正确取舍（改签名确实影响所有调用者），但要提前知道。

### 7.5 场景 D：改 `routers.py` 的 `readyz`（v1 的教学案例）

改动是第 **65** 行：

```python
# mock_server/routers.py:62-66
@router.get("/readyz", tags=["system"], summary="就绪探针")
def readyz():
    with read_tx() as conn:  # 第 64 行
        conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()  # ← 第 65 行
    return {"status": "READY"}
```

| 判据 | 结果 |
|---|---|
| v1：`read_referenced_paths("mock_server/routers.py")` | **`()`（0 条）** → `full=True` → 636 条 |
| v2：`index["mock_server/routers.py"][65]` | **1 个 nodeid** → 选中 **1 条** |
| 选中的 nodeid | `testcases/contract/test_contract_export.py::test_live_error_status_codes_are_reachable` |
| `ratio` | **1/636 ≈ 0.0016** |
| 只跑这 1 条的墙钟 | **2.83s**（Lead 实跑） |
| 全量墙钟 | **196.55s** |
| 差距 | **约 69 倍**（196.55 / 2.83 ≈ 69.4） |

这个场景是整份文档的「主线剧情」：

1. **v1 为什么输**：路由写的是 `@router.get("/readyz")`，`/api/v1` 前缀在 `mock_server/app.py:50` 的 `include_router(router, prefix=API_PREFIX)` 里，而提取正则只认 `/api` 开头（`qaplat/registry.py:33`）。所以 v1 对这个文件提取 0 条 → 全量。
2. **v2 为什么赢**：不问 URL，只问「这一行被谁执行过」。`readyz` 只被契约层那一条用例打到过，所以改它只影响那一条。
3. **69 倍不是常态**：2.83s 里含 pytest 启动与 `mock_server` 起服务的固定开销；如果选中的是 7 条（场景 C），耗时会高于 2.83s，但仍然远低于 196.55s。

#### 7.5.1 在命令行里把这条链真的走一遍

上面那张表是直接调 `select_by_lines()` 拿到的。真正在终端跑，是这样一个来回（工作区干净时）：

```bash
# 只动一行：在 readyz 那次查询的行尾加个注释，不改任何行为
sed -i '65s/$/  # tia-demo/' mock_server/routers.py

# 看这次改动会影响哪些用例（不跑测试）
python -m qaplat.cli tia select --base HEAD
```

输出：

```
模式: 部分选择（lines 策略）
原因: 命中 1 条用例（分布在 1 个测试文件、覆盖 1 条变更行）
变更文件: 1 个
  - mock_server/routers.py
命中测试文件: 1 个
  - testcases/contract/test_contract_export.py
命中接口路径: 0 条
选中: 1 / 689 条用例（0.15%）
用例:
  - testcases/contract/test_contract_export.py::test_live_error_status_codes_are_reachable
已知限制: 影响面按文件/行粒度判定；动态拼接路径提取不到会退化为全量；已删除用例匹配不到同样退化为全量
```

`--json` 是同一份结果的机器可读版：

```json
{"full": false, "strategy": "lines", "selected": 1, "ratio": 0.0015,
 "total_cases": 689, "changed_files": ["mock_server/routers.py"]}
```

**分母是 689，不是 636**——636 是采集 `.tia-coverage` 那一刻的口径，之后又新增了 53 条平台自测（`test_tia.py`），所以当前全量是 689。这就是 6.5 讲的「分母看口径」，别拿两个分母的数直接比。

只跑被选中的那一条：

```bash
python -m pytest "testcases/contract/test_contract_export.py::test_live_error_status_codes_are_reachable" -q
# 1 passed in 2.21s
```

**2.21s vs 196.55s ≈ 89 倍**。它和上面那个 2.83s 不矛盾：两次是不同时间的独立运行，pytest 报的自身计时本身就有波动，而倍数的大头是「起一次 pytest + 起一次 `mock_server`」的固定开销——所以这个倍数只能当量级看，不能当常数用。

### 7.6 四场景汇总

| 场景 | 改动 | v1 | v2 | 差别 |
|---|---|---|---|---|
| A | 测试文件整文件 | 29 条（4.56%） | 29 条（4.56%） | 相同 |
| B | `services.py:128` | 636（100%） | **1 条（0.16%）** | v2 准 |
| C | `gate.py:155` | 636（100%） | **7 条（1.10%）** | v2 准 |
| C' | `gate.py:139`（def 行） | 636（100%） | 636（100%） | 都全量（模块级兜底） |
| D | `routers.py:64` | 636（100%） | **1 条（0.16%）**，2.83s vs 196.55s | v2 准，**约 69 倍** |

> 表中所有百分比的分母都是 **636**（全仓采集口径）。`--layer` 口径下分母不同（smoke 36 / api 132，见 6.5），**跨口径不能直接比**：同一个 1 条用例，写成 `1/636 ≈ 0.16%` 还是 `1/36 ≈ 2.78%`，差别只在分母来自哪一档。

读这张表要抓住两件事：

- **v1 在 B/C/D 三个场景全是满的**，也就是说对被测服务与平台层，v1 基本没产生价值；
- **v2 不是万能的**：C' 说明「改定义行」仍然全量。v2 的准，准在「函数体被谁执行过」这种行上。

### 7.7 漏检率怎么验（以及那 3 条基线红为什么必须先排除）

`docs/tia-plan.md` §5 给的验收口径：

> **漏检率定义**：`全量跑的 failed nodeid 集合` 中，**不在** TIA 选中集里的数量。必须为 0。

按字面直接算会撞上一个坑：本次全量跑有 **3 条 failed**，都是平台层 confcutdir 用例（第 7.1 节列出的那 3 条），而场景 D 只选了 1 条契约用例——按字面定义，漏检率会是 3，而不是 0。

所以正确的验证步骤是三步，不是一步：

1. **先确定基线失败集**：在父提交 `dfc9fe6`（或未改动的干净工作区）上跑一次全量，得到「本来就红」的 nodeid 集合。上游同样失败，说明这 3 条不是本次改动引起的。
2. **再算「本次改动引起的新失败」**：本次全量 failed 集 − 基线失败集。场景 B/C/D 的改动都不该产生新失败，这个差集应当是空集。
3. **最后才是漏检率**：对「新失败集」逐条检查是否在 TIA 选中集里。空集时漏检率天然为 0；非空时才算真漏。

换句话说：**漏检率必须对着「本次改动引入的失败」算，不能对着「全量里所有红的用例」算**，否则仓库里任何一条长期红都会把指标污染成非 0。这也是为什么 CI 里把 TIA 与覆盖率/基线红分开跑（第 8 章）。

---

### 7.8 漏检率实测：故意改坏一行，看 TIA 选不选得到那 7 条真红

7.7 给的是方法，这一节给的是**真正跑过一次的漏检实验**（Lead 实跑）。

#### 7.8.1 实验设计

把 `qaplat/registry.py` 第 70 行——`normalize_path` 里那句路径归一化的核心——临时改掉：

```python
# qaplat/registry.py:70（实验时被临时替换）
text = path.split("?", 1)[0].strip().rstrip("/")

# 实验时改成：
return "/__tia_experiment__"
```

这样**所有**接口路径都会归一化成同一串 `/__tia_experiment__`，凡是「按路径认 operation」的断言必然集体失准——于是平台层会出现一批**真实的红**。

步骤：

1. 改这一行；
2. 跑全量 `-m platform`，记下 failed 集合；
3. 用 TIA 对「`registry.py` 第 70 行」求选中集（`hunks=[ChangedHunk("qaplat/registry.py", (70,))]` + 现有 `.tia-coverage`）；
4. 比对「真红集合」与「选中集合」；
5. **原样恢复**该行，并确认 `git diff qaplat/registry.py` 为空、platform 层回到 `3 failed, 286 passed`。

#### 7.8.2 结果

| 项 | 数值 |
|---|---|
| 全量 `-m platform` | **`10 failed, 286 passed, 400 deselected in 43.81s`** |
| 其中基线失败（改动前就有） | **3 条**（confcutdir 那三条，见 7.1） |
| **本次改动造成的真红** | **7 条** |
| TIA 对第 70 行的选中集 | **11 条**，全在 `testcases/platform/test_registry_coverage.py` |
| **漏检（旧索引，按定义直算）** | **3 条 / 7 条 ≈ 43%** —— 4 条在选中集内，3 条不在（见 7.8.4）。**这是这次实验的真实结果** |
| **漏检（补采后复算）** | **0 条 / 7 条** —— 同一行重采后选中 34 条，那 3 条已全部命中（见 7.8.6 实测） |
| 选中比例 | 11 / 296 ≈ **3.7%**（296 = 286 passed + 10 failed；按恢复后 platform 层 289 条口径算约 3.8%）——这是 **platform 层自身口径**，与 7.6 的 636 全仓口径不同 |

#### 7.8.3 7 条真红分别在哪

**在 `testcases/platform/test_registry_coverage.py` 里 4 条，全部落在 TIA 选中的 11 条之内**——这就是「漏检 0」的直接证据：

- `test_source_path_literal_claims_the_operation`
- `test_platform_prefix_match_does_not_swallow_similar_directories`
- `test_path_claim_still_works_outside_the_repo`
- `test_collect_cases_features_a_second_file`

另外 3 条在 `testcases/platform/test_tia.py`（部·测试新加的），**不在选中集里**：

- `test_injected_path_reader_selects_referencing_cases`
- `test_placeholder_parameters_are_normalized_before_intersection`
- `test_paths_without_any_referencing_case_fall_back_to_full`

#### 7.8.4 那 3 条为什么没被选中：索引陈旧，不是算法漏检

`test_tia.py` 是在 `.tia-coverage` **采集之后**才新增的文件。那份覆盖率数据里没有任何一条 context 属于这个文件，索引里也就没有它——所以无论改哪一行，TIA 都不可能选中它。

这不是判据算错，而是**输入里没有这份新测试的实测数据**。它对应第 9.1 节的第 14 条限制，以及 9.5 节的运维要求：

> **新增测试文件必须重新采集覆盖率，否则新测试永远不会被 TIA 选中。**

把这条剔除之后，本次实验的漏检率是 **0/7 = 0%**。

**上面这句是错的，留着当反面教材。**

按 `tia-plan.md` §5 的字面定义，漏检率就是「新失败集里不在选中集的条数」——分母 7、分子 3，**3/7 ≈ 43%**，不管我怎么解释那 3 条为什么不在。剔除理由再充分，也不能改分子。

「我认为这不是算法判错」和「漏检为 0」是两句话：
- 前者是**归因**：这 3 条漏掉是因为索引里没有新测试的实测数据，不是 `select_by_lines` 判错；
- 后者是**事实陈述**：它需要看选中集，而选中集里确实没有这 3 条。

正确的写法是两句并写、各归各位：

> **旧索引下：漏检 3/7（43%），三条全部来自采集之后才新增的 `test_tia.py`。**
> **补采一次后：同一改动选中 34 条，那 3 条全部命中，漏检 0/7（7.8.6 实测）。**

前者是这次实验的真实结果，后者是「补采即修复」的证据。**把后者冒充前者，就是用一个修好之后的数字去覆盖一个没修之前的失败**——这在对外汇报里是硬伤，简历上尤其不能这么写。

### 7.8.6 补采之后重算一次：那 3 条确实进来了

重采覆盖率（这次 `test_tia.py` 已经存在，它自己也被执行）后，对同一行 `qaplat/registry.py:70` 重新求选中集：

```
full = False | strategy = lines
selected = 34 / 689
test_tia.py 命中 = 23
```

那 3 条全在命中的 23 条里：

- `testcases/platform/test_tia.py::test_injected_path_reader_selects_referencing_cases`
- `testcases/platform/test_tia.py::test_placeholder_parameters_are_normalized_before_intersection`
- `testcases/platform/test_tia.py::test_paths_without_any_referencing_case_fall_back_to_full`

选中数从 11 涨到 34，不是算法变松了，而是**索引变全了**：新采的覆盖率里，`qaplat/registry.py` 第 70 行多了 23 条「执行过这一行」的用例（`test_tia.py` 里大量用例直接调 `normalize_path`/`select`）。

这组对照把限制说得非常清楚：**TIA 的准确率上限由索引新鲜度决定**，不是由选择算法决定。改代码之前先看索引是不是上个月采的——这比调算法重要得多。

复现命令：

```bash
COVERAGE_FILE=.tia-coverage <VENV> -m pytest -m "not ui" --cov=. --cov-context=test --cov-report=
python - <<'PY'
from pathlib import Path
from qaplat import registry, tia
idx = tia.load_context_index(Path(".tia-coverage"), root=Path("."))
sel = tia.select_by_lines((tia.ChangedHunk("qaplat/registry.py", (70,)),), idx, registry.collect_cases([]))
print(sel.full, len(sel.node_ids), sel.total_cases)
PY
```

#### 7.8.5 这组数字比 69 倍更有说服力

场景 D 的「**69 倍**」证明的是**快**：1 条 2.83s vs 全量 196.55s。

这次的「**3.7% 与 100%**」原本想证明「又快又没漏」，实际证明的是**快，但漏了一个可修复的口子**：

- TIA 只跑 platform 层的 **11 / 296** 条（约 3.7%）；
- 而这次改动造成的 **7 条真红**里，只有 **4 条当场被选中**；另外 3 条因索引陈旧确实漏了，**补采一次后全部命中**（7.8.6）。所以这里的准确说法是「3/7 漏检，可修复」，不是「一条都没漏」。

**「快」可以靠少跑骗出来，「没漏」骗不了。** 这就是第 1.3 节那条价值判断的实测凭据：宁可多跑（11 条而不是 7 条），不可漏跑。

## 第 8 章 与门禁的关系：为什么 tia 不挂覆盖率门

### 8.1 `gate.evaluate` 的五条判据（实测源码）

`qaplat/gate.py:312` 的 `evaluate` 只检查五件事，**没有一件事是覆盖率**：

| # | 判据 | 代码行 | 触发时的违规文案 |
|---|---|---|---|
| 1 | `run.total == 0` | `qaplat/gate.py:318` | 该层未收集到任何用例 |
| 2 | `decisive == 0`（passed + failed 为 0） | `qaplat/gate.py:320` | 全部未产出可判定结论（skipped/error） |
| 3 | `run.pass_rate < spec.min_pass_rate` | `qaplat/gate.py:323` | 通过率低于阈值 |
| 4 | `run.errors > spec.max_errors` | `qaplat/gate.py:330` | error 数超出预算 |
| 5 | `run.duration_seconds > spec.max_duration_seconds` | `qaplat/gate.py:335` | 耗时超出预算 |

第 4 条在源码里有一段值得读的注释（`qaplat/gate.py:326`）：

> error 预算**必须独立判定，不能挂进上面的 else-if 链**。理由：分母 `decisive = passed + failed` 不含 error，实测 smoke 层 `total=36 passed=30 failed=0 errors=6` 会算出 `decisive=30`、`pass_rate=1.0`。一旦把它接成 `elif`，该轮会先被 pass_rate 分支判为达标，`run.errors` 永远轮不到检查，「没跑成的用例」就被静默当成通过。

### 8.2 `gate` 的 pytest 命令行里没有 `--cov`

`build_pytest_args`（`qaplat/gate.py:139`）拼出来的 argv 只有三样东西：

```python
# qaplat/gate.py:150（节选）
args: list[str] = []
if spec.marker_expr:
    args += ["-m", spec.marker_expr]
args += ["-p", DUMP_PLUGIN, "--qaplat-dump", str(_dump_path_for(spec.layer.value))]
args += with_confcutdir([str(item) for item in extra])
```

没有 `--cov`、没有 `--cov-context`、没有 `--cov-fail-under`。**覆盖率门禁根本不在 `qaplat gate` 里**。

### 8.3 覆盖率门禁的真身

覆盖率有两道，都在别处：

| 门 | 在哪 | 口径 |
|---|---|---|
| 仓库级 | CI `test.yml:321` 的 `--cov-fail-under=54` | 只挂在 `-m api` 那一步上；按语句数加权 |
| 逐文件 | `scripts/check_coverage.py` 读 `pyproject.toml` 的 `[tool.coverage.perfile]` | `fail_under = 55`（`pyproject.toml:204`）、`min_statements = 20`（`pyproject.toml:207`） |

`pyproject.toml:184` 那段注释解释了为什么需要第二道：

> CI 里的 `--cov-fail-under=54` 是**仓库级**的，按语句数加权平均。一个模块整体掉光，只要总量还高于 54，CI 就是绿的；反过来把一个大模块从 90% 拉到 98%，也能把另一个模块的崩塌补回来。

### 8.4 为什么 tia 模式下这两道门都「天然不挂」

关键在 `qaplat/tia-plan.md` §4.3 的那句话：**`qaplat gate` 的 pytest 命令行不带 `--cov`**。

于是：

- 跑 `gate --select tia` 时，pytest 根本没有采集覆盖率 → `--cov-fail-under` 无从生效；
- `scripts/check_coverage.py` 读的是**上一次 `-m api` 生成的覆盖率产物**，TIA 这一轮不会覆盖它，也不会让它的数字变难看；
- 因此 tia 模式不需要「加一个开关来跳过覆盖率门」——它本来就不在门里。

### 8.5 如果硬把 `--cov` 拼进 tia 那一步，会发生什么

假设有人想「省一次运行，TIA 与覆盖率一起跑」：

```
pytest -m "smoke or api or platform" <1 条 nodeid> --cov=. --cov-fail-under=54
```

结果几乎必然是红的，而且**是假红**：

1. 覆盖率只统计这 1 条用例执行过的行 → 全局覆盖率会从 55% 左右掉到个位数；
2. `--cov-fail-under=54` 判它不达标 → CI 红；
3. 更糟的是，这份「只有 1 条用例」的覆盖率数据如果被别处当作基线，会把所有后续数字都污染。

所以规划里写得很明确（`docs/tia-plan.md` §4.3）：

> 但 CI 里如果先跑 `tia` 再跑 `coverage`，两步必须**分开**，不能把 `--cov` 拼进 tia 那一步。
> `docs/` 与 `README` 必须写明这一点，否则会有人以为「tia 让覆盖率掉了」。

### 8.6 采集 TIA 数据的那一次运行为什么不能当门禁

反过来也要注意：第 2.6 节的采集命令

```bash
COVERAGE_FILE=.tia-coverage pytest -m "not ui" --cov=. --cov-context=test
```

会跑一次**全量非 UI**（602 条），带上覆盖率采集的额外开销。这一次的用例结果**不应该拿来当门禁结论**：

- 它不是为了「判定这一轮代码质量」，而是为了「采集每行被谁执行」；
- 采集时点与判定时点不同：采集发生在提交 A，判定发生在提交 B；两次之间行号可能已经漂移（第 9 章）。

正确做法是两件事分开：**周期性地采一次 context 数据**（例如每晚或每次合并后），**平时用这份数据 + 当前 diff 做选择**。

---

## 第 9 章 已知限制与 v2 路线

### 9.1 已知限制（每条都配一个兜底动作）

| # | 限制 | 具体表现 | 兜底动作 |
|---|---|---|---|
| 1 | **v1 是文件级精度** | 一个文件里所有路由/函数共用一个判断；改任何一行都「命中整个文件」 | v2 行级；无覆盖率数据时退回 v1（`select_from_git` 的 `data_path=None`） |
| 2 | **行级是「按文件严格兜底」而不是「按行严格兜底」** | 同一文件既有命中行又有未命中行时，只选命中行；未命中行可能改变控制流 | 命中为空才全量；v3 可加「未命中行即全量」开关 |
| 3 | **未跟踪文件没有目标行** | `ChangedHunk(path=..., lines=())` | 判「影响面未知」→ 全量 |
| 4 | **纯删除/二进制/重命名没有目标行** | 同上 | 同上 |
| 5 | **模块级行一律全量** | 改 `def`/`import`/常量 → 全量（`gate.py` 233 行里 109 行是这种） | 无法避免：这些行影响所有 import 者 |
| 6 | **采集时点与判定时点可能漂移** | 覆盖率在提交 A 采集，判定在提交 B；期间插入了行 → 行号对不上 | 命中为空 → 全量；建议采集与判定尽量同 commit 或频繁重采 |
| 7 | **UI 层与 `mes_web/*` 没有 context** | `-m "not ui"` 采集，34 条 UI 用例不产生 context；`mes_web/__init__.py`、`app.py`、`run.py` 不在索引里 | 改它们 → 全量（判据④） |
| 8 | **`qaplat/tia.py` 自己不在索引里** | 采集期不 import 它（只有 CLI 用），所以 `measured_files` 有它、`index` 里没有 | 改 TIA 自己 → 全量（安全，但要知道） |
| 9 | **只能看到「执行过」的关系** | 反射/动态 import/`eval` 建立的关系 coverage 看不到 | 命中为空 → 全量 |
| 10 | **不处理删除的用例** | nodeid 已不存在 → 匹配不到 | 全量兜底（判据③） |
| 11 | **路径必须是仓库相对路径** | 覆盖率用绝对路径，git 用相对路径 | `_relative_to_root` 归一化；仓库外文件跳过 |
| 12 | **git 与用例收集都是子进程** | 失败会抛错（`RuntimeError` / `CaseCollectionError`） | CLI 折成退出码 2，不静默 |
| 13 | **外部工具的默认输出可能改写路径** | git 的 `core.quotepath=true` 会把非 ASCII 路径转成八进制假路径（已修复，见 3.8） | `_run_git` 统一加 `-c core.quotepath=false`；换其它外部工具时同样先确认输出口径 |
| 14 | **索引不包含新增测试文件** | 采集之后新增的测试文件，其 nodeid 不在索引里 → 永远不会被选中（7.8 实测 3 条真红因此未选中） | 改测试代码 / 新增测试文件后重采，见 9.5 |

### 9.2 v2 已经做到的

| 能力 | 函数 | 位置 |
|---|---|---|
| 变更文件（committed + 工作区 + 未跟踪） | `changed_files` | `qaplat/tia.py:298` |
| 变更行（`-U0` hunk 解析） | `changed_hunks` | `qaplat/tia.py:334` |
| 行 → 用例倒排索引 | `load_context_index` | `qaplat/tia.py:387` |
| 纯函数行级选择 | `select_by_lines` | `qaplat/tia.py:567` |
| 纯函数文件级选择（降级路径） | `select` | `qaplat/tia.py:462` |
| 串起 IO 的入口（带 `data_path` 开关） | `select_from_git` | `qaplat/tia.py:686` |

平台层自己的测试（部·测试交付）：`testcases/platform/test_tia.py` **53 条全绿**，`qaplat/tia.py` 的行覆盖率为 **90%**。这组用例覆盖了三套判据的主要分支（关键文件、testcases 命中、路径交集、空输入、索引缺失、模块级行、行命中为空），也是「纯函数可以直接喂构造数据断言」这个设计最直接的收益——它们不需要真的改代码或采覆盖率。

**已修的历史缺口（1 条，放在「已经做到」里而不是「已知限制」里）：**

- **现象**：`qaplat/_dump.py` 的 `pytest_collection_modifyitems` 缺 `@pytest.hookimpl(trylast=True)`，在 `-m` deselect 之前记录 → `collect_cases` 拿到全量（689），`total_cases` 不随层变化；更危险的是选中 nodeid 可能跨层（`--layer smoke` 塞进只为 `-m api` 准备的用例）。
- **修法**：加 `trylast=True`（`qaplat/_dump.py:82`）+ `import pytest`（`:23`），让记录发生在 deselect 之后。
- **修后**：`--layer smoke` → 36、`--layer api` → 132、无层预览 → 689，分别与 pytest 自报计数、`AGENTS.md` 的「132 条」对上；`-m platform` 回归 `3 failed, 286 passed, 400 deselected`（仍那三条基线，无新增）。

这条值得单独记，因为它说明**分母不只是显示问题，还是正确性问题**：分母错了，选中集也可能跨层；两者是同一个 hook 顺序错误的两面。

### 9.3 v3 候选（都还没做）

按「收益 / 成本」排序，以下是讨论过但**没有实现**的方向：

1. **「未命中行即全量」开关**：把第 2 条限制（同文件混合命中）收窄成严格模式。代价是选择集变大，收益是更安全；适合允许「多跑一点」的夜间层。
2. **索引缓存**：`load_context_index` 每次都要读 SQLite 并重建 dict。数据量大时可以按文件 mtime 缓存，CLI 多次调用时省时间。
3. **增量采集**：只对本次改动涉及的文件采 context（需要 pytest 侧支持按 nodeid 注入，成本高）。
4. **把 `strategy` 与「兜底原因」做成报表页**：现在只有文本/JSON，度量库里还没有这一维度。
5. **UI 层 context**：UI 用例跑得慢、依赖浏览器；要不要为它采集 context 需要单独评估。

### 9.4 明确不做的事

`docs/tia-plan.md` §8 列了一组排除项，值得原样抄在这里当护栏：

- 不引入 `pytest-testmon`、`coverage` 之外的新依赖；
- 不改 `gate.evaluate()` 的判据与阈值；
- 不改 `qaplat/FROZEN_CLI.md` 与任何 `FROZEN_*` 文件；
- **不默认开启 `--select tia`**，不把它塞进 PR 流水门（先手动跑一段时间看数字）。

---

### 9.5 运维要求：什么时候必须重采 `.tia-coverage`

采集覆盖率 context 那一步，**不是「跑测试」，是「建索引」**。理解这一点，才知道该在什么时候重跑它。

重采命令（原样抄，`--cov-report=` 是有意的空值，关掉终端覆盖率报表）：

```bash
COVERAGE_FILE=.tia-coverage pytest -m "not ui" --cov=. --cov-context=test --cov-report=
```

三个参数各自的理由：

| 参数 | 作用 | 为什么必须 |
|---|---|---|
| `COVERAGE_FILE=.tia-coverage` | 把数据写到专用文件 | 不覆盖日常 `.coverage`；TIA 与常规覆盖率各用各的 |
| `--cov-context=test` | 给每行打上「哪个用例执行过」的标签 | 没有它，`load_context_index` 建不出索引，直接抛 `RuntimeError`（4.4 节⑤） |
| `--cov-report=` | 不生成任何覆盖率报表 | 这次运行的目的不是看覆盖率数字，省掉报表生成的时间 |

**必须重采的时机**：

1. 新增测试文件（7.8 实测：`test_tia.py` 就是采集后新增的，导致 3 条真红不在索引里）；
2. 改了测试文件的结构（fixture、参数化 id 变化会改变 nodeid）；
3. 改动的业务代码与上次采集相隔太久，行号已经漂移；
4. 换了分支或 rebase 之后（行号整体变化）。

**这一步的结果不能当门禁结论**：它是 602 条用例的一次全量执行，会有它自己的红与绿；门禁要看的是「用这份索引 + 当前 diff 选出来的子集」的结果（8.6 节）。换句话说，采集负责「知道谁执行过什么」，判定负责「这次改动该跑谁」——两件事不要混在一次运行里。

### 9.6 采完记得记 HEAD：让「过期」这件事自己喊出来

上面四个时机全靠人记得。记不住的时候，失效是**静默的**：索引旧了，选择器只会少选几条，不报错、不提示。

所以采完立刻记一次 HEAD：

```bash
python -m qaplat.cli tia capture --coverage-data .tia-coverage
# → [qaplat] tia capture: HEAD=875e14a90fdc → .tia-coverage.head
```

它只写一个旁边的小 JSON，不碰索引本身：

```json
{
  "head": "875e14a90fdc53dc120a5bd6927683cddaed757a",
  "captured_at": "2026-10-30T22:01:44+00:00",
  "coverage_file": ".tia-coverage"
}
```

之后每次 `tia select` / `gate --select tia` 都会读它，和当前 HEAD 比：

| 状态 | 你会看到 |
|---|---|
| 与 HEAD 同步 | 什么都没有（不打扰） |
| 落后 | `[qaplat] 警告: 索引落后：采集于 c62af9d74103，当前 HEAD 875e14a90fdc，中间有 3 个提交未被覆盖 —— 这些提交里新增的测试不会被选中` |
| 没有标记文件 | 什么都没有（「不知道」不该天天喊，喊多了就被无视） |

**为什么「不知道」沉默、「落后」必须喊**：这两种情况在运维上正好相反。「没有标记」只说明这个仓库还没用过 capture，不影响现有行为；而「落后」意味着**这次选择的结果可能少了几条**，并且它不会以任何其他方式表现出来。

**为什么只警告、不自动重采**：重采 140–190s。自动重采会让一次 `tia select` 从 6 秒变成 3 分钟——为了一个「可能过期」的判断把最常用的查询拖慢 30 倍，结果是没人再用它。宁可留一条警告让人自己决定。

`.tia-coverage.head` 必须留在 `.gitignore` 里（现在是 `.tia-coverage*`）。它一旦被 `git ls-files --others` 当成「新增文件」，每次选择都会判「影响面未知 → 全量」——**一个用来防漏的标记，自己反而把行级选择关掉了**。

## 附录 A 文件速查

| 文件 | 在 TIA 链上的角色 | 关键位置 |
|---|---|---|
| `.tia-coverage` | 上游数据：每行被哪个 context 执行过（SQLite，757,760 字节） | `meta` / `file`(121) / `context`(1807) / `line_bits` |
| `qaplat/tia.py` | 判断中枢：常量 + 两个纯函数 + 三个 IO 函数 | 常量 50-98；`select` 461；`select_by_lines` 566；`select_from_git` 685 |
| `qaplat/registry.py` | 用例来源：`collect_cases` 起 pytest 子进程收集 nodeid 与 `referenced_paths` | `CaseInfo` 56；`read_referenced_paths` 116；`collect_cases` 202 |
| `qaplat/cli.py` | 入口：`tia select` 打印；`gate --select tia` 把 nodeid 塞进 extra | `cmd_tia_select` 240；`_tia_extra` 219；`_resolve_coverage_data` 175 |
| `qaplat/gate.py` | 执行：拼 pytest argv、起子进程、判五条门禁 | `build_pytest_args` 139；`run_layer` 187；`evaluate` 312 |
| `qaplat/paths.py` | 路径常量：`ROOT` 是仓库根 | `ROOT` 14；`with_confcutdir` 82 |
| `.coveragerc` | 覆盖率采集配置：`parallel = true`（子进程分片，最后 combine） | `[run]` 段 |
| `pytest.ini` | `testpaths = testcases`、`-v -raR --strict-markers` | `addopts` / `testpaths` |
| `mock_server/app.py` | 被测服务装配点：前缀在这里拼给路由 | `API_PREFIX` 29；`include_router` 50 |
| `mock_server/routers.py` | 路由定义（不含 `/api` 前缀，v1 提取 0 条） | `readyz` 61-66，第 64 行是场景 D |
| `docs/tia-plan.md` | 契约基准：接口签名、算法、验收数字 | §3 / §4.1 / §4.3 |
| `docs/TIA完全入门.md` | 本文 | —— |

## 附录 B 三条调用链

### B.1 `tia select`（只打印，不跑 pytest）

```
cli.main                          qaplat/cli.py:614
 └─ cmd_tia_select                qaplat/cli.py:240
     ├─ _resolve_coverage_data    qaplat/cli.py:175
     ├─ _pytest_args_from         qaplat/cli.py:199
     ├─ tia.select_from_git       qaplat/tia.py:686
     │   ├─ registry.collect_cases    qaplat/registry.py:202
     │   │   └─ pytest --collect-only 子进程
     │   ├─ tia.changed_hunks          qaplat/tia.py:334
     │   │   ├─ tia.changed_files      qaplat/tia.py:298
     │   │   │   └─ _run_git   ×1~3    qaplat/tia.py:203
     │   │   └─ _new_lines_from_diff   qaplat/tia.py:262
     │   ├─ tia.load_context_index     qaplat/tia.py:387
     │   │   ├─ coverage.CoverageData.read
     │   │   ├─ _relative_to_root      qaplat/tia.py:245
     │   │   └─ _context_node_id       qaplat/tia.py:370
     │   └─ tia.select_by_lines        qaplat/tia.py:567
     └─ render_json / render_text      qaplat/tia.py:748 / 715
```

### B.2 `gate --layer pr --select tia`（判定 + 真跑）

```
cli.main                          qaplat/cli.py:614
 └─ cmd_gate                       qaplat/cli.py:118
     ├─ _resolve_coverage_data     qaplat/cli.py:175
     ├─ _tia_extra                 qaplat/cli.py:219
     │   └─ tia.select_from_git    qaplat/tia.py:686   ← 与 tia select 同一条路径
     ├─ gate.run_layer             qaplat/gate.py:187
     │   ├─ gate.build_pytest_args qaplat/gate.py:139
     │   └─ pytest 子进程（带 nodeid）
     ├─ _record_layer_run          qaplat/cli.py:100
     └─ gate.evaluate              qaplat/gate.py:312
```

### B.3 数据文件之间的引用关系

```
.coveragerc ──(parallel=true)──→ 采集时分片 ──→ combine ──→ .tia-coverage
                                                              │
pytest.ini ──(testpaths/markers)──→ collect_cases ──→ CaseInfo ┘
                                                              │
git diff -U0 ──→ ChangedHunk ────────────────────────────────┤
                                                              ▼
                                                    select_by_lines
                                                              │
charts/配置：pyproject.toml（mypy/ruff/perfile 覆盖率）        ▼
                                                          Selection
```

## 附录 C 术语表

| 术语 | 含义 |
|---|---|
| **TIA**（Test Impact Analysis） | 变更 → 测试选择：从 git diff 出发，只跑可能受影响的用例 |
| **nodeid** | pytest 的用例唯一标识，形如 `testcases/login/test_login_contract.py::test_lc01_login_success`；参数化还会带 `[参数]` |
| **context** | coverage 的 per-test 标签；`--cov-context=test` 时形如 `<nodeid>|run` |
| **模块级行 / 空 context** | 在 import 期执行的行，当时没有用例在跑，标签是空串；TIA 用空串哨兵标记，判全量 |
| **hunk** | `git diff` 里一段以 `@@ -旧起点,旧行数 +新起点,新行数 @@` 开头的改动块 |
| **`-U0`** | git diff 不输出上下文行；只有真正新增/修改的行进入新增侧 |
| **`...`（三点）** | `git diff base...HEAD` 比较 merge-base 到 HEAD，只算「本分支带来的改动」 |
| **worktree** | 工作区 + 暂存区里相对 HEAD 的改动；`include_worktree=True` 时参与判定 |
| **full** | 全量兜底：`Selection.full=True`，`node_ids` 为空，调用方跑整层 |
| **strategy** | `full` / `files` / `lines`，标明这次用了哪一档判据 |
| **ratio** | `len(node_ids) / total_cases`；全量时是 0（因为 node_ids 为空），不是 1——看 `full` 字段判断 |
| **漏检率** | 全量失败集里，不在选中集里的条数；对「本次改动引入的失败」计 |

## 附录 D FAQ

**Q1：为什么 `ratio` 在全量时是 0，而不是 1？**
因为 `_full` 把 `node_ids` 置空（`qaplat/tia.py:279`），「全量」的语义是「不选具体 nodeid」。要判断是不是全量，看 `full` 字段，不要看 `ratio`。

**Q2：改了 `qaplat/tia.py` 自己会怎样？**
它不在覆盖索引里（采集期不 import 它），所以判「不在覆盖索引里」→ 全量。安全，但等于这次 TIA 没生效。

**Q3：为什么 `select` 不直接自己读文件？**
契约要求它是纯函数（测试要能只喂构造数据）。第③级的路径确实只能从变更源码取，这是算法的信息需求，所以用 `path_reader` 注入：默认 `registry.read_referenced_paths`，测试传替身时全程零 IO（`qaplat/tia.py:462` 的 docstring）。

**Q4：没有 `.tia-coverage` 会怎样？**
`_resolve_coverage_data` 找不到就返回 `None`，`select_from_git` 退回 v1（文件级）。日志里会打一行「未找到覆盖率数据，退化为文件级策略」。

**Q5：覆盖率数据存在但没开 `--cov-context=test` 呢？**
`load_context_index` 建不出索引 → 抛 `RuntimeError`，CLI 返回退出码 2。它不会静默退回 v1，因为「数据存在但没 context」是你**以为**行级生效了的典型陷阱。

**Q6：为什么 `strategy` 有三个值，而不是两个？**
`full` 表示没判定出来，`files`/`lines` 表示两种判定方式。消费方要能区分「为什么全量」（看 `reason`）和「按什么选中」（看 `strategy`）。

**Q7：改测试文件为什么不做行级？**
测试代码的行归属（fixture、参数化、import 期）不可靠；按文件命中更稳，而且这一档本来就是 TIA 收益最大的场景（改哪个文件跑哪个文件）。

**Q8：多个文件同时改，结果怎么合并？**
取并集：每个文件各自命中一批 nodeid，最后 `selected` 是集合并集。任一文件触发全量 → 整体立刻全量（判据顺序保证「最早的全量」生效）。

**Q9：全量时为什么 CLI 不把 636 个 nodeid 拼进去？**
Windows 命令行有长度上限，而且「全量」的语义就是「等价于不带 `--select`」。`_tia_extra` 在 `full=True` 时只返回 `passthrough`（`qaplat/cli.py:219`）。

**Q10：为什么 3 条 failed 不算 TIA 的锅？**
它们在上游 `dfc9fe6` 上同样失败（confcutdir 相关平台用例），与本次改动无关。漏检率要对「本次改动引入的新失败」算，第 7.7 节给了三步法。

**Q11：`pytest-of-lefan` 这种临时目录会进覆盖率吗？**
有可能（如果测试在仓库内建临时目录并被 `--cov=.` 覆盖）。这类文件通常不在索引关心的路径里，命中也只会触发「不在索引 → 全量」，不会产生错误判断。

**Q12：这份文档里的行号会不会过期？**
会。行号是写文档那一刻（`qaplat/tia.py` 766 行版本）的真实值；代码一改就可能漂移。**函数名比行号稳定**——查代码时优先用 `grep -n "def select_by_lines" qaplat/tia.py` 定位，再用行号附近的内容确认。

---

## 一句话总结

**TIA 的全部智慧可以压成一句话：不要猜「这行代码属于谁」，去问「这行代码被谁执行过」。**

- 能问出来（行级有 context）→ 只跑执行过它的那些用例；
- 问不出来（模块级、未跟踪、无索引、路径拼不出来）→ 老实跑全量，并把原因写进 `reason`。

`qaplat/tia.py` 的 766 行里，真正「聪明」的地方只占一小块（`load_context_index` + `select_by_lines`）；其余大部分代码——三条 git 命令、`tail` 截断、空索引抛错、`_full` 统一出口——都在做同一件事：**保证这套判据失效时，失败得大声，而不是安静地漏掉一条回归。**
