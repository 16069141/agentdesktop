"""图片解析（P0.9 视觉输入）：
- data URL 落盘 → 本机 HTTP URL（供本地视觉模型 image_url）
- OCR 文字提取（macOS Apple Vision / Windows Media.Ocr）→ 文本注入
  （远程兼容网关访问不到本机 URL 时，文字型截图靠 OCR 依然可读）

依赖：
- macOS: ocrmac（Apple Vision framework，离线、中文准）
- Windows: PowerShell Windows.Media.Ocr（Win10 1803+，随系统自带，
  中文识别依赖系统中文语言包；无包时返回空字符串，不抛错）
"""
from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# 后端数据目录：agent/data/uploads/images（与 files.py UPLOADS_DIR 一致；
# 本文件在 app/ 下，上溯两级即 agent/）
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
IMAGES_DIR = _DATA_DIR / "uploads" / "images"

# OCR 结果上限，防止截图文字过多撑爆上下文
OCR_MAX_CHARS = 4000

_DATA_URL_RE = re.compile(r"data:image/(png|jpe?g|webp|gif);base64,(.+)", re.S)

_EXT_MAP = {
    "png": ".png",
    "jpeg": ".jpg",
    "jpg": ".jpg",
    "webp": ".webp",
    "gif": ".gif",
}


def backend_port() -> int:
    """后端监听端口（与 main.py 一致）。"""
    return int(os.environ.get("AGENT_PORT", "8766"))


def is_local_base_url(base_url: str) -> bool:
    """判断模型网关是否在本机（本机网关才能访问 127.0.0.1 图片 URL）。"""
    base_url = (base_url or "").strip().lower()
    return (
        base_url.startswith("http://127.0.0.1")
        or base_url.startswith("http://localhost")
        or base_url.startswith("http://0.0.0.0")
        or base_url.startswith("http://[::1]")
    )


def save_data_url_image(data_url: str) -> tuple[Path, str] | None:
    """把 data:image/...;base64,... 落盘到 uploads/images/。

    返回 (绝对路径, 文件名)；非 data URL 或解码失败返回 None。
    """
    if not isinstance(data_url, str):
        return None
    m = _DATA_URL_RE.match(data_url)
    if not m:
        return None
    fmt, b64 = m.group(1), m.group(2)
    try:
        raw = base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[vision] data URL base64 解码失败: %s", exc)
        return None
    if not raw:
        return None
    ext = _EXT_MAP.get(fmt, ".png")
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"chat-{uuid.uuid4().hex[:12]}{ext}"
    path = IMAGES_DIR / name
    try:
        path.write_bytes(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[vision] 图片落盘失败: %s", exc)
        return None
    return path, name


def image_http_url(name: str) -> str:
    """本机可访问的图片 URL（供本地视觉模型 image_url 使用）。"""
    return f"http://127.0.0.1:{backend_port()}/api/files/images/{name}"


def ocr_image(path: Path) -> str:
    """OCR 提取图片文字。失败返回空字符串（不抛错，调用方忽略即可）。

    macOS 用 Apple Vision（ocrmac，离线）；Windows 用系统 Media.Ocr。
    """
    if not path or not path.exists():
        return ""
    try:
        if os.name == "nt":
            return _ocr_windows(path)
        return _ocr_macos(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[vision] OCR 失败: %s", exc)
        return ""


def _ocr_macos(path: Path) -> str:
    try:
        from ocrmac import ocrmac

        res = ocrmac.OCR(
            str(path), language_preference=["zh-Hans", "en-US"]
        ).recognize()
        lines = [t for t, _conf, _box in res if t]
        text = "\n".join(lines).strip()
        return text[:OCR_MAX_CHARS] if text else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("[vision] macOS OCR(ocrmac) 不可用: %s", exc)
        return ""


def _ocr_windows(path: Path) -> str:
    """Windows 系统 OCR（Windows.Media.Ocr，Win10 1803+）。

    通过 PowerShell 调用，与 speech.py 的 SAPI 分支同模式；
    无中文语言包时返回空字符串。
    """
    ps_script = (
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
        "[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime]|Out-Null;"
        "function Await($WinRtTask, $ResultType) {"
        "$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? {"
        "$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and "
        "$_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'"
        "})[0];"
        "$asTaskGeneric = $asTask.MakeGenericMethod($ResultType);"
        "$netTask = $asTaskGeneric.Invoke($null, @($WinRtTask));"
        "$netTask.Wait(-1) | Out-Null;"
        "$netTask.Result;"
        "}"
        "$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($args[0])) "
        "([Windows.Storage.StorageFile]);"
        "$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) "
        "([Windows.Storage.Streams.IRandomAccessStream]);"
        "$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) "
        "([Windows.Graphics.Imaging.BitmapDecoder]);"
        "$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) "
        "([Windows.Graphics.Imaging.SoftwareBitmap]);"
        "$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages();"
        "if ($null -eq $engine) { exit 0 };"
        "$result = Await ($engine.RecognizeAsync($bitmap)) "
        "([Windows.Media.Ocr.OcrResult]);"
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
        "Write-Output $result.Text;"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script, str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        text = (r.stdout or "").strip()
        return text[:OCR_MAX_CHARS] if text else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("[vision] Windows OCR 不可用: %s", exc)
        return ""


def describe_image(data_url: str) -> dict:
    """对一个 data URL 图片做完整解析（供 orchestrator 调用）。

    返回 {"ocr_text": str, "local_url": str|None, "path": str|None}：
    - ocr_text: OCR 识别文字（可能为空）
    - local_url: 落盘后的本机 HTTP URL（供本地视觉模型）
    - path: 落盘绝对路径（供工具读取）
    """
    saved = save_data_url_image(data_url)
    if not saved:
        return {"ocr_text": "", "local_url": None, "path": None}
    path, name = saved
    ocr_text = ocr_image(path)
    return {
        "ocr_text": ocr_text,
        "local_url": image_http_url(name),
        "path": str(path),
    }
