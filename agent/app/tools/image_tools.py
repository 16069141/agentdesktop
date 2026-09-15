"""P0.7 图像生成工具：OpenAI 兼容 images/generations 端点。

支持任何兼容该协议的图像服务：
  - 火山方舟 Seedream：base_url=https://ark.cn-beijing.volces.com/api/v3
  - OpenRouter：base_url=https://openrouter.ai/api/v1
  - 其他兼容服务（本地 SD-WebUI 等）

配置（设置页「图像生成」）：
  image_api.base_url + image_api.model（settings.json / PUT /api/settings）
  API Key 存钥匙串（ref=image-api:key，设置页写入）

行为契约：
  - 未配置 → 返回 success=false + 配置引导（不抛错，模型知道功能存在但需配置）；
  - prompt 必填；width/height 默认 1024；可传 image_reference_url 做图生图
    （服务不支持参考图时自动忽略该字段）；
  - 图片落盘 data/uploads/images/，返回 path（进交付卡片）+ url（前端可预览）。
"""

import base64
import logging
import time
from pathlib import Path
from typing import Any, Dict

from . import BaseTool

logger = logging.getLogger(__name__)

IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads" / "images"

# 默认尺寸白名单（服务方按 64 对齐；非法尺寸回落到 1024）
_ALLOWED_SIZES = {
    (1024, 1024), (768, 1024), (1024, 768), (1280, 720), (720, 1280), (1536, 1024), (1024, 1536),
}


class GenerateImageTool(BaseTool):
    name = "generate_image"
    description = (
        "根据文字描述生成图像（文生图；也可传参考图做图生图）。"
        "需要先配置图像生成服务（设置页「图像生成」填入服务地址与 API Key，"
        "默认支持火山方舟 Seedream / OpenRouter 等 OpenAI 兼容端点）。\n"
        "用法：prompt 必填，用中文或英文描述画面内容、风格、构图与细节；"
        "width/height 可选（默认 1024x1024，支持 1024、768x1024、1280x720 等常用尺寸）；"
        "image_reference_url 可选（基于参考图生成）。\n"
        "生成结果会保存为图片文件并交付。适合海报/插画/配图/头像/商品图等视觉内容；"
        "不适合生成精确文字排版（模型文字渲染不稳）。"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "画面描述：主体、场景、风格（如赛博朋克/水彩/3D渲染）、光线、构图、色彩",
            },
            "width": {"type": "integer", "description": "图片宽度（默认 1024）"},
            "height": {"type": "integer", "description": "图片高度（默认 1024）"},
            "image_reference_url": {
                "type": "string",
                "description": "参考图 URL（可选，做图生图/风格参考；服务不支持时自动忽略）",
            },
        },
        "required": ["prompt"],
    }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        prompt = (arguments.get("prompt") or "").strip()
        if not prompt:
            return {"success": False, "error": "prompt 不能为空"}
        width = int(arguments.get("width") or 1024)
        height = int(arguments.get("height") or 1024)
        if (width, height) not in _ALLOWED_SIZES:
            width, height = 1024, 1024
        ref_url = (arguments.get("image_reference_url") or "").strip()

        # ── 读配置：base_url/model 来自 settings.json，Key 来自钥匙串 ──
        try:
            from ..api.settings import _load_settings
            from ..security import keychain
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"读取配置失败：{exc}"}

        cfg = _load_settings().get("image_api") or {}
        base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
        model = str(cfg.get("model") or "").strip()
        api_key = keychain.retrieve_sync("image-api:key") or ""

        if not base_url or not model or not api_key:
            return {
                "success": False,
                "error": (
                    "图像生成服务未配置。请在「设置 → 图像生成」填入："
                    "① 服务地址（火山方舟 Seedream 用 https://ark.cn-beijing.volces.com/api/v3，"
                    "OpenRouter 用 https://openrouter.ai/api/v1）② 模型名 ③ API Key。"
                ),
            }

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": f"{width}x{height}",
            "response_format": "url",
        }
        if ref_url:
            # Seedream 兼容字段；不兼容的服务会忽略未知字段
            payload["image_reference_urls"] = [ref_url]

        try:
            import httpx
        except ImportError:
            return {"success": False, "error": "依赖缺失：httpx 未安装"}

        try:
            async with httpx.AsyncClient(timeout=240) as client:
                resp = await client.post(
                    f"{base_url}/images/generations",
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code >= 400:
                    detail = resp.text[:300]
                    return {
                        "success": False,
                        "error": f"图像服务返回 {resp.status_code}：{detail}",
                    }
                data = (resp.json().get("data") or [])
                if not data:
                    return {"success": False, "error": "图像服务未返回结果"}

                img_bytes: bytes | None = None
                img_url = data[0].get("url") or ""
                if img_url:
                    img_resp = await client.get(img_url, timeout=180)
                    if img_resp.status_code >= 400:
                        return {"success": False, "error": f"下载生成图片失败：HTTP {img_resp.status_code}"}
                    img_bytes = img_resp.content
                else:
                    b64 = data[0].get("b64_json")
                    if b64:
                        img_bytes = base64.b64decode(b64)
                if not img_bytes:
                    return {"success": False, "error": "图像服务返回空内容"}
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[image] 生成失败: {exc}")
            return {"success": False, "error": f"图像生成失败：{exc}"}

        # ── 落盘（固定目录，不写用户任意路径）──
        try:
            IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            name = f"img-{int(time.time() * 1000)}.png"
            out_path = IMAGES_DIR / name
            out_path.write_bytes(img_bytes)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"图片保存失败：{exc}"}

        logger.info(f"[image] 生成成功: {name} ({len(img_bytes)} bytes, {width}x{height})")
        return {
            "success": True,
            "path": str(out_path),
            "url": f"/api/files/images/{name}",
            "width": width,
            "height": height,
            "note": "已生成图像，可点击交付卡片打开或预览。",
        }
