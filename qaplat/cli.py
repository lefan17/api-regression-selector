"""``python -m qaplat.cli <cmd>`` 的命令行入口。

退出码是这套 CLI 唯一被 CI 消费的接口，因此逐条固定、不做「差不多」：

============  ==========================================================
``run``       原样透传 pytest 的退出码（0 通过 / 1 有失败 / 2 中断 / 5 没收集到用例）
``gate``      0 达标 / 1 未达标 / 2 用法错误——**不用 pytest 的退出码**，因为门禁回答的是
              「这一层能不能放行」，不是「pytest 有没有报错」
``registry``  恒 0（信息查询，覆盖率为空也是有效结论）
``metrics``   恒 0
``report``    恒 0
``drift``     0 无漂移 / 1 有漂移
``tia``       恒 0（信息查询）；选择器自身失败（git / 用例收集）才是 2
============  ==========================================================

``gate --select tia`` 只跑「受本次改动影响」的用例，**默认关闭**：不给 ``--select`` 时
行为与引入 TIA 之前逐字一致（全量）。选不出安全子集（``sel.full``）时同样退回全量。

``--db`` 是全局选项，必须写在子命令之前（``qaplat --db x.db gate --layer smoke``）；
写在其后被 argparse 当成子命令的未知参数并报用法错误（退出码 2）。
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from qaplat import gate, registry, report
from qaplat.gate import LAYERS, Layer, LayerSpec
from qaplat.metrics import MetricsStore, RunRecord, case_outcomes_from_pairs, new_run_id
from qaplat.paths import DEFAULT_DB, EXPORT_SCRIPT, ROOT

#: 门禁未达标的退出码（契约固定）。
EXIT_GATE_FAILED = 1
#: CLI 用法错误（argparse 也用它）。
EXIT_USAGE = 2

EXIT_DRIFT = 1


def _resolve_layer(name: str) -> LayerSpec:
    """层名 → ``LayerSpec``；非法层名抛 ``SystemExit(2)``。

    这里刻意不用 ``argparse`` 的 ``choices``：``choices`` 报错时的提示是 argparse 生成的
    英文长句，而这套 CLI 的使用者是中文环境的开发者与 CI 日志阅读者，一句
    「未知层级 xxx，可选：smoke / pr / nightly / release」更有用。
    """
    try:
        layer = Layer(name)
    except ValueError:
        valid = " / ".join(item.value for item in Layer)
        print(f"未知层级 {name!r}，可选：{valid}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE) from None
    return LAYERS[layer]


def _open_store(db: str | Path) -> MetricsStore:
    store = MetricsStore(db)
    store.migrate()
    return store


def _git_sha() -> str | None:
    """当前 HEAD 短 sha；不在 git 仓库或 git 不可用时返回 ``None``（不抛异常）。

    commit 是度量里最有用的维度（「从哪个 commit 开始变差」），但拿不到也不该让门禁挂掉。
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    sha = (proc.stdout or "").strip()
    return sha if proc.returncode == 0 and sha else None


# --------------------------------------------------------------------------- run


def cmd_run(args: argparse.Namespace) -> int:
    """按层跑 pytest 并原样透传退出码（不落库——`gate` 才落库）。

    刻意不在 ``run`` 里落库：``run`` 的语义是「我想用这层的表达式跑一次，看输出」，
    频繁试跑不该污染度量库。要留痕就用 ``gate``。
    """
    spec = _resolve_layer(args.layer)
    argv = [sys.executable, "-m", "pytest", *gate.build_pytest_args(spec, args.passthrough)]
    print(f"[qaplat] 层={spec.layer.value} -m {spec.marker_expr or '(全量)'}", flush=True)
    print("[qaplat] " + " ".join(argv), flush=True)
    proc = subprocess.run(argv, cwd=str(ROOT), check=False)
    return int(proc.returncode)


# --------------------------------------------------------------------------- gate


def _record_layer_run(store: MetricsStore, spec: LayerSpec, run: gate.PytestRun, *, commit: str | None) -> RunRecord:
    run_id = new_run_id(spec.layer.value)
    record = gate.run_record_from(spec, run, run_id=run_id, commit_sha=commit)
    cases = case_outcomes_from_pairs(run_id, run.cases)
    store.record_run(record, cases)
    return record


