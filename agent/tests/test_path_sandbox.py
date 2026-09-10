"""文件路径沙箱回归测试。

锁定多工具共用的路径校验：
- _resolve_in_roots：多根匹配 + 越权拒绝 + 凭据目录（.ssh 等）拒绝
- _assert_writable：禁止写根下一级隐藏配置（.zshrc/.config/…）
- FilesystemTool / CodeTool / DocToHtmlTool 均走同一沙箱

异步工具用 asyncio.run 驱动，避免引入 pytest-asyncio 依赖。
"""
import asyncio
from pathlib import Path

import pytest

from app.tools import (
    CodeTool,
    DocToHtmlTool,
    FilesystemTool,
    _assert_writable,
    _resolve_in_roots,
)

arun = asyncio.run


@pytest.fixture()
def roots(tmp_path):
    a = tmp_path / "root_a"
    b = tmp_path / "root_b"
    a.mkdir()
    b.mkdir()
    return [a, b]


class TestResolveInRoots:
    def test_inside_first_root(self, roots):
        f = roots[0] / "x.txt"
        f.write_text("a")
        assert _resolve_in_roots(str(f), roots) == f

    def test_inside_second_root(self, roots):
        # 多根都参与校验（旧实现只查第一个根，第二根失效）
        f = roots[1] / "y.txt"
        f.write_text("b")
        assert _resolve_in_roots(str(f), roots) == f

    def test_outside_all_roots_raises(self, roots, tmp_path):
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir(exist_ok=True)
        outside.write_text("x")
        with pytest.raises(PermissionError, match="outside allowed roots"):
            _resolve_in_roots(str(outside), roots)

    def test_credential_dir_blocked_even_inside_root(self, roots):
        ssh = roots[0] / ".ssh"
        ssh.mkdir()
        key = ssh / "id_rsa"
        key.write_text("PRIVATE")
        with pytest.raises(PermissionError, match="凭据/密钥目录"):
            _resolve_in_roots(str(key), roots)

    def test_credential_dir_variants(self, roots):
        for name in (".aws", ".kube", ".docker", ".gnupg"):
            d = roots[0] / name
            d.mkdir()
            target = d / "cred"
            target.write_text("x")
            with pytest.raises(PermissionError, match="凭据/密钥目录"):
                _resolve_in_roots(str(target), roots)


class TestAssertWritable:
    def test_normal_file_writable(self, roots):
        f = roots[0] / "report.txt"
        _assert_writable(f, roots)  # 不抛即通过

    def test_root_level_dotfile_denied(self, roots):
        with pytest.raises(PermissionError, match="禁止写入隐藏配置"):
            _assert_writable(roots[0] / ".zshrc", roots)

    def test_root_level_dotdir_denied(self, roots):
        with pytest.raises(PermissionError, match="禁止写入隐藏配置"):
            _assert_writable(roots[0] / ".config" / "app" / "s.json", roots)

    def test_nested_dotdir_below_normal_dir_allowed(self, roots):
        # 非根层级的 .cache（项目内缓存目录）不拦
        _assert_writable(roots[0] / "project" / ".cache" / "x.tmp", roots)


class TestFilesystemTool:
    @pytest.fixture()
    def fs(self, roots):
        return FilesystemTool(allowed_roots=[str(r) for r in roots])

    def test_write_normal_ok(self, fs, roots):
        f = roots[0] / "note.txt"
        r = arun(fs.execute({"action": "write", "path": str(f), "content": "hi"}))
        assert r["success"] is True
        assert f.read_text() == "hi"

    def test_write_root_dotfile_denied(self, fs, roots):
        r = arun(fs.execute(
            {"action": "write", "path": str(roots[0] / ".zshrc"), "content": "x"}))
        assert r["success"] is False

    def test_write_ssh_authorized_keys_denied(self, fs, roots):
        ssh = roots[0] / ".ssh"
        ssh.mkdir()
        r = arun(fs.execute(
            {"action": "write",
             "path": str(ssh / "authorized_keys"), "content": "x"}))
        assert r["success"] is False

    def test_write_dotconfig_denied(self, fs, roots):
        cfgdir = roots[0] / ".config" / "app"
        cfgdir.mkdir(parents=True)
        r = arun(fs.execute(
            {"action": "write", "path": str(cfgdir / "settings.json"),
             "content": "{}"}))
        assert r["success"] is False

    def test_read_credential_denied(self, fs, roots):
        ssh = roots[0] / ".ssh"
        ssh.mkdir()
        key = ssh / "id_rsa"
        key.write_text("PRIVATE KEY")
        r = arun(fs.execute({"action": "read", "path": str(key)}))
        assert r["success"] is False

    def test_write_outside_roots_denied(self, fs, tmp_path):
        outside = tmp_path / "escape.txt"
        r = arun(fs.execute(
            {"action": "write", "path": str(outside), "content": "x"}))
        assert r["success"] is False

    def test_read_in_second_root_ok(self, fs, roots):
        f = roots[1] / "b.txt"
        f.write_text("in-b")
        r = arun(fs.execute({"action": "read", "path": str(f)}))
        assert r["success"] is True
        assert "in-b" in r["content"]


class TestCodeTool:
    @pytest.fixture()
    def code(self, roots):
        return CodeTool(allowed_roots=[str(r) for r in roots])

    def test_inline_code_analyzed(self, code):
        r = arun(code.execute({"code": "def f():\n    return 1\n",
                              "language": "python"}))
        assert r["success"] is True

    def test_file_in_root_analyzed(self, code, roots):
        f = roots[0] / "m.py"
        f.write_text("def g():\n    return 2\n")
        r = arun(code.execute({"path": str(f)}))
        assert r["success"] is True

    def test_file_outside_roots_denied(self, code, tmp_path):
        f = tmp_path / "secret.py"
        f.write_text("SECRET=1\n")
        r = arun(code.execute({"path": str(f)}))
        assert r["success"] is False
        assert "outside allowed roots" in r["error"]

    def test_credential_file_denied(self, code, roots):
        ssh = roots[0] / ".ssh"
        ssh.mkdir()
        f = ssh / "id_rsa"
        f.write_text("KEY")
        r = arun(code.execute({"path": str(f)}))
        assert r["success"] is False


class TestDocToHtmlTool:
    @pytest.fixture()
    def doc(self, roots):
        return DocToHtmlTool(allowed_roots=[str(r) for r in roots])

    def test_outside_path_denied_before_read(self, doc, tmp_path):
        # 越权路径应在读取 docx 之前就被沙箱拦下
        f = tmp_path / "x.docx"
        f.write_text("not-a-real-docx")
        r = arun(doc.execute({"path": str(f)}))
        assert r["success"] is False
        assert "outside allowed roots" in r["error"]
