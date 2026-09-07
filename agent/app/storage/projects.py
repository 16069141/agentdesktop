"""Phase B (P2) 仓储：项目空间（projects / project_members / project_tasks /
project_assets）与字段映射（field_mappings）。

多人协同项目空间（需求 §B.3.1）：
- 项目 CRUD + 成员三级权限（admin / editor / viewer）
- 任务三模式（independent 独立 / shared 分享 / collaborative 协作）
- 资产库（服务端 RAG 检索，经 knowledge_servers 连接）
- 配置共享（Skills / MCP / Agent 配置全员复用）

字段映射（需求 §B.3.3）：跨系统字段映射 JSON 配置（可视化拖拽 P3）。
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


class ProjectRepo:
    """projects 仓储。"""

    async def create(self, p: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO projects (id, name, description, owner, config_share,
                                         status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (p["id"], p["name"], p.get("description", ""), p.get("owner", ""),
                 json.dumps(p.get("config_share") or {}, ensure_ascii=False),
                 int(p.get("status", 1)), _now_ms(), _now_ms()),
            )
            await db.commit()
            return await self.get(p["id"])
        finally:
            await db.close()

    async def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute("SELECT * FROM projects ORDER BY created_at DESC")
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def update(self, project_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            if "config_share" in fields and isinstance(fields["config_share"], dict):
                fields["config_share"] = json.dumps(fields["config_share"], ensure_ascii=False)
            sets = ", ".join(f"{k} = ?" for k in fields)
            await db.execute(f"UPDATE projects SET {sets}, updated_at = ? WHERE id = ?",
                             list(fields.values()) + [_now_ms(), project_id])
            await db.commit()
            return await self.get(project_id)
        finally:
            await db.close()

    async def delete(self, project_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM project_members WHERE project_id = ?", (project_id,))
            await db.execute("DELETE FROM project_tasks WHERE project_id = ?", (project_id,))
            await db.execute("DELETE FROM project_assets WHERE project_id = ?", (project_id,))
            await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        cfg = row["config_share"]
        try:
            cfg = json.loads(cfg) if cfg else {}
        except (TypeError, ValueError):
            cfg = {}
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "owner": row["owner"],
            "configShare": cfg,
            "status": bool(row["status"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


class ProjectMemberRepo:
    """project_members 仓储（三级权限）。"""

    VALID_ROLES = {"admin", "editor", "viewer"}

    async def add(self, project_id: str, username: str, role: str = "viewer") -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT OR REPLACE INTO project_members (project_id, username, role, joined_at)
                   VALUES (?, ?, ?, ?)""",
                (project_id, username, role, _now_ms()),
            )
            await db.commit()
            return {"projectId": project_id, "username": username, "role": role}
        finally:
            await db.close()

    async def list(self, project_id: str) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute(
                "SELECT * FROM project_members WHERE project_id = ? ORDER BY joined_at",
                (project_id,),
            )
            return [{"username": r["username"], "role": r["role"], "joinedAt": r["joined_at"]}
                    for r in await rows.fetchall()]
        finally:
            await db.close()

    async def get_role(self, project_id: str, username: str) -> Optional[str]:
        db = await connect()
        try:
            row = await db.execute(
                "SELECT role FROM project_members WHERE project_id = ? AND username = ?",
                (project_id, username),
            )
            row = await row.fetchone()
            return row["role"] if row else None
        finally:
            await db.close()

    async def remove(self, project_id: str, username: str) -> None:
        db = await connect()
        try:
            await db.execute(
                "DELETE FROM project_members WHERE project_id = ? AND username = ?",
                (project_id, username),
            )
            await db.commit()
        finally:
            await db.close()


