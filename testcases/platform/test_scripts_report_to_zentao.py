"""禅道上报脚本的 dry-run 安全承诺与退出码（`scripts/report_to_zentao.py`）。

为什么这些用例必须存在
----------------------
`README.md:431` 承诺「默认 ``--dry-run`` 只打印将要发送的 payload，真实上报要显式 ``--send``」。
「默认不动手」是一条**安全**承诺：破坏它不会报错，只会在某个人跑脚本看一眼的时候，
把一批假结果真的写进测试管理系统 —— 那比崩溃糟得多，因为它是静默的。

同一份文档 `scripts/report_to_zentao.py:33-34` 还列了 5 个退出码（1 输入不可解析 /
2 两档互斥 / 3 缺凭据 / 4 ``--strict`` 有未映射编号 / 5 发送失败），逐个钉在这。

网络怎么挡：``post_request``（脚本 :330）是唯一会碰网络的函数，把它换掉并计数，
再额外把 ``urllib.request.urlopen`` 换成「一调就炸」的哨兵 —— 万一有人绕过 ``post_request``
另外开一条发请求的路，这条哨兵会当场把用例打红，而不是让用例悄悄发出去。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.platform

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "report_to_zentao.py"


def _child_env() -> dict[str, str]:
    """起子进程用的环境：钉住 ``PYTHONIOENCODING=utf-8``。

    子进程的 stderr 走管道，默认按**本机 locale** 编码（Windows 中文是 cp936），
    这里钉成 utf-8，下面按 ``encoding="utf-8"`` **严格**解码才成立。
    否则中文路径会被 GBK 写成另一串字节，UTF-8 解码出 ``\\ufffd``，
    于是断言失败看起来像「路径不对」，实际是「两边编码不一致」。
    与 ``testcases/api/test_flaky_governance_meta.py:45-48``、
    ``testcases/mes/test_data_lifecycle.py:47-48`` 的写法一致。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _load_module() -> ModuleType:
    """按路径加载被测脚本。

    不能写 ``from scripts import report_to_zentao``：``scripts/`` 没有 ``__init__.py``，
    那样同一个文件会以 ``report_to_zentao`` 与 ``scripts.report_to_zentao`` 两个模块名
    被 mypy 发现，直接以 exit 2 中止（与邻居 ``test_scripts_check_deps_sync.py`` 同因）。
    """
    spec = importlib.util.spec_from_file_location("_report_to_zentao_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report_to_zentao = _load_module()

# 一个真实存在的文件（脚本 :197-206 会用「后缀能落到真实文件上」回推 nodeid）。
REAL_FILE = "testcases/platform/test_gate_layers.py"
NODEID = f"{REAL_FILE}::test_layered_gate_expressions"
UNMAPPED_NODEID = f"{REAL_FILE}::test_no_such_case"


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    """三份输入：case-index（编号→nodeid）、junit.xml（nodeid→结局）、case-map（编号→禅道 id）。"""
    index = tmp_path / "case-index.json"
    index.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "PT-01", "nodeid": NODEID},
                    {"case_id": "PT-02", "nodeid": UNMAPPED_NODEID},
                    {"case_id": "PT-03", "nodeid": None},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" tests="2">'
        '<testcase classname="testcases.platform.test_gate_layers" name="test_layered_gate_expressions" />'
        '<testcase classname="testcases.platform.test_gate_layers" name="test_no_such_case">'
        '<failure message="assert 1 == 2">boom</failure></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    case_map = tmp_path / "case-map.json"
    case_map.write_text(
        json.dumps({"_testtask": 7, "cases": {"PT-01": 1001, "PT-02": {"zentao_case_id": 1002}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "case_index": index,
        "junit": junit,
        "ledger": tmp_path / "no-ledger.json",
        "case_map": case_map,
        "out": tmp_path / "payload.json",
    }


def _argv(inputs: dict[str, Path], *extra: str) -> list[str]:
    return [
        "--case-index",
        str(inputs["case_index"]),
        "--junit-xml",
        str(inputs["junit"]),
        "--ledger",
        str(inputs["ledger"]),
        "--case-map",
        str(inputs["case_map"]),
        *extra,
    ]


@pytest.fixture
def network_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """记录所有「本来会发出去的请求」，并把 urlopen 变成哨兵。"""
    sent: list[Any] = []

    def fake_post(credentials: Any, request: Any, timeout: float) -> tuple[int, str]:
        sent.append((credentials, request, timeout))
        return 200, "{}"

    def forbidden_urlopen(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run 之外的路径真的上网了 —— 这是被禁用的一步，不只是在测试里")

    monkeypatch.setattr(report_to_zentao, "post_request", fake_post)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_urlopen)
    return sent


def test_default_mode_sends_nothing(
    inputs: dict[str, Path], network_spy: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """README.md:431 的核心承诺：不带 ``--send`` 时一次网络请求都不发，退出码 0。"""
    code = report_to_zentao.main(_argv(inputs))

    out = capsys.readouterr().out
    assert code == 0
    assert network_spy == [], f"默认档发了 {len(network_spy)} 条请求，安全承诺已破"
    assert "dry-run" in out


def test_explicit_dry_run_behaves_the_same(
    inputs: dict[str, Path], network_spy: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """``--dry-run`` 只是把默认档的意图写出来（脚本 :381-384），行为必须与默认档一模一样。"""
    code = report_to_zentao.main(_argv(inputs, "--dry-run"))

    assert code == 0
    assert network_spy == []


def test_dry_run_artifact_is_parseable_and_carries_no_credentials(
    inputs: dict[str, Path], network_spy: list[Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--out`` 落盘的 payload 要能被 ``json.loads``，且凭据值整条不跟着产物走（脚本 :419-420）。"""
    secret = "sk-should-never-appear-in-artifact"
    monkeypatch.setenv("ZENTAO_URL", "https://zentao.example.com")
    monkeypatch.setenv("ZENTAO_TOKEN", secret)

    code = report_to_zentao.main(_argv(inputs, "--out", str(inputs["out"])))

    capsys.readouterr()
    assert code == 0
    assert network_spy == []
    raw = inputs["out"].read_text(encoding="utf-8")
    artifact = json.loads(raw)
    assert artifact["schema"] == "api-regression-selector/zentao-payload@1"
    assert artifact["dry_run"] is True
    assert artifact["requests"], "--out 写出了空 requests：dry-run 的产物没有任何信息量"
    first = artifact["requests"][0]
    assert set(first) == {"method", "path", "query", "body", "case_id", "zentao_case_id", "nodeid"}
    assert first["method"] == "POST"
    assert first["path"] == "/api.php/v1/testcases/1001/results"
    assert first["query"] == {"testtask": 7}
    assert first["body"]["steps"][0]["result"] == "pass"
    # 凭据只从环境变量读（脚本 :156-161），绝不写进产物。
    assert secret not in raw
    assert "authorization" not in raw.lower()
    assert [row["case_id"] for row in artifact["skipped"]] == ["PT-03"], "没 nodeid 的编号必须出现在 skipped 里"
    if artifact["skipped"]:
        assert set(artifact["skipped"][0]) == {"case_id", "why", "nodeid"}


def test_send_actually_sends(
    inputs: dict[str, Path], network_spy: list[Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--send`` 才发；断言发出的条数、路径与结果体（用假 ``post_request``，不碰真网络）。"""
    monkeypatch.setenv("ZENTAO_URL", "https://zentao.example.com")
    monkeypatch.setenv("ZENTAO_TOKEN", "fake-token")

    code = report_to_zentao.main(_argv(inputs, "--send"))

    capsys.readouterr()
    assert code == 0
    assert len(network_spy) == 2, f"--send 应发 2 条（PT-01 通过 / PT-02 失败），实际 {len(network_spy)}"
    credentials, first, timeout = network_spy[0]
    assert credentials.url == "https://zentao.example.com"
    assert credentials.token == "fake-token"
    assert first.path == "/api.php/v1/testcases/1001/results"
    assert timeout > 0
    results = {(req.case_id, req.result): req for _, req, _ in network_spy}
    assert ("PT-01", "pass") in results
    assert ("PT-02", "fail") in results


def test_send_and_dry_run_together_exit_2(
    inputs: dict[str, Path], network_spy: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """退出码 2：两档互斥（脚本 :390-393）。谁赢都是一种沉默的猜测。"""
    assert report_to_zentao.main(_argv(inputs, "--dry-run", "--send")) == 2
    captured = capsys.readouterr()
    assert "互斥" in captured.err
    assert network_spy == [], "互斥档居然还是发了请求"


def test_send_without_credentials_exit_3(
    inputs: dict[str, Path], network_spy: list[Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """退出码 3：缺凭据是**设计路径**，不是崩溃（脚本 :93-113、:33-34）。"""
    monkeypatch.delenv("ZENTAO_URL", raising=False)
    monkeypatch.delenv("ZENTAO_TOKEN", raising=False)

    code = report_to_zentao.main(_argv(inputs, "--send"))

    captured = capsys.readouterr()
    assert code == 3
    assert "ZENTAO_URL" in captured.err
    assert "ZENTAO_TOKEN" in captured.err
    assert network_spy == []


def test_strict_with_unmapped_case_exit_4(
    inputs: dict[str, Path], network_spy: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """退出码 4：``--strict`` 且有未映射/未跑到的编号（脚本 :446-448）。"""
    code = report_to_zentao.main(_argv(inputs, "--strict"))

    captured = capsys.readouterr()
    assert code == 4
    assert "未映射" in captured.err
    assert network_spy == []


def test_credentials_never_leak_into_payload_or_log(
    inputs: dict[str, Path], network_spy: list[Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``Credentials.__repr__``（脚本 :89-90）的存在意义就是「print 一下也不漏 token」。"""
    monkeypatch.setenv("ZENTAO_URL", "https://zentao.example.com")
    monkeypatch.setenv("ZENTAO_TOKEN", "sk-should-never-appear")

    credentials = report_to_zentao.read_credentials(
        {"ZENTAO_URL": "https://zentao.example.com", "ZENTAO_TOKEN": "sk-should-never-appear"}
    )
    assert "sk-should-never-appear" not in repr(credentials)

    report_to_zentao.main(_argv(inputs, "--send"))
    captured = capsys.readouterr()
    assert "sk-should-never-appear" not in captured.out
    assert "sk-should-never-appear" not in captured.err


def test_missing_input_file_exits_1(tmp_path: Path, network_spy: list[Any]) -> None:
    """退出码 1：输入文件缺失时以 ``SystemExit`` 结束，不是静默空跑（脚本 :164-170）。

    ``load_json`` 抛的是 ``SystemExit("消息")``，直接跑进程时 CPython 会把消息打到 stderr 并以 1 退出；
    但 ``SystemExit.code`` 在进程内读到的就是那串消息本身。所以这里两件事都钉：
    进程内断言「消息带着路径」，另外真起一次子进程断言**解释器看到的退出码就是 1**。
    """
    missing = tmp_path / "nope.json"
    argv = [
        "--case-index",
        str(missing),
        "--junit-xml",
        str(tmp_path / "nope.xml"),
        "--ledger",
        str(tmp_path / "nope-ledger.json"),
        "--case-map",
        str(tmp_path / "nope-map.json"),
    ]
    with pytest.raises(SystemExit) as excinfo:
        report_to_zentao.main(argv)
    assert str(missing) in str(excinfo.value.code), "退出消息里没有说清楚是哪个文件不见了"
    assert network_spy == []

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_child_env(),
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 1, f"文档承诺输入缺失退出码 1，实测 {proc.returncode}：{proc.stderr}"
    assert str(missing) in proc.stderr


def test_unparseable_json_input_exits_1(tmp_path: Path, network_spy: list[Any]) -> None:
    """退出码 1：JSON 坏了也要以 1 结束（脚本 :169-170），而不是抛 ``JSONDecodeError`` 崩掉。"""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    argv = [
        "--case-index",
        str(broken),
        "--junit-xml",
        str(tmp_path / "nope.xml"),
        "--ledger",
        str(tmp_path / "nope-ledger.json"),
        "--case-map",
        str(tmp_path / "nope-map.json"),
    ]
    with pytest.raises(SystemExit) as excinfo:
        report_to_zentao.main(argv)
    assert "不是合法 JSON" in str(excinfo.value.code)
    assert network_spy == []

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_child_env(),
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 1, f"坏 JSON 的退出码应当仍是 1，实测 {proc.returncode}：{proc.stderr}"


def test_send_failure_exit_5(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """退出码 5：某条发送失败即中止（脚本 :440-444）。"""
    monkeypatch.setenv("ZENTAO_URL", "https://zentao.example.com")
    monkeypatch.setenv("ZENTAO_TOKEN", "fake-token")
    monkeypatch.setattr(report_to_zentao, "post_request", lambda *a, **k: (500, "server error"))

    code = report_to_zentao.main(_argv(inputs, "--send"))

    captured = capsys.readouterr()
    assert code == 5
    assert "ERR" in captured.out
    assert "中止" in captured.err
