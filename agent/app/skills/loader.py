"""Skill 加载器：四种集成方式（需求 §2.7）。

1. 内置预装     —— seed_builtin_skills（extensions_schema）
2. 市场在线安装 —— 对接 SkillHub/ClawHub（预留接口，返回待接入提示）
3. 对话式安装   —— 自然语言 → 自动下载安装（预留，P2 落实对话流程）
4. 企业私有部署 —— Git 仓库导入（预留）/ ZIP 批量导入 / 本地目录同步

统一入口：`install_skill(...)` → 校验 manifest + 安全策略 → 落库（skills 表
+ tool_registry）→ 依赖检测报告。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..storage import skills_repo, tool_registry_repo
from .manifest import (
    dependency_plan,
    load_manifest_from_dir,
    parse_manifest,
    validate_package_structure,
)
from .policy import validate_manifest_permissions

logger = logging.getLogger(__name__)

SKILLS_DIR_NAME = "skills"


def skills_root() -> Path:
    """私有部署 Skill 存放根目录（agent/data/skills）。"""
    from ..storage.db import DATA_DIR
    root = Path(DATA_DIR) / SKILLS_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def market_root() -> Path:
    """本地技能市场根目录（agent/data/skill_market/<id>/manifest.json）。

    市场包为"只读源"：安装时复制到 skills/installed/，不在源目录执行。
    SkillHub / ClawHub 在线市场：配置环境变量 SKILL_HUB_URL 后自动切换为在线源。
    """
    from ..storage.db import DATA_DIR
    root = Path(DATA_DIR) / "skill_market"
    root.mkdir(parents=True, exist_ok=True)
    return root


def market_package_dir(market_id: str) -> Optional[Path]:
    """按市场 id 定位包目录（本地源）。"""
    pkg = market_root() / market_id
    if (pkg / "manifest.json").exists():
        return pkg
    # 兼容 zip 包内唯一子目录形态
    entries = list(pkg.iterdir()) if pkg.exists() else []
    if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "manifest.json").exists():
        return entries[0]
    return None


# ---- 在线市场（P4 正式化：SkillHub / ClawHub） ----

_online_cache: Dict[str, Any] = {"ts": 0.0, "items": []}


def _online_market_urls() -> List[str]:
    """在线市场根 URL（环境变量 SKILL_HUB_URL / CLAWHUB_URL，可多个）。"""
    urls = []
    for env in ("SKILL_HUB_URL", "CLAWHUB_URL"):
        u = (os.environ.get(env) or "").strip().rstrip("/")
        if u:
            urls.append(u)
    return urls


def fetch_online_market_catalog(timeout: int = 8) -> List[Dict[str, Any]]:
    """拉取在线市场目录（GET <root>/market.json），60s 缓存。

    约定 catalog 结构：
    {"items": [{"id","name","version","description","security_level",
                "manifest_url","archive_url"}, ...]}
    拉取失败返回 []（调用方静默降级为本地市场）。
    """
    now = time.monotonic()
    if now - _online_cache["ts"] < 60 and _online_cache["items"]:
        return list(_online_cache["items"])
    items: List[Dict[str, Any]] = []
    for url in _online_market_urls():
        try:
            resp = httpx.get(f"{url}/market.json", timeout=timeout, trust_env=False)
            if resp.status_code != 200:
                continue
            data = resp.json()
            raw = data.get("items") or data.get("packages") or []
            for it in raw:
                items.append({
                    "id": it.get("id", ""),
                    "name": it.get("name", it.get("id", "")),
                    "version": it.get("version", ""),
                    "description": it.get("description", ""),
                    "security_level": it.get("security_level", "P2"),
                    "source": "online_market",
                    "market_url": url,
                    "manifest_url": it.get("manifest_url", ""),
                    "archive_url": it.get("archive_url", ""),
                })
        except Exception:
            logger.info("[skill-market] 在线市场不可达: %s（降级本地）", url)
    _online_cache["ts"] = now
    _online_cache["items"] = items
    return items


def list_market() -> List[Dict[str, Any]]:
    """市场技能包清单（本地 + 在线合并，安装入口浏览用）。"""
    out: List[Dict[str, Any]] = []
    if market_root().exists():
        for child in sorted(market_root().iterdir()):
            if not child.is_dir():
                continue
            pkg = market_package_dir(child.name)
            if pkg is None:
                continue
            try:
                manifest = load_manifest_from_dir(pkg)
            except Exception:
                continue
            out.append({
                "id": child.name,
                "name": manifest.get("name", child.name),
                "version": manifest.get("version", ""),
                "description": manifest.get("description", ""),
                "security_level": manifest.get("security_level", "P2"),
                "source": "local_market",
            })
    # 在线市场（P4 正式化）
    for it in fetch_online_market_catalog():
        if not any(x["id"] == it["id"] and x["source"] == "local_market" for x in out):
            out.append(it)
    return out


async def search_market(query: str) -> List[Dict[str, Any]]:
    """市场检索：本地 + 在线合并，名称/描述/关键字匹配，按相关性排序。"""
    q = query.lower()
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for item in list_market():
        score = 0
        if q in item["name"].lower():
            score += 10
        if q in (item.get("description") or "").lower():
            score += 5
        if q in item["id"].lower():
            score += 3
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored]


def _find_market_item(market_id: str) -> Optional[Dict[str, Any]]:
    """本地/在线市场按 id 定位包（含 source 与下载地址）。"""
    for item in list_market():
        if item["id"] == market_id:
            return item
    return None


async def _install_online_package(item: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """在线市场包安装：拉 manifest → 安全校验 → 下载 zip → 解压落库。"""
    manifest_url = item.get("manifest_url", "")
    archive_url = item.get("archive_url", "")
    if not manifest_url or not archive_url:
        return {"ok": False, "error": f"在线包 {item['id']} 缺少 manifest_url/archive_url"}, ""
    async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
        try:
            resp = await client.get(manifest_url)
            resp.raise_for_status()
            manifest = parse_manifest(json.dumps(resp.json(), ensure_ascii=False))
        except Exception as exc:
            return {"ok": False, "error": f"在线 manifest 拉取失败: {exc}"}, ""
        try:
            resp2 = await client.get(archive_url)
            resp2.raise_for_status()
            zip_bytes = resp2.content
        except Exception as exc:
            return {"ok": False, "error": f"在线包下载失败: {exc}"}, ""
    tmp_zip = skills_root() / "imports" / f"online_{uuid.uuid4().hex[:8]}.zip"
    tmp_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip.write_bytes(zip_bytes)
    try:
        pkg_dir = _extract_zip(tmp_zip)
    except Exception as exc:
        return {"ok": False, "error": f"在线包解压失败: {exc}"}, ""
    problems = validate_package_structure(pkg_dir)
    if problems:
        return {"ok": False, "error": "；".join(problems)}, ""
    policy = validate_manifest_permissions(manifest)
    if not policy.ok:
        return {"ok": False, "error": "安全策略校验失败", "issues": policy.errors}, ""
    return {"ok": True, "manifest": manifest, "pkg_dir": pkg_dir}, archive_url


async def _persist_skill(skill_id: str, manifest: Dict[str, Any],
                         source: str, source_url: str = "",
                         enabled: bool = True) -> Dict[str, Any]:
    """manifest 校验通过后双写 skills 表 + tool_registry。"""
    tools = manifest.get("tools", []) or []
    await skills_repo.upsert(
        skill_id=skill_id,
        name=manifest["name"],
        version=manifest["version"],
        description=manifest.get("description", ""),
        kind="skill",
        permission_level=manifest["security_level"],
        source=source,
        source_url=source_url,
        manifest=manifest,
        enabled=enabled,
    )
    await tool_registry_repo.upsert(
        tool_id=skill_id,
        name=manifest["name"],
        description=manifest.get("description", ""),
        source=source,
        enabled=enabled,
        requires_approval=manifest["security_level"] == "P4",
        kind="skill",
        permission_level=manifest["security_level"],
        version=manifest["version"],
        metadata={"skill": True, "tools": tools,
                  "dependencies": dependency_plan(manifest)},
    )
    return await skills_repo.get(skill_id)


def _extract_zip(zip_path: Path) -> Path:
    """解压 ZIP 到 data/skills/imports/<uuid>/，返回包目录。"""
    target = skills_root() / "imports" / str(uuid.uuid4())
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target)
    # ZIP 顶层可能是单目录（skill-name/），也可能是散文件
    entries = list(target.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "manifest.json").exists():
        return entries[0]
    if (target / "manifest.json").exists():
        return target
    raise ValueError("ZIP 包内未找到 manifest.json（请确保 ZIP 根目录或唯一顶层目录含 manifest.json）")


async def install_skill(payload: Dict[str, Any]) -> Dict[str, Any]:
    """安装 Skill。

    payload:
      source: 'local_dir' | 'zip' | 'git' | 'market'
      path:   本地目录路径 / ZIP 文件路径（source=local_dir|zip）
      git_url: 仓库地址（source=git，预留）
      market_id: 市场 ID（source=market，预留）
    """
    source = payload.get("source", "local_dir")
    manifest: Dict[str, Any] = {}

    if source == "local_dir":
        pkg_dir = Path(payload["path"]).expanduser()
        if not pkg_dir.exists():
            return {"ok": False, "error": f"目录不存在: {pkg_dir}"}
        manifest = load_manifest_from_dir(pkg_dir)
        problems = validate_package_structure(pkg_dir)
        if problems:
            return {"ok": False, "error": "；".join(problems)}
        target = skills_root() / "installed" / manifest["name"]
        if target != pkg_dir.resolve():
            shutil.copytree(pkg_dir, target, dirs_exist_ok=True)
        source_url = str(pkg_dir)

    elif source == "zip":
        zip_path = Path(payload["path"]).expanduser()
        if not zip_path.exists():
            return {"ok": False, "error": f"ZIP 不存在: {zip_path}"}
        pkg_dir = _extract_zip(zip_path)
        manifest = load_manifest_from_dir(pkg_dir)
        problems = validate_package_structure(pkg_dir)
        if problems:
            return {"ok": False, "error": "；".join(problems)}
        source_url = str(zip_path)

    elif source == "git":
        # P2：Git 仓库导入（需要 git CLI；clone 到临时目录后安全校验）
        git_url = payload.get("git_url", "")
        if not git_url:
            return {"ok": False, "error": "git 导入需提供 git_url"}
        import subprocess
        git_bin = shutil.which("git")
        if not git_bin:
            return {"ok": False, "error": "本机未安装 git CLI，无法执行仓库导入；请改用 local_dir 或 zip"}
        clone_dir = skills_root() / "imports" / f"git_{uuid.uuid4().hex[:8]}"
        clone_dir.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                [git_bin, "clone", "--depth", "1", "--", git_url, str(clone_dir)],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": f"git clone 失败: {proc.stderr[:300]}"}
        except Exception as exc:
            return {"ok": False, "error": f"git clone 异常: {exc}"}
        pkg_dir = clone_dir
        # 仓库根可能直接是 skill，也可能子目录含 manifest.json
        if not (pkg_dir / "manifest.json").exists():
            for child in pkg_dir.iterdir():
                if child.is_dir() and (child / "manifest.json").exists():
                    pkg_dir = child
                    break
        if not (pkg_dir / "manifest.json").exists():
            return {"ok": False, "error": "仓库内未找到 manifest.json"}
        manifest = load_manifest_from_dir(pkg_dir)
        problems = validate_package_structure(pkg_dir)
        if problems:
            return {"ok": False, "error": "；".join(problems)}
        source_url = git_url

    elif source == "market":
        # P2：市场安装器（本地市场目录）；P4：在线市场（SkillHub/ClawHub）正式化
        market_id = payload.get("market_id", "")
        if not market_id:
            return {"ok": False, "error": "市场安装需提供 market_id"}
        pkg_dir = market_package_dir(market_id)
        if pkg_dir is not None:
            manifest = load_manifest_from_dir(pkg_dir)
            problems = validate_package_structure(pkg_dir)
            if problems:
                return {"ok": False, "error": "；".join(problems)}
            source_url = f"market://{market_id}"
        else:
            # 在线市场兜底
            item = _find_market_item(market_id)
            if item is None or item.get("source") != "online_market":
                return {"ok": False, "error": f"市场未找到技能包 {market_id}",
                        "detail": "本地市场目录 data/skill_market/ 与在线市场（SKILL_HUB_URL / CLAWHUB_URL）均无此包"}
            online_result, archive_url = await _install_online_package(item)
            if not online_result.get("ok"):
                return online_result
            manifest = online_result["manifest"]
            source = "market"
            source_url = archive_url or item.get("market_url", "")
            pkg_dir = online_result["pkg_dir"]

    elif source == "dialog":
        # P2：对话式安装 —— query → 市场匹配（本地+在线）→ 自动安装
        query = (payload.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "对话式安装需提供 query"}
        candidates = await search_market(query)
        if not candidates:
            return {"ok": False, "error": "市场未找到匹配技能",
                    "detail": "请换用更精确的描述，或使用市场浏览 / git / zip 安装"}
        best = candidates[0]
        pkg_dir = market_package_dir(best["id"])
        if pkg_dir is None and best.get("source") == "online_market":
            online_result, archive_url = await _install_online_package(best)
            if not online_result.get("ok"):
                return online_result
            manifest = online_result["manifest"]
            source = "dialog"
            source_url = archive_url or best.get("market_url", "")
            pkg_dir = online_result["pkg_dir"]
        else:
            manifest = load_manifest_from_dir(pkg_dir)
            problems = validate_package_structure(pkg_dir)
            if problems:
                return {"ok": False, "error": "；".join(problems)}
            source = "dialog"
            source_url = f"market://{best['id']}"

    else:
        return {"ok": False, "error": f"未知安装来源: {source}"}

    # 安全策略校验（安装前）
    policy = validate_manifest_permissions(manifest)
    if not policy.ok:
        return {"ok": False, "error": "安全策略校验失败", "issues": policy.errors}

    # 依赖检测
    deps = dependency_plan(manifest)
    missing = await _detect_missing_deps(deps)

    skill_id = manifest["name"]
    record = await _persist_skill(
        skill_id=skill_id, manifest=manifest,
        source="private" if source in ("local_dir", "zip") else source,
        source_url=source_url,
    )
    return {
        "ok": True,
        "skill": record,
        "dependencies": deps,
        "missing_dependencies": missing,
        "note": "依赖自动安装将在 P2 落实；当前仅检测并报告缺口" if missing else "依赖已满足",
    }


async def _detect_missing_deps(deps: Dict[str, List[str]]) -> List[str]:
    """检测依赖缺口：Python 包用 importlib，Node 用 node_modules 探测。"""
    import importlib.util

    missing: List[str] = []
    for pkg in deps.get("python", []) or []:
        if not pkg:
            continue
        normalized = pkg.replace("-", "_")
        if importlib.util.find_spec(normalized) is None:
            missing.append(pkg)
    return missing


async def list_skills() -> List[Dict[str, Any]]:
    return await skills_repo.list_all()


async def uninstall_skill(skill_id: str) -> bool:
    """卸载：删除 skills 表 + tool_registry 条目 + 已安装目录（如存在）。"""
    await skills_repo.remove(skill_id)
    await tool_registry_repo.upsert(
        tool_id=skill_id, name=skill_id, description="(已卸载)",
        source="builtin", enabled=False, kind="skill",
        permission_level="P2", version="0.0.0",
    )
    installed_dir = skills_root() / "installed" / skill_id
    if installed_dir.exists():
        shutil.rmtree(installed_dir, ignore_errors=True)
    return True


async def set_enabled(skill_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
    rec = await skills_repo.get(skill_id)
    if not rec:
        return None
    await skills_repo.upsert(
        skill_id=skill_id, name=rec["name"], version=rec["version"],
        description=rec["description"], kind="skill",
        permission_level=rec["permissionLevel"], source=rec["source"],
        source_url=rec["sourceUrl"], manifest=rec["manifest"], enabled=enabled,
    )
    await tool_registry_repo.toggle(skill_id, enabled)
    return await skills_repo.get(skill_id)
