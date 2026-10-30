#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 pytest 运行结果按用例编号上报给禅道（ZenTao）。

用法::

    python scripts/report_to_zentao.py                    # 默认 dry-run，只打印将发送的请求
    python scripts/report_to_zentao.py --dry-run          # 同上，显式写出这层意图
    python scripts/report_to_zentao.py --out p.json       # dry-run 同时落盘 payload（不含凭据）
    python scripts/report_to_zentao.py --send             # 真的发请求，需要 ZENTAO_URL / ZENTAO_TOKEN
    python scripts/report_to_zentao.py --send --strict    # 有编号没登记进映射表时以退出码 4 失败

为什么要两个输入源
------------------
**JUnit XML 表达不了 rerun**（``scripts/flaky_report.py:15`` 已就此写过一段：``_pytest/junitxml.py``
的 ``pytest_runtest_logreport`` 只认 passed/failed/skipped，重跑只能靠「同一 nodeid 出现多个
``<testcase>``」这种随版本变化的副作用去猜）。而禅道的用例结果只有 ``pass|fail|blocked|n/a``
四个取值，同样没有 rerun 这一维。所以：

* 执行结果取自 ``--junit-xml``（pytest 自己的产物）；
* 重跑次数取自 ``--ledger``（根 ``conftest.py`` 写的 ``reports/flaky-governance.json``）；
* 两者 join 出来的「重跑 N 次后通过」被折叠进禅道 ``steps[].real`` 的正文里——禅道侧没有
  字段能装它，硬塞进 ``result`` 会伪造出一个不存在的枚举值。

编号 → 禅道用例 ID 的映射是**版本控制的配置**（``docs/zentao-case-map.json``），不是脚本猜的。
禅道里的用例 ID 由禅道自己分配，谁也推不出来；映射表为空时脚本照样能跑完 dry-run，只是把
「未映射」清单原样列出来，绝不静默跳过。

凭据只从环境变量读（``ZENTAO_URL`` / ``ZENTAO_TOKEN`` / ``ZENTAO_PRODUCT``），命令行不接受
凭据参数——那会把它写进 shell 历史和进程表。凭据不落盘：``--out`` 写出的 payload 里
认证头整条缺席，控制台只打印「已设置 / 未设置」。

退出码：0 正常；1 输入文件缺失或不可解析（``SystemExit`` 带消息，解释器按 1 退出）；2 ``--dry-run``
与 ``--send`` 同时给出；3 ``--send`` 缺凭据；4 ``--strict`` 且存在未映射编号；5 有请求发送失败。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_INDEX = REPO_ROOT / "reports" / "case-index.json"
DEFAULT_JUNIT = REPO_ROOT / "reports" / "junit.xml"
DEFAULT_LEDGER = REPO_ROOT / "reports" / "flaky-governance.json"
DEFAULT_CASE_MAP = REPO_ROOT / "docs" / "zentao-case-map.json"
DEFAULT_OUT = REPO_ROOT / "reports" / "zentao-payload.json"

# 禅道 RESTful API v1.0：执行用例。公开文档 https://www.zentao.net/book/api/962.html
# （2024-09-30 最后编辑）实测抓取的原文给出 POST /testcases/id/results、
# 头 `Token` 必填、查询参数 testtask（int，否，默认 0）/ version（int，否，默认最新版本）、
# 请求体 steps（array，必填，元素 {result, real}，result ∈ n/a|fail|blocked|pass）。
RESULTS_PATH = "/api.php/v1/testcases/{case_id}/results"

# 禅道 result 的合法取值，一个都不能多。见文档 962 的「请求体」表。
ZENTAO_RESULTS = ("pass", "fail", "blocked", "n/a")

# pytest 结局 → 禅道 result。映射是**有损**的：禅道没有 error / retried 这两个概念。
OUTCOME_TO_RESULT = {
    "passed": "pass",
    "failed": "fail",
    "error": "blocked",
    "skipped": "n/a",
    "xfailed": "n/a",
    "xpassed": "pass",
}

# 只读这三条。ZENTAO_TOKEN 是凭据本体，任何情况下不回显。
ENV_URL = "ZENTAO_URL"
ENV_TOKEN = "ZENTAO_TOKEN"
ENV_PRODUCT = "ZENTAO_PRODUCT"


@dataclass
class Credentials:
    url: str
    token: str

    def __repr__(self) -> str:  # 免得有人 print 一下就把 token 打进日志
        return f"Credentials(url={self.url!r}, token=<已设置>)"


