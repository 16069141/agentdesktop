"""语音接口：TTS 合成（macOS say）+ ASR 转写（faster-whisper 本地离线）。

P0.6 语音交互：
- POST /api/speech/synthesize   {text, voice?} → {path, url}  文本转语音（WAV）
- POST /api/speech/transcribe   multipart 音频（webm/wav/m4a…）→ {text}  语音转文本
- GET  /api/speech/audio/{name} → WAV 文件（供 <audio> 播放）

设计原则：
    - 全本地：TTS 用系统 say（零依赖）；ASR 用 faster-whisper 本地推理，离线可用；
    - 音频落盘 data/uploads/speech/，通过 /api/speech/audio/{name} 访问；
    - 模型首次加载较慢（下载 + 载入），之后常驻内存复用；
    - 所有文件名安全化，防路径穿越。
"""

import base64
import io
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/speech", tags=["speech"])

# 音频落盘目录（与上传文件同根，便于统一备份/清理）
SPEECH_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads" / "speech"
SPEECH_DIR.mkdir(parents=True, exist_ok=True)

MAX_AUDIO_BYTES = 50 * 1024 * 1024  # 50 MB 录音上限
MAX_TTS_CHARS = 8000  # 单次朗读文本上限（say 长文本也会正常生成，只是没必要）

# 本地捆绑的 whisper 模型（agent/data/models/whisper-base，从 modelscope 预下载，
# 离线可用）；不存在时回退到联网从 HuggingFace 拉取 "base"。
LOCAL_WHISPER_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "whisper-base"

_whisper_model = None
_whisper_lock = None  # 首次加载串行化


def _find_zh_voice() -> str:
    """选择系统中文本最匹配的中文声线。

    坑：macOS 的 Eddy/Samantha 等是多语言声线，默认语言说中文会静音（输出近空音频）。
    必须选明确的 zh_CN 声线：优先 Tingting / Meijia / Sinji，否则取首个 zh_CN。
    """
    try:
        out = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, timeout=10
        ).stdout
        lines = out.splitlines()
        for name in ("Tingting", "Meijia", "Sinji"):
            for line in lines:
                if line.startswith(name) and re.search(r"zh_CN|zh-CN", line, re.I):
                    return name
        for line in lines:
            if re.search(r"zh_CN|zh-CN", line, re.I):
                head = line.split()[0]
                if head:
                    return head
    except Exception:  # noqa: BLE001
        pass
    return "Tingting"


def _synthesize_macos(text: str, voice: str, wav_path: Path) -> None:
    """macOS：say 输出 AIFF → afconvert 转 16bit PCM WAV（浏览器可直放）。"""
    aiff_path = SPEECH_DIR / f"{wav_path.name}.aiff"
    try:
        subprocess.run(
            ["say", "-v", voice, "-o", str(aiff_path), text],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if not aiff_path.exists() or aiff_path.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="语音合成失败（say 未产出音频）")
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff_path), str(wav_path)],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if not wav_path.exists():
            raise HTTPException(status_code=500, detail="语音格式转换失败（afconvert）")
    finally:
        aiff_path.unlink(missing_ok=True)


def _synthesize_windows(text: str, wav_path: Path) -> None:
    """Windows：PowerShell System.Speech（SAPI）直接输出 WAV，优先中文声线。

    Windows 无 say/afconvert；SAPI 是系统自带，零依赖。
    文本经 base64 传入，规避 PowerShell 引号转义地狱；路径转正斜杠防反斜杠转义。
    """
    b64 = base64.b64encode(text.encode("utf-8")).decode()
    wav_posix = str(wav_path).replace("\\", "/")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        f"$t=[System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}'));"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v=$s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1;"
        "if($v){ try{ $s.SelectVoice($v.VoiceInfo.Name) }catch{} };"
        f"$s.SetOutputToWaveFile('{wav_posix}');"
        "$s.Speak($t);$s.Dispose()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        timeout=90,
        check=False,
    )
    if not wav_path.exists() or wav_path.stat().st_size == 0:
        raise HTTPException(
            status_code=500,
            detail="语音合成失败（Windows SAPI 未产出音频，请确认系统安装了语音声线）",
        )


def _find_ffmpeg():
    """定位 ffmpeg：PATH → 内置 data/bin/ffmpeg(.exe)。找不到返回 None。"""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    found = shutil.which(exe)
    if found:
        return [found]
    bundled = Path(__file__).resolve().parent.parent.parent / "data" / "bin" / exe
    if bundled.exists():
        return [str(bundled)]
    return None


