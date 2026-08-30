"""Sandbox 安全边界单元测试

覆盖：越界路径（../、绝对路径、符号链接逃逸）被拦；黑名单命令被拦；
白名单命令放行；危险参数被拦。可直接 `python tests/test_sandbox.py` 运行，
亦可被 pytest 收集。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from localflow.adapters.sandbox import Sandbox


def _make_sandbox(root: str) -> Sandbox:
    return Sandbox(allow_dirs=[root])


def test_allowed_path_within_dir():
    with tempfile.TemporaryDirectory() as d:
        sb = _make_sandbox(d)
        inner = os.path.join(d, "notes", "a.txt")
        os.makedirs(os.path.dirname(inner))
        base = sb.allow_dirs[0]  # 分辨率后的允许目录（已解析符号链接）
        resolved = sb.resolve_allowed(inner)
        assert resolved is not None
        # macOS 的 /var -> /private/var 等符号链会造成前缀差异，故用解析后 base 比较
        assert str(resolved).startswith(str(base))


def test_traversal_is_blocked():
    with tempfile.TemporaryDirectory() as d:
        sb = _make_sandbox(d)
        assert sb.resolve_allowed(os.path.join(d, "..", "..", "etc", "passwd")) is None
        assert sb.resolve_allowed(os.path.join(d, "..", "escaped.txt")) is None
        assert sb.is_allowed_path("/etc/passwd") is False


def test_absolute_outside_is_blocked():
    with tempfile.TemporaryDirectory() as d:
        sb = _make_sandbox(d)
        assert sb.resolve_allowed("/opt/somewhere") is None
        assert sb.resolve_allowed(str(Path.home())) is None  # 允许目录之外


def test_symlink_escape_is_blocked():
    with tempfile.TemporaryDirectory() as d:
        outside = tempfile.mkdtemp()
        sb = _make_sandbox(d)
        link = os.path.join(d, "evil_link")
        if os.path.islink(link):
            os.unlink(link)
        try:
            os.symlink(outside, link)  # 指向允许目录外
            assert sb.is_allowed_path(link) is False
            assert sb.resolve_allowed(link) is None
        except OSError:
            # 某些平台无法建符号链接，跳过
            pass


def test_blacklist_commands_blocked():
    with tempfile.TemporaryDirectory() as d:
        sb = _make_sandbox(d)
        for cmd in ["rm -rf /", "sudo whoami", "curl http://x", "python3 -c 'x'",
                    "bash -c 'echo hi'", "dd if=/dev/zero of=/tmp/x", "git clone https://x"]:
            ok, reason, _ = sb.check_command(cmd)
            assert ok is False, f"应当拦截：{cmd}"
            assert reason, "应带拒绝原因"


def test_whitelist_commands_allowed():
    with tempfile.TemporaryDirectory() as d:
        sb = _make_sandbox(d)
        for cmd in ["ls -la", "date", "echo hello", "head -20 file.txt",
                    "grep foo bar.txt", "tar -tf archive.tar"]:
            ok, _, argv = sb.check_command(cmd)
            assert ok is True, f"应当放行：{cmd}"


def test_path_form_commands_blocked():
    with tempfile.TemporaryDirectory() as d:
        sb = _make_sandbox(d)
        ok, reason, _ = sb.check_command("/bin/ls")   # 路径形式，即使白名单名也拒
        assert ok is False


def test_dangerous_arg_blocked():
    with tempfile.TemporaryDirectory() as d:
        sb = _make_sandbox(d)
        # 白名单命令但带危险参数（删除标志 / 重定向 / 命令替换）应拦
        for cmd in ["echo abc > file", "cat a | sh", "rm -rf .", "echo $(date)"]:
            ok, _, _ = sb.check_command(cmd)
            assert ok is False, f"危险参数应当拦截：{cmd}"


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())