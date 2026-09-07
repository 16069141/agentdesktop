"""文件上传与解析接口。

支持格式：
    - 纯文本：.txt / .md / .csv / .json / .log
    - PDF：.pdf（pypdf 提取文本层）
    - Word：.docx（python-docx 提取段落）
    - Excel：.xlsx（openpyxl 读取所有 sheet 的单元格文本）

设计原则：
    - 只提取文本层，不做 OCR（扫描件 PDF 需另行处理）；
    - 提取结果不落盘，直接返回前端，由前端随消息一并提交；
    - 单文件上限 20 MB，超出直接拒绝；
    - 解析失败时返回友好错误，不抛 500。
"""

import hashlib
import io
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

# 上传文件的落盘目录（允许 Agent 的 filesystem 工具读取原始文件）
UPLOADS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# 支持的扩展名 → 解析器标识
SUPPORTED_EXTENSIONS = {
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".csv": "text",
    ".json": "text",
    ".log": "text",
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_TEXT_CHARS = 200_000  # 提取文本上限，避免超大文档撑爆上下文


def _extract_text(file_bytes: bytes, filename: str, content_type: str) -> dict[str, Any]:
    """根据扩展名分发到对应解析器，返回提取结果。"""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    kind = SUPPORTED_EXTENSIONS.get(ext)

    if kind is None:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{ext or '未知'}。支持：txt / md / pdf / docx / xlsx",
        )

    if kind == "text":
        text = _parse_text(file_bytes)
    elif kind == "pdf":
        text = _parse_pdf(file_bytes)
    elif kind == "docx":
        text = _parse_docx(file_bytes)
    elif kind == "xlsx":
        text = _parse_xlsx(file_bytes)
    elif kind == "pptx":
        text = _parse_pptx(file_bytes)
    else:
        raise HTTPException(status_code=400, detail=f"未知解析器：{kind}")

    # 超长截断并标注
    truncated = False
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        truncated = True

    return {
        "filename": filename,
        "content_type": content_type,
        "size": len(file_bytes),
        "kind": kind,
        "extracted_text": text,
        "char_count": len(text),
        "truncated": truncated,
    }


def _parse_text(file_bytes: bytes) -> str:
    """纯文本文件：尝试 UTF-8，失败回退 GBK，再失败用 latin-1 兜底。"""
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return file_bytes.decode("utf-8", errors="replace")


