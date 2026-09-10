"""SQLite 仓储扩展（Phase 2：tool_registry + usage_log）。

新增两张表：
- tool_registry：MCP 工具注册表，对齐 PHASE0-DATA-MODEL-FROZEN.md §1.4
- usage_log：用量统计，对齐 PHASE0-DATA-MODEL-FROZEN.md §1.5

注意：本文件依赖 db.py 的 init_db()，不在本文件重复建表。
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


class ToolRegistryRepo:
    """tool_registry 仓储（统一元数据中心：tool / skill / mcp / connector）。"""

    async def upsert(
        self,
        tool_id: str,
        name: str,
        description: str,
        source: str,
        enabled: bool = True,
        requires_approval: bool = False,
        kind: str = "tool",
        permission_level: str = "P2",
        version: str = "0.1.0",
        metadata: dict | None = None,
    ) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO tool_registry
                   (id, name, description, source, enabled, requires_approval,
                    last_loaded_at, kind, permission_level, version, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name,
                       description=excluded.description,
                       source=excluded.source,
                       enabled=excluded.enabled,
                       requires_approval=excluded.requires_approval,
                       kind=excluded.kind,
                       permission_level=excluded.permission_level,
                       version=excluded.version,
                       metadata=excluded.metadata,
                       last_loaded_at=excluded.last_loaded_at""",
                (
                    tool_id,
                    name,
                    description,
                    source,
                    int(enabled),
                    int(requires_approval),
                    _now_ms(),
                    kind,
                    permission_level,
                    version,
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                ),
            )
            await db.commit()
            # 直接返回构造字典，省去回查的一次连接开关（启动加速）
            return {
                "id": tool_id,
                "name": name,
                "description": description,
                "source": source,
                "enabled": enabled,
                "requiresApproval": requires_approval,
                "lastLoadedAt": _now_ms(),
                "lastUsedAt": None,
                "kind": kind,
                "permissionLevel": permission_level,
                "version": version,
                "metadata": metadata,
            }
        finally:
            await db.close()

    async def get(self, tool_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute(
                "SELECT * FROM tool_registry WHERE id = ?", (tool_id,)
            )
            row = await row.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)
        finally:
            await db.close()

    async def list_all(
        self,
        kind: str | None = None,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> List[Dict[str, Any]]:
        """列出工具条目，支持按 kind / source / enabled 筛选（P0 统一元数据中心）。"""
        sql = "SELECT * FROM tool_registry"
        conds, params = [], []
        if kind:
            conds.append("kind = ?")
            params.append(kind)
        if source:
            conds.append("source = ?")
            params.append(source)
        if enabled is not None:
            conds.append("enabled = ?")
            params.append(int(enabled))
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id"
        db = await connect()
        try:
            rows = await db.execute(sql, tuple(params))
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def toggle(self, tool_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            await db.execute(
                "UPDATE tool_registry SET enabled = ? WHERE id = ?",
                (int(enabled), tool_id),
            )
            await db.commit()
            return await self.get(tool_id)
        finally:
            await db.close()

    async def touch_last_used(self, tool_id: str) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE tool_registry SET last_used_at = ? WHERE id = ?",
                (_now_ms(), tool_id),
            )
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        metadata = row["metadata"]
        if metadata:
            try:
                metadata = json.loads(metadata)
            except (TypeError, ValueError):
                pass
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "source": row["source"],
            "enabled": bool(row["enabled"]),
            "requiresApproval": bool(row["requires_approval"]),
            "lastLoadedAt": row["last_loaded_at"],
            "lastUsedAt": row["last_used_at"],
            "kind": row["kind"],
            "permissionLevel": row["permission_level"],
            "version": row["version"],
            "metadata": metadata,
        }


