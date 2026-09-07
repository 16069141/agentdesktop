"""项目空间路由（需求 §B.3.1：多人协同）。

接口：
  GET    /api/projects                               — 项目列表
  POST   /api/projects                               — 创建项目（当前用户为 owner）
  GET    /api/projects/{id}                          — 项目详情（含成员/任务/资产）
  PUT    /api/projects/{id}                          — 更新项目（owner / admin）
  DELETE /api/projects/{id}                          — 删除项目（owner / admin）

  GET/POST /api/projects/{id}/members                — 成员列表 / 添加成员（owner/admin）
  DELETE   /api/projects/{id}/members/{username}     — 移除成员（owner/admin）

  GET/POST /api/projects/{id}/tasks                  — 任务列表 / 创建（editor+）
  PUT/DELETE /api/projects/{id}/tasks/{task_id}      — 更新 / 删除任务（editor+）

  GET/POST /api/projects/{id}/assets                 — 资产列表 / 添加（editor+）
  DELETE   /api/projects/{id}/assets/{asset_id}      — 删除资产（editor+）
  POST     /api/projects/{id}/assets/search          — 资产库服务端 RAG 检索

  GET/PUT /api/projects/{id}/config-sharing          — 配置共享开关（owner/admin）
  GET     /api/projects/{id}/shared-config           — 共享配置聚合视图（全员可读）

成员三级：admin 管理 / editor 读写任务与资产 / viewer 只读。
身份：X-User-Name 头；无身份头时视为 local（项目创建者，等同于 admin）。
"""
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..storage import (
    project_asset_repo,
    project_member_repo,
    project_repo,
    project_task_repo,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _current_user(request: Request) -> str:
    """从身份透传头取当前用户；无头则 local。"""
    name = request.headers.get("X-User-Name", "").strip()
    return name or "local"


async def _member_role(project_id: str, username: str) -> Optional[str]:
    """成员角色；owner 视为 admin。"""
    project = await project_repo.get(project_id)
    if not project:
        return None
    if project["owner"] == username:
        return "admin"
    return await project_member_repo.get_role(project_id, username)


async def _require(project_id: str, username: str, min_role: str) -> str:
    """权限检查：admin > editor > viewer。不满足抛 403。"""
    order = {"admin": 3, "editor": 2, "viewer": 1}
    role = await _member_role(project_id, username)
    if role is None:
        raise HTTPException(status_code=403, detail="非项目成员，无权访问")
    if order.get(role, 0) < order[min_role]:
        raise HTTPException(status_code=403, detail=f"需要 {min_role} 及以上权限")
    return role


class ProjectCreate(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    config_share: dict = {}


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[bool] = None
    config_share: Optional[dict] = None


class MemberAdd(BaseModel):
    username: str
    role: str = "viewer"


class TaskCreate(BaseModel):
    id: Optional[str] = None
    title: str
    description: str = ""
    status: str = "todo"
    mode: str = "shared"
    assignee: str = ""


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    mode: Optional[str] = None
    assignee: Optional[str] = None


class AssetCreate(BaseModel):
    id: Optional[str] = None
    name: str
    type: str = "doc"
    content: str = ""
    knowledge_server: Optional[str] = None


class AssetSearch(BaseModel):
    query: str
    top_k: int = 5


# ============ 项目 ============

@router.get("")
async def list_projects(request: Request):
    user = _current_user(request)
    projects = await project_repo.list_all()
    # 非成员不可见；local（本机）可见全部
    if user == "local":
        return projects
    out = []
    for p in projects:
        role = await _member_role(p["id"], user)
        if role:
            out.append({**p, "myRole": role})
    return out


@router.post("")
async def create_project(body: ProjectCreate, request: Request):
    user = _current_user(request)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name 不能为空")
    pid = body.id or f"proj_{uuid.uuid4().hex[:8]}"
    if await project_repo.get(pid):
        raise HTTPException(status_code=409, detail=f"项目 {pid} 已存在")
    project = await project_repo.create({
        "id": pid, "name": body.name.strip(), "description": body.description,
        "owner": user, "config_share": body.config_share or {},
    })
    await project_member_repo.add(pid, user, "admin")
    return project


@router.get("/{project_id}")
async def get_project(project_id: str, request: Request):
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    if user != "local":
        await _require(project_id, user, "viewer")
    project = dict(project)
    project["members"] = await project_member_repo.list(project_id)
    project["tasks"] = await project_task_repo.list(project_id)
    project["assets"] = await project_asset_repo.list(project_id)
    return project


@router.put("/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, request: Request):
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    await _require(project_id, user, "admin")
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.description is not None:
        fields["description"] = body.description
    if body.status is not None:
        fields["status"] = int(body.status)
    if body.config_share is not None:
        fields["config_share"] = body.config_share
    updated = await project_repo.update(project_id, fields)
    return updated


@router.delete("/{project_id}")
async def delete_project(project_id: str, request: Request):
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    await _require(project_id, user, "admin")
    await project_repo.delete(project_id)
    return {"ok": True}


# ============ 成员 ============

@router.get("/{project_id}/members")
async def list_members(project_id: str, request: Request):
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    await _require(project_id, user, "viewer")
    return await project_member_repo.list(project_id)


@router.post("/{project_id}/members")
async def add_member(project_id: str, body: MemberAdd, request: Request):
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="username 不能为空")
    if body.role not in project_member_repo.VALID_ROLES:
        raise HTTPException(status_code=400, detail="role 仅支持 admin/editor/viewer")
    user = _current_user(request)
    await _require(project_id, user, "admin")
    return await project_member_repo.add(project_id, body.username.strip(), body.role)


@router.delete("/{project_id}/members/{username}")
async def remove_member(project_id: str, username: str, request: Request):
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    await _require(project_id, user, "admin")
    await project_member_repo.remove(project_id, username)
    return {"ok": True}


# ============ 任务 ============

@router.get("/{project_id}/tasks")
async def list_tasks(project_id: str, request: Request):
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    await _require(project_id, user, "viewer")
    return await project_task_repo.list(project_id)


@router.post("/{project_id}/tasks")
async def create_task(project_id: str, body: TaskCreate, request: Request):
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="title 不能为空")
    if body.mode not in project_task_repo.VALID_MODES:
        raise HTTPException(status_code=400, detail="mode 仅支持 independent/shared/collaborative")
    if body.status not in project_task_repo.VALID_STATUS:
        raise HTTPException(status_code=400, detail="status 仅支持 todo/doing/done")
    user = _current_user(request)
    await _require(project_id, user, "editor")
    task = await project_task_repo.create({
        "id": body.id or f"task_{uuid.uuid4().hex[:8]}",
        "project_id": project_id, "title": body.title.strip(),
        "description": body.description, "status": body.status,
        "mode": body.mode, "assignee": body.assignee, "created_by": user,
    })
    return task


@router.put("/{project_id}/tasks/{task_id}")
async def update_task(project_id: str, task_id: str, body: TaskUpdate, request: Request):
    task = await project_task_repo.get(task_id)
    if not task or task["projectId"] != project_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    user = _current_user(request)
    await _require(project_id, user, "editor")
    fields: dict[str, Any] = {}
    for key, attr in (("title", "title"), ("description", "description"),
                      ("status", "status"), ("mode", "mode"), ("assignee", "assignee")):
        value = getattr(body, key)
        if value is not None:
            if attr == "status" and value not in project_task_repo.VALID_STATUS:
                raise HTTPException(status_code=400, detail="status 仅支持 todo/doing/done")
            if attr == "mode" and value not in project_task_repo.VALID_MODES:
                raise HTTPException(status_code=400, detail="mode 仅支持 independent/shared/collaborative")
            fields[attr] = value
    return await project_task_repo.update(task_id, fields)


@router.delete("/{project_id}/tasks/{task_id}")
async def delete_task(project_id: str, task_id: str, request: Request):
    task = await project_task_repo.get(task_id)
    if not task or task["projectId"] != project_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    user = _current_user(request)
    await _require(project_id, user, "editor")
    await project_task_repo.delete(task_id)
    return {"ok": True}


# ============ 资产库（服务端 RAG） ============

@router.get("/{project_id}/assets")
async def list_assets(project_id: str, request: Request):
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    await _require(project_id, user, "viewer")
    return await project_asset_repo.list(project_id)


@router.post("/{project_id}/assets")
async def create_asset(project_id: str, body: AssetCreate, request: Request):
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name 不能为空")
    if body.type not in project_asset_repo.VALID_TYPES:
        raise HTTPException(status_code=400, detail="type 仅支持 doc/data/image/url")
    user = _current_user(request)
    await _require(project_id, user, "editor")
    asset = await project_asset_repo.create({
        "id": body.id or f"ast_{uuid.uuid4().hex[:8]}",
        "project_id": project_id, "name": body.name.strip(), "type": body.type,
        "content": body.content, "knowledge_server": body.knowledge_server,
        "created_by": user,
    })
    return asset


@router.delete("/{project_id}/assets/{asset_id}")
async def delete_asset(project_id: str, asset_id: str, request: Request):
    asset = await project_asset_repo.get(asset_id)
    if not asset or asset["projectId"] != project_id:
        raise HTTPException(status_code=404, detail="资产不存在")
    user = _current_user(request)
    await _require(project_id, user, "editor")
    await project_asset_repo.delete(asset_id)
    return {"ok": True}


@router.post("/{project_id}/assets/search")
async def search_assets(project_id: str, body: AssetSearch, request: Request):
    """资产库服务端 RAG 检索：遍历项目资产，关联知识库连接的 search()。

    本地 RAG 已移除；检索全部经由已配置的 knowledge_servers 连接（服务端）。
    资产无关联连接时返回资产元数据命中（名称/内容全文匹配）。
    """
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    user = _current_user(request)
    await _require(project_id, user, "viewer")

    assets = await project_asset_repo.list(project_id)
    results: list[dict] = []
    seen_servers: set[str] = set()

    for asset in assets:
        server_id = asset.get("knowledgeServer")
        if server_id and server_id not in seen_servers:
            seen_servers.add(server_id)
            try:
                server_hits = await _search_knowledge(server_id, query, body.top_k)
                for hit in server_hits:
                    results.append({
                        "assetId": asset["id"], "assetName": asset["name"],
                        "serverId": server_id, "score": hit.get("score"),
                        "content": hit.get("content") or hit.get("text") or "",
                        "source": hit.get("source") or "",
                    })
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("[projects] 资产 RAG 检索失败 server=%s: %s", server_id, exc)
        # 资产自身全文匹配（无连接时仍可发现资产）
        if not server_id and query.lower() in asset["content"].lower():
            results.append({
                "assetId": asset["id"], "assetName": asset["name"],
                "serverId": None, "score": 1.0,
                "content": asset["content"][:500], "source": "asset.content",
            })

    return {"project_id": project_id, "query": query, "results": results[:body.top_k * 3]}


async def _search_knowledge(server_id: str, query: str, top_k: int) -> list[dict]:
    from ..knowledge_connectors import create_connector
    from ..security import keychain
    from ..storage.knowledge_servers import KnowledgeServerRepo

    repo = KnowledgeServerRepo()
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"知识库连接 {server_id} 不存在")
    if not server.get("enabled"):
        raise HTTPException(status_code=403, detail=f"知识库连接 {server_id} 已停用")
    api_key = ""
    ref = server.get("api_key_ref")
    if ref:
        api_key = await keychain.retrieve(ref) or ""
    connector = create_connector(server, api_key=api_key)
    return await connector.search(query, top_k=top_k)