def cmd_gate(args: argparse.Namespace) -> int:
    """跑一层 → 落库 → 与阈值比对 → 打印结论。

    落库在比对**之前**：哪怕这一层红灯，数据也必须留下。否则「只有绿灯进库」，
    度量库里永远看不到问题期，flaky 检测也会因为缺少失败样本而永远为空。
    """
    spec = _resolve_layer(args.layer)
    commit = args.commit or _git_sha()
    # 行级判据（v2）要的覆盖率数据在这里定：显式路径写错时必须**在开库之前**就以用法错误
    # 退出，不然会留下一个已建表的空库，让人误以为「这轮跑过了」。
    data_path = _resolve_coverage_data(args.coverage_data) if args.select == "tia" else None
    store = _open_store(args.db)

    print(
        f"[qaplat] 层={spec.layer.value} 表达式={spec.marker_expr or '(全量)'} 阈值={spec.min_pass_rate:.0%}",
        flush=True,
    )
    # 默认（args.select is None）就是原来的全量跑法，extra 原样等于 args.passthrough；
    # ``--select tia`` 才把选中集塞进 extra。extra 在 build_pytest_args 里排在最后，
    # 所以 nodeid 只是「限定 pytest 的用例范围」，不覆盖 -m 表达式。
    extra: Sequence[str] = args.passthrough
    if args.select == "tia":
        try:
            extra = _tia_extra(args, spec, data_path)
        except Exception as exc:  # git / pytest 子进程的失败模式是开放的，统一折成用法错误
            print(f"[qaplat] tia 选择失败：{exc}", file=sys.stderr)
            store.close()
            return EXIT_USAGE
    run = gate.run_layer(spec, extra)
    record = _record_layer_run(store, spec, run, commit=commit)
    result = gate.evaluate(spec, record)

    print(
        f"[qaplat] total={result.total} passed={result.passed} failed={result.failed} "
        f"errors={result.errors} skipped={result.skipped} xfailed={result.xfailed}",
        flush=True,
    )
    print(
        f"[qaplat] pass_rate={result.pass_rate:.1%} duration={result.duration_seconds:.1f}s "
        f"within_budget={result.within_budget} run_id={result.run_id}",
        flush=True,
    )
    if result.violations:
        for violation in result.violations:
            print(f"[qaplat] 违规：{violation}", file=sys.stderr, flush=True)
        print(f"[qaplat] 门禁未达标：{spec.layer.value}", flush=True)
    else:
        print(f"[qaplat] 门禁达标：{spec.layer.value}", flush=True)
    store.close()
    return gate.exit_code_for(result)


# --------------------------------------------------------------------------- tia
#
# ``tia`` 刻意在函数内部 import：它是后加的模块，写在模块顶部会让 run / gate / metrics /
# report 全都依赖它的存在，而「不带 ``--select`` 时行为逐字不变」是这一轮的回归红线。


def _resolve_coverage_data(arg: str | None) -> Path | None:
    """决定行级 TIA（v2）用的 per-test 覆盖率数据；``None`` 表示退回文件级（v1）。

    显式给的路径不存在时**报错退出 2**，不静默退回 v1：调用方既然点名了这份数据，悄悄
    换一套判据只会让人以为行级生效了，然后拿着「文件级选中集」去解释行级效果。
    没给时才走约定：先 ``.tia-coverage``（``pytest --cov-context=test`` 的产物），再
    ``.coverage``；两份都没有就返回 ``None``，并明说退化了——否则日志里看不出这次是行级
    还是文件级。
    """
    if arg:
        path = Path(arg)
        if not path.exists():
            print(f"覆盖率数据不存在：{arg}", file=sys.stderr)
            raise SystemExit(EXIT_USAGE)
        return path
    for candidate in (ROOT / ".tia-coverage", ROOT / ".coverage"):
        if candidate.exists():
            return candidate
    print("[qaplat] tia: 未找到覆盖率数据，退化为文件级策略", flush=True)
    return None