class UsageLogRepo:
    """usage_log 仓储。"""

    async def insert(
        self,
        conversation_id: str,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        tool_calls: int = 0,
        cost: float = 0.0,
    ) -> int:
        db = await connect()
        try:
            cur = await db.execute(
                """INSERT INTO usage_log
                   (conversation_id, model_id, prompt_tokens, completion_tokens, tool_calls, cost, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    conversation_id,
                    model_id,
                    prompt_tokens,
                    completion_tokens,
                    tool_calls,
                    cost,
                    _now_ms(),
                ),
            )
            await db.commit()
            return cur.lastrowid
        finally:
            await db.close()

    async def recent(self, n: int = 7) -> List[Dict[str, Any]]:
        """返回最近 n 条用量记录（含会话标题），按 created_at 倒序。"""
        db = await connect()
        try:
            rows = await db.execute(
                """SELECT u.*, c.title as conversation_title, c.model_id as conv_model_id
                   FROM usage_log u
                   LEFT JOIN conversations c ON u.conversation_id = c.id
                   ORDER BY u.created_at DESC
                   LIMIT ?""",
                (n,),
            )
            results = []
            for r in await rows.fetchall():
                results.append(
                    {
                        "id": r["id"],
                        "conversationId": r["conversation_id"],
                        "conversationTitle": r["conversation_title"],
                        "modelId": r["model_id"],
                        "promptTokens": r["prompt_tokens"],
                        "completionTokens": r["completion_tokens"],
                        "toolCalls": r["tool_calls"],
                        "cost": r["cost"],
                        "createdAt": r["created_at"],
                    }
                )
            return results
        finally:
            await db.close()

    async def totals_by_day(self, days: int = 7) -> List[Dict[str, Any]]:
        """按天聚合（用于图表）。"""
        db = await connect()
        try:
            rows = await db.execute(
                """SELECT date(created_at / 1000, 'unixepoch', 'localtime') as day,
                          SUM(prompt_tokens) as prompt,
                          SUM(completion_tokens) as completion,
                          SUM(tool_calls) as tools,
                          SUM(cost) as cost
                   FROM usage_log
                   WHERE created_at >= (strftime('%s', 'now', '-{} days') * 1000)
                   GROUP BY day
                   ORDER BY day DESC""".format(days)
            )
            return [
                {
                    "day": r["day"],
                    "promptTokens": r["prompt"],
                    "completionTokens": r["completion"],
                    "toolCalls": r["tools"],
                    "cost": r["cost"],
                }
                for r in await rows.fetchall()
            ]
        finally:
            await db.close()

    async def totals_by_model(self, days: int = 30) -> List[Dict[str, Any]]:
        """按模型聚合（P4 成本精细化：单模型 token/调用/成本）。"""
        db = await connect()
        try:
            rows = await db.execute(
                """SELECT model_id,
                          SUM(prompt_tokens) as prompt,
                          SUM(completion_tokens) as completion,
                          SUM(tool_calls) as tools,
                          SUM(cost) as cost,
                          COUNT(*) as calls
                   FROM usage_log
                   WHERE created_at >= (strftime('%s', 'now', '-{} days') * 1000)
                   GROUP BY model_id
                   ORDER BY cost DESC""".format(days)
            )
            return [
                {
                    "model": r["model_id"] or "unknown",
                    "calls": r["calls"],
                    "promptTokens": r["prompt"],
                    "completionTokens": r["completion"],
                    "toolCalls": r["tools"],
                    "cost": r["cost"],
                }
                for r in await rows.fetchall()
            ]
        finally:
            await db.close()


# 单例
tool_registry_repo = ToolRegistryRepo()
usage_log_repo = UsageLogRepo()


# ============ Phase B (P0)：企业级安全底座仓储 ============


class EnterpriseUsersRepo:
    """enterprise_users 仓储（统一认证与权限 §2.3）。"""

    async def upsert(
        self,
        user_id: str,
        username: str,
        display_name: str = "",
        role: str = "member",
        data_scope: str = "personal",
        department: str = "",
        sso_provider: str | None = None,
        sso_subject: str | None = None,
        status: bool = True,
    ) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO enterprise_users
                   (id, username, display_name, role, data_scope, department,
                    sso_provider, sso_subject, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       username=excluded.username,
                       display_name=excluded.display_name,
                       role=excluded.role,
                       data_scope=excluded.data_scope,
                       department=excluded.department,
                       sso_provider=excluded.sso_provider,
                       sso_subject=excluded.sso_subject,
                       status=excluded.status,
                       updated_at=excluded.updated_at""",
                (
                    user_id,
                    username,
                    display_name,
                    role,
                    data_scope,
                    department,
                    sso_provider,
                    sso_subject,
                    int(status),
                    _now_ms(),
                    _now_ms(),
                ),
            )
            await db.commit()
            return await self.get(user_id)
        finally:
            await db.close()

    async def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute(
                "SELECT * FROM enterprise_users WHERE id = ?", (user_id,)
            )
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute(
                "SELECT * FROM enterprise_users WHERE username = ?", (username,)
            )
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute("SELECT * FROM enterprise_users ORDER BY username")
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def remove(self, user_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM enterprise_users WHERE id = ?", (user_id,))
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "displayName": row["display_name"],
            "role": row["role"],
            "dataScope": row["data_scope"],
            "department": row["department"],
            "ssoProvider": row["sso_provider"],
            "ssoSubject": row["sso_subject"],
            "status": bool(row["status"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


class AuditLogRepo:
    """audit_logs 仓储（追加只写 —— 不提供 UPDATE/DELETE 接口）。"""

    async def insert(
        self,
        conversation_id: str = "",
        session_id: str = "",
        actor: str = "local",
        tool_name: str = "",
        action: str = "execute",
        params_redacted: str = "{}",
        result_preview: str = "",
        risk_level: str = "low",
        approved: bool | None = None,
        latency_ms: int = 0,
    ) -> int:
        db = await connect()
        try:
            cur = await db.execute(
                """INSERT INTO audit_logs
                   (conversation_id, session_id, actor, tool_name, action,
                    params_redacted, result_preview, risk_level, approved,
                    latency_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    conversation_id,
                    session_id,
                    actor,
                    tool_name,
                    action,
                    params_redacted,
                    result_preview,
                    risk_level,
                    None if approved is None else int(approved),
                    latency_ms,
                    _now_ms(),
                ),
            )
            await db.commit()
            return cur.lastrowid
        finally:
            await db.close()

    async def query(
        self,
        conversation_id: str | None = None,
        tool_name: str | None = None,
        actor: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """审计查询 / 操作回放：按会话或工具名串联完整调用链。"""
        sql = "SELECT * FROM audit_logs"
        conds, params = [], []
        if conversation_id:
            conds.append("conversation_id = ?")
            params.append(conversation_id)
        if tool_name:
            conds.append("tool_name = ?")
            params.append(tool_name)
        if actor:
            conds.append("actor = ?")
            params.append(actor)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        db = await connect()
        try:
            rows = await db.execute(sql, tuple(params))
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def replay(self, conversation_id: str) -> List[Dict[str, Any]]:
        """操作回放：按时间正序返回某会话的完整工具调用链。"""
        db = await connect()
        try:
            rows = await db.execute(
                "SELECT * FROM audit_logs WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            )
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def replay_with_summary(self, conversation_id: str) -> Dict[str, Any]:
        """操作回放（P4 优化）：完整调用链 + 结构化摘要（成功/失败/耗时/工具分布）。"""
        steps = await self.replay(conversation_id)
        ok = sum(
            1 for s in steps if s.get("action") != "rejected" and not s.get("error")
        )
        failed = sum(1 for s in steps if s.get("error"))
        rejected = sum(1 for s in steps if s.get("action") == "rejected")
        total_ms = sum(int(s.get("latencyMs") or 0) for s in steps)
        tools: Dict[str, int] = {}
        for s in steps:
            t = (s.get("toolName") or "unknown").split(":")[0]
            tools[t] = tools.get(t, 0) + 1
        return {
            "conversationId": conversation_id,
            "steps": steps,
            "summary": {
                "total": len(steps),
                "ok": ok,
                "failed": failed,
                "rejected": rejected,
                "totalLatencyMs": total_ms,
                "avgLatencyMs": round(total_ms / len(steps)) if steps else 0,
                "tools": dict(sorted(tools.items(), key=lambda x: -x[1])),
            },
        }

    async def count(self) -> int:
        db = await connect()
        try:
            row = await db.execute("SELECT COUNT(*) AS c FROM audit_logs")
            r = await row.fetchone()
            return int(r["c"])
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "conversationId": row["conversation_id"],
            "sessionId": row["session_id"],
            "actor": row["actor"],
            "toolName": row["tool_name"],
            "action": row["action"],
            "paramsRedacted": row["params_redacted"],
            "resultPreview": row["result_preview"],
            "riskLevel": row["risk_level"],
            "approved": row["approved"],
            "latencyMs": row["latency_ms"],
            "createdAt": row["created_at"],
        }


class SkillsRepo:
    """skills 仓储（Skill 集成技术方案 §2.7）。"""

    async def upsert(
        self,
        skill_id: str,
        name: str,
        version: str = "0.1.0",
        description: str = "",
        kind: str = "skill",
        permission_level: str = "P2",
        source: str = "builtin",
        source_url: str = "",
        manifest: dict | None = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO skills
                   (id, name, version, description, kind, permission_level,
                    source, source_url, manifest, enabled, installed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name,
                       version=excluded.version,
                       description=excluded.description,
                       kind=excluded.kind,
                       permission_level=excluded.permission_level,
                       source=excluded.source,
                       source_url=excluded.source_url,
                       manifest=excluded.manifest,
                       enabled=excluded.enabled""",
                (
                    skill_id,
                    name,
                    version,
                    description,
                    kind,
                    permission_level,
                    source,
                    source_url,
                    json.dumps(manifest, ensure_ascii=False) if manifest else None,
                    int(enabled),
                    _now_ms(),
                ),
            )
            await db.commit()
            # 直接返回构造字典，省去回查的一次连接开关（启动加速）
            return {
                "id": skill_id,
                "name": name,
                "version": version,
                "description": description,
                "kind": kind,
                "permissionLevel": permission_level,
                "source": source,
                "sourceUrl": source_url,
                "manifest": manifest,
                "enabled": enabled,
                "installedAt": _now_ms(),
            }
        finally:
            await db.close()

    async def get(self, skill_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute("SELECT * FROM skills ORDER BY id")
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def remove(self, skill_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        manifest = row["manifest"]
        if manifest:
            try:
                manifest = json.loads(manifest)
            except (TypeError, ValueError):
                pass
        return {
            "id": row["id"],
            "name": row["name"],
            "version": row["version"],
            "description": row["description"],
            "kind": row["kind"],
            "permissionLevel": row["permission_level"],
            "source": row["source"],
            "sourceUrl": row["source_url"],
            "manifest": manifest,
            "enabled": bool(row["enabled"]),
            "installedAt": row["installed_at"],
        }


# 单例
enterprise_users_repo = EnterpriseUsersRepo()
audit_log_repo = AuditLogRepo()
skills_repo = SkillsRepo()