# ============ 配置共享 ============

@router.get("/{project_id}/config-sharing")
async def get_config_sharing(project_id: str, request: Request):
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    await _require(project_id, user, "viewer")
    return project["configShare"]


@router.put("/{project_id}/config-sharing")
async def set_config_sharing(project_id: str, body: dict, request: Request):
    if not await project_repo.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    await _require(project_id, user, "admin")
    allowed = {k: bool(v) for k, v in body.items() if k in ("skills", "mcp", "agent")}
    updated = await project_repo.update(project_id, {"config_share": allowed})
    return updated["configShare"]


@router.get("/{project_id}/shared-config")
async def shared_config(project_id: str, request: Request):
    """共享配置聚合视图：启用中的 Skills / 连接器 / Agent 设置（全员可读）。"""
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    user = _current_user(request)
    if user != "local":
        await _require(project_id, user, "viewer")
    share = project["configShare"] or {}
    out: dict[str, Any] = {"projectId": project_id, "sharing": share}

    if share.get("skills"):
        from ..skills.loader import list_skills
        skills = await list_skills()
        out["skills"] = [s for s in skills if s.get("enabled")]
    if share.get("mcp"):
        from ..storage import connector_config_repo, db_connector_repo
        out["connectors"] = await connector_config_repo.list_all()
        out["dbConnectors"] = await db_connector_repo.list_all()
    if share.get("agent"):
        from ..storage import tool_registry_repo
        all_tools = await tool_registry_repo.list_all()
        out["tools"] = [t for t in all_tools if t.get("enabled")]
    return out