class ProjectTaskRepo:
    """project_tasks 仓储（独立 / 分享 / 协作三模式）。"""

    VALID_MODES = {"independent", "shared", "collaborative"}
    VALID_STATUS = {"todo", "doing", "done"}

    async def create(self, t: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO project_tasks (id, project_id, title, description, status,
                                              mode, assignee, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (t["id"], t["project_id"], t["title"], t.get("description", ""),
                 t.get("status", "todo"), t.get("mode", "shared"),
                 t.get("assignee", ""), t.get("created_by", ""), _now_ms(), _now_ms()),
            )
            await db.commit()
            return await self.get(t["id"])
        finally:
            await db.close()

    async def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute("SELECT * FROM project_tasks WHERE id = ?", (task_id,))
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list(self, project_id: str) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute(
                "SELECT * FROM project_tasks WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            )
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def update(self, task_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            sets = ", ".join(f"{k} = ?" for k in fields)
            await db.execute(f"UPDATE project_tasks SET {sets}, updated_at = ? WHERE id = ?",
                             list(fields.values()) + [_now_ms(), task_id])
            await db.commit()
            return await self.get(task_id)
        finally:
            await db.close()

    async def delete(self, task_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM project_tasks WHERE id = ?", (task_id,))
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "mode": row["mode"],
            "assignee": row["assignee"],
            "createdBy": row["created_by"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


class ProjectAssetRepo:
    """project_assets 仓储（资产库 + 服务端 RAG）。"""

    VALID_TYPES = {"doc", "data", "image", "url"}

    async def create(self, a: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO project_assets (id, project_id, name, type, content,
                                               knowledge_server, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (a["id"], a["project_id"], a["name"], a.get("type", "doc"),
                 a.get("content", ""), a.get("knowledge_server"), a.get("created_by", ""),
                 _now_ms()),
            )
            await db.commit()
            return await self.get(a["id"])
        finally:
            await db.close()

    async def get(self, asset_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute("SELECT * FROM project_assets WHERE id = ?", (asset_id,))
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list(self, project_id: str) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute(
                "SELECT * FROM project_assets WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            )
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def delete(self, asset_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM project_assets WHERE id = ?", (asset_id,))
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "projectId": row["project_id"],
            "name": row["name"],
            "type": row["type"],
            "content": row["content"],
            "knowledgeServer": row["knowledge_server"],
            "createdBy": row["created_by"],
            "createdAt": row["created_at"],
        }


class FieldMappingRepo:
    """field_mappings 仓储（跨系统字段映射配置）。"""

    VALID_TRANSFORMS = {"none", "map", "format", "join"}

    async def create(self, m: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO field_mappings (id, name, connector_id, source_field,
                                               target_field, transform, enabled,
                                               created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (m["id"], m["name"], m["connector_id"], m["source_field"],
                 m["target_field"],
                 json.dumps(m.get("transform") or {"type": "none"}, ensure_ascii=False),
                 int(m.get("enabled", True)), _now_ms(), _now_ms()),
            )
            await db.commit()
            return await self.get(m["id"])
        finally:
            await db.close()

    async def get(self, mapping_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute("SELECT * FROM field_mappings WHERE id = ?", (mapping_id,))
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self, connector_id: str | None = None) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            if connector_id:
                rows = await db.execute(
                    "SELECT * FROM field_mappings WHERE connector_id = ? ORDER BY name",
                    (connector_id,),
                )
            else:
                rows = await db.execute("SELECT * FROM field_mappings ORDER BY name")
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def update(self, mapping_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            if "transform" in fields and isinstance(fields["transform"], dict):
                fields["transform"] = json.dumps(fields["transform"], ensure_ascii=False)
            sets = ", ".join(f"{k} = ?" for k in fields)
            await db.execute(f"UPDATE field_mappings SET {sets}, updated_at = ? WHERE id = ?",
                             list(fields.values()) + [_now_ms(), mapping_id])
            await db.commit()
            return await self.get(mapping_id)
        finally:
            await db.close()

    async def delete(self, mapping_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM field_mappings WHERE id = ?", (mapping_id,))
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        tr = row["transform"]
        try:
            tr = json.loads(tr) if tr else {"type": "none"}
        except (TypeError, ValueError):
            tr = {"type": "none"}
        return {
            "id": row["id"],
            "name": row["name"],
            "connectorId": row["connector_id"],
            "sourceField": row["source_field"],
            "targetField": row["target_field"],
            "transform": tr,
            "enabled": bool(row["enabled"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


# 单例
project_repo = ProjectRepo()
project_member_repo = ProjectMemberRepo()
project_task_repo = ProjectTaskRepo()
project_asset_repo = ProjectAssetRepo()
field_mapping_repo = FieldMappingRepo()
