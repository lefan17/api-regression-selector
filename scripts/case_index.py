#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""产出「用例编号 → pytest nodeid」的可机读索引。

用法::

    python scripts/case_index.py                 # 抽编号 + 收集 nodeid，写 reports/case-index.json
    python scripts/case_index.py --json          # 索引原文打到 stdout
    python scripts/case_index.py --static-only   # 不跑 pytest，nodeid 留空（仅有 base）
    python scripts/case_index.py --out other.json
    python scripts/case_index.py --verify        # 有编号却没解析出 nodeid 时以退出码 3 失败

产出 ``reports/case-index.json``；``reports/`` 已被 .gitignore 忽略，是产物，不入仓。

为什么要两个来源
----------------
编号只能从源码抽（docstring、``parametrize`` 的字符串字面量），nodeid 只有 pytest
说了算。用 AST 去*猜* nodeid 会在三类写法上猜错，本仓库三类都真实存在：

* ``@pytest.mark.parametrize("limit", [5, 10, 20], ids=lambda v: f"limit-{v}")``
  —— ``ids`` 是**可调用对象**，pytest 生成 ``limit-5``，静态猜只会得到 ``5``；
* 类内测试 ``class TestIdempotency:`` —— nodeid 多一层 ``::TestIdempotency``；
* ``@pytest.mark.parametrize(("op_id", "status"), UNDECLARED_BUT_REACHABLE, ...)``
  —— 参数值是**模块级名字**，AST 里根本不是字面量。

所以：编号由 AST 给，nodeid 由 ``pytest --collect-only`` 给，两者在
``(模块, 限定函数名)`` 上做 join。``--static-only`` 是离线降级档，此时 parametrize
用例的 nodeid 无法确定，索引只给 ``nodeid: null`` + ``nodeid_base``，绝不编造。

编号出现在两种位置（本仓库的核心难点）
--------------------------------------
1. 函数 docstring 的**行首**——``\"\"\"LC-01 正确用户名 + 正确密码 ...\"\"\"``；
2. ``parametrize`` 的**字符串参数值**——``({}, "LC-06 缺全部字段")``，
   LC-06~LC-13 这 8 条挤在同一个函数里。

刻意只认「行首/串首」的编号，行内其它编号只记为*引用*。原因：``FIND-01``~``FIND-06``、
``DEF-01``~``DEF-06`` 是**缺陷编号**而不是用例编号，它们只以 ``【FIND-04】`` 这种行内
形式出现在 docstring 里；若算作用例编号，索引会凭空多出 12 条不存在的"用例"。

退出码：0 正常；1 扫描目录不存在（``SystemExit`` 带消息时解释器按 1 退出）；3 ``--verify`` 时存在未解析的编号。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from ast import AsyncFunctionDef, FunctionDef
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "reports" / "case-index.json"
DEFAULT_TESTCASES = REPO_ROOT / "testcases"

# 编号的字面形状；「行首」语义由 _leading_ids 负责。
CASE_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d{1,4})\b")
# ``LC-06~LC-13``、``LC-06~13`` 两种区间写法都出现在 docs/login-test-cases.md。
RANGE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d{1,4})\s*[~～]\s*(?:([A-Z][A-Z0-9]{1,9})-)?(\d{1,4})\b")
# 编号之间的分隔符：「LC-01、LC-02」也算并列。
SEPARATOR_RE = re.compile(r"^[、,，/·\s]*$")
# 用例编号后面必须跟着正文；纯数据字面量（WH-01、MAT-CU-001）据此被挡掉。
DESCRIPTIVE_ID_RE = re.compile(r"^([A-Z][A-Z0-9]{1,9}-\d{1,4})\s+\S")
# 挡住 ``XX-01~9999`` 把索引撑爆。
MAX_RANGE_SPAN = 200

# 编号体系登记表。编号的*形状*分不出「用例」还是「缺陷」——``LC-01`` 与 ``DEF-03``
# 在语法上一模一样，区别只在意图。所以这里显式登记，不做猜测；未登记的前缀一律
# 保守地**不算**用例编号，并单独报告，避免索引被凭空撑大。
CASE_ID_SCHEMES = {
    "LC": "登录契约用例（docs/login-test-cases.md，30 行表格）",
}
NON_CASE_ID_SCHEMES = {
    "DEF": "契约缺陷（docs/wave1-contract-handoff.md「真实缺陷清单」）",
    "FIND": "登录缺陷（testcases/login/test_login_contract.py 内联标记）",
}


