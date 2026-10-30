"""TIA（变更 → 测试选择）：三级保守策略 + 行级覆盖映射。

契约基准：``docs/tia-plan.md`` §3（三级保守策略）与 §4.1（接口签名）。本模块只回答
「这次改动该跑哪些用例」，不跑用例、不改门禁判据——真正的执行由 ``qaplat.gate`` 接收
选中的 nodeid 完成。

设计取向只有一条：**宁可多跑，不可漏跑**。每一级判据都先问「这个变更的影响面说清楚了
吗」，说不清就退化为全量（``Selection.full=True``），绝不用一个看起来更小的集合去猜。
漏跑的代价是「门禁绿了、回归漏了」，比多跑十几分钟贵得多。

## 为什么有两套策略（v1 文件级 / v2 行级）

v1（:func:`select`）依赖路径字面量：改 ``mock_server/routers.py`` 时读该文件、提取
``/api...`` 字面量，再与用例的 ``referenced_paths`` 求交集。它在「改测试文件」这一档
非常好用，但对被测源码存在一个**实测失效**：本仓路由写成 ``@router.get("/healthz")``
这样的子路径，完整的 ``/api/v1`` 前缀是在 ``mock_server/app.py`` 的 ``include_router``
里拼出来的，于是 ``mock_server/routers.py`` 能提取出的路径是 **0 条**——按 v1 的兜底
规则只能全量，TIA 对被测源码就没有收益。

v2（:func:`select_by_lines`）因此改用 coverage 的 context 数据：用
``pytest --cov-context=test`` 跑一遍，每条用例执行过的行都会带上 ``<nodeid>|run`` 这个
context；反过来按「变更行 → 执行过它的用例」即可做到行级，还能区分同一文件里的不同
路由。零新依赖（coverage / pytest-cov 本来就在 requirements.txt 里）。

两套策略共用 :class:`Selection`、:func:`changed_files`、:func:`render_text`、
:func:`render_json`。唯一一处刻意的差异：v2 的关键文件清单**去掉 ``qaplat/``**——平台层
源码恰好是 coverage 数据覆盖最完整、行级映射最有意义的部分，把它继续判成全量等于自废
v2（见 :data:`_LINES_FULL_PREFIXES`）。
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from qaplat import registry
from qaplat.paths import ROOT
from qaplat.registry import read_referenced_paths

#: 关键文件（目录前缀）：命中即全量，取值来自 tia-plan.md §3.1 的清单。
#:
#: 用「前缀」而不是 glob：``data/`` 这类目录前缀天然覆盖任意深度；``requirements``
#: 前缀一个就覆盖 ``requirements.txt`` / ``requirements-perf.txt`` / ``requirements.lock``
#: 三种写法（也顺带覆盖 ``requirements-notes.md`` 这种罕见文件名——方向是「多跑」，
#: 不是漏跑，所以这个代价可以接受）。稳定排序，保证输出与断言可复现。
ALWAYS_FULL_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        {
            "data/",
            "docs/openapi.json",
            "qaplat/",
            "requirements",
            "scripts/",
            "schemas/",
            "traffic-replay/",
        }
    )
)

#: 关键文件名：任意层级命中即全量。``conftest.py`` 与 pytest 配置改变的是**所有**用例的
#: 运行条件（夹具、marker、addopts、testpaths），与它落在哪个目录无关，所以按 basename
#: 比对而不是按路径前缀。
ALWAYS_FULL_NAMES: frozenset[str] = frozenset({"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg"})

#: v1 的测试目录前缀：变更文件落在这里时只跑该文件自己的用例。
_TESTCASES_PREFIX = "testcases/"

#: v2 的关键前缀：与 v1 相同，唯独去掉 ``qaplat/``。
#:
#: 理由：v1 把 ``qaplat/**`` 判全量，是因为它只能靠路径字面量猜影响面，而平台层代码里
#: 根本没有``/api`` 字面量；v2 走 coverage 行数据，平台层正是映射最准的部分。如果继续
#: 判全量，改一行报表代码就要跑全仓，v2 等于没用。
_LINES_FULL_PREFIXES: tuple[str, ...] = tuple(prefix for prefix in ALWAYS_FULL_PREFIXES if prefix != "qaplat/")

#: 变更源码 → 接口路径字面量的提取器签名。默认实现是
#: :func:`qaplat.registry.read_referenced_paths`（会读文件）；测试可注入替身，让
#: :func:`select` 全程零 IO。
PathReader = Callable[[str], Sequence[str]]

#: 单次 git 调用的墙钟上限（秒）。卡住时按失败处理——读 diff 挂死比报错更难排查。
_GIT_TIMEOUT_SECONDS = 120

#: ``git diff -U0`` 的 hunk 头：``@@ -旧起点,旧行数 +新起点,新行数 @@``。行数省略时是 1。
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)

#: ``--cov-context=test`` 写入的 context 后缀（``<nodeid>|run`` 的 ``|`` 之后那段）。
#: 实测 161 个 context 只有这三种后缀；只在**后缀落在这个集合里**时才切，避免误伤参数化
#: id 里自带的 ``|``（如 ``test_x[a|b]``）。
_CONTEXT_STAGES: frozenset[str] = frozenset({"run", "setup", "teardown"})

#: 空 context 的哨兵值：该行在**模块导入期**就执行了（当时没有任何用例在跑），因此不属于
#: 任何用例。行级策略把「变更行含空 context」判成全量兜底（理由见 :func:`select_by_lines`）。
#: 用空串而不是另造对象，是为了让索引的类型保持契约里的 ``tuple[str, ...]``。
_MODULE_LEVEL = ""


@dataclass(frozen=True)
class ChangedHunk:
    """一个变更文件里的新增/修改行号（``git diff -U0`` 的 ``+`` 侧）。

    ``lines`` 为空是**有意义的信号**，不是「没变化」：纯删除、二进制、纯重命名、未跟踪
    文件都落在这里。行级策略必须把它当成「影响面未知」全量兜底——没有目标行不等于没有
    影响。
    """

    path: str
    lines: tuple[int, ...]


@dataclass(frozen=True)
class Selection:
    """一次 TIA 判定的完整结果（纯数据，不含任何 IO 句柄）。

    字段名是 :func:`render_json` 对外契约的一部分，改名前先改 tia-plan.md §4.1。
    ``matched_files`` / ``matched_paths`` 在 v1 里分别对应「步骤②命中的测试文件」与
    「步骤③命中的接口路径」；v2 不做接口路径匹配，因此 ``matched_paths`` 恒为空，
    ``matched_files`` 记录的是**选中用例所在的测试文件**（对读日志的人更有用）。
    """

    node_ids: tuple[str, ...]
    full: bool
    reason: str
    changed_files: tuple[str, ...]
    total_cases: int
    matched_files: tuple[str, ...]
    matched_paths: tuple[str, ...]
    #: 本次用了哪一档判据：``full``（全量兜底）/ ``files``（v1 文件级）/ ``lines``（v2 行级）。
    #: 有默认值是为了让「旧的三档构造方式」仍然能写出 Selection。
    strategy: str = "files"

    @property
    def ratio(self) -> float:
        """选中占比 = ``len(node_ids) / total_cases``；一条用例都没有时返回 0.0。

        除以 0 没有意义，但这里不抛异常：空用例库是**已经发生的状态**（例如收集失败后
        上层仍要打印一份结果），让报表层崩掉只会把「用例库空了」伪装成「tia 代码坏了」。
        """
        if self.total_cases <= 0:
            return 0.0
        return len(self.node_ids) / self.total_cases


def _normalize_path(raw: str) -> str:
    """把一个路径归一化成「仓库相对、``/`` 分隔」的形态。

    三个来源的路径写法不一致，必须在这里对齐，否则「改了这个文件」和「这个文件里的行」
    永远对不上：

    * git 输出用 ``/``（即使 Windows）；
    * Windows 侧调用方、测试、用户手敲参数常用 ``\\``；
    * coverage 存的是绝对路径，需要剥掉仓库根前缀。

    只做字符串处理、不碰磁盘（``resolve()`` 会查文件系统，这里刻意不用）。
    """
    text = raw.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    root_posix = ROOT.as_posix().rstrip("/")
    prefix = f"{root_posix}/"
    if text.lower().startswith(prefix.lower()):
        text = text[len(prefix) :]
    return text


def _file_of(node_id: str) -> str:
    """nodeid 的文件部分（``path::name`` → ``path``），已归一化。"""
    return _normalize_path(node_id.split("::", 1)[0])


def _basename(path: str) -> str:
    """路径最后一段（归一化后 ``/`` 分隔，直接按 ``/`` 切）。"""
    return path.rsplit("/", 1)[-1]


def _is_always_full(path: str, *, prefixes: Sequence[str] = ALWAYS_FULL_PREFIXES) -> bool:
    """该路径是否命中「变更即全量」清单。

    ``prefixes`` 可替换，是给 v2 用的：行级策略要排除 ``qaplat/``（见
    :data:`_LINES_FULL_PREFIXES`），但文件名维度（conftest / pytest 配置）两档一致。
    """
    if _basename(path) in ALWAYS_FULL_NAMES:
        return True
    return any(path == prefix or path.startswith(prefix) for prefix in prefixes)


def _tail(text: str, lines: int = 12, width: int = 200) -> str:
    """取输出的最后几行（每行截到 ``width`` 字符）。

    git 的报错可能很长（多文件失败时一个文件一段），异常信息里只需要「末尾发生了什么」，
    所以从尾部截。空输出返回「（空）」，避免异常里出现一段看不懂的空白。
    """
    kept = [line for line in (text or "").splitlines() if line.strip()][-lines:]
    if not kept:
        return "（空）"
    return "\n".join(line[:width] for line in kept)


def _run_git(args: Sequence[str], cwd: Path, *, timeout: float = _GIT_TIMEOUT_SECONDS) -> str:
    """在 ``cwd`` 里跑一条 git 命令并返回 stdout；任何失败都抛 :class:`RuntimeError`。

    为什么失败不返回空串：``changed_files`` 一旦「成功」地返回空元组，上层会读成
    「本次没有变更，用例全都不用跑」——这正是 TIA 最危险的失效模式（git 不在 PATH、
    base 写错、不在仓库里、权限问题，任何一个原因都会变成「静默跳过全部门禁」）。
    所以这里把命令、cwd、退出码、stdout 与 stderr 的尾部一起抛出来，让人一眼看出是
    哪一类失效，而不是去猜。
    """
    # ``-c core.quotepath=false``：git 默认把非 ASCII 路径转义成八进制（``"\345\256\214..."``），
    # 于是 ``docs/TIA完全入门.md`` 会变成一串 ``"docs/TIA/345/256/214/..."`` —— 既匹配不上
    # coverage 索引里的真实路径，也匹配不上任何用例路径，最后把「新建一篇中文名文档」
    # 误判成「影响面未知」→ 全量兜底。关掉它，路径才是磁盘上那一串。
    # ``-c`` 必须写在子命令**之前**才对这一条 git 调用生效，所以拼在 argv 最前面。
    argv = ["git", "-c", "core.quotepath=false", *args]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"无法执行 git（命令: {argv!r}，cwd: {cwd}）：{type(exc).__name__}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git 超时（{timeout:g}s，命令: {argv!r}，cwd: {cwd}）") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            "git 读取变更失败，TIA 不能在这种状态下判断影响面：\n"
            f"  命令: {' '.join(argv)}\n"
            f"  cwd: {cwd}\n"
            f"  returncode: {proc.returncode}\n"
            f"  stdout 尾部:\n{_tail(proc.stdout or '')}\n"
            f"  stderr 尾部:\n{_tail(proc.stderr or '')}"
        )
    return proc.stdout or ""


def _relative_to_root(measured: str, root: Path) -> str | None:
    """把 coverage 记录的文件路径变成仓库相对路径；落在 ``root`` 之外返回 None。

    实现用 ``Path(...).resolve().relative_to(root)``：coverage 存的是绝对路径（``D:\\...``
    形态，``measured_files()`` 原样返回），而 git / nodeid 用仓库相对路径，只有先把两边
    都落成绝对路径再相减，比较结果才不受分隔符与大小写写法影响。

    ``relative_to`` 失败（仓库外的文件，例如 site-packages 或别的仓）就返回 None 由调用方
    跳过——那是**正常情况**，不是错误，不该让整份索引读不出来。
    """
    try:
        relative = Path(measured).resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    return relative.as_posix()


def _new_lines_from_diff(patch: str) -> set[int]:
    """从一段 ``-U0`` 的 diff 文本里抠出**新文件**侧的行号。

    hunk 头 ``@@ -a,b +c,d @@`` 里 ``+c,d`` 表示新文件第 ``c`` 行起共 ``d`` 行；
    ``d == 0`` 是「只删不加」，没有目标行（删除的影响由「命中为空 → 全量」兜住，
    不在这里编造行号）。
    """
    lines: set[int] = set()
    for match in _HUNK_RE.finditer(patch):
        start = int(match.group(3))
        count = int(match.group(4) or 1)
        if count <= 0:
            continue
        lines.update(range(start, start + count))
    return lines


def _full(changed: tuple[str, ...], total: int, reason: str, strategy: str) -> Selection:
    """构造「全量兜底」的 Selection：不选任何具体 nodeid，由调用方跑整层。

    ``full=True`` 时 ``node_ids`` 恒为空——契约里「全量」的语义是「等价于不带 --select」，
    把整层用例再抄一份进 node_ids 只会让 JSON 变大，还会诱导调用方把 636 个 nodeid 拼进
    命令行。
    """
    return Selection(
        node_ids=(),
        full=True,
        reason=reason,
        changed_files=changed,
        total_cases=total,
        matched_files=(),
        matched_paths=(),
        strategy=strategy,
    )


def changed_files(
    base: str = "HEAD~1",
    *,
    include_worktree: bool = True,
    cwd: Path | None = None,
) -> tuple[str, ...]:
    """本次改动涉及的仓库相对路径：committed（``base...HEAD``）+ 可选的工作区/未跟踪。

    三条命令是契约固定的（tia-plan.md §4.1）：

    * ``git diff --name-only <base>...HEAD``；
    * ``git diff --name-only HEAD``（只读工作区与暂存区里相对 HEAD 的变化）；
    * ``git ls-files --others --exclude-standard``（未跟踪、但不被 .gitignore 忽略的新文件）。

    用**三点** ``...`` 而不是两点 ``..``：PR 门禁关心的是「本分支相对 merge-base 改了
    什么」；``..`` 会把 base 分支自己走远的那部分也算进来，变更集于是随 base 的推进而
    抖动。

    ``include_worktree`` 默认开——本地手动跑 TIA 时最常看的正是还没提交的改动；CI 里应
    关掉，否则构建机上残留的生成物会被当成业务变更（这就是 CLI ``--no-worktree`` 的来由）。

    结果去重后**稳定排序**：同一份 diff 必须产出同一份 tuple，报表、缓存与断言才可 diff。
    git 本身的失败由 :func:`_run_git` 抛 :class:`RuntimeError`，绝不静默返回空元组。
    """
    workdir = Path(cwd) if cwd is not None else ROOT
    found: set[str] = set()
    found.update(line.strip() for line in _run_git(["diff", "--name-only", f"{base}...HEAD"], workdir).splitlines())
    if include_worktree:
        found.update(line.strip() for line in _run_git(["diff", "--name-only", "HEAD"], workdir).splitlines())
        found.update(
            line.strip() for line in _run_git(["ls-files", "--others", "--exclude-standard"], workdir).splitlines()
        )
    found.discard("")
    return tuple(sorted(found))


def changed_hunks(
    base: str = "HEAD~1",
    *,
    include_worktree: bool = True,
    cwd: Path | None = None,
) -> tuple[ChangedHunk, ...]:
    """本次改动落在新文件里的行号，按文件归组（``git diff -U0`` 的 ``+`` 侧）。

    为什么必须 ``-U0``：默认 3 行上下文会把「没改的行」也算成变更行，命中面被无谓放大，
    行级精度直接退化；行级策略要的恰恰是**真正新增/修改的那几行**。

    为什么删除行不产生目标行：删除意味着「旧行为没了」，新文件里没有对应行可以映射；
    这类变更会由「命中为空 → 全量兜底」兜住（见 :func:`select_by_lines`），而不是在这里
    编一个不存在行号去误命中。

    为什么逐文件再调一次 ``git diff``，而不是解析全量 diff 的 ``+++ b/...`` 头：路径里
    带空格、非 ASCII 或引号时 git 会做 C 风格转义，解析头部要写一套反转义且容易错；
    这层多起几个子进程是毫秒级成本，换来的是「路径永远来自 ``--name-only``，与
    :func:`changed_files` 逐字一致」。

    ``lines`` 为空表示「文件确实变了，但拿不到目标行」（纯删除、二进制、纯重命名、
    未跟踪文件）——行级策略把它当「影响面未知」全量兜底。
    """
    workdir = Path(cwd) if cwd is not None else ROOT
    merged: dict[str, set[int]] = {}
    for raw in changed_files(base, include_worktree=include_worktree, cwd=workdir):
        path = _normalize_path(raw)
        if not path:
            continue
        target = merged.setdefault(path, set())
        target.update(_new_lines_from_diff(_run_git(["diff", "-U0", f"{base}...HEAD", "--", raw], workdir)))
        if include_worktree:
            target.update(_new_lines_from_diff(_run_git(["diff", "-U0", "HEAD", "--", raw], workdir)))
    return tuple(ChangedHunk(path=path, lines=tuple(sorted(merged[path]))) for path in sorted(merged))


def _context_node_id(context: str) -> str | None:
    """把一个 coverage context 还原成 nodeid；空 context 还原成模块级哨兵；其余返回 None。

    ``<nodeid>|run`` → ``nodeid``；空串 → :data:`_MODULE_LEVEL`（该行在 import 期执行，
    不归属任何用例，但必须留下痕迹）；既不是空串也不是「nodeid + 已知阶段」的形态
    （``--cov-context=dynamic`` 写的 ``zeta`` 之类）→ None，直接丢弃：它不是用例，留着会
    让命中集合里混进永远匹配不上 ``CaseInfo`` 的字符串。
    """
    if not context.strip():
        return _MODULE_LEVEL
    head, separator, suffix = context.rpartition("|")
    text = head if separator and suffix in _CONTEXT_STAGES and head.strip() else context
    if "::" not in text:
        return None
    return text.strip()


def load_context_index(
    data_path: Path,
    *,
    root: Path | None = None,
) -> dict[str, dict[int, tuple[str, ...]]]:
    """把 ``.coverage`` 读成「仓库相对路径 → {行号: (nodeid, ...)}」的倒排索引。

    三个必须处理的细节，缺一个都会让行级映射悄悄失效：

    1. **context 不是裸 nodeid**：``--cov-context=test`` 写入的是 ``<nodeid>|<阶段>``，
       阶段实测只有 ``run`` / ``setup`` / ``teardown`` 三种。只在**后缀确实属于这三种**
       时才按最后一个 ``|`` 切；参数化 id 里可能自带 ``|``（``test_x[a|b]``），无条件
       ``split("|")[0]`` 会把 id 切坏、导致永远匹配不上。``setup`` / ``teardown`` 同样
       算命中——那是该用例的夹具执行了这一行，它确实依赖这行代码。
    2. **空 context 是模块级代码的标记**：import 期执行的行 context 是空串（当时没有
       任何用例在跑），它不归属任何用例，但**也不能直接丢掉**——丢掉之后「改了 import
       行」会看起来像「没有任何用例执行过它」，判据就退化成猜。这里用 :data:`_MODULE_LEVEL`
       （空串）作为哨兵存进该行的元组，由 :func:`select_by_lines` 判成全量兜底。
       ``qaplat/registry.py`` 实测 218 行里有 57 行带这个标记。
    3. **路径归一化**：coverage 存绝对路径（``D:\\...``），git / nodeid 用仓库相对路径；
       两边不对齐就永远查不到。用 ``Path.resolve().relative_to(root)`` 相减，``root``
       默认仓库根，在临时克隆或别的工作目录里跑时显式传；仓库外的文件直接跳过（那是
       site-packages 或别的仓，属正常情况，不该让整份索引读不出来）。

    数据缺失时**抛错而不是返回空字典**：空索引会让所有判定退化为全量（看似安全），但真正
    的成因（``--data`` 写错、忘了 ``--cov-context=test``、在别的仓库采集的）就被完全藏起
    来了——这类「静默退化」正是 TIA 最不该有的失效方式。CI 里采集通常写
    ``COVERAGE_FILE=.tia-coverage``（``.coveragerc`` 开了 ``parallel=true``，结束时自动
    combine 成单份），所以 ``data_path`` 传 ``Path(".tia-coverage")`` 即可。
    """
    path = Path(data_path)
    if not path.is_file():
        raise RuntimeError(
            f"coverage 数据文件不存在或不是文件：{path}。行级 TIA 需要先跑一次 "
            "`pytest --cov=<被测包> --cov-context=test`，再用 --data 指到那份 .coverage。"
        )
    from coverage import CoverageData

    data = CoverageData(str(path))
    data.read()
    root_path = (root if root is not None else ROOT).resolve()
    index: dict[str, dict[int, tuple[str, ...]]] = {}
    for measured in data.measured_files():
        relative = _relative_to_root(measured, root_path)
        if relative is None:
            continue
        per_line: dict[int, tuple[str, ...]] = {}
        for line, contexts in (data.contexts_by_lineno(measured) or {}).items():
            node_ids: set[str] = set()
            for context in contexts:
                node_id = _context_node_id(context)
                if node_id is not None:
                    node_ids.add(node_id)
            if node_ids:
                per_line[int(line)] = tuple(sorted(node_ids))
        if per_line:
            index[relative] = per_line
    if not index:
        raise RuntimeError(
            "coverage 数据里没有可用的「用例 context」：行级 TIA 只能用 "
            "`--cov-context=test` 采集的数据，且被测文件必须落在 root 之下。"
            f"（data_path={path}，root={root_path}，measured_files={len(data.measured_files())}）"
        )
    return index


def _extract_paths(reader: PathReader, path: str) -> frozenset[str]:
    """调提取器，并把结果按 registry 的规则归一化（去查询串、占位符统一成 ``{}``）。

    归一化放在这里而不是依赖提取器：注入的替身（测试）不一定懂归一化，而交集比较
    对 ``/api/v1/materials/{code}`` 与 ``/api/v1/materials/{}`` 必须一视同仁。
    """
    return frozenset(registry.normalize_path(str(item)) for item in reader(path) if str(item).strip())


def select(
    changed: Sequence[str],
    cases: Sequence[registry.CaseInfo],
    *,
    path_reader: PathReader | None = None,
) -> Selection:
    """v1 文件级选择：三级保守策略，逐级独立判定（tia-plan.md §3）。

    ``select`` 本身不做 git、不起子进程，只对传入的字符串与 ``CaseInfo`` 做集合运算；
    唯一可能碰磁盘的是第③级的路径提取器，见下面的 ``path_reader`` 说明。

    ① **关键文件**（:data:`ALWAYS_FULL_NAMES` / :data:`ALWAYS_FULL_PREFIXES` 命中）→
       立刻全量返回，不再看其余文件。这一级改的是**所有用例的运行条件**（夹具、marker、
       依赖、契约与数据源），任何按文件做的缩小都是猜测；「立刻返回」也让判据只有一种
       解释，不会出现「一半命中、一半兜底」的混合结论。
    ② **测试文件**（在 ``testcases/`` 下）→ 选中该文件的全部 nodeid。用例改的是断言
       本身，影响面就是它自己，这也是 TIA 收益最大的一档。若该文件一条用例都匹配不到
       （被删/改名/未收集，见 tia-plan.md §3.2），目标状态未知 → 全量兜底，不静默跳过。
    ③ **其他源码** → 读变更文件里的 ``/api...`` 字面量，与用例的 ``referenced_paths``
       求交集。字面量为空（公共模块、``f"/api/{x}"`` 这类动态拼接、非源码文件）或命中
       0 条（这段代码没有任何用例引用）都说明影响面无法判定 → 全量兜底。

    为什么用 ``path_reader`` 注入而不是内部直接读盘：契约要求 ``select`` 是纯函数，测试
    要能只喂构造数据；而第③级的接口路径**只能**从变更源码里取（``cases`` 里没有「变更
    文件」的信息），这是算法本身的信息需求，不是可以省掉的 IO。默认实现
    :func:`qaplat.registry.read_referenced_paths` 会读文件；传替身（如
    ``lambda path: ("/api/login",)``）时 ``select`` 全程零 IO、可精确断言。这是「纯函数」
    与「真实仓库能用」之间唯一诚实的折中。

    相对路径按**当前工作目录**解析（与 pytest / CLI 以仓库根为 cwd 的约定一致）；在别的
    目录里调用请传绝对路径或自己注入 reader。
    """
    normalized: set[str] = set()
    for item in changed:
        path = _normalize_path(item)
        if path:
            normalized.add(path)
    used = tuple(sorted(normalized))
    total = len(cases)
    if not used:
        return _full(used, total, "没有变更文件，无法判定影响面，按全量兜底", "full")

    reader: PathReader = path_reader if path_reader is not None else read_referenced_paths
    by_file: dict[str, list[str]] = {}
    for case in cases:
        by_file.setdefault(_file_of(case.node_id), []).append(case.node_id)
    case_paths: list[tuple[str, frozenset[str]]] = [
        (case.node_id, frozenset(registry.normalize_path(p) for p in case.referenced_paths)) for case in cases
    ]

    selected: set[str] = set()
    matched_files: list[str] = []
    matched_paths: set[str] = set()

    for path in used:
        if _is_always_full(path):
            return _full(used, total, f"关键文件 {path} 命中必跑清单（改动影响所有用例），退化为全量", "full")
        if path.startswith(_TESTCASES_PREFIX):
            owned = by_file.get(path)
            if not owned:
                return _full(
                    used,
                    total,
                    f"{path} 在 testcases/ 下但没有任何已收集用例（可能已删除或改名），退化为全量",
                    "full",
                )
            matched_files.append(path)
            selected.update(owned)
            continue
        source_paths = _extract_paths(reader, path)
        if not source_paths:
            return _full(
                used,
                total,
                f"{path} 未提取到 /api 路径字面量（公共模块/动态拼接/非源码），无法判定影响面，退化为全量",
                "full",
            )
        hit_any = False
        for node_id, paths_in_case in case_paths:
            common = source_paths & paths_in_case
            if common:
                hit_any = True
                selected.add(node_id)
                matched_paths.update(common)
        if not hit_any:
            return _full(
                used,
                total,
                f"{path} 提取到 {len(source_paths)} 条接口路径，但没有任何用例引用它们，退化为全量",
                "full",
            )
    if not selected:
        return _full(used, total, "未命中任何用例，退化为全量", "full")
    return Selection(
        node_ids=tuple(sorted(selected)),
        full=False,
        reason=f"命中 {len(matched_files)} 个测试文件、{len(matched_paths)} 条接口路径",
        changed_files=used,
        total_cases=total,
        matched_files=tuple(sorted(matched_files)),
        matched_paths=tuple(sorted(matched_paths)),
        strategy="files",
    )


def select_by_lines(
    hunks: Sequence[ChangedHunk],
    index: Mapping[str, Mapping[int, Sequence[str]]],
    cases: Sequence[registry.CaseInfo],
) -> Selection:
    """v2 行级选择：变更行 → 执行过这些行的用例（纯函数，零 IO）。

    判据顺序（每一步都朝「多跑」的方向倒）：

    1. **关键文件**（:data:`_LINES_FULL_PREFIXES`，即 v1 清单去掉 ``qaplat/``）→ 全量。
       这些文件改变的是运行条件/契约/依赖，行级映射解释不了它们的影响面。
    2. **测试文件**（``testcases/`` 下）→ 选中该文件的全部用例。测试代码的行虽然也会被
       coverage 记录（用例体在自己 context 下执行），但 fixture、参数化与 import 期代码
       的行归属很不可靠，按文件命中更稳，也保住了「改测试文件只跑它」这个最大收益。
    3. **其他源码** → 取该文件变更行在 ``index`` 里的 context 集合，与已收集用例求交集。
       没有目标行（纯删除/二进制/重命名/未跟踪）、文件不在索引里（没有任何用例执行过它，
       或采集没包含它）、或行级命中为空，都退化为全量。
    4. **模块级行**（索引里带 :data:`_MODULE_LEVEL` 空串哨兵的行，即 import 期执行的
       import/常量/装饰器）→ 全量。这类行没有归属用例，但它一改，所有 import 它的用例
       的运行条件都变了；按「没有用例命中 → 全量」处理既安全又不需要知道谁 import 它。

    **已知限制**（写在这里而不是留给读者猜）：同一文件里既有命中行又有未命中行时，只要
    命中不为空就按命中行选，不会因为「有几行没人执行」而全量。这是行级 TIA 的固有取舍：
    未执行过的行按定义不影响既有用例的**当前**行为，但**改动它**可能改变控制流、让原本
    不执行的代码开始执行。v3 若要做全，应当补「未命中行出现即全量」的开关，而不是在这里
    悄悄放宽。

    ``matched_paths`` 恒为空：行级策略不做接口路径匹配。``matched_files`` 填的是**选中
    用例所在的测试文件**，方便读日志的人知道该去哪个文件核对。
    """
    merged: dict[str, set[int]] = {}
    for hunk in hunks:
        path = _normalize_path(hunk.path)
        if not path:
            continue
        merged.setdefault(path, set()).update(int(line) for line in hunk.lines)
    changed = tuple(sorted(merged))
    total = len(cases)
    if not changed:
        return _full(changed, total, "没有可用的变更行（diff 为空），无法判定影响面，按全量兜底", "full")

    by_file: dict[str, list[str]] = {}
    for case in cases:
        by_file.setdefault(_file_of(case.node_id), []).append(case.node_id)
    known = {case.node_id for case in cases}

    selected: set[str] = set()
    matched_files: list[str] = []
    hit_line_count = 0

    for path in changed:
        if _is_always_full(path, prefixes=_LINES_FULL_PREFIXES):
            return _full(changed, total, f"关键文件 {path} 命中必跑清单（改动影响所有用例），退化为全量", "full")
        if path.startswith(_TESTCASES_PREFIX):
            owned = by_file.get(path)
            if not owned:
                return _full(
                    changed,
                    total,
                    f"{path} 在 testcases/ 下但没有任何已收集用例（可能已删除或改名），退化为全量",
                    "full",
                )
            matched_files.append(path)
            selected.update(owned)
            continue
        lines = sorted(merged[path])
        if not lines:
            return _full(
                changed,
                total,
                f"{path} 没有可用的新增行（纯删除/二进制/重命名/未跟踪），影响面未知，退化为全量",
                "full",
            )
        line_index = index.get(path)
        if not line_index:
            return _full(
                changed,
                total,
                f"{path} 不在覆盖索引里（没有任何用例执行过它，或采集未包含该文件），退化为全量",
                "full",
            )
        for line in lines:
            if _MODULE_LEVEL in (line_index.get(line) or ()):
                return _full(
                    changed,
                    total,
                    f"{path} 第 {line} 行属于模块级代码（import 期执行、不归属任何用例），"
                    "改动会影响所有 import 它的用例，退化为全量",
                    "full",
                )
        file_hits: set[str] = set()
        for line in lines:
            for node_id in line_index.get(line) or ():
                if node_id in known:
                    file_hits.add(node_id)
        if not file_hits:
            return _full(
                changed,
                total,
                f"{path} 的 {len(lines)} 条变更行没有被任何已收集用例执行过，退化为全量",
                "full",
            )
        hit_line_count += sum(1 for line in lines if line_index.get(line))
        selected.update(file_hits)
    if not selected:
        return _full(changed, total, "未命中任何用例，退化为全量", "full")
    selected_files = tuple(sorted({_file_of(node_id) for node_id in selected}))
    return Selection(
        node_ids=tuple(sorted(selected)),
        full=False,
        reason=f"命中 {len(selected)} 条用例（分布在 {len(selected_files)} 个测试文件、覆盖 {hit_line_count} 条变更行）",
        changed_files=changed,
        total_cases=total,
        matched_files=selected_files,
        matched_paths=(),
        strategy="lines",
    )


def select_from_git(
    base: str = "HEAD~1",
    *,
    pytest_args: Sequence[str] = (),
    include_worktree: bool = True,
    cwd: Path | None = None,
    data_path: Path | None = None,
) -> Selection:
    """从 git 读变更 + 收集全量用例 → 做选择（唯一把几种 IO 串起来的入口）。

    ``data_path`` 给了就走行级（v2）：先 :func:`changed_hunks` 取变更行，再
    :func:`load_context_index` 读覆盖索引，最后交给 :func:`select_by_lines`。没给就完全
    退回 v1 文件级策略——两条路径共用同一份 :class:`Selection` 契约，调用方（CLI / gate）
    不需要知道自己拿的是哪一档。

    ``pytest_args`` 透传给 :func:`qaplat.registry.collect_cases`：用例集合必须与真正要跑
    的那一层一致（例如 ``["-m", "smoke"]`` 或某个子目录），否则「全量」的基数与 nodeid
    都对不上。注意 ``collect_cases`` 固定以仓库根为 cwd 起子进程，``cwd`` 只影响 git 与
    覆盖率路径的解析；``data_path`` 的相对路径按**当前进程 cwd** 由 coverage 自己解析。
    """
    if data_path is not None:
        cases = registry.collect_cases(pytest_args)
        hunks = changed_hunks(base, include_worktree=include_worktree, cwd=cwd)
        index = load_context_index(data_path, root=cwd if cwd is not None else ROOT)
        return select_by_lines(hunks, index, cases)
    changed = changed_files(base, include_worktree=include_worktree, cwd=cwd)
    cases = registry.collect_cases(pytest_args)
    return select(changed, cases)


def render_text(sel: Selection) -> str:
    """人类可读的选择结果（CLI 默认输出）。

    刻意不做表格：终端与 CI 日志里最重要的是「全量还是部分、为什么、选中多少」三件事，
    多余的对齐只会在不同终端宽度下错位。用例逐行列出，方便直接复制成
    ``pytest <nodeid>`` 复现。末尾固定打印已知限制（tia-plan.md §3.2 要求显式声明），
    免得有人把「文件/行级粒度」读成「精确到行级且万无一失」。
    """
    if sel.full:
        mode = f"全量兜底（{sel.strategy}）"
    else:
        mode = f"部分选择（{sel.strategy} 策略）"
    lines = [
        f"模式: {mode}",
        f"原因: {sel.reason}",
        f"变更文件: {len(sel.changed_files)} 个",
    ]
    lines.extend(f"  - {item}" for item in sel.changed_files)
    lines.append(f"命中测试文件: {len(sel.matched_files)} 个")
    lines.extend(f"  - {item}" for item in sel.matched_files)
    lines.append(f"命中接口路径: {len(sel.matched_paths)} 条")
    lines.extend(f"  - {item}" for item in sel.matched_paths)
    lines.append(f"选中: {len(sel.node_ids)} / {sel.total_cases} 条用例（{sel.ratio:.2%}）")
    if sel.node_ids:
        lines.append("用例:")
        lines.extend(f"  - {node_id}" for node_id in sel.node_ids)
    lines.append(
        "已知限制: 影响面按文件/行粒度判定；动态拼接路径提取不到会退化为全量；已删除用例匹配不到同样退化为全量"
    )
    return "\n".join(lines)


def render_json(sel: Selection) -> str:
    """机器可读的选择结果（CLI ``--json`` / CI 解析）。

    字段名是**对外契约**（tia-plan.md §4.1），一个都不许改名；``strategy`` 是行级策略
    追加的键（``full`` / ``files`` / ``lines``），让消费方能区分「为什么全量」与「按什么
    选中」。``ratio`` 保留 4 位小数：与契约示例（0.1368）一致，也避免浮点尾巴让 diff 抖动。
    """
    payload = {
        "full": sel.full,
        "strategy": sel.strategy,
        "reason": sel.reason,
        "total_cases": sel.total_cases,
        "selected": len(sel.node_ids),
        "ratio": round(sel.ratio, 4),
        "changed_files": list(sel.changed_files),
        "matched_files": list(sel.matched_files),
        "matched_paths": list(sel.matched_paths),
        "node_ids": list(sel.node_ids),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- 索引新鲜度

#: 索引旁路标记文件的后缀：``.tia-coverage`` → ``.tia-coverage.head``。
#:
#: 不把 HEAD 塞进 SQLite 里：那是 coverage 自己的 schema，加行要跟着它的版本走，还可能在
#: ``combine`` 时被重写。单独一个 JSON 旁路文件更稳——写坏了最多是「判断不出新鲜度」，
#: 不会让索引本身读不出来。
HEAD_MARKER_SUFFIX = ".head"


def head_marker_path(data_path: Path) -> Path:
    """``.tia-coverage`` → ``.tia-coverage.head``（只加后缀，不改目录）。"""
    path = Path(data_path)
    return path.with_name(path.name + HEAD_MARKER_SUFFIX)


def write_index_head(data_path: Path, *, cwd: Path | None = None) -> Path:
    """把**采集时**的 HEAD 记到索引旁边的标记文件里，返回该文件路径。

    为什么需要它：行级 TIA 的准确率上限由**索引新鲜度**决定，不是由选择算法决定。
    ``qaplat/registry.py:70`` 的漏检实验里，旧索引下漏检 3/7 —— 那 3 条是采集之后才新增的
    ``testcases/platform/test_tia.py``，索引里根本没有这份测试的实测数据。而这类失效
    **不会报错**：选择器照常返回一个更小的集合，只是悄悄少了几条。

    记下 HEAD 之后，:func:`index_staleness` 才有对照物；不记就只能靠人记得上次采集是什么
    时候。采集脚本应当在 ``pytest --cov-context=test`` 跑完之后立刻调一次。
    """
    workdir = Path(cwd) if cwd is not None else Path.cwd()
    head = _run_git(["rev-parse", "HEAD"], workdir).strip()
    payload = {
        "head": head,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage_file": Path(data_path).name,
    }
    marker = head_marker_path(data_path)
    # newline="\n"：本仓是 LF 仓库，别让 Windows 默认换行翻译把标记文件写成 CRLF。
    with marker.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return marker


def read_index_head(data_path: Path) -> dict[str, str] | None:
    """读标记文件；不存在、读不动、内容不是对象，都返回 ``None``（= 新鲜度未知）。"""
    marker = head_marker_path(data_path)
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return {str(key): str(value) for key, value in payload.items()}


def index_staleness(data_path: Path, *, cwd: Path | None = None) -> tuple[bool, str]:
    """判断索引是否落后于当前 HEAD，返回 ``(是否陈旧, 一句人话)``。

    「不知道」和「同步」必须分开返回，因为排查时它们是两件事：

    - 没有标记文件 → ``(False, "未记录采集时的 HEAD")``：不知道，但不阻止使用；
    - HEAD 相同 → ``(False, "与 HEAD 同步（abcdef123456）")``；
    - HEAD 不同 → ``(True, "落后 N 个提交")``，N 由 ``git rev-list --count`` 算出——
      一个数字比一句「可能过期了」有用得多，它能直接回答「这次改动该不该先重采」。

    只读不写、不抛：新鲜度检查是**旁路提示**，它自己坏掉不应该让 `tia select` 失败。
    """
    payload = read_index_head(data_path)
    if payload is None:
        return False, "索引未记录采集时的 HEAD（无法判断新鲜度；采集后调 write_index_head 即可）"
    captured = payload.get("head", "").strip()
    if not captured:
        return False, "索引标记里没有 head 字段（无法判断新鲜度）"
    workdir = Path(cwd) if cwd is not None else Path.cwd()
    try:
        current = _run_git(["rev-parse", "HEAD"], workdir).strip()
    except RuntimeError as exc:
        return False, f"读不到当前 HEAD，无法判断索引新鲜度：{exc}"
    if captured == current:
        return False, f"索引与 HEAD 同步（{current[:12]}）"
    behind = "?"
    try:
        behind = _run_git(["rev-list", "--count", f"{captured}..{current}"], workdir).strip()
    except RuntimeError:
        pass
    return True, (
        f"索引落后：采集于 {captured[:12]}，当前 HEAD {current[:12]}，"
        f"中间有 {behind} 个提交未被覆盖 —— 这些提交里新增的测试不会被选中"
    )