def _decode_wav_pyav(data: bytes):
    """ffmpeg 不可用时的兜底：PyAV 解码 → 16k 单声道 float32（faster-whisper 直接吃）。"""
    try:
        import av
        import numpy as np
    except ImportError:
        return None
    try:
        container = av.open(io.BytesIO(data))
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            return None
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        chunks = []
        for frame in container.decode(stream):
            for rf in resampler.resample(frame):
                chunks.append(rf.to_ndarray())
        for rf in resampler.resample(None):  # flush 尾部
            chunks.append(rf.to_ndarray())
        container.close()
        if not chunks:
            return None
        mono = np.concatenate(chunks, axis=1)[0]
        return mono.astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001
        return None
    """惰性加载 faster-whisper 模型（base, int8, CPU），进程内复用。"""
    global _whisper_model, _whisper_lock
    if _whisper_model is not None:
        return _whisper_model
    if _whisper_lock is None:
        import threading

        _whisper_lock = threading.Lock()
    with _whisper_lock:
        if _whisper_model is not None:
            return _whisper_model
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="语音识别依赖未安装（faster-whisper），请先安装："
                "python -m pip install faster-whisper",
            )
        logger.info("[speech] 首次加载 whisper base 模型（CPU int8）…")
        model_ref = (
            str(LOCAL_WHISPER_DIR)
            if (LOCAL_WHISPER_DIR / "model.bin").exists()
            else "base"
        )
        _whisper_model = WhisperModel(model_ref, device="cpu", compute_type="int8")
        logger.info(f"[speech] whisper 模型就绪（{model_ref}）")
        return _whisper_model


def _safe_name(name: str) -> str:
    """只保留字母数字 . _ -，防路径穿越。"""
    safe = "".join(c for c in name if c.isalnum() or c in "._-")
    return safe or "audio.wav"


@router.post("/synthesize")
async def synthesize(payload: dict) -> dict:
    """文本转语音（TTS）。body: {text, voice?} → {path, url}"""
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本为空")
    if len(text) > MAX_TTS_CHARS:
        raise HTTPException(
            status_code=400, detail=f"文本过长（{len(text)} 字），上限 {MAX_TTS_CHARS} 字"
        )
    voice = str(payload.get("voice") or _find_zh_voice())

    name = f"tts-{int(time.time() * 1000)}.wav"
    wav_path = SPEECH_DIR / name
    try:
        if os.name == "nt":
            _synthesize_windows(text, wav_path)
        else:
            _synthesize_macos(text, voice, wav_path)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[speech] 合成失败: {exc}")
        raise HTTPException(status_code=500, detail=f"语音合成失败：{exc}")

    logger.info(f"[speech] TTS 完成: voice={voice} chars={len(text)} → {name}")
    return {"path": str(wav_path), "url": f"/api/speech/audio/{name}", "voice": voice}


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)) -> dict:
    """语音转文本（ASR）。multipart 字段 file，支持 webm/wav/m4a/mp3。"""
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail=f"音频过大（{len(data) / 1024 / 1024:.1f} MB）")

    tag = uuid.uuid4().hex[:8]
    tmp_in = SPEECH_DIR / f"in-{tag}.webm"
    tmp_wav = SPEECH_DIR / f"in-{tag}.wav"
    try:
        tmp_in.write_bytes(data)
        # 统一转 16k 单声道 WAV 再喂 whisper（兼容 webm/opus 等编码）
        ffmpeg = _find_ffmpeg()
        wav_ok = False
        if ffmpeg is not None:
            r = subprocess.run(
                [*ffmpeg, "-y", "-i", str(tmp_in), "-ar", "16000", "-ac", "1", str(tmp_wav)],
                capture_output=True,
                timeout=120,
                check=False,
            )
            wav_ok = tmp_wav.exists() and tmp_wav.stat().st_size > 0
            if not wav_ok:
                logger.warning(
                    "[speech] ffmpeg 解码失败，尝试 PyAV 兜底: %s",
                    (r.stderr or b"").decode("utf-8", errors="replace")[-200:],
                )

        model = _get_whisper_model()
        if wav_ok:
            segments, _info = model.transcribe(
                str(tmp_wav), language="zh", vad_filter=True, beam_size=1
            )
        else:
            audio = _decode_wav_pyav(data)
            if audio is None:
                detail = "音频解码失败：ffmpeg 不可用且 PyAV 无法解码该格式"
                raise HTTPException(status_code=400, detail=detail)
            segments, _info = model.transcribe(
                audio, language="zh", vad_filter=True, beam_size=1
            )
        text = "".join(seg.text for seg in segments).strip()
        if not text:
            return {"text": "", "language": "zh"}
        return {"text": text, "language": "zh"}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[speech] 转写失败: {exc}")
        raise HTTPException(status_code=500, detail=f"语音转写失败：{exc}")
    finally:
        tmp_in.unlink(missing_ok=True)
        tmp_wav.unlink(missing_ok=True)


@router.get("/audio/{name}")
async def get_audio(name: str):
    """返回合成的 WAV 音频（供 <audio> 标签播放）。"""
    safe = _safe_name(Path(name).name)
    path = SPEECH_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="音频不存在或已过期")
    return FileResponse(path, media_type="audio/wav")