class MissingCredentials(Exception):
    """缺凭据时以退出码 3 结束——这是设计路径，不是崩溃。

    刻意**不**继承 ``SystemExit``：``SystemExit`` 的 ``message`` 属性在 code 为 int 时
    不会被解释器打印，"缺凭据"就会静默变成一次无输出的退出，使用者看不到该设哪个变量。
    由 ``main`` 捕获后写 stderr，退出码仍是 3。
    """

    code = 3

    def __init__(self, missing: list[str]) -> None:
        names = "、".join(missing)
        self.message = (
            "缺少环境变量：" + names + "\n"
            "凭据只从环境变量读，命令行没有对应参数（避免进 shell 历史与进程表）。例如：\n"
            "  $env:ZENTAO_URL='https://zentao.example.com'; $env:ZENTAO_TOKEN='<token>'\n"
            "  python scripts/report_to_zentao.py --send\n"
            "没有真实禅道实例时不要用 --send：默认的 dry-run 不需要任何凭据。"
        )
        super().__init__(self.message)


@dataclass
class Outcome:
    """一个 nodeid 的最终结局 + 重跑次数（重跑来自账本，不来自 JUnit XML）。"""

    nodeid: str
    outcome: str
    reruns: int = 0
    detail: str = ""


@dataclass
class PlannedRequest:
    """一条将要发出的禅道请求；认证头**不在**这里，发送时才注入。"""

    case_id: str
    zentao_case_id: int
    nodeid: str
    result: str
    real: str
    query: dict[str, int]

    @property
    def path(self) -> str:
        return RESULTS_PATH.format(case_id=self.zentao_case_id)

    @property
    def body(self) -> dict[str, Any]:
        return {"steps": [{"result": self.result, "real": self.real}]}

    def to_json(self) -> dict[str, Any]:
        return {
            "method": "POST",
            "path": self.path,
            "query": self.query,
            "body": self.body,
            "case_id": self.case_id,
            "zentao_case_id": self.zentao_case_id,
            "nodeid": self.nodeid,
        }


def read_credentials(env: Mapping[str, str]) -> Credentials:
    """凭据只从环境变量读，一处读、一处存，别的地方不许再碰 os.environ。"""
    missing = [name for name in (ENV_URL, ENV_TOKEN) if not env.get(name, "").strip()]
    if missing:
        raise MissingCredentials(missing)
    return Credentials(url=env[ENV_URL].strip().rstrip("/"), token=env[ENV_TOKEN].strip())


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"输入文件不存在：{path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"输入文件不是合法 JSON：{path} —— {exc}") from None


def parse_junit(path: Path) -> dict[str, Outcome]:
    """JUnit XML → {nodeid: Outcome}。

    ``classname`` 是点分模块路径（``testcases.login.test_login_contract``），
    还原成 ``testcases/login/test_login_contract.py::<name>`` 时按「后缀能落到真实文件上」
    的最长前缀去切——类内测试会让 classname 多带一层类名，切错就会伪造出不存在的 nodeid。
    """
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        raise SystemExit(f"输入文件不存在：{path}") from None
    except ET.ParseError as exc:
        raise SystemExit(f"JUnit XML 不可解析：{path} —— {exc}") from None

    outcomes: dict[str, Outcome] = {}
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        nodeid = _nodeid_from_junit(classname, name)
        outcome, detail = _outcome_of(case)
        outcomes[nodeid] = Outcome(nodeid=nodeid, outcome=outcome, detail=detail)
    return outcomes


def _nodeid_from_junit(classname: str, name: str) -> str:
    parts = classname.split(".") if classname else []
    for cut in range(len(parts), 0, -1):
        candidate = REPO_ROOT.joinpath(*parts[:cut]).with_suffix(".py")
        if candidate.is_file():
            rel = candidate.relative_to(REPO_ROOT).as_posix()
            rest = parts[cut:]
            return "::".join([rel, *rest, name]) if rest else f"{rel}::{name}"
    # 还原不出来就原样留着：宁可在「未匹配」里露出来，也不要编一个 nodeid。
    return f"{classname}::{name}" if classname else name


def _outcome_of(case: ET.Element) -> tuple[str, str]:
    for kind in ("failure", "error"):
        node = case.find(kind)
        if node is not None:
            message = (node.get("message") or "").strip().splitlines()
            head = message[0] if message else kind
            return ("failed" if kind == "failure" else "error"), head[:500]
    skipped = case.find("skipped")
    if skipped is not None:
        # pytest 用 skipped 承载 xfail/xpass/skip 三种结局，靠 type 区分；
        # 禅道没有这三者的对应值，最后都落到 n/a。
        kind = (skipped.get("type") or "").lower()
        reason = (skipped.get("message") or "").strip()
        outcome = "xfailed" if "xfail" in kind else "xpassed" if "xpass" in kind else "skipped"
        return outcome, reason[:500]
    return "passed", ""


