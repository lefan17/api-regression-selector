"""TIA 选择器的纯函数契约与行级覆盖映射（`qaplat/tia.py`）。

为什么这些用例必须存在
----------------------
「宁可多跑，不可漏跑」是 TIA 唯一的设计取向（`docs/tia-plan.md` §3）。这条取向被破坏时
**不会报错**：它会安静地少选一批用例，让门禁绿着放行一次回归——比崩溃糟得多，因为它是
静默的。所以下面每条用例都钉「兜底方向」：凡是「影响面说不清」的分支（关键文件、空变更、
提取不到路径、没人执行过的行、import 期执行的模块级行），断言一律是 `full=True`，
而不是「选了个小集合也说得过去」。

口径与实现对齐（对着 `qaplat/tia.py` 现有代码写，不按设计稿猜）
--------------------------------------------------------------
1. `mock_server/routers.py` 的路由写成 `@router.get("/healthz")` 子路径，`/api/v1` 前缀在
   `mock_server/app.py` 的 `include_router(prefix=...)` 里拼，所以
   `registry.read_referenced_paths("mock_server/routers.py")` 返回**空元组** → v1 对它只能
   全量。第③级（接口路径交集）因此必须靠 `path_reader=` 注入替身来测，否则「命中路径」
   那条分支永远走不到；本文件里这类替身一旦被误调用就直接抛 AssertionError。
2. `testcases/platform/**` 在 `registry.is_platform_self_test()` 里被排除，本文件的用例
   一律构造 `CaseInfo` 直接传参（不依赖磁盘上的真实用例集，否则断言会随用例库膨胀而漂移）。
3. 行级索引（v2）优先用 `tmp_path` 里自造的 `.coverage`（`coverage.CoverageData` 写真实
   sqlite，`set_context()` 决定每个 context 的行归属）；只有一条集成用例读仓库根真实的
   `.tia-coverage`（602 条用例、117 个文件），它不存在就 skip——不能因为「数据没采到」
   让平台层单测变红。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from coverage import CoverageData

from qaplat import registry, tia

pytestmark = pytest.mark.platform

REPO_ROOT = Path(__file__).resolve().parents[2]
#: 仓库根的真实行级覆盖数据（仓库根 `.tia-coverage`，602 条用例采集，117 个被测文件）。
REAL_COVERAGE = REPO_ROOT / ".tia-coverage"

TEST_FILE = "testcases/api/test_login_contract.py"
OTHER_TEST_FILE = "testcases/platform/test_gate_layers.py"
NODE_A = f"{TEST_FILE}::test_lc01_login_success"
NODE_B = f"{TEST_FILE}::test_lc02_login_failed"
NODE_C = f"{OTHER_TEST_FILE}::test_layered_gate_expressions"

#: 本文件用到的全量用例库：两条同文件、一条异文件，路径归属各不相同。
#: `CaseInfo` 是 frozen dataclass，模块级共享不会在用例之间串数据。
CASES: tuple[registry.CaseInfo, ...] = (
    registry.CaseInfo(node_id=NODE_A, markers=("api",), referenced_paths=("/api/v1/login",), covers=()),
    registry.CaseInfo(node_id=NODE_B, markers=("api",), referenced_paths=("/api/v1/orders",), covers=()),
    registry.CaseInfo(node_id=NODE_C, markers=("platform",), referenced_paths=(), covers=()),
)


def _boom(path: str) -> tuple[str, ...]:
    """注入用的 `path_reader` 替身：被调用即说明走了「不该读源码」的分支。

    比 `lambda path: ()` 更强：后者把「没调用」与「调用了但返回空」混成同一种结果，
    而这两者走的是**完全不同**的判据（①/② 短路 vs ③ 兜底），必须能区分。
    """
    raise AssertionError(f"本用例不应读取源码：{path}")


def _build_coverage(
    data_path: Path,
    root: Path,
    groups: Sequence[tuple[str | None, dict[str, set[int]]]],
) -> Path:
    """按 `(context, {仓库相对路径: 行号})` 的顺序写一份真实可读的 `.coverage`。

    用 `CoverageData.set_context()` 而不是 README 里的 `add_context`：coverage 7.16 的
    `CoverageData` 并没有 `add_context` / `add_file`（已 `inspect.signature` 核实），
    行归属完全由「写之前当前 context 是谁」决定。context 传 `None` 就是空 context，
    也就是 `--cov-context=test` 采集时「模块导入期执行、不归属任何用例」那一档。
    """
    data = CoverageData(str(data_path))
    for context, files in groups:
        data.set_context(context)
        data.add_lines({str(root / relative): lines for relative, lines in files.items()})
    data.write()
    return data_path


# ---------------------------------------------------------------------------
# v1：文件级三级保守策略（docs/tia-plan.md §3）
# ---------------------------------------------------------------------------


def test_changed_test_file_selects_only_its_own_cases() -> None:
    """分支①：变更落在 testcases/ 下 → 只选该文件 nodeid，strategy="files"，且完全不读源码。"""
    sel = tia.select([TEST_FILE], CASES, path_reader=_boom)

    assert sel.full is False
    assert sel.strategy == "files"
    assert sel.node_ids == (NODE_A, NODE_B)
    assert sel.matched_files == (TEST_FILE,)
    assert sel.matched_paths == ()
    assert sel.changed_files == (TEST_FILE,)
    assert sel.total_cases == 3
    assert sel.reason == "命中 1 个测试文件、0 条接口路径"


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "pytest.ini",
        "conftest.py",
        "testcases/login/conftest.py",
    ],
)
def test_always_full_names_force_full(path: str) -> None:
    """分支②：conftest / pytest 配置改的是**所有**用例的运行条件，命中即立刻全量。

    第 4 个参数就是这条用例的重点：`conftest.py` 按 basename 比对而不是按目录前缀，
    落在 `testcases/login/` 下也照样全量（那个目录的夹具同样影响整层）。
    """
    sel = tia.select([path, TEST_FILE], CASES, path_reader=_boom)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.total_cases == 3
    assert sel.matched_files == ()
    assert sel.matched_paths == ()
    assert sel.changed_files == tuple(sorted({path, TEST_FILE}))
    assert sel.reason == f"关键文件 {path} 命中必跑清单（改动影响所有用例），退化为全量"


@pytest.mark.parametrize(
    "path",
    [
        "qaplat/gate.py",
        "data/x.json",
        "docs/openapi.json",
        "requirements.txt",
        "requirements.lock",
        "scripts/report_to_zentao.py",
        "schemas/order.json",
        "traffic-replay/replay/model.py",
    ],
)
def test_always_full_prefixes_force_full(path: str) -> None:
    """分支③：关键目录/文件前缀命中即全量（§3.1 清单；一个前缀覆盖任意深度）。"""
    sel = tia.select([path], CASES, path_reader=_boom)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.changed_files == (path,)
    assert sel.reason == f"关键文件 {path} 命中必跑清单（改动影响所有用例），退化为全量"


def test_always_full_lists_are_stable_and_v2_drops_qaplat() -> None:
    """两份清单本身是契约：v1 稳定排序；v2 刻意去掉 `qaplat/`（平台层是行级映射最准的地方）。

    这条断言的是「两档策略的**唯一**刻意差异」，它退化（有人把 `qaplat/` 加回行级清单）
    时，`qaplat tia` 对平台层源码的收益会默默变回 0，而不会有任何用例变红。
    """
    assert tia.ALWAYS_FULL_PREFIXES == tuple(sorted(tia.ALWAYS_FULL_PREFIXES))
    assert "qaplat/" in tia.ALWAYS_FULL_PREFIXES
    assert tia.ALWAYS_FULL_NAMES == frozenset({"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg"})
    assert tia._LINES_FULL_PREFIXES == tuple(prefix for prefix in tia.ALWAYS_FULL_PREFIXES if prefix != "qaplat/")
    assert "qaplat/" not in tia._LINES_FULL_PREFIXES


def test_injected_path_reader_selects_referencing_cases() -> None:
    """分支④：v1 第③级——变更源码提取到 `/api` 路径，只选引用它的用例。

    这是「路径交集」逻辑唯一的可测入口：真实 `mock_server/routers.py` 提取不出路径
    （前缀在 app.py 拼），不注入替身就永远进不到这个分支。
    """
    sel = tia.select(["apis/user_api.py"], CASES, path_reader=lambda _path: ("/api/v1/login",))

    assert sel.full is False
    assert sel.strategy == "files"
    assert sel.node_ids == (NODE_A,)
    assert sel.matched_files == ()
    assert sel.matched_paths == ("/api/v1/login",)
    assert sel.changed_files == ("apis/user_api.py",)
    assert sel.reason == "命中 0 个测试文件、1 条接口路径"


def test_placeholder_parameters_are_normalized_before_intersection() -> None:
    """第③级比较前要先归一化：`{material_code}` 与 `{code}` 指向同一个 operation。

    不归一化的话，改路由实现时用例明明引用了同一接口却匹配不上 → 静默漏选。
    """
    cases = (
        registry.CaseInfo(
            node_id="testcases/api/test_materials.py::test_get_material",
            markers=("api",),
            referenced_paths=("/api/v1/materials/{material_code}",),
            covers=(),
        ),
    )
    sel = tia.select(["apis/user_api.py"], cases, path_reader=lambda _path: ("/api/v1/materials/{code}",))

    assert sel.full is False
    assert sel.node_ids == ("testcases/api/test_materials.py::test_get_material",)
    assert sel.matched_paths == ("/api/v1/materials/{}",)


def test_paths_without_any_referencing_case_fall_back_to_full() -> None:
    """分支⑤：提取到了路径，但没有任何用例引用它（有代码没人测）→ 全量兜底。"""
    sel = tia.select(["apis/user_api.py"], CASES, path_reader=lambda _path: ("/api/v1/unknown",))

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.changed_files == ("apis/user_api.py",)
    assert sel.reason == "apis/user_api.py 提取到 1 条接口路径，但没有任何用例引用它们，退化为全量"


def test_real_source_module_without_path_literals_falls_back_to_full() -> None:
    """分支⑥：真实的 `apis/user_api.py` 里没有 `/api` 字面量 → 全量兜底。

    这里刻意用 `apis/`（同进程 import，行级判定真实可用）而不是 `mock_server/`：
    后者已被收进「必跑清单」，永远走全量，测不到这一分支。
    第一行是**前提断言**：它红掉说明「这个文件现在有路径字面量了」，该换一个纯业务模块
    来做场景，而不是放宽 `select` 的判据。
    """
    source = "apis/user_api.py"
    assert registry.read_referenced_paths(REPO_ROOT / source) == ()

    sel = tia.select([source], CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.reason == (
        "apis/user_api.py 未提取到 /api 路径字面量（公共模块/动态拼接/非源码），无法判定影响面，退化为全量"
    )


@pytest.mark.parametrize(
    "path",
    [
        "mock_server/routers.py",
        "mock_server/app.py",
        "mock_server/services.py",
        "login_app/app.py",
        "mes_web/app.py",
        "ui/pages/login_page.py",
    ],
)
def test_subprocess_served_code_always_falls_back_to_full(path: str) -> None:
    """被测服务由子进程拉起，coverage context 在 spawn 那一刻固定 → 行级判定会静默漏掉绝大多数用例。

    实测 `mock_server/routers.py` 的 444 行只关联 **10 个用例**（uvicorn 子进程里执行的行
    全部算到「启动它的那条用例」头上），而实际有 423 条用例在打接口。所以这些目录必须留在
    `ALWAYS_FULL_PREFIXES`：哪怕索引里「看起来」有精确的行数据，那正是陷阱。
    """
    sel = tia.select_by_lines([tia.ChangedHunk(path, (10,))], {}, CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert "必跑清单" in sel.reason


@pytest.mark.parametrize("changed", [(), ("",), ("   ",), ("./",)])
def test_no_changed_files_falls_back_to_full(changed: tuple[str, ...]) -> None:
    """分支⑦：变更集为空（含归一化后变空的写法）→ 全量，绝不返回「什么都不用跑」。"""
    sel = tia.select(changed, CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.changed_files == ()
    assert sel.total_cases == 3
    assert sel.reason == "没有变更文件，无法判定影响面，按全量兜底"


def test_v1_test_file_without_collected_cases_falls_back_to_full() -> None:
    """第②级的失败侧：在 testcases/ 下但一条用例都没有（已删除/改名）→ 全量。

    若这里静默返回空选择，删掉一个测试文件就等于「这次不用跑任何用例」。
    """
    sel = tia.select(["testcases/api/test_deleted.py"], CASES, path_reader=_boom)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert (
        sel.reason
        == "testcases/api/test_deleted.py 在 testcases/ 下但没有任何已收集用例（可能已删除或改名），退化为全量"
    )


def test_backslash_and_dot_slash_paths_are_normalized() -> None:
    """路径归一化：Windows 反斜杠、`./` 前缀、仓库绝对路径都要落到同一个仓库相对写法。

    三者只要有一种没对上，`testcases/` 下的变更就会被当成「其他源码」去读字面量，
    TIA 收益最大的一档直接失效。
    """
    mixed = tia.select(
        ["testcases\\api\\test_login_contract.py", "./testcases/api/test_login_contract.py"],
        CASES,
        path_reader=_boom,
    )
    assert mixed.changed_files == (TEST_FILE,)
    assert mixed.node_ids == (NODE_A, NODE_B)

    absolute = tia.select([str(REPO_ROOT / "testcases" / "api" / "test_login_contract.py")], CASES, path_reader=_boom)
    assert absolute.changed_files == (TEST_FILE,)
    assert absolute.node_ids == (NODE_A, NODE_B)


def test_selection_ratio_handles_zero_total_cases() -> None:
    """分支⑧：`total_cases == 0` 时 ratio 返回 0.0，不抛 ZeroDivisionError（也覆盖负数）。"""
    empty = tia.Selection(
        node_ids=(),
        full=True,
        reason="用例库为空",
        changed_files=(),
        total_cases=0,
        matched_files=(),
        matched_paths=(),
    )
    assert empty.ratio == 0.0

    negative = tia.Selection(
        node_ids=(NODE_A,),
        full=False,
        reason="用例库计数异常",
        changed_files=(TEST_FILE,),
        total_cases=-3,
        matched_files=(TEST_FILE,),
        matched_paths=(),
    )
    assert negative.ratio == 0.0

    quarter = tia.Selection(
        node_ids=(NODE_A, NODE_B),
        full=False,
        reason="正常情况",
        changed_files=(TEST_FILE,),
        total_cases=8,
        matched_files=(TEST_FILE,),
        matched_paths=(),
    )
    assert quarter.ratio == 0.25


def test_render_json_matches_the_documented_contract() -> None:
    """分支⑨：`render_json` 的键集合与取值是 CI 解析契约（§4.1），改名/漏键都算破坏契约。"""
    contract_keys = {
        "full",
        "strategy",
        "reason",
        "total_cases",
        "selected",
        # 纯增量字段：老字段一个没动，`effective_count` 只回答「这一轮实际要跑多少条」，
        # 免得上游 CI 把全量时的 `selected: 0` 读成「无事可做」。
        "effective_count",
        "ratio",
        "changed_files",
        "matched_files",
        "matched_paths",
        "node_ids",
    }

    partial = json.loads(tia.render_json(tia.select([TEST_FILE], CASES, path_reader=_boom)))
    assert set(partial) == contract_keys
    assert partial["full"] is False
    assert partial["strategy"] == "files"
    assert partial["total_cases"] == 3
    assert partial["selected"] == 2
    assert partial["effective_count"] == 2  # 部分选择时与 selected 一致
    assert partial["ratio"] == 0.6667
    assert partial["node_ids"] == [NODE_A, NODE_B]
    assert partial["changed_files"] == [TEST_FILE]
    assert partial["matched_files"] == [TEST_FILE]
    assert partial["matched_paths"] == []

    whole = json.loads(tia.render_json(tia.select([], CASES)))
    assert set(whole) == contract_keys
    assert whole["full"] is True
    assert whole["strategy"] == "full"
    assert whole["selected"] == 0
    # 关键差异：全量时 selected 是 0（执行集不是清单），但实际要跑的是全部 3 条。
    assert whole["effective_count"] == 3
    assert whole["ratio"] == 0.0
    assert whole["node_ids"] == []
    assert whole["changed_files"] == []


def test_node_ids_are_sorted_regardless_of_case_input_order() -> None:
    """分支⑩：`cases` 的输入顺序不得影响输出——nodeid 必须字典序，否则报表与 diff 抖动。"""
    forward = tia.select([TEST_FILE], CASES, path_reader=_boom)
    reversed_cases = tia.select([TEST_FILE], list(reversed(CASES)), path_reader=_boom)
    shuffled = tia.select([TEST_FILE], [CASES[1], CASES[2], CASES[0]], path_reader=_boom)

    assert forward.node_ids == (NODE_A, NODE_B)
    assert reversed_cases.node_ids == forward.node_ids
    assert shuffled.node_ids == forward.node_ids


def test_new_lines_from_diff_parses_unified_zero_context_hunks() -> None:
    """`git diff -U0` 的 hunk 头解析：省略行数按 1 算，纯删除取删除位置的相邻两行作锚点。

    纯删除（``d == 0``）在新文件里没有对应行，但删除同样会打破依赖它的用例。
    只收新增行会让「同一文件里删一处、又在别处加几行」丢掉删除侧的影响，
    所以这里退一步取 ``c-1`` 与 ``c``：宁可多选几条，也不静默漏选。
    """
    patch = (
        "@@ -10,0 +11,2 @@ def create_order():\n"
        "+    total = 0\n"
        "+    return total\n"
        "@@ -20 +25 @@ def other():\n"
        "+    pass\n"
        "@@ -30,3 +31,0 @@ def removed():\n"
        "-    gone\n"
    )

    # 11/12 新增；25 是省略行数时的 +25；30/31 是纯删除 hunk 的锚点（+31 → 30 与 31）
    assert tia._new_lines_from_diff(patch) == {11, 12, 25, 30, 31}


def test_pure_deletion_elsewhere_does_not_erase_the_whole_file_from_selection() -> None:
    """同文件里「删一处、加另一处」时，删除侧不能因为「没有新行号」而整段消失。

    这是行级策略最容易静默漏选的一种改动：只收 ``+`` 行，删除就不与任何用例关联了。
    回归保护对应 `qaplat/tia.py` 的 `_new_lines_from_diff`——纯删除 hunk 会取
    ``c-1`` 与 ``c`` 作锚点。
    """
    patch = "@@ -10,3 +10,0 @@\n-a\n-b\n-c\n@@ -30,0 +28,1 @@\n+z\n"

    got = tia._new_lines_from_diff(patch)

    assert 28 in got, "新增行必须在集合里"
    assert got & {9, 10}, f"删除位置（+10 附近）必须留下锚点，实际 {sorted(got)}"


def test_git_failure_raises_instead_of_returning_an_empty_change_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """git 读不到变更时必须抛错：返回空元组会被上层读成「本次没有变更，一条用例都不用跑」。

    这是 TIA 最危险的失效模式（git 不在 PATH / base 写错 / 不在仓库里 / 权限问题），所以
    断言不只是「抛了异常」，而是异常里必须带命令、cwd、退出码与两路输出的尾部——否则排障
    时要靠猜。替身换掉 `subprocess.run`，不依赖本机真的处于某个 git 状态。
    """
    fake = subprocess.CompletedProcess(args=["git"], returncode=128, stdout="", stderr="fatal: not a git repository\n")
    monkeypatch.setattr(tia.subprocess, "run", lambda *_args, **_kwargs: fake)

    with pytest.raises(RuntimeError) as excinfo:
        tia.changed_files(cwd=tmp_path)

    message = str(excinfo.value)
    assert "git 读取变更失败" in message
    assert "returncode: 128" in message
    assert "fatal: not a git repository" in message
    assert str(tmp_path) in message
    assert "（空）" in message  # stdout 为空时的显式占位，避免异常里出现一段看不懂的空白


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (FileNotFoundError(2, "No such file or directory"), "无法执行 git"),
        (subprocess.TimeoutExpired(cmd="git", timeout=120), "git 超时"),
    ],
)
def test_git_execution_problems_raise_actionable_errors(
    exc: Exception,
    needle: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """「执行不了 git」与「git 跑超时」也要变成带上下文的 RuntimeError，而不是静默空变更。"""

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(tia.subprocess, "run", _raise)

    with pytest.raises(RuntimeError, match=needle):
        tia.changed_files(cwd=tmp_path)


def test_render_text_declares_the_known_limits() -> None:
    """文本输出必须显式声明已知限制（§3.2），否则有人会把文件级粒度读成「万无一失」。"""
    partial = tia.render_text(tia.select([TEST_FILE], CASES, path_reader=_boom))
    assert "模式: 部分选择（files 策略）" in partial
    assert "选中: 2 / 3 条用例（66.67%）" in partial
    assert f"  - {NODE_A}" in partial
    assert "已知限制: 影响面按文件/行粒度判定" in partial

    whole = tia.render_text(tia.select([], CASES))
    assert "模式: 全量兜底（full）" in whole
    # 全量兜底时 node_ids 恒为空，但「选中 0 条（0.00%）」会与「全量」自相矛盾，
    # 也会让 CI 把 selected=0 读成「无事可做」——文本必须说清将要跑全量。
    assert "选中: 全量 3 条用例" in whole
    assert "0.00%" not in whole
    assert "用例:" not in whole


# ---------------------------------------------------------------------------
# v2：行级覆盖映射
# ---------------------------------------------------------------------------


def test_load_context_index_normalizes_paths_and_strips_stage_suffix(tmp_path: Path) -> None:
    """分支⑪：`.coverage` → 「仓库相对路径 → {行: (nodeid,)}」，后缀剥离、路径归一化。

    三种 context 形态一起写：`|run`（用例体）、`|setup`（夹具执行到该行，同样算命中）、
    以及第二个文件上的 `|run`。断言的是索引的**形状**，不是它有多少条。
    """
    root = tmp_path / "clone"
    (root / "mock_server").mkdir(parents=True)
    (root / "qaplat").mkdir()
    data = _build_coverage(
        tmp_path / ".coverage",
        root,
        [
            (f"{TEST_FILE}::test_lc01_login_success|run", {"apis/user_api.py": {10, 11}}),
            (f"{TEST_FILE}::test_lc01_login_success|setup", {"apis/user_api.py": {3}}),
            ("testcases/mes/test_order_flow.py::test_create|run", {"qaplat/gate.py": {7}}),
        ],
    )

    index = tia.load_context_index(data, root=root)

    assert sorted(index) == ["apis/user_api.py", "qaplat/gate.py"]
    services = index["apis/user_api.py"]
    assert sorted(services) == [3, 10, 11]
    assert services[10] == (f"{TEST_FILE}::test_lc01_login_success",)
    assert services[3] == (f"{TEST_FILE}::test_lc01_login_success",)
    assert index["qaplat/gate.py"] == {7: ("testcases/mes/test_order_flow.py::test_create",)}


def test_context_suffix_pipeline_keeps_parameterized_ids_and_drops_dynamic_contexts(tmp_path: Path) -> None:
    """`|` 只在后缀确实属于 run/setup/teardown 时才切，其余 context 一律丢弃。

    * `test_pair[a|b]|run`：参数化 id 自带 `|`，无条件 `split("|")[0]` 会切坏它；
    * `test_pair[a|b]`（裸 nodeid）：没有阶段后缀也认，别把独有用例的归因搞丢；
    * `zeta`（`--cov-context=dynamic` 的裸标签）：不是用例，留着只会污染命中集合；
    * `|run`（只有阶段没有 nodeid）：head 为空，丢弃。
    """
    root = tmp_path / "clone"
    root.mkdir()
    data = _build_coverage(
        tmp_path / ".coverage",
        root,
        [
            ("testcases/api/test_param.py::test_pair[a|b]|run", {"apis/user_api.py": {5}}),
            ("testcases/api/test_param.py::test_pair[a|b]", {"apis/user_api.py": {7}}),
            ("zeta", {"apis/user_api.py": {6}}),
            ("|run", {"apis/user_api.py": {8}}),
        ],
    )

    per_line = tia.load_context_index(data, root=root)["apis/user_api.py"]

    assert per_line[5] == ("testcases/api/test_param.py::test_pair[a|b]",)
    assert per_line[7] == ("testcases/api/test_param.py::test_pair[a|b]",)
    assert 6 not in per_line
    assert 8 not in per_line


def test_files_outside_root_are_skipped_not_fatal(tmp_path: Path) -> None:
    """仓库外的文件（site-packages / 别的仓）跳过即可，不该让整份索引读不出来。"""
    root = tmp_path / "clone"
    root.mkdir()
    inside = root / "apis" / "user_api.py"
    outside = tmp_path / "elsewhere" / "lib.py"
    outside.parent.mkdir()
    data = CoverageData(str(tmp_path / ".coverage"))
    data.set_context("testcases/api/test_a.py::test_a|run")
    data.add_lines({str(inside): {3}, str(outside): {4}})
    data.write()

    assert sorted(tia.load_context_index(tmp_path / ".coverage", root=root)) == ["apis/user_api.py"]


def test_doc_only_change_selects_nothing_instead_of_everything() -> None:
    """纯文档变更（.md）不该触发全量：没有用例读它们，为一份 README 跑满一整层是纯浪费。

    这条守护 `_is_doc_only` + `strategy="docs"` 这一档。README / AGENTS / docs/*.md 的
    日常改动占 PR 相当大比例，如果每次都被判全量，TIA 在日常使用中等于不存在。
    传入空索引即可验证：文档变更在查索引之前就分流了。
    """
    sel = tia.select_by_lines([tia.ChangedHunk("README.md", (5,))], {}, CASES)

    assert sel.full is False
    assert sel.strategy == "docs"
    assert sel.node_ids == ()
    assert sel.total_cases == 3
    assert "仅文档变更" in sel.reason


def test_mixed_doc_and_code_change_only_judges_the_code_part() -> None:
    """代码 + 文档混合变更：文档不参与判定，也不能因为「不在索引里」而触发全量。"""
    hunks = [tia.ChangedHunk("README.md", (5,)), tia.ChangedHunk(TEST_FILE, (1,))]

    sel = tia.select_by_lines(hunks, {}, CASES)

    assert sel.full is False
    assert sel.strategy == "lines"  # 行级入口恒为 lines；文件级分流是 select() 的职责
    assert sel.node_ids == (NODE_A, NODE_B)
    assert "README.md" in sel.changed_files  # 报告里仍保留完整变更集


def test_openapi_json_is_a_contract_not_documentation() -> None:
    """docs/openapi.json 是契约而不是文档：它必须继续全量，不能被文档白名单吞掉。"""
    sel = tia.select_by_lines([tia.ChangedHunk("docs/openapi.json", (12,))], {}, CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert "必跑清单" in sel.reason


def test_v1_select_also_skips_doc_only_changes() -> None:
    """v1（文件级）也要有 docs 分流，否则改一份 README 会命中一堆假路径。

    修复前 `select()` 会去 README 正文里读 `/api` 字面量：实测 `README.md` 因此命中
    **208 条**用例、`CHANGELOG.md` 命中 56 条。这里用替身 reader 把「假路径」放大，
    确保分流发生在读文件**之前**——reader 一次都不该被调用。
    """
    calls: list[str] = []

    def reader(path: str) -> tuple[str, ...]:
        calls.append(path)
        return ("/api/login",)

    sel = tia.select(["README.md", "docs/tia-plan.md"], CASES, path_reader=reader)

    assert sel.full is False
    assert sel.strategy == "docs"
    assert sel.node_ids == ()
    assert calls == []  # 关键：没有对文档做路径提取
    assert "仅文档变更" in sel.reason


def test_requirements_txt_is_not_swallowed_by_the_doc_whitelist() -> None:
    """`requirements.txt` 也是 `.txt`，但它改的是所有用例的运行环境 → 必须全量。

    这是 `_is_doc_only` 里「必跑清单优先」那条顺序的回归保护：把两者顺序调换，
    `requirements.txt` 就会被当成文档跳过，改依赖将不再触发任何测试。
    """
    sel = tia.select(["requirements.txt"], CASES, path_reader=lambda path: ())

    assert sel.full is True
    assert sel.strategy == "full"
    assert "必跑清单" in sel.reason


def test_module_level_line_forces_full(tmp_path: Path) -> None:
    """分支⑫：变更行带空 context（import 期执行）→ 全量，reason 点名「模块级代码」。

    这类行不归属任何用例，但它一改，所有 import 该模块的用例的运行条件都变了。
    从真实的 `.coverage` 一路读到 `select_by_lines`，验的是整条链。
    """
    root = tmp_path / "clone"
    root.mkdir()
    data = _build_coverage(tmp_path / ".coverage", root, [(None, {"apis/user_api.py": {1, 2}})])
    index = tia.load_context_index(data, root=root)

    assert index["apis/user_api.py"][1] == (tia._MODULE_LEVEL,)

    sel = tia.select_by_lines([tia.ChangedHunk("apis/user_api.py", (2,))], index, CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.changed_files == ("apis/user_api.py",)
    assert "第 2 行属于模块级代码" in sel.reason
    assert "import 期执行、不归属任何用例" in sel.reason


def test_executed_lines_select_their_cases_in_lines_strategy() -> None:
    """分支⑬：变更行被某用例执行过 → full=False、strategy="lines"、node_ids 恰为索引里那些。

    索引里还塞了一条「本次没收集到」的 nodeid（用例已被删除）：它必须被过滤掉，
    否则 CLI 会把一个 pytest 认不出的 nodeid 拼进命令行，整层直接报 usage error。
    """
    index = {
        "apis/user_api.py": {
            10: (NODE_A,),
            11: (NODE_B,),
            12: ("testcases/api/test_removed.py::test_gone",),
        }
    }

    sel = tia.select_by_lines([tia.ChangedHunk("apis/user_api.py", (10, 11, 12))], index, CASES)

    assert sel.full is False
    assert sel.strategy == "lines"
    assert sel.node_ids == (NODE_A, NODE_B)
    assert sel.matched_files == (TEST_FILE,)
    assert sel.matched_paths == ()
    assert sel.changed_files == ("apis/user_api.py",)
    assert sel.reason == "命中 2 条用例（分布在 1 个测试文件、覆盖 3 条变更行）"


@pytest.mark.parametrize(
    ("index", "needle"),
    [
        ({"apis/user_api.py": {10: (NODE_A,)}}, "没有被任何已收集用例执行过"),
        ({}, "不在覆盖索引里"),
        (
            {"apis/user_api.py": {10: ("testcases/api/test_removed.py::test_gone",)}},
            "没有被任何已收集用例执行过",
        ),
    ],
)
def test_unexecuted_changed_lines_fall_back_to_full(
    index: dict[str, dict[int, tuple[str, ...]]],
    needle: str,
) -> None:
    """分支⑭：变更行没人执行过 / 文件不在索引里 / 只有已删除用例命中 → 三种都全量兜底。

    这是行级策略最危险的失效面：把「没有任何用例执行过这一行」读成「这一行不重要」，
    就会漏掉「改了控制流、让原本不执行的代码开始执行」这类回归。
    """
    sel = tia.select_by_lines([tia.ChangedHunk("apis/user_api.py", (99,))], index, CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert needle in sel.reason


def test_hunk_without_target_lines_falls_back_to_full() -> None:
    """分支⑮：`lines=()` 是「文件变了但拿不到目标行」（纯删除/二进制/重命名/未跟踪）→ 全量。"""
    sel = tia.select_by_lines(
        [tia.ChangedHunk("apis/user_api.py", ())],
        {"apis/user_api.py": {10: (NODE_A,)}},
        CASES,
    )

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.reason == ("apis/user_api.py 没有可用的新增行（纯删除/二进制/重命名/未跟踪），影响面未知，退化为全量")


def test_testcase_hunk_selects_its_cases_without_touching_coverage() -> None:
    """分支⑯：行级命中 `testcases/` 下的文件 → 选该文件全部用例，不走覆盖索引。

    索引传空字典就是这条的证明：如果实现去查 coverage，空索引会走成「不在覆盖索引里」的全量。
    """
    sel = tia.select_by_lines([tia.ChangedHunk(TEST_FILE, (12,))], {}, CASES)

    assert sel.full is False
    assert sel.strategy == "lines"
    assert sel.node_ids == (NODE_A, NODE_B)
    assert sel.matched_files == (TEST_FILE,)
    assert sel.matched_paths == ()
    assert sel.reason == "命中 2 条用例（分布在 1 个测试文件、覆盖 0 条变更行）"


@pytest.mark.parametrize(
    "hunks",
    [
        [],
        [tia.ChangedHunk("", (5,))],
        [tia.ChangedHunk("./", (5,))],
    ],
)
def test_no_hunks_falls_back_to_full(hunks: list[tia.ChangedHunk]) -> None:
    """分支⑮的另一半：hunk 列表为空/路径归一化后为空 → 「没有可用的变更行」→ 全量。"""
    sel = tia.select_by_lines(hunks, {}, CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.changed_files == ()
    assert sel.total_cases == 3
    assert sel.reason == "没有可用的变更行（diff 为空），无法判定影响面，按全量兜底"


def test_qaplat_source_is_line_level_in_v2_but_full_in_v1() -> None:
    """v2 与 v1 唯一的刻意差异：`qaplat/` 在行级策略里不再判全量。

    平台层恰好是 coverage 覆盖最完整的部分；继续判全量等于自废 v2（改一行报表就跑全仓）。
    同一个变更文件在两档下必须给出不同的 `strategy`——这条用例就是那个契约。
    """
    hunk = tia.ChangedHunk("qaplat/gate.py", (7,))
    index = {"qaplat/gate.py": {7: (NODE_C,)}}

    by_lines = tia.select_by_lines([hunk], index, CASES)
    assert by_lines.full is False
    assert by_lines.strategy == "lines"
    assert by_lines.node_ids == (NODE_C,)
    assert by_lines.reason == "命中 1 条用例（分布在 1 个测试文件、覆盖 1 条变更行）"

    by_files = tia.select(["qaplat/gate.py"], CASES, path_reader=_boom)
    assert by_files.full is True
    assert by_files.strategy == "full"


@pytest.mark.parametrize("path", ["data/x.json", "pytest.ini", "testcases/login/conftest.py"])
def test_v2_still_treats_config_and_data_as_full(path: str) -> None:
    """v2 只放开了 `qaplat/`：数据源、配置、任意层级 conftest 依旧是全量。

    这三类改变的是所有用例的输入/运行条件，行级映射解释不了它们的影响面。
    """
    sel = tia.select_by_lines([tia.ChangedHunk(path, (3,))], {path: {3: (NODE_A,)}}, CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert sel.changed_files == (path,)
    assert path in sel.reason
    assert "关键文件" in sel.reason


def test_v2_test_file_without_collected_cases_falls_back_to_full() -> None:
    """v2 第②级的失败侧：testcases/ 下但没有任何已收集用例 → 全量（与 v1 同一条判据）。"""
    sel = tia.select_by_lines([tia.ChangedHunk("testcases/api/test_deleted.py", (1,))], {}, CASES)

    assert sel.full is True
    assert sel.strategy == "full"
    assert sel.node_ids == ()
    assert (
        sel.reason
        == "testcases/api/test_deleted.py 在 testcases/ 下但没有任何已收集用例（可能已删除或改名），退化为全量"
    )


def test_missing_coverage_file_raises_even_if_it_looks_safe(tmp_path: Path) -> None:
    """数据缺失必须抛错：返回空索引会把「忘了 `--cov-context=test`」伪装成「都安全」。"""
    with pytest.raises(RuntimeError, match="coverage 数据文件不存在或不是文件"):
        tia.load_context_index(tmp_path / "no-such.coverage", root=tmp_path)


def test_coverage_without_case_contexts_raises(tmp_path: Path) -> None:
    """有数据但一条「用例 context」都没有（例如用 dynamic 采集）→ 抛错而不是空字典。"""
    root = tmp_path / "clone"
    root.mkdir()
    data = _build_coverage(tmp_path / ".coverage", root, [("zeta", {"apis/user_api.py": {5}})])

    with pytest.raises(RuntimeError, match="没有可用的「用例 context」"):
        tia.load_context_index(data, root=root)


def test_real_coverage_tia_index_has_the_expected_shape() -> None:
    """集成：仓库根真实的 `.tia-coverage` 必须能读成一份结构合理的行级索引。

    文件不存在就 skip：它由采集产出、不进 git，平台层单测不该因为「本机没采过」变红。

    这里**刻意不断言精确文件数**：索引规模随「仓库里有多少被执行的模块」变化，每新增一个
    被覆盖的源文件就会 +1（实测从 117 涨到 119，只因为多了一个 test_tia.py），写死数字
    等于让一条本该验「读取链路通不通」的用例变成「采集快照比对」，下次采集必红。
    真正要守住的是结构：规模下界、关键文件在、行号是 int、模块级哨兵存在、nodeid 干净。
    """
    if not REAL_COVERAGE.is_file():
        pytest.skip(f"未采集到行级覆盖数据：{REAL_COVERAGE}")

    index = tia.load_context_index(REAL_COVERAGE)

    # 下界而非等值：这份索引覆盖 qaplat/ + mock_server/ + testcases/ 的主要模块，
    # 2026-10 实测 119；低于 100 说明读取链路大面积失效，值得红。
    assert len(index) >= 100
    assert "qaplat/registry.py" in index
    assert "apis/user_api.py" in index
    registry_lines = index["qaplat/registry.py"]
    assert all(isinstance(line, int) for line in registry_lines)
    assert any(tia._MODULE_LEVEL in node_ids for node_ids in registry_lines.values())

    every_node = {node_id for per_line in index.values() for node_ids in per_line.values() for node_id in node_ids}
    assert tia._MODULE_LEVEL in every_node
    assert all(node_id == "" or "::" in node_id for node_id in every_node)
    assert all(not any(stage in node_id for stage in ("|run", "|setup", "|teardown")) for node_id in every_node)


# --------------------------------------------------------------------------- 索引新鲜度
#
# 这一组守的是「静默少跑」：索引比 HEAD 旧时，新增的测试文件在索引里没有数据，
# 选择器会安静地少选几条——不报错、不提示。漏检实验里旧索引下漏检 3/7 就是这条路径。
# 所以这里要钉死：记录 HEAD 能往返、坏文件不炸、陈旧必须能被判成 True。


def test_head_marker_path_appends_suffix_without_moving_directory(tmp_path: Path) -> None:
    """标记文件与索引同目录、名字加后缀；不改目录也不改扩展名语义。"""
    marker = tia.head_marker_path(tmp_path / ".tia-coverage")

    assert marker == tmp_path / ".tia-coverage.head"
    assert marker.parent == tmp_path


def test_write_then_read_index_head_round_trips(tmp_path: Path) -> None:
    """写进去的 HEAD 必须原样读得回来，并且真的等于当前 git HEAD。"""
    data = tmp_path / ".tia-coverage"
    data.write_bytes(b"fake-sqlite-placeholder")

    marker = tia.write_index_head(data, cwd=REPO_ROOT)

    assert marker.is_file()
    payload = tia.read_index_head(data)
    assert payload is not None
    assert (
        payload["head"]
        == subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    assert payload["coverage_file"] == ".tia-coverage"
    assert payload["captured_at"].endswith("+00:00")


def test_read_index_head_returns_none_when_marker_is_absent(tmp_path: Path) -> None:
    """没有标记文件 = 新鲜度未知，不是错误：必须返回 None 而不是抛。"""
    assert tia.read_index_head(tmp_path / ".tia-coverage") is None


def test_read_index_head_returns_none_on_corrupt_marker(tmp_path: Path) -> None:
    """标记文件被写坏时返回 None：新鲜度判断可以失效，但它不能把选择流程带崩。"""
    data = tmp_path / ".tia-coverage"
    data.write_bytes(b"x")
    tia.head_marker_path(data).write_text("{not json", encoding="utf-8")

    assert tia.read_index_head(data) is None


def test_staleness_reports_unknown_when_marker_is_absent(tmp_path: Path) -> None:
    """无标记时返回 (False, 说明)——「不知道」不阻止使用，但要说清不知道什么。"""
    stale, message = tia.index_staleness(tmp_path / ".tia-coverage", cwd=REPO_ROOT)

    assert stale is False
    assert "未记录采集时的 HEAD" in message


def test_staleness_reports_synced_when_head_matches(tmp_path: Path) -> None:
    """刚 capture 完再查，必须是「同步」而不是「落后」。"""
    data = tmp_path / ".tia-coverage"
    data.write_bytes(b"x")
    tia.write_index_head(data, cwd=REPO_ROOT)

    stale, message = tia.index_staleness(data, cwd=REPO_ROOT)

    assert stale is False
    assert "同步" in message


def test_staleness_detects_a_commit_the_index_predates(tmp_path: Path) -> None:
    """标记里的 HEAD 与当前不同时判 True，并给出「落后」说明。

    这里用一个**不可能存在的** commit id：目的不是算准「落后几个提交」，而是证明
    「只要两个 HEAD 不相等就一定判陈旧」——相比「恰好等于 HEAD 才算新鲜」，
    这个方向宁可多报一次过期，也不要放过一次真过期。
    """
    data = tmp_path / ".tia-coverage"
    data.write_bytes(b"x")
    stale, _ = tia.index_staleness(data, cwd=REPO_ROOT)
    assert stale is False  # 无标记

    marker = tia.head_marker_path(data)
    marker.write_text(json.dumps({"head": "0" * 40}), encoding="utf-8")

    stale, message = tia.index_staleness(data, cwd=REPO_ROOT)

    assert stale is True
    # 这个 commit id 根本不存在，`rev-list --count` 会失败 → 走「算不出相差几个」分支。
    # 断言只钉「说了它不同 + 建议重采」，不钉具体措辞。
    assert ("不在同一条历史线" in message) or ("比当前 HEAD 新" in message)
    assert "建议重采" in message


def _fake_git(monkeypatch: pytest.MonkeyPatch, behind: str) -> None:
    """把 `rev-parse` 固定成 b*40、`rev-list --count` 固定成给定值。"""
    real = tia._run_git

    def fake(args: Sequence[str], cwd: Path, **kwargs: object) -> str:
        if args and args[0] == "rev-parse":
            return "b" * 40
        if args and args[0] == "rev-list":
            return behind
        return real(args, cwd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tia, "_run_git", fake)


def test_staleness_reports_how_many_commits_are_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """能算出相差几个提交时，文案要给出那个数字——它直接回答「该不该先重采」。"""
    data = tmp_path / ".tia-coverage"
    data.write_bytes(b"x")
    tia.head_marker_path(data).write_text(json.dumps({"head": "a" * 40}), encoding="utf-8")
    _fake_git(monkeypatch, "3")

    stale, message = tia.index_staleness(data, cwd=tmp_path)

    assert stale is True
    assert "中间有 3 个提交未被覆盖" in message


def test_staleness_does_not_claim_being_behind_when_index_is_ahead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """索引比 HEAD 新（切到旧分支）时，「落后 0 个提交」是句假话。

    此时 `captured..current` 的计数是 0，但索引既不旧、也不是「落后 0」——
    它在另一条历史线上，行号可能整体错位。文案必须说清这件事。
    """
    data = tmp_path / ".tia-coverage"
    data.write_bytes(b"x")
    tia.head_marker_path(data).write_text(json.dumps({"head": "a" * 40}), encoding="utf-8")
    _fake_git(monkeypatch, "0")

    stale, message = tia.index_staleness(data, cwd=tmp_path)

    assert stale is True
    assert "比当前 HEAD 新" in message
    assert "落后 0" not in message


def test_capture_command_writes_marker_and_refuses_missing_data(tmp_path: Path) -> None:
    """``tia capture``：数据在就写标记并返回 0，不在就返回退出码 2（绝不静默通过）。"""
    from qaplat import cli

    missing = tmp_path / ".tia-coverage"
    assert cli.cmd_tia_capture(_capture_args(missing)) == cli.EXIT_USAGE

    missing.write_bytes(b"x")
    assert cli.cmd_tia_capture(_capture_args(missing)) == 0
    assert tia.head_marker_path(missing).is_file()


def _capture_args(data_path: Path) -> argparse.Namespace:
    """给 ``cmd_tia_capture`` 造一个最小 Namespace：它只读 ``coverage_data``。"""
    return argparse.Namespace(coverage_data=str(data_path))


def test_gate_skips_the_whole_layer_when_only_docs_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """仅文档变更时本层一条用例都不该跑，更不能让空 extra 漏下去把整层跑一遍。

    回归保护：``strategy="docs"`` 的 ``node_ids`` 是空的，若直接拼进 ``extra``，
    pytest 会收集整层——那正是「改一份 README 却跑满 697 条」的老问题。
    """
    from qaplat import cli

    docs_sel = tia.Selection(
        node_ids=(),
        full=False,
        reason="仅文档变更（1 个文件），不影响任何用例",
        changed_files=("README.md",),
        total_cases=100,
        matched_files=(),
        matched_paths=(),
        strategy="docs",
    )
    monkeypatch.setattr(tia, "select_from_git", lambda **_kw: docs_sel)
    called: list[object] = []
    monkeypatch.setattr(cli.gate, "run_layer", lambda *a, **k: called.append(a))

    code = cli.main(["--db", str(tmp_path / "m.db"), "gate", "--layer", "smoke", "--select", "tia"])

    assert code == cli.EXIT_OK
    assert called == []  # 一层都没跑
    assert "本层没有受影响的用例" in capsys.readouterr().out
