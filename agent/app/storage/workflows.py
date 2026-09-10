"""Phase B (P3) 仓储：自动化工作流（workflows / workflow_runs）。

需求 §B.4.1 自动化工作流（修改/迭代版）：
- 触发器：manual / schedule / event / webhook / db
- 步骤 DSL：condition / connector / data / project / notify
- 执行记录：status（running/success/failed/skipped）+ 每步日志
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _new_id(prefix: str = "wf") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class WorkflowRepo:
    """workflows 仓储。"""

    async def create(self, w: Dict[str, Any]) -> Dict[str, Any]:
        now = _now_ms()
        wf_id = w.get("id") or _new_id()
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO workflows (id, name, description, trigger_type,
                                          trigger_config, steps, enabled, created_by,
                                          created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (wf_id, w["name"], w.get("description", ""), w["trigger_type"],
                 json.dumps(w.get("trigger_config") or {}, ensure_ascii=False),
                 json.dumps(w.get("steps") or [], ensure_ascii=False),
                 int(w.get("enabled", 1)), w.get("created_by", ""), now, now),
            )
            await db.commit()
            return await self.get(wf_id)
        finally:
            await db.close()

    async def get(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            sql = "SELECT * FROM workflows"
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at DESC"
            rows = await db.execute(sql)
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def update(self, workflow_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            allowed = {"name", "description", "trigger_type", "trigger_config",
                       "steps", "enabled"}
            clean = {k: v for k, v in fields.items() if k in allowed}
            if not clean:
                return await self.get(workflow_id)
            if "trigger_config" in clean and isinstance(clean["trigger_config"], dict):
                clean["trigger_config"] = json.dumps(clean["trigger_config"], ensure_ascii=False)
            if "steps" in clean and isinstance(clean["steps"], list):
                clean["steps"] = json.dumps(clean["steps"], ensure_ascii=False)
            if "enabled" in clean:
                clean["enabled"] = int(bool(clean["enabled"]))
            sets = ", ".join(f"{k} = ?" for k in clean)
            await db.execute(f"UPDATE workflows SET {sets}, updated_at = ? WHERE id = ?",
                             list(clean.values()) + [_now_ms(), workflow_id])
            await db.commit()
            return await self.get(workflow_id)
        finally:
            await db.close()

    async def delete(self, workflow_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM workflow_runs WHERE workflow_id = ?", (workflow_id,))
            await db.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
            await db.commit()
        finally:
            await db.close()

    def _row_to_dict(self, row) -> Dict[str, Any]:
        d = dict(row)
        d["triggerConfig"] = json.loads(d.pop("trigger_config") or "{}")
        d["steps"] = json.loads(d.pop("steps") or "[]")
        d["enabled"] = bool(d.pop("enabled"))
        return d


class WorkflowRunRepo:
    """workflow_runs 仓储。"""

    async def create(self, run_id: str, workflow_id: str, trigger: str) -> None:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO workflow_runs (id, workflow_id, trigger, status,
                                              started_at, finished_at)
                   VALUES (?, ?, ?, 'running', ?, NULL)""",
                (run_id, workflow_id, trigger, _now_ms()),
            )
            await db.commit()
        finally:
            await db.close()

    async def finish(self, run_id: str, status: str, error: str = "",
                     logs: Optional[List[Dict[str, Any]]] = None) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE workflow_runs SET status = ?, error = ?, logs = ?, finished_at = ? WHERE id = ?",
                (status, error, json.dumps(logs or [], ensure_ascii=False), _now_ms(), run_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_by_workflow(self, workflow_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute(
                "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY started_at DESC LIMIT ?",
                (workflow_id, min(limit, 200)),
            )
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def list_all(self, limit: int = 50) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute(
                "SELECT * FROM workflow_runs ORDER BY started_at DESC LIMIT ?",
                (min(limit, 200),),
            )
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    def _row_to_dict(self, row) -> Dict[str, Any]:
        d = dict(row)
        d["logs"] = json.loads(d.pop("logs") or "[]")
        return d


# 单例
workflow_repo = WorkflowRepo()
workflow_run_repo = WorkflowRunRepo()