def load_reruns(path: Path | None) -> dict[str, int]:
    """从 flaky 账本读「这个 nodeid 重跑了几次」。账本缺了就是没有重跑可言。"""
    if path is None or not path.is_file():
        return {}
    reruns: dict[str, int] = {}
    data = load_json(path)
    for key in ("rerun_passed", "rerun_still_failed"):
        for row in data.get(key, []):
            nodeid = row.get("nodeid") if isinstance(row, dict) else None
            if nodeid:
                reruns[nodeid] = max(reruns.get(nodeid, 0), int(row.get("reruns", 1)))
    return reruns


def load_case_map(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """读「用例编号 → 禅道用例 ID」映射表；表不存在时返回空表（dry-run 仍可用）。

    以 ``_`` 开头的键是元信息（``_testtask`` / ``_comment`` 等），不是编号——
    禅道用例 ID 由禅道分配，谁都推不出来，所以这张表只能由人在建完用例后填。
    """
    if not path.is_file():
        return {}, {}
    data = load_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"映射表顶层必须是对象：{path}")
    raw = data.get("cases", {})
    if not isinstance(raw, dict):
        raise SystemExit(f"映射表的 cases 必须是对象：{path}")
    out: dict[str, dict[str, Any]] = {}
    for case_id, value in raw.items():
        if isinstance(value, int):
            out[case_id] = {"zentao_case_id": value}
        elif isinstance(value, dict) and isinstance(value.get("zentao_case_id"), int):
            out[case_id] = value
        else:
            raise SystemExit(f"映射表条目形状不对：{case_id} -> {value!r}（应为 int 或含 zentao_case_id 的对象）")
    return out, {k: v for k, v in data.items() if k.startswith("_")}


def build_plan(
    index: dict[str, Any],
    outcomes: dict[str, Outcome],
    reruns: dict[str, int],
    case_map: dict[str, dict[str, Any]],
    testtask: int,
) -> tuple[list[PlannedRequest], list[dict[str, Any]]]:
    """把「编号 → nodeid」与「nodeid → 结局」join 成待发请求，并列出所有被跳过的编号。

    跳过一律有名字：没跑到的编号、没登记的编号、结局不是合法禅道 result 的编号，
    三种都要出现在 skipped 里。静默跳过等于制造「全绿」的假象。
    """
    plan: list[PlannedRequest] = []
    skipped: list[dict[str, Any]] = []
    for row in index.get("cases", []):
        case_id = row["case_id"]
        nodeid = row.get("nodeid")
        if not nodeid:
            skipped.append({"case_id": case_id, "why": "case-index 里没有 nodeid", "nodeid": None})
            continue
        outcome = outcomes.get(nodeid)
        if outcome is None:
            skipped.append({"case_id": case_id, "why": "本次运行没跑到这个 nodeid", "nodeid": nodeid})
            continue
        mapping = case_map.get(case_id)
        if not mapping:
            skipped.append({"case_id": case_id, "why": "禅道用例 ID 未登记", "nodeid": nodeid})
            continue
        result = OUTCOME_TO_RESULT.get(outcome.outcome)
        if result not in ZENTAO_RESULTS:
            skipped.append(
                {"case_id": case_id, "why": f"结局 {outcome.outcome!r} 无法映射到禅道 result", "nodeid": nodeid}
            )
            continue
        query = {"testtask": testtask}
        if isinstance(mapping.get("version"), int):
            query["version"] = mapping["version"]
        plan.append(
            PlannedRequest(
                case_id=case_id,
                zentao_case_id=int(mapping["zentao_case_id"]),
                nodeid=nodeid,
                result=result,
                real=_describe(outcome, reruns.get(nodeid, outcome.reruns)),
                query=query,
            )
        )
    return plan, skipped


def _describe(outcome: Outcome, reruns: int) -> str:
    """写进禅道 ``real`` 字段的执行说明。

    重跑必须写在这里：禅道的 ``result`` 只有四个枚举值，多塞一维就是伪造。
    """
    bits = [f"api-regression-selector 自动上报：{outcome.outcome}"]
    if reruns:
        bits.append(f"重跑 {reruns} 次（JUnit XML 不表达 rerun，此数取自 reports/flaky-governance.json）")
    bits.append(f"nodeid={outcome.nodeid}")
    if outcome.detail:
        bits.append(outcome.detail.replace("\n", " ")[:300])
    return "；".join(bits)