def _pytest_args_from(value: str) -> list[str]:
    """把 ``--pytest-args "-m smoke"`` 这一整串切成 argv。

    这里用「一个字符串」而不是 ``nargs=REMAINDER``：``tia select`` 后面没有位置参数，
    REMAINDER 会把拼错的选项也一并吞进 pytest 参数（静默变成「跑了错的范围」而不是报用法错误）。
    切分交给 ``shlex``，``-k "login and not slow"`` 这类带空格的值才不会被拆散；
    代价是反斜杠会按转义处理，Windows 路径请写正斜杠（本仓统一用 ``/``）。
    """
    return shlex.split(value) if value else []


def _layer_collect_args(spec: LayerSpec) -> list[str]:
    """层 → 与 ``gate`` 同口径的收集参数（只有 ``-m`` 表达式，不带 dump 插件）。

    tia 的分母必须是「这一层真正会跑的用例」：``gate --layer pr`` 只跑
    ``-m smoke or api or platform``，若按全量收集，ratio 的分母会虚高，选中集里还会混进
    该层根本不会执行的用例。
    """
    return ["-m", spec.marker_expr] if spec.marker_expr else []


def _tia_extra(args: argparse.Namespace, spec: LayerSpec, data_path: Path | None) -> list[str]:
    """``gate --select tia`` 的 ``extra``：选中 nodeid + 调用方附加参数。

    ``data_path`` 非空就走行级（v2），为空退回文件级（v1）——两条路径共用同一个
    ``Selection`` 契约，这里不需要分叉。
    ``sel.full`` 为真时**只**返回原 ``passthrough``：全量兜底的含义是「影响面判定不了」，
    这时再叠一层 nodeid 过滤等于把兜底又收窄回去。
    """
    from qaplat import tia

    sel = tia.select_from_git(base=args.base, pytest_args=_layer_collect_args(spec), data_path=data_path)
    _warn_if_index_stale(data_path)
    print(
        f"[qaplat] tia: selected={len(sel.node_ids)}/{sel.total_cases} "
        f"(ratio={sel.ratio:.2%}) full={sel.full} strategy={sel.strategy} reason={sel.reason}",
        flush=True,
    )
    if sel.full:
        return list(args.passthrough)
    return list(sel.node_ids) + list(args.passthrough)


def _warn_if_index_stale(data_path: Path | None) -> None:
    """索引落后于当前 HEAD 时在 stderr 提醒一次；**不改变退出码，也不改选择结果**。

    只在真能判断出「落后」时说话：没有标记文件就保持沉默——每次 select 都输出一句
    「不知道」会把人训练成无视警告。而一旦落后就必须说，因为这类失效**不报错**：
    选择器照常返回一个更小的集合，只是悄悄少了几条用例。
    """
    if data_path is None:
        return
    from qaplat import tia

    try:
        stale, message = tia.index_staleness(data_path)
    except Exception:  # 旁路提示自己坏掉，不该让一次选择失败
        return
    if stale:
        print(f"[qaplat] 警告: {message}", file=sys.stderr)
        print("[qaplat] 提示: 重采覆盖率后跑 `tia capture`，可让索引重新对齐 HEAD", file=sys.stderr)


def cmd_tia_select(args: argparse.Namespace) -> int:
    """打印「本次改动该跑哪些用例」，不跑 pytest。

    恒返回 0：一条都没选中（例如改动全落在没有被任何用例引用的源码上）也是有效结论，
    选择器会自己给出 ``full`` 与 ``reason``。只有选择器**自身**失败（git 不可用、用例收集
    崩了）才返回 2——那种情况下的「0 条」是假结论，不能当信息查询的答案返回。
    """
    from qaplat import tia

    data_path = _resolve_coverage_data(args.coverage_data)
    try:
        sel = tia.select_from_git(
            base=args.base,
            pytest_args=_pytest_args_from(args.pytest_args),
            include_worktree=not args.no_worktree,
            data_path=data_path,
        )
    except Exception as exc:  # git / pytest 子进程的失败模式是开放的，统一折成用法错误
        print(f"[qaplat] tia 选择失败：{exc}", file=sys.stderr)
        return EXIT_USAGE
    _warn_if_index_stale(data_path)
    print(tia.render_json(sel) if args.json else tia.render_text(sel))
    return 0


