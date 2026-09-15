"""P0.8 视频生成工具：文生视频（异步任务式）。

兼容两类端点（自动探测）：
  - OpenAI 风格：POST {base_url}/videos/generations → {id}；GET {base_url}/videos/generations/{id} → {status, data:[{url}]}
  - 火山方舟 Seedance：POST {base_url}/contents/generations/tasks → {id}；GET {base_url}/contents/generations/tasks/{id} → {status, content:{video_url}}
  同步返回 data[0].url 的服务也支持（一步完成，不轮询）。

配置（设置页「视频生成」）：
  video_api.base_url + video_api.model（settings.json / PUT /api/settings）
  API Key 存钥匙串（ref=video-api:key）

行为契约：
  - 未配置 → success=false + 配置引导（不抛错）；
  - prompt 必填；size/duration 可选（默认 1280x720 / 5s）；
  - 轮询上限约 8 分钟，超时返回任务仍在进行（含 task id）而非假失败；
  - 产物落盘 data/uploads/videos/，返回 path（进交付卡片）+ url（前端 <video> 预览）。
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import BaseTool

logger = logging.getLogger(__name__)

VIDEOS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads" / "videos"

_SUPPORTED_SIZES = {"1280x720", "720x1280", "1920x1080", "1080x1920", "1024x1024"}
_MAX_POLL_SECONDS = 480  # 8 分钟
_POLL_INTERVAL = 5


class GenerateVideoTool(BaseTool):
    name = "generate_video"
    description = (
        "根据文字描述生成视频（文生视频，含画面与动态）。"
        "需要先配置视频生成服务（设置页「视频生成」填入服务地址与 API Key；"
        "兼容 OpenAI 风格端点与火山方舟 Seedance）。\n"
        "用法：prompt 必填，描述画面内容、镜头运动、风格、光线与节奏；"
        "size 可选（默认 1280x720，支持 720x1280 竖屏等）；duration 可选（5 或 10 秒）。\n"
        "生成是异步任务，通常需要 1-5 分钟，完成后交付 mp4 文件。"
        "适合宣传片/短视频/产品演示/概念演示；不适合精确字幕或长对话剧情。"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "视频描述：主体、场景、镜头运动（推近/环绕/平移）、风格、光线、节奏",
            },
            "size": {
                "type": "string",
                "enum": ["1280x720", "720x1280", "1920x1080", "1080x1920", "1024x1024"],
                "description": "分辨率（默认 1280x720；竖屏用 720x1280）",
            },
            "duration": {
                "type": "integer",
                "enum": [5, 10],
                "description": "时长秒数（默认 5；服务不支持时忽略）",
            },
        },
        "required": ["prompt"],
    }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        prompt = (arguments.get("prompt") or "").strip()
        if not prompt:
            return {"success": False, "error": "prompt 不能为空"}
        size = str(arguments.get("size") or "1280x720")
        if size not in _SUPPORTED_SIZES:
            size = "1280x720"
        duration = int(arguments.get("duration") or 5)
        if duration not in (5, 10):
            duration = 5

        # ── 读配置 ──
        try:
            from ..api.settings import _load_settings
            from ..security import keychain
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"读取配置失败：{exc}"}

        cfg = _load_settings().get("video_api") or {}
        base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
        model = str(cfg.get("model") or "").strip()
        api_key = keychain.retrieve_sync("video-api:key") or ""

        if not base_url or not model or not api_key:
            return {
                "success": False,
                "error": (
                    "视频生成服务未配置。请在「设置 → 视频生成」填入："
                    "① 服务地址（火山方舟 Seedance 用 https://ark.cn-beijing.volces.com/api/v3，"
                    "或 OpenAI 兼容视频服务地址）② 模型名 ③ API Key。"
                ),
            }

        try:
            import httpx
        except ImportError:
            return {"success": False, "error": "依赖缺失：httpx 未安装"}

        headers = {"Authorization": f"Bearer {api_key}"}
        payload: Dict[str, Any] = {"model": model, "prompt": prompt, "size": size}
        if duration:
            payload["duration"] = duration

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # ── 提交任务：优先 OpenAI 风格，404/405 回退 Ark 风格 ──
                resp = await client.post(
                    f"{base_url}/videos/generations", json=payload, headers=headers
                )
                task_url: Optional[str] = None
                result_url: Optional[str] = None
                task_id: Optional[str] = None
                poll_path: Optional[str] = None

                if resp.status_code in (404, 405, 501):
                    ark_payload = {
                        "model": model,
                        "content": [{"type": "text", "text": prompt}],
                    }
                    resp = await client.post(
                        f"{base_url}/contents/generations/tasks",
                        json=ark_payload,
                        headers=headers,
                    )

                if resp.status_code >= 400:
                    return {
                        "success": False,
                        "error": f"视频服务提交失败 HTTP {resp.status_code}：{resp.text[:300]}",
                    }
                body = resp.json()
                data = body.get("data") or []
                if data and data[0].get("url"):
                    result_url = data[0]["url"]  # 同步返回
                else:
                    task_id = body.get("id")
                    if task_id:
                        poll_path = f"/videos/generations/{task_id}"

                # ── 异步任务：轮询（OpenAI 风格路径失败则回退 Ark 风格路径）──
                if task_id and not result_url:
                    started = time.monotonic()
                    while time.monotonic() - started < _MAX_POLL_SECONDS:
                        await asyncio.sleep(_POLL_INTERVAL)
                        pr = await client.get(f"{base_url}{poll_path}", headers=headers)
                        if pr.status_code in (404, 405):
                            # 回退 Ark：GET /contents/generations/tasks/{id}
                            poll_path = f"/contents/generations/tasks/{task_id}"
                            pr = await client.get(f"{base_url}{poll_path}", headers=headers)
                        if pr.status_code >= 400:
                            return {
                                "success": False,
                                "error": f"查询任务失败 HTTP {pr.status_code}：{pr.text[:300]}",
                            }
                        pbody = pr.json()
                        status = str(pbody.get("status") or "").lower()
                        if status in ("failed", "cancelled", "error"):
                            return {
                                "success": False,
                                "error": f"视频生成失败（任务 {task_id}，状态 {status}）",
                            }
                        if status in ("succeeded", "completed", "success"):
                            pdata = pbody.get("data") or []
                            result_url = (
                                pdata[0].get("url")
                                if pdata and isinstance(pdata[0], dict)
                                else ""
                            )
                            if not result_url:
                                content = pbody.get("content") or {}
                                result_url = (
                                    content.get("video_url")
                                    if isinstance(content, dict)
                                    else ""
                                )
                            if result_url:
                                break
                    if not result_url:
                        return {
                            "success": False,
                            "error": (
                                f"视频仍在生成中（任务 {task_id}，已等待 "
                                f"{int(time.monotonic() - started)} 秒）。"
                                "可稍后重试同一描述。"
                            ),
                        }

                if not result_url:
                    return {"success": False, "error": "视频服务未返回可下载地址"}

                # ── 下载产物 ──
                vresp = await client.get(result_url, timeout=180)
                if vresp.status_code >= 400:
                    return {"success": False, "error": f"下载视频失败：HTTP {vresp.status_code}"}
                video_bytes = vresp.content
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[video] 生成失败: {exc}")
            return {"success": False, "error": f"视频生成失败：{exc}"}

        if not video_bytes:
            return {"success": False, "error": "视频服务返回空内容"}

        # ── 落盘（固定目录）──
        try:
            VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
            name = f"vid-{int(time.time() * 1000)}.mp4"
            out_path = VIDEOS_DIR / name
            out_path.write_bytes(video_bytes)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"视频保存失败：{exc}"}

        logger.info(f"[video] 生成成功: {name} ({len(video_bytes)} bytes, {size})")
        return {
            "success": True,
            "path": str(out_path),
            "url": f"/api/files/videos/{name}",
            "size": size,
            "note": "已生成视频，可点击交付卡片播放或打开。",
        }