def post_request(credentials: Credentials, request: PlannedRequest, timeout: float) -> tuple[int, str]:
    """真的发一条请求。认证头只在这一层出现，绝不进 payload 产物。"""
    query = urllib.parse.urlencode(request.query)
    url = f"{credentials.url}{request.path}?{query}"
    body = json.dumps(request.body, ensure_ascii=False).encode("utf-8")
    http_request = urllib.request.Request(  # noqa: S310 - 地址由使用者自己的环境变量给出
        url,
        data=body,
        method="POST",
        headers={
            # 文档 962 的请求头表写的是 `Token`（大写 T）；v2 手册里是 `token`。
            # HTTP 头名大小写不敏感，但服务端网关未必，保持与 v1 文档一致。
            "Token": credentials.token,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(http_request, timeout=timeout) as resp:  # noqa: S310
        return resp.status, resp.read().decode("utf-8", errors="replace")


def render_plan(plan: list[PlannedRequest], skipped: list[dict[str, Any]], base_url: str) -> str:
    lines = [f"待发送 {len(plan)} 条请求 → {base_url}（认证头：Token <不回显>）"]
    for req in plan:
        lines.append(f"  POST {req.path}?{urllib.parse.urlencode(req.query)}")
        lines.append(f"       case_id={req.case_id} nodeid={req.nodeid} result={req.result}")
        lines.append(f"       body={json.dumps(req.body, ensure_ascii=False)}")
    if skipped:
        lines.append(f"跳过 {len(skipped)} 条（不静默）：")
        for row in skipped:
            lines.append(f"  {row['case_id']}: {row['why']}  nodeid={row['nodeid']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except MissingCredentials as exc:
        # 缺凭据是**设计路径**：给一条能照着做的提示，再以 3 退出，不要抛栈。
        print(exc.message, file=sys.stderr)
        return exc.code


def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 pytest 运行结果按用例编号上报给禅道（默认 dry-run）。")
    parser.add_argument("--case-index", type=Path, default=DEFAULT_CASE_INDEX, help="case_index.py 的产物")
    parser.add_argument("--junit-xml", type=Path, default=DEFAULT_JUNIT, help="pytest 的 JUnit XML")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help="flaky 账本（提供 rerun 次数）")
    parser.add_argument("--case-map", type=Path, default=DEFAULT_CASE_MAP, help="编号 → 禅道用例 ID 的映射表")
    parser.add_argument("--out", type=Path, default=None, help=f"dry-run 时把 payload 写到文件（如 {DEFAULT_OUT}）")
    parser.add_argument("--send", action="store_true", help="真的上报；不加就是 dry-run，不产生任何网络请求")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="显式声明 dry-run（与默认行为相同）。只是让命令行里能把这层意图写出来，不接受与 --send 同时出现",
    )
    parser.add_argument("--strict", action="store_true", help="存在未映射编号时以退出码 4 失败")
    parser.add_argument("--timeout", type=float, default=10.0, help="单条请求超时（秒）")
    parser.add_argument("--tolerate-errors", action="store_true", help="单条发送失败不中断，继续发后面的")
    args = parser.parse_args(argv)

    if args.dry_run and args.send:
        # 两者互斥：同时给出时谁赢都是一种沉默的猜测，不如直接拒绝。
        print("--dry-run 与 --send 互斥：要么看着 payload，要么真的上报。", file=sys.stderr)
        return 2

    index = load_json(args.case_index)
    outcomes = parse_junit(args.junit_xml)
    reruns = load_reruns(args.ledger)
    case_map, meta = load_case_map(args.case_map)
    testtask = int(meta.get("_testtask", 0))

    plan, skipped = build_plan(index, outcomes, reruns, case_map, testtask=testtask)

    credentials: Credentials | None = None
    base_url = "(dry-run，未读凭据)"
    if args.send:
        import os

        credentials = read_credentials(os.environ)
        base_url = credentials.url

    print(render_plan(plan, skipped, base_url))

    if args.out is not None:
        artifact = {
            "schema": "api-regression-selector/zentao-payload@1",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dry_run": not args.send,
            "zentao_base_url": base_url,
            # 认证头整条缺席——产物是会被上传/贴进 issue 的东西，凭据不许跟着走。
            "note": "认证头不在产物里；发送时由 ZENTAO_TOKEN 环境变量注入 Token 头。",
            "requests": [req.to_json() for req in plan],
            "skipped": skipped,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\npayload 已写入: {args.out}（不含凭据）")

    failures = 0
    if args.send:
        assert credentials is not None
        for req in plan:
            try:
                status, text = post_request(credentials, req, args.timeout)
            except urllib.error.HTTPError as exc:
                status, text = exc.code, exc.read().decode("utf-8", errors="replace")
            except urllib.error.URLError as exc:
                status, text = 0, f"连接失败：{exc.reason}"
            ok = 200 <= status < 300
            print(f"  {'OK ' if ok else 'ERR'} {req.case_id} -> HTTP {status} {text.strip()[:200]}")
            if not ok:
                failures += 1
                if not args.tolerate_errors:
                    print(f"\n第 {failures} 条失败即中止（--tolerate-errors 可继续发完）", file=sys.stderr)
                    return 5

    if args.strict and skipped:
        print(f"\n未映射/未跑到的编号 {len(skipped)} 条", file=sys.stderr)
        return 4
    return 5 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
