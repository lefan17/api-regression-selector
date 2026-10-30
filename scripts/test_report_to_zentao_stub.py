#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""禅道上报脚本的桩服务测试：没有真实禅道实例时，用本地 HTTP 桩证明「请求长得对」。

为什么不用 unittest.mock 把 urllib 换掉
--------------------------------------
mock 掉 ``urlopen`` 只能证明「我调用了自己以为的那个函数」，证明不了发出的东西真是
一个合法 HTTP 请求。桩服务走的是**真 socket、真 header、真字节**，连 urllib 对 header
名的大小写归一化、URL 查询串编码、以及 JSON 的 UTF-8 编码都一起验了——
而这恰好是这类上报脚本最容易出错的地方。

两条路径都要覆盖
----------------
* ``--dry-run``（默认档）：**一个字节都不许上网**。测试在桩服务*已关闭*的前提下跑，
  仍要求退出码 0，且桩上一条记录都没有。
* ``--send``：真发。断言方法 / 路径 / 查询参数 / 认证头 / 请求体形状逐项正确。

为什么放在 ``scripts/`` 而不是 ``testcases/``
---------------------------------------------
本任务边界规定 ``testcases/**`` 只许加 docstring 或编号，不许新增/改动测试；
``scripts/`` 是可动的。代价是 ``pytest.ini`` 的 ``testpaths = testcases`` 不会自动
收集本文件，必须显式指路径：

    python -m pytest scripts/test_report_to_zentao_stub.py

接进 CI 只是加一行 step，见 ``docs/wave3-zentao-handoff.md`` 的「整合清单」。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "report_to_zentao.py"

# 凭据只从环境变量读；测试里给的是明显的假值，好让「产物 / 日志里 grep 不到它」可验证。
FAKE_TOKEN = "STUB-TOKEN-DO-NOT-LOG-8f3a91"
ENV_URL = "ZENTAO_URL"
ENV_TOKEN = "ZENTAO_TOKEN"

NODEID_OK = "testcases/login/test_login_contract.py::test_lc01_login_success"
NODEID_FAIL = "testcases/login/test_login_contract.py::test_lc06_to_lc13_missing_fields_400[payload0-LC-06 缺全部字段]"

# 文档 962 给的是 `POST /api.php/v1/testcases/{id}/results`，id 为禅道分配的用例 ID。
EXPECTED_PATH_101 = "/api.php/v1/testcases/101/results"
EXPECTED_PATH_106 = "/api.php/v1/testcases/106/results"


@dataclass
class Received:
    """桩收到的一条请求。方法/路径/查询/头/体全部保留原始值。"""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class _Handler(BaseHTTPRequestHandler):
    """只实现 ``POST /api.php/v1/testcases/{id}/results``，成功返回 ``{"message": ""}``。"""

    protocol_version = "HTTP/1.1"
    status = 200
    payload: dict[str, Any] = {"message": ""}

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的命名约定
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        split = urlsplit(self.path)
        self.server.recorded.append(  # type: ignore[attr-defined]
            Received(
                method=self.command,
                path=split.path,
                query=parse_qs(split.query),
                # 头名统一小写存。HTTP 头名大小写不敏感，脚本发的是 `Token`；
                # 按小写查，免得测试把「大小写」误当成契约的一部分。
                headers={k.lower(): v for k, v in self.headers.items()},
                body=body,
            )
        )
        blob = json.dumps(self.payload).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def log_message(self, *args: Any) -> None:
        """静音：桩的访问日志会污染 pytest 输出。"""


class _FailingHandler(_Handler):
    """永远 500，用来验错误分支。"""

    status = 500
    payload: dict[str, Any] = {"error": "not found"}


class _Server(ThreadingHTTPServer):
    recorded: list[Received]

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], recorded: list[Received]):
        self.recorded = recorded
        super().__init__(address, handler)


@dataclass
class StubZenTao:
    """本地桩禅道。端口取 0 让内核分配，避免固定端口撞车。"""

    handler: type[BaseHTTPRequestHandler] = _Handler
    recorded: list[Received] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._server = _Server(("127.0.0.1", 0), self.handler, self.recorded)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # server_address 的类型标注允许 bytes（unix socket 场景），这里显式解码，
        # 免得 f-string 把 bytes 渲染成 b'127.0.0.1'。
        host, port = self._server.server_address[:2]
        host = host.decode() if isinstance(host, bytes) else host
        self.url = f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def stub() -> Iterator[StubZenTao]:
    server = StubZenTao()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def failing_stub() -> Iterator[StubZenTao]:
    server = StubZenTao(handler=_FailingHandler)
    try:
        yield server
    finally:
        server.close()


@dataclass
class Inputs:
    """一次上报所需的四个输入文件，全部落在 tmp_path，绝不碰仓库真产物。"""

    case_index: Path
    junit: Path
    ledger: Path
    case_map: Path
    out: Path


def write_inputs(tmp_path: Path, *, map_lc06: bool = True) -> Inputs:
    """造一份最小但形状真实的输入：2 条编号、1 条通过 1 条失败、LC-01 重跑过 2 次。"""
    case_index = tmp_path / "case-index.json"
    case_index.write_text(
        json.dumps(
            {
                "schema": "api-regression-selector/case-index@1",
                "cases": [
                    {"case_id": "LC-01", "nodeid": NODEID_OK, "suite": "login"},
                    {"case_id": "LC-06", "nodeid": NODEID_FAIL, "suite": "login"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # name 里带 parametrize 的 id（含中文与空格），classname 是点分模块路径——
    # 正是 report_to_zentao.py 的 _nodeid_from_junit 要还原的形状。
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites><testsuite name="pytest" tests="2" failures="1">\n'
        '<testcase classname="testcases.login.test_login_contract"'
        ' name="test_lc01_login_success" time="0.1" />\n'
        '<testcase classname="testcases.login.test_login_contract"'
        ' name="test_lc06_to_lc13_missing_fields_400[payload0-LC-06 缺全部字段]" time="0.1">\n'
        '  <failure message="AssertionError: 状态码不是 400">traceback</failure>\n'
        "</testcase>\n"
        "</testsuite></testsuites>\n",
        encoding="utf-8",
    )

    # 账本形状对齐 conftest.py 的 _write_flaky_sidecar：rerun_passed 是 [{nodeid, reruns}]。
    ledger = tmp_path / "flaky-governance.json"
    ledger.write_text(
        json.dumps({"schema": 1, "rerun_passed": [{"nodeid": NODEID_OK, "reruns": 2}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    cases: dict[str, Any] = {"LC-01": {"zentao_case_id": 101}}
    if map_lc06:
        # 故意用「对象 + version」这种较长形状，顺带验证 version 会进查询串。
        cases["LC-06"] = {"zentao_case_id": 106, "version": 2}
    case_map = tmp_path / "zentao-case-map.json"
    case_map.write_text(json.dumps({"_testtask": 77, "cases": cases}, ensure_ascii=False), encoding="utf-8")

    return Inputs(case_index, junit, ledger, case_map, tmp_path / "zentao-payload.json")


def run_script(inputs: Inputs, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """在仓库根跑一次上报脚本。env=None 表示环境里**没有** ZENTAO_URL / ZENTAO_TOKEN。"""
    base = dict(os.environ)
    base.pop(ENV_URL, None)
    base.pop(ENV_TOKEN, None)
    base["PYTHONIOENCODING"] = "utf-8"
    if env:
        base.update(env)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--case-index",
            str(inputs.case_index),
            "--junit-xml",
            str(inputs.junit),
            "--ledger",
            str(inputs.ledger),
            "--case-map",
            str(inputs.case_map),
            *extra,
        ],
        cwd=str(REPO_ROOT),
        env=base,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


# ---------------------------------------------------------------------------
# 路径一：--dry-run（默认档）——一个字节都不许上网
# ---------------------------------------------------------------------------


def test_dry_run_sends_nothing_even_when_stub_is_closed(tmp_path: Path, stub: StubZenTao) -> None:
    """先把桩关掉，再跑 dry-run：仍要退出码 0，且桩上一条记录都没有。

    桩既然已 shutdown，dry-run 若试图连接必然抛 URLError 并留下非 0 退出码；
    所以「退出码 0」+「记录为空」合起来就是「没上网」的硬证据。
    """
    inputs = write_inputs(tmp_path)
    port = urlsplit(stub.url).port
    stub.close()

    with socket.socket() as probe:
        probe.settimeout(1.0)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "桩没关掉，这个测试就证明不了任何事"

    # 环境变量**给足了**：dry-run 不该因此去联网，也不该去读它。
    proc = run_script(inputs, env={ENV_URL: stub.url, ENV_TOKEN: FAKE_TOKEN})

    assert proc.returncode == 0, proc.stderr
    assert "待发送 2 条请求" in proc.stdout
    assert "未读凭据" in proc.stdout
    assert FAKE_TOKEN not in proc.stdout
    assert stub.recorded == []


def test_dry_run_flag_is_accepted_and_conflicts_with_send(tmp_path: Path, stub: StubZenTao) -> None:
    """显式 --dry-run 与默认档等价；与 --send 同时给出必须拒绝，而不是悄悄选一个。"""
    inputs = write_inputs(tmp_path)

    explicit = run_script(inputs, "--dry-run", env={ENV_URL: stub.url, ENV_TOKEN: FAKE_TOKEN})
    assert explicit.returncode == 0, explicit.stderr
    assert "待发送 2 条请求" in explicit.stdout
    assert "未读凭据" in explicit.stdout
    assert stub.recorded == []

    both = run_script(inputs, "--dry-run", "--send", env={ENV_URL: stub.url, ENV_TOKEN: FAKE_TOKEN})
    assert both.returncode == 2
    assert "--dry-run 与 --send 互斥" in both.stderr
    assert stub.recorded == []


def test_dry_run_payload_shape(tmp_path: Path) -> None:
    """dry-run + --out：产物形状要对，且认证头整条缺席。"""
    inputs = write_inputs(tmp_path)
    proc = run_script(inputs, "--out", str(inputs.out))
    assert proc.returncode == 0, proc.stderr

    text = inputs.out.read_text(encoding="utf-8")
    artifact = json.loads(text)
    assert artifact["dry_run"] is True
    assert artifact["skipped"] == []
    assert [r["method"] for r in artifact["requests"]] == ["POST", "POST"]
    assert [r["path"] for r in artifact["requests"]] == [EXPECTED_PATH_101, EXPECTED_PATH_106]
    assert artifact["requests"][0]["query"] == {"testtask": 77}
    assert artifact["requests"][1]["query"] == {"testtask": 77, "version": 2}

    first, second = (r["body"] for r in artifact["requests"])
    assert set(first) == {"steps"}, "禅道 962 的请求体只有 steps，多送字段是猜测"
    assert set(first["steps"][0]) == {"result", "real"}, "steps 元素只认 result 与 real"
    assert first["steps"][0]["result"] == "pass"
    assert second["steps"][0]["result"] == "fail"
    # 重跑只能落在 real 里：禅道的 result 是四值枚举，多塞一维就是伪造。
    assert "重跑 2 次" in first["steps"][0]["real"]
    # 凭据本体不许出现；认证头（Token / Authorization）整条不许出现。
    assert FAKE_TOKEN not in text
    assert not [k for r in artifact["requests"] for k in r if "token" in k.lower() or "auth" in k.lower()]


def test_dry_run_lists_unmapped_case_code_by_name(tmp_path: Path) -> None:
    """没登记禅道 ID 的编号必须有名字地出现在 skipped 里，--strict 才升级为失败。"""
    inputs = write_inputs(tmp_path, map_lc06=False)

    soft = run_script(inputs)
    assert soft.returncode == 0, soft.stderr
    assert "跳过 1 条（不静默）" in soft.stdout
    assert "LC-06: 禅道用例 ID 未登记" in soft.stdout

    strict = run_script(inputs, "--strict")
    assert strict.returncode == 4
    assert "未映射/未跑到的编号 1 条" in strict.stderr


def test_dry_run_reports_case_that_did_not_run(tmp_path: Path) -> None:
    """本次没跑到的编号也算 skipped 的一种，同样要点名。"""
    inputs = write_inputs(tmp_path)
    inputs.junit.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites><testsuite name="pytest" tests="1" failures="0">\n'
        '<testcase classname="testcases.login.test_login_contract"'
        ' name="test_lc01_login_success" time="0.1" />\n'
        "</testsuite></testsuites>\n",
        encoding="utf-8",
    )
    proc = run_script(inputs)
    assert proc.returncode == 0, proc.stderr
    assert "LC-06: 本次运行没跑到这个 nodeid" in proc.stdout


# ---------------------------------------------------------------------------
# 路径二：--send ——真发，桩逐字段断言
# ---------------------------------------------------------------------------


def test_send_posts_expected_requests(tmp_path: Path, stub: StubZenTao) -> None:
    inputs = write_inputs(tmp_path)
    proc = run_script(inputs, "--send", env={ENV_URL: stub.url, ENV_TOKEN: FAKE_TOKEN})
    assert proc.returncode == 0, proc.stderr

    assert len(stub.recorded) == 2, [r.path for r in stub.recorded]
    first, second = stub.recorded

    assert first.method == "POST"
    assert first.path == EXPECTED_PATH_101
    assert first.query == {"testtask": ["77"]}
    assert first.headers["token"] == FAKE_TOKEN
    assert first.headers["content-type"] == "application/json"
    assert first.json()["steps"][0]["result"] == "pass"
    assert "重跑 2 次" in first.json()["steps"][0]["real"]

    assert second.method == "POST"
    assert second.path == EXPECTED_PATH_106
    # URL 里的查询串必须真的是 URL 编码过的，不是拼上去就算数。
    assert second.query == {"testtask": ["77"], "version": ["2"]}
    assert second.headers["token"] == FAKE_TOKEN
    assert set(second.json()) == {"steps"}
    assert set(second.json()["steps"][0]) == {"result", "real"}
    assert second.json()["steps"][0]["result"] == "fail"
    assert "状态码不是 400" in second.json()["steps"][0]["real"]

    # 认证头只在上行，不许回显到 stdout/stderr。
    assert FAKE_TOKEN not in proc.stdout
    assert FAKE_TOKEN not in proc.stderr


def test_send_without_credentials_refuses_and_sends_nothing(tmp_path: Path, stub: StubZenTao) -> None:
    """凭据只从环境变量来：不给就退出码 3，且一条请求都不发。"""
    inputs = write_inputs(tmp_path)
    proc = run_script(inputs, "--send")

    assert proc.returncode == 3
    assert "缺少环境变量：ZENTAO_URL、ZENTAO_TOKEN" in proc.stderr
    assert stub.recorded == [], "缺凭据却发了请求"


def test_send_aborts_on_5xx_and_tolerate_errors_finishes(tmp_path: Path, failing_stub: StubZenTao) -> None:
    """5xx 必须变成失败退出码 5；--tolerate-errors 才允许把剩下的发完。"""
    inputs = write_inputs(tmp_path)

    proc = run_script(inputs, "--send", env={ENV_URL: failing_stub.url, ENV_TOKEN: FAKE_TOKEN})
    assert proc.returncode == 5, proc.stdout
    assert "ERR LC-01 -> HTTP 500" in proc.stdout
    assert len(failing_stub.recorded) == 1, "默认遇错即停，不该继续发第二条"

    tolerated = run_script(
        inputs, "--send", "--tolerate-errors", env={ENV_URL: failing_stub.url, ENV_TOKEN: FAKE_TOKEN}
    )
    assert tolerated.returncode == 5
    assert len(failing_stub.recorded) == 3, "--tolerate-errors 要把剩下的也发完"
    assert "ERR LC-06 -> HTTP 500" in tolerated.stdout


def test_send_cannot_reach_stub_is_a_failure_not_a_pass(tmp_path: Path, stub: StubZenTao) -> None:
    """连不上必须是失败。这条同时反向证明 test_dry_run_sends_nothing 里的桩确实是关掉的。"""
    inputs = write_inputs(tmp_path)
    url = stub.url
    stub.close()

    proc = run_script(inputs, "--send", env={ENV_URL: url, ENV_TOKEN: FAKE_TOKEN})
    assert proc.returncode == 5, proc.stdout
    assert "连接失败" in proc.stdout
