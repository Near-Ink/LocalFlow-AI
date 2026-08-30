"""沙箱执行器 — 路径边界与命令治理

给 agent 的工具执行层提供统一安全边界，分三层：
1. 路径边界：只允许在用户声明的 allow_dirs 内读写，拦截 ``../``、越界绝对路径
   与符号链接逃逸。
2. 命令治理：白名单放行常用只读/打包命令；黑名单二次拦截删除/格式化/特权/网络
   攻击与泛执行类命令，防止借白名单命令意破坏。
3. 执行约束：强制超时与输出截断，防止失控进程/超大输出。

注意：本模块的路径与命令校验为纯函数逻辑，独立可测（见 backend/tests/）。
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..ports.tool import ToolResult


# ---- 命令白名单：只读 / 打包 / 信息类 ----
DEFAULT_COMMAND_WHITELIST: set[str] = {
    "ls", "grep", "find", "cat", "head", "tail", "wc",
    "echo", "printf", "date", "stat", "du", "file",
    "uniq", "sort", "tr", "cut", "basename", "dirname",
    "tar", "zip", "unzip", "diff", "whoami", "pwd",
}

# ---- 命令黑名单：破坏 / 授权提升 / 网络外联 / 泛执行 ----
DEFAULT_COMMAND_BLACKLIST: set[str] = {
    # 删除 / 移动 / 破坏
    "rm", "mv", "rmdir", "truncate", "shred",
    # 格式化 / 分区 / 引导
    "mkfs", "mkfs.ext2", "mkfs.ext3", "mkfs.ext4", "mkfs.vfat",
    "mkfs.xfs", "fdisk", "parted", "gdisk", "sfdisk", "fsck",
    "dd", "sync", "eject", "mount", "umount",
    # 关机 / 重启 / 系统级
    "shutdown", "reboot", "poweroff", "halt", "init", "systemctl",
    "service", "update-runlevel", "telinit",
    # 授权提升 / 权限
    "sudo", "su", "chown", "chmod", "chattr", "setfacl", "login",
    # 进程 / 会话
    "kill", "pkill", "killall", "nohup", "screen", "tmux",
    # 网络外联（防偷传数据 / 防 SSRF）
    "curl", "wget", "nc", "ncat", "telnet", "ssh", "scp", "rsync",
    "ftp", "socat", "who-is", "nslookup", "dig",
    # 泛执行 / 包管理（防逃逸执行任意代码）
    "bash", "sh", "zsh", "dash", "python", "python3", "perl", "ruby",
    "node", "npm", "npx", "pnpm", "yarn", "go", "rustc", "gcc", "cc",
    "make", "cmake", "git", "pip", "pip3", "docker", "podman",
}

# ---- 危险参数的通配匹配（简化且稳健，作为二次防线） ----
_DANGEROUS_ARG_TOKENS = (
    "--force", "-rf", "-fr", "-r", "-R", "--recursive", "--delete",
    "-R-f", ">", ">>", "<", "|", "&", ";", "$(", "`", 
)


def _is_dangerous_arg(token: str) -> bool:
    """判断单个参数是否触发危险模式（删除标志、shell 元字符等）"""
    for d in _DANGEROUS_ARG_TOKENS:
        if d in token:
            return True
    # 以删除类命令开头（如 cat 意外被拼成 '' rm -rf）的保守拦截
    if re.match(r"^rm\b", token):
        return True
    return False


class Sandbox:
    """命令执行沙箱 — 路径边界 + 命令治理"""

    def __init__(
        self,
        allow_dirs: Optional[Sequence[str]] = None,
        command_whitelist: Optional[set[str]] = None,
        command_blacklist: Optional[set[str]] = None,
        default_timeout: float = 15.0,
        max_output_bytes: int = 200_000,
    ) -> None:
        self.allow_dirs = self._resolve_dirs(allow_dirs or [])
        self.whitelist = set(command_whitelist or DEFAULT_COMMAND_WHITELIST)
        self.blacklist = set(command_blacklist or DEFAULT_COMMAND_BLACKLIST)
        self.default_timeout = default_timeout
        self.max_output_bytes = max_output_bytes

    # ------------------------------------------------------------------
    # 路径边界
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_dirs(dirs: Sequence[str]) -> List[Path]:
        """把允许目录列表解析为绝对 realpath 列表"""
        seen: set[str] = set()
        result: List[Path] = []
        for d in dirs:
            if not d:
                continue
            rp = Path(d).expanduser().resolve(strict=False)
            key = str(rp)
            if key not in seen:
                seen.add(key)
                result.append(rp)
        return result

    def is_allowed_path(self, path: str) -> bool:
        """路径是否落在允许目录内（含符号链接逃逸防护）"""
        return self.resolve_allowed(path) is not None

    def resolve_allowed(self, path: str) -> Optional[Path]:
        """把路径归一化并校验在允许目录内；返回 realpath 或 None。

        规则：先 expanduser 再 resolve()（解析符号链接），得到一个绝对真实路径，
        校验其是否落在任意允许目录下方（含允许目录本身）。任何 ../ 逃逸、
        符号链接指向允许目录外、或被拒绝都会返回 None。
        """
        if not path or not path.strip():
            return None
        try:
            target = Path(path).expanduser()
            rp = target.resolve(strict=False)  # 解析符号链接 -> 防逃逸
        except (OSError, ValueError, RuntimeError) as e:
            return None
        if not self.allow_dirs:
            return None
        for base in self.allow_dirs:
            try:
                if rp == base or rp.is_relative_to(base):
                    return rp
            except (ValueError, OSError):
                continue
        return None

    # ------------------------------------------------------------------
    # 命令治理
    # ------------------------------------------------------------------

    def check_command(self, cmdline: str) -> Tuple[bool, Optional[str], Optional[List[str]]]:
        """校验一条命令是否可执行。

        Returns:
            (是否通过, 拒绝原因, 解析后的 argv)。不通过时 argv 为 None。
        """
        try:
            argv = shlex.split(cmdline)
        except ValueError as e:
            return False, f"命令解析失败：{e}", None

        if not argv:
            return False, "空命令", None

        cmd = argv[0]
        cmd_base = os.path.basename(cmd)

        # 命令名不能是路径形式（防止指定 /bin/sh 之类绕过白名单）
        if cmd != cmd_base:
            return False, f"命令不得为路径形式：{cmd}", None

        # 黑名单先拦（即使命令名不在白名单也记录）
        if cmd_base in self.blacklist:
            return False, f"命令 '{cmd_base}' 在黑名单中（已拦截）", None

        # 白名单放行
        if cmd_base not in self.whitelist:
            return False, f"命令 '{cmd_base}' 不在白名单内（默认拒绝）", None

        # 危险参数二次拦截
        for tok in argv[1:]:
            if _is_dangerous_arg(tok):
                return False, f"危险参数被拦截：{tok}", None

        return True, None, argv

    # ------------------------------------------------------------------
    # 执行（限时 + 截断）
    # ------------------------------------------------------------------

    def run_command(
        self,
        cmdline: str,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        max_output_bytes: Optional[int] = None,
    ) -> ToolResult:
        """校验并在沙箱内执行命令，返回截断后的结果。"""
        ok, reason, argv = self.check_command(cmdline)
        if not ok:
            return ToolResult(ok=False, error=reason or "命令被拒绝", require_confirm=False)

        # 若指定 cwd，须在允许目录内
        run_cwd: Optional[str] = None
        if cwd:
            allowed = self.resolve_allowed(cwd)
            if allowed is None:
                return ToolResult(ok=False, error=f"工作目录越界：{cwd}", require_confirm=False)
            run_cwd = str(allowed)

        cap = max_output_bytes or self.max_output_bytes
        try:
            proc = subprocess.run(
                argv,
                cwd=run_cwd,
                capture_output=True,
                text=True,
                timeout=timeout or self.default_timeout,
            )
        except FileNotFoundError:
            return ToolResult(ok=False, error=f"命令不存在：{argv[0]}")
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, error=f"命令超时（>{timeout or self.default_timeout}s）")
        except Exception as e:  # 其它系统错误
            return ToolResult(ok=False, error=f"执行失败：{e}")

        # 截断输出
        out = proc.stdout[:cap] + ("…(已截断)" if len(proc.stdout) > cap else "")
        err = proc.stderr[:cap] + ("…(已截断)" if len(proc.stderr) > cap else "")

        if proc.returncode == 0:
            return ToolResult(ok=True, output=out, data={"exit_code": 0, "stderr": err})
        return ToolResult(
            ok=False,
            output=out,
            error=f"退出码 {proc.returncode}",
            data={"exit_code": proc.returncode, "stderr": err},
        )