@dataclass
class CaseHit:
    """一处编号归属；``nodeid_token`` 用于在真实 nodeid 里定位它。"""

    case_id: str
    source: str  # "docstring" | "param"
    nodeid_token: str | None  # 出现在 nodeid 参数段里的判别串；None = 只能按函数定位


@dataclass
class TestFunc:
    module: str  # 仓库根相对 POSIX 路径
    qualname: str  # 类内测试形如 TestIdempotency.test_x
    name: str
    line: int
    suite: str
    has_params: bool = False
    hits: list[CaseHit] = field(default_factory=list)
    docstring_ids: list[str] = field(default_factory=list)
    inline_refs: list[tuple[list[str], str]] = field(default_factory=list)
    # 出现在行首、但前缀未登记为用例编号的编号（DEF-/FIND- 缺陷号等）。
    other_ids: list[str] = field(default_factory=list)


def _expand_range(prefix: str, start: str, end_prefix: str | None, end: str) -> list[str]:
    if end_prefix is not None and end_prefix != prefix:
        return []  # 跨前缀不是区间，宁可漏也不编。
    lo, hi = int(start), int(end)
    if hi < lo or hi - lo >= MAX_RANGE_SPAN:
        return []
    width = max(len(start), len(end))
    return [f"{prefix}-{n:0{width}d}" for n in range(lo, hi + 1)]


def _id_spans(text: str) -> list[tuple[int, int, list[str]]]:
    """按出现顺序给出 (start, end, ids)；区间先吃掉自身跨度，免得被拆成两个单点。"""
    out: list[tuple[int, int, list[str]]] = []
    consumed: list[tuple[int, int]] = []
    for m in RANGE_RE.finditer(text):
        ids = _expand_range(m.group(1), m.group(2), m.group(3), m.group(4))
        if ids:
            out.append((m.start(), m.end(), ids))
            consumed.append((m.start(), m.end()))
    for m in CASE_ID_RE.finditer(text):
        if not any(s <= m.start() and m.end() <= e for s, e in consumed):
            out.append((m.start(), m.end(), [m.group(0)]))
    out.sort(key=lambda t: t[0])
    return out


def _leading_ids(text: str) -> tuple[list[str], list[tuple[list[str], str]]]:
    """把一行切成「行首编号」与「其余引用」。"""
    leading: list[str] = []
    refs: list[tuple[list[str], str]] = []
    pos = 0
    still_leading = True
    for start, end, ids in _id_spans(text):
        if still_leading and not SEPARATOR_RE.match(text[pos:start]):
            still_leading = False
        if still_leading:
            leading.extend(ids)
        else:
            refs.append((ids, text[start:end]))
        pos = end
    return leading, refs


def _first_docstring_line(node: FunctionDef | AsyncFunctionDef) -> str:
    raw = ast.get_docstring(node, clean=False)
    for line in raw.splitlines() if raw else []:
        if line.strip():
            return line.strip()
    return ""


def _parametrize_has_values(node: FunctionDef | AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "parametrize" and len(dec.args) >= 2:
            return True
    return False


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_param_strings(node: FunctionDef | AsyncFunctionDef) -> list[str]:
    """parametrize 里所有字符串字面量（含嵌套元组）；非字面量自然拿不到，正合预期。"""
    out: list[str] = []

    def walk(value: ast.AST, depth: int = 0) -> None:
        if depth > 4:
            return
        if (s := _const_str(value)) is not None:
            out.append(s.strip())
        elif isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            for elt in value.elts:
                walk(elt, depth + 1)

    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call) or getattr(dec.func, "attr", None) != "parametrize":
            continue
        for arg in dec.args[1:]:
            walk(arg)
        for kw in dec.keywords:
            if kw.arg == "ids":
                walk(kw.value)
    return out


def _prefix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[0]


def _scan_function(module: str, suite: str, qualname: str, node: FunctionDef | AsyncFunctionDef) -> TestFunc:
    func = TestFunc(module=module, qualname=qualname, name=node.name, line=node.lineno, suite=suite)
    first_line = _first_docstring_line(node)
    func.docstring_ids, func.inline_refs = _leading_ids(first_line)
    func.has_params = _parametrize_has_values(node)

    # 只有登记过的前缀才算用例编号；其余（DEF-/FIND- 缺陷号、以及任何没登记的形状）
    # 记进 func.other_ids 由上层单独报告，不进索引。
    case_doc_ids, other = _split_schemes(func.docstring_ids)
    func.docstring_ids = case_doc_ids
    func.other_ids.extend(other)
    for case_id in case_doc_ids:
        # docstring 行首的编号只能定位到函数；是否能精确到参数行由 join 阶段决定。
        func.hits.append(CaseHit(case_id, "docstring", None))

    for text in _iter_param_strings(node):
        m = DESCRIPTIVE_ID_RE.match(text)
        if not m:
            continue  # 纯数据字面量（"WH-01"），不是用例编号。
        leading, _ = _leading_ids(text)
        case_ids, other = _split_schemes(leading)
        func.other_ids.extend(other)
        for case_id in case_ids:
            if case_id == m.group(1):
                # 该字面量本身就是 pytest 生成的 id 片段，可直接在 nodeid 里找它。
                func.hits.append(CaseHit(case_id, "param", text))
    return func