def cmd_tia_capture(args: argparse.Namespace) -> int:
    """把当前 HEAD 记到索引旁边，供下次 ``tia select`` 判断新鲜度。

    只写标记、不跑采集：采集本身要 140–190s，交给调用方（CI 或本地脚本）决定怎么跑。
    覆盖率数据文件不存在时返回 2——在这里静默通过，等于让人以为索引是新鲜的。
    """
    from qaplat import tia

    data_path = Path(args.coverage_data)
    if not data_path.is_file():
        print(f"[qaplat] tia capture: 覆盖率数据不存在：{data_path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        marker = tia.write_index_head(data_path)
    except RuntimeError as exc:
        print(f"[qaplat] tia capture: 写 HEAD 标记失败：{exc}", file=sys.stderr)
        return EXIT_USAGE
    payload = tia.read_index_head(data_path) or {}
    head = payload.get("head", "")
    print(f"[qaplat] tia capture: HEAD={head[:12]} → {marker}")
    return 0


# --------------------------------------------------------------------------- registry


def _emit_coverage(coverage: Sequence[registry.OperationCoverage], *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "operation_id": c.operation_id,
                        "method": c.method,
                        "path": c.path,
                        "covered_by": list(c.covered_by),
                    }
                    for c in coverage
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    covered = sum(1 for c in coverage if c.covered_by)
    print(
        f"[qaplat] operation 总数={len(coverage)} 已覆盖={covered} 未覆盖={len(coverage) - covered} "
        f"覆盖率={registry.coverage_ratio(coverage):.1%}"
    )
    for item in coverage:
        mark = "OK  " if item.covered_by else "GAP "
        if item.covered_by:
            files = registry.owner_files(item.covered_by)
            detail = f"{len(item.covered_by)} 条用例 / {len(files)} 个文件"
        else:
            detail = "无归属"
        print(f"  {mark} {item.method:6s} {item.path:52s} {item.operation_id:44s} {detail}")


def cmd_registry_build(args: argparse.Namespace) -> int:
    spec_path = args.spec or registry.DEFAULT_SPEC_PATH
    operations = registry.load_operations(spec_path)
    cases = registry.collect_cases(args.passthrough)
    coverage = registry.build_coverage(cases, operations)
    out = registry.dump_coverage_json(coverage, len(operations), len(cases), args.json)
    print(f"[qaplat] 契约 {spec_path}：{len(operations)} 个 operation；收集用例 {len(cases)} 条")
    print(f"[qaplat] 覆盖矩阵已写出：{out}")
    _emit_coverage(coverage, as_json=False)
    return 0


def cmd_registry_gaps(args: argparse.Namespace) -> int:
    """只打印未覆盖的 operationId。

    覆盖矩阵文件不存在时**现场重建**而不是报错退出：``gaps`` 是最常被单独调用的命令
    （「现在还有哪些接口没测」），要求先跑一次 ``build`` 属于把内部实现泄漏给用户。
    """
    coverage = registry.load_coverage_matrix(args.json)
    if not coverage:
        operations = registry.load_operations(args.spec or registry.DEFAULT_SPEC_PATH)
        coverage = registry.build_coverage(registry.collect_cases(args.passthrough), operations)
    gaps = registry.uncovered(coverage)
    covered = len(coverage) - len(gaps)
    print(
        f"[qaplat] operation 总数={len(coverage)} 已覆盖={covered} 未覆盖={len(gaps)} "
        f"覆盖率={registry.coverage_ratio(coverage):.1%}"
    )
    if not gaps:
        print("[qaplat] 无缺口：每个 operation 都有至少一个用例归属")
        return 0
    for item in gaps:
        print(f"  GAP  {item.method:6s} {item.path:52s} {item.operation_id}")
    return 0


# --------------------------------------------------------------------------- metrics


def cmd_metrics_show(args: argparse.Namespace) -> int:
    store = _open_store(args.db)
    runs = store.recent_runs(limit=args.last)
    print(f"[qaplat] 最近 {len(runs)} 轮（库：{args.db}）")
    if not runs:
        print("  （无记录。先跑一次 `python -m qaplat.cli gate --layer smoke`）")
        store.close()
        return 0
    print(
        f"  {'run_id':46s} {'层':8s} {'通过率':>8s} {'总':>5s} {'通过':>5s} {'失败':>5s} {'错误':>5s} "
        f"{'跳过':>5s} {'xfail':>6s} {'耗时s':>8s} {'结论':8s}"
    )
    for run in runs:
        print(
            f"  {run.run_id:46s} {run.layer:8s} {run.pass_rate:7.1%} {run.total:5d} {run.passed:5d} "
            f"{run.failed:5d} {run.errors:5d} {run.skipped:5d} {run.xfailed:6d} {run.duration_seconds:8.1f} {run.outcome:8s}"
        )

    print("[qaplat] 分层聚合")
    for layer, n, rate, dur in store.layer_summary(last_n_runs=max(args.last, 20)):
        print(f"  {layer:8s} 轮次={n:3d} 平均通过率={rate:7.1%} 平均耗时={dur:8.1f}s")

    flaky = store.flaky_cases(last_n_runs=args.last)
    print(f"[qaplat] 不稳定用例（最近 {args.last} 轮，既通过过也失败过）：{len(flaky)} 条")
    for case_id, passes, appearances in flaky[:20]:
        print(f"  FLAKY {case_id}  通过 {passes}/{appearances} 轮")
    store.close()
    return 0


def cmd_metrics_flaky(args: argparse.Namespace) -> int:
    store = _open_store(args.db)
    flaky = store.flaky_cases(last_n_runs=args.last)
    print(f"[qaplat] 最近 {args.last} 轮内跨轮次不稳定用例：{len(flaky)} 条")
    for case_id, passes, appearances in flaky:
        failures = appearances - passes
        print(f"  FLAKY {case_id}  通过 {passes} 轮 / 失败 {failures} 轮（共出现 {appearances} 轮）")
    if not flaky:
        print("  （无。要么每轮都稳定通过，要么失败样本还不够——单轮全绿时该结果不代表用例稳定）")
    store.close()
    return 0


# --------------------------------------------------------------------------- report


def cmd_report(args: argparse.Namespace) -> int:
    store = _open_store(args.db)
    coverage = registry.load_coverage_matrix(args.coverage_json)
    result = report.build_report(store, args.out, coverage=coverage if coverage else None)
    print(f"[qaplat] 报表已生成：{result.index}")
    print(
        f"[qaplat] 轮次={result.runs_count} 用例级记录={result.cases_count} "
        f"接口覆盖={result.covered_total}/{result.operations_total} 真实回归={result.regressions}"
    )
    for page in result.pages:
        print(f"  {page.name}")
    store.close()
    return 0


# --------------------------------------------------------------------------- drift


def cmd_drift_record(args: argparse.Namespace) -> int:
    """跑契约漂移检查（``scripts/export_openapi.py --check``）并把结论落库。

    漂移判定完全交给那个脚本，CLI 不重复实现比对逻辑：两处实现迟早会分叉，而「哪个才是
    标准答案」这个问题不该在漂移门禁上出现。``changed`` 从脚本的 ``[漂移]`` 行里取文件名。
    """
    script = args.script or EXPORT_SCRIPT
    if not Path(script).exists():
        print(f"契约检查脚本不存在：{script}", file=sys.stderr)
        return EXIT_USAGE
    # 子进程走管道时 Windows 上的 Python 默认按 cp936 编码 stdout，而这里按 utf-8 解码，
    # 脚本打印的中文（``[漂移]`` 前缀、文件名）会变成替换字符，``_changed_files`` 随之
    # 认不出漂移行、只留下「未知」。显式指定子进程编码才是唯一可靠的做法。
    child_env = dict(os.environ)
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=str(ROOT),
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    drifted = proc.returncode != 0
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    print(stdout.rstrip())
    if stderr.strip():
        print(stderr.rstrip(), file=sys.stderr)

    changed = _changed_files(stdout + "\n" + stderr, drifted)
    store = _open_store(args.db)
    store.record_drift(args.commit, drifted, changed)
    history = store.contract_drift_history(limit=1)
    print(
        f"[qaplat] 已记录漂移检查 commit={args.commit} drifted={drifted} "
        f"变化文件={list(changed)} 历史条数={len(history)}"
    )
    store.close()
    if drifted:
        print("[qaplat] 契约漂移：入库基线文件与代码重新导出的结果不一致", file=sys.stderr)
        return EXIT_DRIFT
    print("[qaplat] 契约一致，无漂移")
    return 0


def _changed_files(text: str, drifted: bool) -> tuple[str, ...]:
    """从脚本输出里取被报漂移的文件名（去重、稳定排序）。"""
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[漂移]"):
            remainder = stripped[len("[漂移]") :].strip()
            token = remainder.split(" ", 1)[0] if remainder else ""
            if token:
                names.add(token)
    if not names and drifted:
        names.add("(未知，见脚本输出)")
    return tuple(sorted(names))


# --------------------------------------------------------------------------- 解析


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qaplat",
        description="测试平台层：分层门禁 / 度量 / 覆盖矩阵 / 契约漂移",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        metavar="PATH",
        help=f"度量数据库路径（默认 {DEFAULT_DB}）",
    )
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    p_run = sub.add_parser("run", help="按层拼 -m 表达式并调用 pytest（退出码原样透传）")
    p_run.add_argument("--layer", required=True, metavar="LAYER", help="smoke | pr | nightly | release")
    p_run.add_argument("passthrough", nargs=argparse.REMAINDER, help="-- 之后的参数原样传给 pytest")
    p_run.set_defaults(func=cmd_run)

    p_gate = sub.add_parser("gate", help="跑一层并落库，再与阈值比对（0 达标 / 1 未达标 / 2 用法错误）")
    p_gate.add_argument("--layer", required=True, metavar="LAYER", help="smoke | pr | nightly | release")
    p_gate.add_argument("--commit", default=None, metavar="SHA", help="记录用的 commit（默认取当前 HEAD）")
    p_gate.add_argument(
        "--select",
        choices=["tia"],
        default=None,
        metavar="MODE",
        help="只跑受本次改动影响的用例（tia）；不给则跑该层全量（默认关闭）",
    )
    p_gate.add_argument(
        "--base",
        default="HEAD~1",
        metavar="REF",
        help="--select tia 的变更基准（git diff base...HEAD，默认 HEAD~1）",
    )
    p_gate.add_argument(
        "--coverage-data",
        default=None,
        metavar="PATH",
        help="--select tia 用的 per-test 覆盖率数据（.coverage 文件）；给了就走行级，不给退回文件级",
    )
    p_gate.add_argument("passthrough", nargs=argparse.REMAINDER, help="-- 之后的参数原样传给 pytest")
    p_gate.set_defaults(func=cmd_gate)

    p_reg = sub.add_parser("registry", help="用例 ↔ operationId 覆盖矩阵")
    reg_sub = p_reg.add_subparsers(dest="registry_command", metavar="<子命令>")
    p_build = reg_sub.add_parser("build", help="生成覆盖矩阵")
    p_build.add_argument("--json", default=str(registry.COVERAGE_JSON), metavar="PATH", help="矩阵输出路径")
    p_build.add_argument("--spec", default=None, metavar="PATH", help="OpenAPI 基准文件")
    p_build.add_argument("passthrough", nargs=argparse.REMAINDER, help="-- 之后的参数原样传给 pytest")
    p_build.set_defaults(func=cmd_registry_build)
    p_gaps = reg_sub.add_parser("gaps", help="只打印未被任何用例覆盖的 operationId")
    p_gaps.add_argument("--json", default=str(registry.COVERAGE_JSON), metavar="PATH", help="矩阵文件路径")
    p_gaps.add_argument("--spec", default=None, metavar="PATH", help="OpenAPI 基准文件")
    p_gaps.add_argument("passthrough", nargs=argparse.REMAINDER, help="-- 之后的参数原样传给 pytest")
    p_gaps.set_defaults(func=cmd_registry_gaps)
    p_reg.set_defaults(func=_help_handler(p_reg))

    p_metrics = sub.add_parser("metrics", help="运行度量查询")
    m_sub = p_metrics.add_subparsers(dest="metrics_command", metavar="<子命令>")
    p_show = m_sub.add_parser("show", help="打印最近 N 轮的通过率/耗时/不稳定用例")
    p_show.add_argument("--last", type=int, default=10, metavar="N", help="最近 N 轮（默认 10）")
    p_show.set_defaults(func=cmd_metrics_show)
    p_flaky = m_sub.add_parser("flaky", help="打印跨轮次不稳定用例清单")
    p_flaky.add_argument("--last", type=int, default=10, metavar="N", help="最近 N 轮（默认 10）")
    p_flaky.set_defaults(func=cmd_metrics_flaky)
    p_metrics.set_defaults(func=_help_handler(p_metrics))

    p_report = sub.add_parser("report", help="生成静态 HTML 报表（含契约漂移页）")
    p_report.add_argument("--out", default=str(report.DEFAULT_REPORT_DIR), metavar="DIR", help="输出目录")
    p_report.add_argument(
        "--coverage-json", default=str(registry.COVERAGE_JSON), metavar="PATH", help="覆盖矩阵 JSON 路径"
    )
    p_report.set_defaults(func=cmd_report)

    p_drift = sub.add_parser("drift", help="契约漂移门禁")
    d_sub = p_drift.add_subparsers(dest="drift_command", metavar="<子命令>")
    p_drift_rec = d_sub.add_parser("record", help="跑漂移检查并落库（0 无漂移 / 1 有漂移）")
    p_drift_rec.add_argument("--commit", required=True, metavar="SHA", help="本次检查对应的 commit")
    p_drift_rec.add_argument("--script", default=None, metavar="PATH", help="漂移检查脚本路径")
    p_drift_rec.set_defaults(func=cmd_drift_record)
    p_drift.set_defaults(func=_help_handler(p_drift))

    p_tia = sub.add_parser("tia", help="变更 → 用例选择：只挑受本次改动影响的用例")
    tia_sub = p_tia.add_subparsers(dest="tia_command", metavar="<子命令>")
    p_tia_select = tia_sub.add_parser("select", help="按 git diff 算出该跑哪些用例（只打印，不跑 pytest）")
    p_tia_select.add_argument(
        "--base", default="HEAD~1", metavar="REF", help="变更基准（git diff base...HEAD，默认 HEAD~1）"
    )
    p_tia_select.add_argument("--json", action="store_true", help="输出机器可解析的 JSON（字段固定，供 CI 解析）")
    p_tia_select.add_argument(
        "--no-worktree", action="store_true", help="只看已提交改动，不把工作区改动与未跟踪文件算进来"
    )
    p_tia_select.add_argument(
        "--pytest-args",
        default="",
        metavar="ARGS",
        help='收集用例时附加的 pytest 参数，整串用引号包住（如 "-m smoke"）',
    )
    p_tia_select.add_argument(
        "--coverage-data",
        default=None,
        metavar="PATH",
        help="per-test 覆盖率数据（.coverage 文件）；给了就走行级 TIA，不给退回文件级",
    )
    p_tia_select.set_defaults(func=cmd_tia_select)
    p_tia_capture = tia_sub.add_parser(
        "capture",
        help="采集后记录 HEAD，让 tia select 能判断索引是否过期（不跑 pytest，只写标记）",
    )
    p_tia_capture.add_argument(
        "--coverage-data",
        default=".tia-coverage",
        metavar="PATH",
        help="刚采集出的覆盖率数据文件（默认 .tia-coverage），HEAD 会记到它旁边的 <PATH>.head",
    )
    p_tia_capture.set_defaults(func=cmd_tia_capture)
    p_tia.set_defaults(func=_help_handler(p_tia))

    return parser


def _print_help(parser: argparse.ArgumentParser) -> None:
    parser.print_help()


def _help_handler(parser: argparse.ArgumentParser) -> Callable[[argparse.Namespace], int]:
    """``python -m qaplat.cli registry``（不带子命令）时打印帮助并返回 0，而不是抛异常。

    包成闭包是为了满足 mypy 的 ``func-returns-value``：直接把 ``_print_help(...)`` 写进
    lambda 会被判成「用了一个没有返回值的函数的值」。
    """

    def handler(_args: argparse.Namespace) -> int:
        _print_help(parser)
        return 0

    return handler


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8。

    Windows 上非交互管道默认按 cp936 编码，帮助文本里的 ``↔`` 与用例中文名会直接让
    ``print`` 抛 ``UnicodeEncodeError``——一个查询命令因为帮助文字挂掉，是最没必要的一种失败。
    已有编码时附加 ``errors="replace"`` 作为兜底，宁可显示成 ``?`` 也不要中断。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口；返回进程退出码。"""
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return EXIT_USAGE
    return int(args.func(args))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