def _parse_pdf(file_bytes: bytes) -> str:
    """PDF：用 pypdf 提取每一页的文本层。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise HTTPException(status_code=500, detail="PDF 解析依赖未安装（pypdf）")

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages: list[str] = []
        for i, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[files] PDF 第 {i + 1} 页提取失败: {exc}")
                page_text = ""
            if page_text.strip():
                pages.append(f"--- 第 {i + 1} 页 ---\n{page_text.strip()}")
        result = "\n\n".join(pages)
        if not result.strip():
            raise HTTPException(
                status_code=400,
                detail="PDF 未提取到文本层（可能是扫描件，需 OCR 处理）",
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[files] PDF 解析失败: {exc}")
        raise HTTPException(status_code=400, detail=f"PDF 解析失败：{exc}")


def _parse_docx(file_bytes: bytes) -> str:
    """Word docx：提取所有段落文本，含表格单元格。"""
    try:
        from docx import Document
    except ImportError:
        raise HTTPException(status_code=500, detail="Word 解析依赖未安装（python-docx）")

    try:
        doc = Document(io.BytesIO(file_bytes))
        parts: list[str] = []

        # 段落
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)

        # 表格
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))

        result = "\n".join(parts)
        if not result.strip():
            raise HTTPException(status_code=400, detail="Word 文档未提取到文本内容")
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[files] Word 解析失败: {exc}")
        raise HTTPException(status_code=400, detail=f"Word 解析失败：{exc}")


def _parse_xlsx(file_bytes: bytes) -> str:
    """Excel xlsx：读取所有 sheet，每个 sheet 输出为 Markdown 表格。"""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise HTTPException(status_code=500, detail="Excel 解析依赖未安装（openpyxl）")

    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        sheets_text: list[str] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows: list[list[str]] = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                # 跳过全空行
                if any(c.strip() for c in cells):
                    rows.append(cells)

            if not rows:
                continue

            # 取第一行作为表头（如果有）
            header = rows[0]
            body = rows[1:] if len(rows) > 1 else []

            lines = [f"### Sheet: {sheet_name}", ""]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("| " + " | ".join(["---"] * len(header)) + " |")
            for r in body:
                # 补齐列数
                while len(r) < len(header):
                    r.append("")
                lines.append("| " + " | ".join(r[: len(header)]) + " |")
            sheets_text.append("\n".join(lines))

        wb.close()
        result = "\n\n".join(sheets_text)
        if not result.strip():
            raise HTTPException(status_code=400, detail="Excel 文件未提取到有效数据")
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[files] Excel 解析失败: {exc}")
        raise HTTPException(status_code=400, detail=f"Excel 解析失败：{exc}")


def _parse_pptx(file_bytes: bytes) -> str:
    """PowerPoint pptx：提取每张幻灯片的标题、正文和表格文本。"""
    try:
        from pptx import Presentation
    except ImportError:
        raise HTTPException(status_code=500, detail="PowerPoint 解析依赖未安装（python-pptx）")

    try:
        prs = Presentation(io.BytesIO(file_bytes))
        slides_text: list[str] = []

        for slide_idx, slide in enumerate(prs.slides, start=1):
            parts: list[str] = []

            # 遍历所有形状，提取文本
            for shape in slide.shapes:
                # 文本框 / 标题 / 占位符
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = "".join(run.text for run in para.runs).strip()
                        if text:
                            parts.append(text)

                # 表格
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if cells:
                            parts.append(" | ".join(cells))

                # 分组形状（递归提取）
                if shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
                    for sub in shape.shapes:
                        if sub.has_text_frame:
                            for para in sub.text_frame.paragraphs:
                                text = "".join(run.text for run in para.runs).strip()
                                if text:
                                    parts.append(text)

            if parts:
                slides_text.append(f"--- 第 {slide_idx} 页 ---\n" + "\n".join(parts))

        result = "\n\n".join(slides_text)
        if not result.strip():
            raise HTTPException(status_code=400, detail="PowerPoint 未提取到文本内容")
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[files] PowerPoint 解析失败: {exc}")
        raise HTTPException(status_code=400, detail=f"PowerPoint 解析失败：{exc}")


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传单个文件并解析提取文本。

    请求：multipart/form-data，字段名 file
    响应：{ filename, content_type, size, kind, extracted_text, char_count, truncated }
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")

    # 读取文件内容并校验大小
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(file_bytes) / 1024 / 1024:.1f} MB），上限 20 MB",
        )

    logger.info(
        f"[files] 上传文件: {file.filename} ({len(file_bytes)} bytes, {file.content_type})"
    )

    result = _extract_text(file_bytes, file.filename, file.content_type or "")
    logger.info(
        f"[files] 解析完成: {result['filename']} → {result['char_count']} 字符"
        + ("（已截断）" if result["truncated"] else "")
    )

    # ── 落盘：保存原始文件，供 Agent 的 filesystem 工具后续读取 ──
    # 目录按文件内容哈希分片，避免同名文件互相覆盖；只读、归属明确。
    digest = hashlib.sha1(file_bytes).hexdigest()[:12]
    file_dir = UPLOADS_DIR / digest
    file_dir.mkdir(parents=True, exist_ok=True)
    # 安全化文件名：仅保留字母数字 . _ -，防止路径穿越
    safe_name = "".join(
        c for c in (file.filename or "upload") if c.isalnum() or c in "._-"
    ) or "upload.bin"
    saved_path = file_dir / safe_name
    if not saved_path.exists():
        saved_path.write_bytes(file_bytes)
    result["saved_path"] = str(saved_path)
    logger.info(f"[files] 已落盘: {saved_path}")
    return result
