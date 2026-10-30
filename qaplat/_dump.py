"""pytest 侧的采集插件（由 ``qaplat gate`` / ``registry build`` 以 ``-p qaplat._dump`` 加载）。

一次子进程同时拿到三样东西，避免为了凑数据把同一套用例跑三遍：

1. **用例级结论**（``cases``）——``--collect-only`` 时不产生；
2. **每个 nodeid 的 markers**（``markers``）；
3. **每个 nodeid 的源文件**（``files``）——registry 靠它读源码里的接口路径字符串。

产物是一个 JSON 文件，路径由 ``--qaplat-dump`` 指定。

xdist 限制：``-n`` 时用例级结论仍由 controller 汇总（``pytest_runtest_logreport`` 会在
controller 触发），但 markers/files 只在 worker 里可枚举，controller 侧为空。平台的四层
门禁刻意不用 ``-n``（层内耗时本来就短，且用例级结果要完整），因此这条限制不影响门禁；
真要用 ``-n`` 跑 registry 需自行补 marker 来源。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_STATE: dict[str, Any] = {
    "dump": None,
    "records": {},
    "markers": {},
    "files": {},
    "covers": {},
    "renamed": {},
}


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("qaplat")
    group.addoption(
        "--qaplat-dump",
        action="store",
        default=None,
        metavar="PATH",
        help="把本轮采集到的用例级结果 / markers / 源文件写成 JSON（供 qaplat 读取）",
    )


def pytest_configure(config: Any) -> None:
    _STATE["dump"] = config.getoption("--qaplat-dump", None)
    _STATE["records"] = {}
    _STATE["markers"] = {}
    _STATE["files"] = {}
    _STATE["covers"] = {}
    _STATE["renamed"] = {}


def _qualified_nodeid(item: Any) -> str:
    """给跨盘目标补回 nodeid 的路径前缀。

    pytest 的 nodeid 是「相对 rootdir 的路径 + ``::名字``」。当要收集的文件**与 rootdir
    不在同一个盘符**时（Windows 上 ``tmp_path`` 在 C:、仓库在 D:），
    ``_pytest.pathlib.bestrelpath`` 的 ``os.path.commonpath`` 直接抛
    ``ValueError`` 落进兜底分支，算出**空串**——于是 nodeid 变成 ``::test_probe_a``，
    前面那半截没了。
    这不是「显示不好看」的问题：``registry.collect_cases`` 靠 nodeid 前缀找源文件
    （``node_id.split("::", 1)[0]``），前缀空掉就读不到源码，``referenced_paths``
    整条来源静默归零 —— 覆盖率报表上表现为「这条用例没覆盖任何接口」，
    而实际上它覆盖了。

    修法不是去改 pytest，而是**在采集期就把它补回来**：``--collect-only`` 时
    ``item.path`` 是绝对路径（``_dump.py`` 本来就在用它写 ``files``），
    所以前缀由这里给准。判据尽量窄：只有 nodeid 真的以 ``::`` 开头才动手，
    正常相对路径一律原样返回。
    """
    nodeid = item.nodeid
    if not nodeid.startswith("::"):
        return nodeid
    path = getattr(item, "path", None)
    if path is None:
        return nodeid
    return f"{path}{nodeid}"


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session: Any, config: Any, items: list[Any]) -> None:
    # ``trylast=True`` 不是可选的性能优化，是**正确性**要求：``-m`` 的 deselect 也在
    # ``pytest_collection_modifyitems`` 里做（``_pytest/mark/__init__.py``），而 ``-p``
    # 加载的插件注册得晚、默认**先**执行，于是这个 hook 看到的还是「deselect 之前」的
    # 全量 items —— 带 ``-m smoke`` 收集时 pytest 声称 ``36/689 tests collected``，
    # 这里却照样写下 689 条 marker。后果不只是 tia 的 ratio 分母偏大（689 而非 36），
    # 更危险的是 ``collect_cases(['-m', 'smoke'])`` 返回的清单里混着 smoke 层以外的
    # 用例，``gate --layer smoke --select tia`` 就可能把一条 ``-m api`` 的用例塞进
    # smoke 那轮的 pytest 命令行。加 trylast 之后 items 是 deselect 后的最终选择。
    for item in items:
        nodeid = _qualified_nodeid(item)
        if nodeid != item.nodeid:
            # 只在跨盘这种退化情形下留痕，方便排查用；正常收集时这个字典是空的。
            _STATE["renamed"][item.nodeid] = nodeid
        _STATE["markers"][nodeid] = sorted({marker.name for marker in item.iter_markers()})
        path = getattr(item, "path", None)
        if path is not None:
            _STATE["files"][nodeid] = str(path)
        # ``covers`` 的取值只能在收集期拿：marker 参数（``marker.args``）不进入任何
        # pytest 报告，事后无从恢复。用 marker 参数而不是正则扫源码，是为了让
        # ``covers = ("a", "b")`` 这类归并写法也能被正确读出。
        covers = [str(arg) for marker in item.iter_markers(name="covers") for arg in marker.args if arg]
        if covers:
            _STATE["covers"][nodeid] = sorted(set(covers))


def pytest_runtest_logreport(report: Any) -> None:
    """把 setup/call/teardown 三个阶段压成一条用例结论。

    优先级：setup 失败 → ``error``（没跑成，不是被测问题）；call 失败 → ``failed``；
    teardown 失败且前面没失败过 → ``error``；``wasxfail`` 的 skipped → ``xfailed``。

    失败时顺带留下 ``message``（``longreprtext`` 的前 2000 字符）。这是归因分类
    （``qaplat.classify``）唯一的证据来源——pytest 的报告文本不落盘就再也拿不回来，
    而事后重跑一轮只为拿到那句报错，代价是这个平台最不该浪费的东西：时间。
    """
    entry = _STATE["records"].setdefault(
        report.nodeid,
        {"nodeid": report.nodeid, "outcome": "passed", "duration_seconds": 0.0, "message": ""},
    )
    entry["duration_seconds"] += float(report.duration or 0.0)
    if report.when == "setup":
        if report.failed:
            entry["outcome"] = "error"
        elif report.skipped:
            entry["outcome"] = "skipped"
    elif report.when == "call":
        if report.failed:
            entry["outcome"] = "failed"
        elif report.skipped:
            entry["outcome"] = "xfailed" if getattr(report, "wasxfail", None) is not None else "skipped"
    elif report.when == "teardown" and report.failed and entry["outcome"] == "passed":
        entry["outcome"] = "error"
    if report.failed and not entry["message"]:
        entry["message"] = str(getattr(report, "longreprtext", "") or "")[:2000]


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    target = _STATE.get("dump")
    if not target:
        return
    config = session.config
    if hasattr(config, "workerinput"):
        # worker 不写文件，避免和 controller 抢同一个路径；结论由 controller 汇总。
        return
    payload = {
        "exitstatus": int(exitstatus),
        "cases": [
            {
                "nodeid": n,
                "outcome": r["outcome"],
                "duration_seconds": r["duration_seconds"],
                "message": r.get("message", ""),
            }
            for n, r in sorted(_STATE["records"].items())
        ],
        "markers": _STATE["markers"],
        "files": _STATE["files"],
        "covers": _STATE["covers"],
        # 跨盘退化时 nodeid 被补过前缀的痕迹（正常收集时为空）。留下它是为了让
        # 「为什么这个 nodeid 和 pytest 报的不一样」有据可查，而不是让人怀疑采集插件。
        "renamed": _STATE["renamed"],
    }
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