def _split_schemes(ids: list[str]) -> tuple[list[str], list[str]]:
    kept = [i for i in ids if _prefix(i) in CASE_ID_SCHEMES]
    return kept, [i for i in ids if _prefix(i) not in CASE_ID_SCHEMES]


def _iter_test_funcs(module: str, suite: str, body: list[ast.stmt], prefix: str = "") -> list[TestFunc]:
    """按源码顺序收集测试函数，类内测试在 qualname 前加一层类名。

    写成独立递归函数而不是循环里的嵌套闭包：闭包会捕获循环变量 module/suite，
    ruff 的 B023 正是为这种后期绑定报错。递归参数是函数形参，没有这个问题。
    """
    found: list[TestFunc] = []
    for child in body:
        if isinstance(child, ast.ClassDef):
            found.extend(_iter_test_funcs(module, suite, child.body, f"{prefix}{child.name}."))
        elif isinstance(child, (FunctionDef, AsyncFunctionDef)) and child.name.startswith("test_"):
            found.append(_scan_function(module, suite, f"{prefix}{child.name}", child))
    return found


def scan(testcases_dir: Path) -> tuple[list[TestFunc], list[str]]:
    funcs: list[TestFunc] = []
    broken: list[str] = []
    for path in sorted(testcases_dir.rglob("test_*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        parts = path.relative_to(testcases_dir).parts
        suite = parts[0] if len(parts) > 1 else "."
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            broken.append(f"{rel}: {exc}")
            continue
        funcs.extend(_iter_test_funcs(rel, suite, tree.body))
    return funcs, broken


def collect_nodeids(testcases_dir: Path) -> tuple[dict[tuple[str, str], list[str]], dict[str, Any]]:
    """跑一次真实收集，返回 {(模块, 限定函数名): [nodeid]} 与诊断信息。"""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(testcases_dir),
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "--continue-on-collection-errors",
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    by_func: dict[tuple[str, str], list[str]] = {}
    flat: list[str] = []
    for line in proc.stdout.splitlines():
        nodeid = line.strip()
        if "::" not in nodeid or nodeid.startswith(("ERROR", "FAILED", "=")):
            continue
        flat.append(nodeid)
        head, _, tail = nodeid.partition("::")
        parts = [p for p in tail.split("::")]
        parts[-1] = re.sub(r"\[.*\]$", "", parts[-1])
        by_func.setdefault((head, ".".join(parts)), []).append(nodeid)
    diag = {
        "command": " ".join(cmd),
        "collected_nodeids": len(flat),
        "exit_code": proc.returncode,
        "stderr_tail": proc.stderr.strip().splitlines()[-3:],
    }
    return by_func, diag


def _resolve(func: TestFunc, nodeids: list[str]) -> list[tuple[CaseHit, str | None, str]]:
    """给每条编号挑 nodeid；返回 (编号, nodeid, 解析方式)。

    同一编号可能同时来自 docstring 区间与参数行字面量（``LC-06~LC-13`` 与
    ``"LC-06 缺全部字段"``）。此时参数行更精确，docstring 那条要丢掉，否则
    8 条编号会被 docstring 回退放大成 8×8 条。
    """
    out: list[tuple[CaseHit, str | None, str]] = []
    base = [n for n in nodeids if "[" not in n]

    by_case: dict[str, list[CaseHit]] = {}
    for hit in func.hits:
        by_case.setdefault(hit.case_id, []).append(hit)

    # 按编号排序而不是按出现顺序：产物要能 diff，顺序不能随源码行漂移。
    for _, hits in sorted(by_case.items()):
        token_hits = [h for h in hits if h.nodeid_token is not None]
        chosen = token_hits or hits
        resolved_any = False
        for hit in chosen:
            if hit.nodeid_token is not None:
                head = hit.nodeid_token.split()[0]
                token_re = re.compile(re.escape(head) + r"(?![\d])")
                matched = [n for n in nodeids if "[" in n and token_re.search(n.split("[", 1)[1])]
                if matched:
                    resolved_any = True
                    for n in matched:
                        out.append((hit, n, "param-token"))
        if resolved_any:
            continue
        if len(base) == 1:
            out.append((chosen[0], base[0], "base"))
        elif nodeids:
            # docstring 写了区间、参数行却没带编号：只能定位到函数，精度到此为止。
            for n in nodeids:
                out.append((chosen[0], n, "function-level"))
        else:
            out.append((chosen[0], None, "unresolved"))
    return out


def build_index(testcases_dir: Path, collected: dict[tuple[str, str], list[str]] | None) -> dict[str, Any]:
    funcs, broken = scan(testcases_dir)
    if collected is None:
        collected = {}
    used: set[str] = set()
    cases: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    all_nodeids: list[str] = [n for v in collected.values() for n in v]
    static_only = not collected

    for func in funcs:
        nodeids = collected.get((func.module, func.qualname), [])
        rows = _resolve(func, nodeids)
        if not rows:
            continue
        for hit, nodeid, method in rows:
            if nodeid is not None:
                used.add(nodeid)
            row = {
                "case_id": hit.case_id,
                "file": func.module,
                "function": func.name,
                "qualname": func.qualname,
                "line": func.line,
                "nodeid": nodeid,
                "suite": func.suite,
                "resolution": method,
            }
            if nodeid is None:
                unresolved.append(row)
            cases.append(row)

    indexed = [c for c in cases if c["nodeid"] is not None]
    unindexed = [
        {"nodeid": n, "file": n.split("::", 1)[0], "suite": n.split("::", 1)[0].split("/")[1] if "/" in n else "."}
        for n in all_nodeids
        if n not in used
    ]

    def counts(key: str, rows: list[dict[str, Any]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            out[str(row[key])] = out.get(str(row[key]), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def prefixes(ids: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for cid in ids:
            p = _prefix(cid)
            out[p] = out.get(p, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    ref_prefixes: dict[str, int] = {}
    for func in funcs:
        for ids, _ in func.inline_refs:
            for cid in ids:
                p = _prefix(cid)
                ref_prefixes[p] = ref_prefixes.get(p, 0) + 1

    # 行首出现、前缀未登记为用例编号的编号（DEF-/FIND- 缺陷号）。
    other_prefixes: dict[str, int] = {}
    for func in funcs:
        for cid in func.other_ids:
            p = _prefix(cid)
            other_prefixes[p] = other_prefixes.get(p, 0) + 1

    multi = [
        {"nodeid": n, "case_ids": sorted({c["case_id"] for c in indexed if c["nodeid"] == n})}
        for n in dict.fromkeys(c["nodeid"] for c in indexed)
        if len({c["case_id"] for c in indexed if c["nodeid"] == n}) > 1
    ]

    return {
        "schema": "api-regression-selector/case-index@1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "static-only" if static_only else "collect",
        "testcases_dir": testcases_dir.relative_to(REPO_ROOT).as_posix()
        if testcases_dir.is_relative_to(REPO_ROOT)
        else str(testcases_dir),
        # 登记表随产物一起落盘：读索引的人不必回头翻脚本才知道哪些前缀算用例编号。
        "case_id_schemes": CASE_ID_SCHEMES,
        "non_case_id_schemes": NON_CASE_ID_SCHEMES,
        "totals": {
            "test_functions": len(funcs),
            "nodeids_collected": len(all_nodeids) if not static_only else None,
            "nodeids_with_case_id": len({c["nodeid"] for c in indexed}),
            "nodeids_without_case_id": len(unindexed) if not static_only else None,
            "case_id_entries": len(cases),
            "case_id_resolved": len(indexed),
            "case_id_unresolved": len(unresolved),
            # 只定位到函数、未能定位到具体参数行的条目：缺陷归因精度上限。
            "case_id_function_level_only": sum(1 for c in indexed if c["resolution"] == "function-level"),
            "distinct_case_ids": len({c["case_id"] for c in cases}),
            "functions_with_case_id": len({(c["file"], c["qualname"]) for c in cases}),
        },
        "case_id_prefixes": prefixes(sorted({c["case_id"] for c in cases})),
        # 仅作文本引用（非行首）的编号：缺陷号在注释/断言消息里的提及。
        "referenced_id_prefixes": ref_prefixes,
        # 行首出现但前缀未登记为用例编号的编号（DEF-03、DEF-05）。
        "non_case_id_prefixes": other_prefixes,
        "cases": sorted(cases, key=lambda c: (c["case_id"], str(c["nodeid"]))),
        "unresolved": unresolved,
        "unindexed": unindexed,
        "unindexed_by_suite": counts("suite", unindexed),
        "unindexed_by_file": counts("file", unindexed),
        "one_nodeid_multiple_case_ids": multi,
        "parse_errors": broken,
    }


def render_text(index: dict[str, Any]) -> str:
    t = index["totals"]
    lines = [f"模式: {index['mode']}    扫描目录: {index['testcases_dir']}"]
    lines.append(f"测试函数: {t['test_functions']}")
    if index["mode"] == "collect":
        lines.append(f"收集到 nodeid: {t['nodeids_collected']}")
        lines.append(f"有编号的 nodeid: {t['nodeids_with_case_id']}")
        lines.append(f"无编号的 nodeid: {t['nodeids_without_case_id']}   <-- 覆盖缺口，不掩饰")
    else:
        lines.append("静态模式：nodeid 只给 base，参数化用例的 nodeid 未知（不编造）")
    lines += [
        f"编号条目: {t['case_id_entries']}（已解析 {t['case_id_resolved']} / 未解析 {t['case_id_unresolved']}）",
        f"其中只定位到函数、定位不到参数行的: {t['case_id_function_level_only']}",
        f"去重编号: {t['distinct_case_ids']}    带编号的函数: {t['functions_with_case_id']}",
        "",
        f"已登记为用例编号的前缀: {'、'.join(index['case_id_schemes']) or '（无）'}",
        f"已登记为**非**用例编号的前缀: {'、'.join(index['non_case_id_schemes']) or '（无）'}",
        "编号前缀分布（去重后）:",
    ]
    lines += [f"  {k}: {v}" for k, v in index["case_id_prefixes"].items()] or ["  （无）"]
    if index["mode"] == "collect":
        lines += ["", "无编号 nodeid 按套件:"]
        lines += [f"  {k}: {v}" for k, v in index["unindexed_by_suite"].items()] or ["  （无）"]
    if index["one_nodeid_multiple_case_ids"]:
        lines += ["", "一条 nodeid 承载多个编号（缺陷定位精度到此为止）:"]
        lines += [f"  {m['nodeid']}  ->  {'、'.join(m['case_ids'])}" for m in index["one_nodeid_multiple_case_ids"]]
    if index["referenced_id_prefixes"]:
        lines += ["", "仅作文本引用、不算用例编号的前缀:"]
        lines += [f"  {k}: {v}" for k, v in index["referenced_id_prefixes"].items()]
    if index.get("non_case_id_prefixes"):
        lines += ["", "行首出现但前缀未登记为用例编号（缺陷号等）:"]
        lines += [f"  {k}: {v}" for k, v in index["non_case_id_prefixes"].items()]
    if index["unresolved"]:
        lines += ["", "有编号但没能定位到 nodeid:"]
        lines += [f"  {u['case_id']}  {u['file']}::{u['qualname']}" for u in index["unresolved"]]
    if index["parse_errors"]:
        lines += ["", "解析失败:"] + [f"  {e}" for e in index["parse_errors"]]
    if "collection" in index:
        c = index["collection"]
        lines += ["", f"收集命令退出码 {c['exit_code']}；{c['command']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描 testcases/ 生成用例编号 → pytest nodeid 索引。")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"输出路径（默认 {DEFAULT_OUT}）")
    parser.add_argument("--testcases-dir", type=Path, default=DEFAULT_TESTCASES, help="扫描目录")
    parser.add_argument("--json", action="store_true", help="把索引原文打到 stdout")
    parser.add_argument("--static-only", action="store_true", help="不跑 pytest，nodeid 全部留空")
    parser.add_argument("--verify", action="store_true", help="有未解析编号时以退出码 3 失败")
    args = parser.parse_args(argv)

    if not args.testcases_dir.is_dir():
        raise SystemExit(f"扫描目录不存在：{args.testcases_dir}")

    collected = None
    diag: dict[str, Any] | None = None
    if not args.static_only:
        collected, diag = collect_nodeids(args.testcases_dir)

    index = build_index(args.testcases_dir, collected)
    if diag is not None:
        index["collection"] = diag

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(index, ensure_ascii=False, indent=2))
    else:
        print(render_text(index))
        print(f"\n索引已写入: {args.out}")

    if args.verify and index["unresolved"]:
        print(f"\n未解析编号 {len(index['unresolved'])} 条", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
