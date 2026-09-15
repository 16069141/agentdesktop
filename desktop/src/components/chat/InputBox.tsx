import React, { useState, useRef } from 'react'
import type { ModelInfo, FileAttachment } from '../../types'
import { api } from '../../api'
import { useUiStore } from '../../store/useUiStore'
import WorkspaceSelector from './WorkspaceSelector'
import SkillSelector from './SkillSelector'

interface InputBoxProps {
  onSend: (message: string, images?: string[], attachments?: FileAttachment[]) => void
  /** P0 后台执行开关：开启后任务放后台跑，可关页面、随时查进度/取消/续跑 */
  bgMode?: boolean
  onBgModeChange?: (v: boolean) => void
  onStop: () => void
  isStreaming: boolean
  /** 未选中会话时禁用输入 */
  disabled?: boolean
  /** 底部 token 余量提示 */
  tokenHint?: string
  /** 可用模型列表 */
  models: ModelInfo[]
  /** 当前选中模型 ID */
  currentModelId: string
  /** 切换模型回调 */
  onModelChange: (id: string) => void
  /** 功能按钮导航回调（如切换到技能/连接器页） */
  onNavigate?: (tab: string) => void
}

/** 支持的文档扩展名 */
const DOC_EXTENSIONS = ['.txt', '.md', '.markdown', '.csv', '.json', '.log', '.pdf', '.docx', '.xlsx', '.pptx']
const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg']
const MAX_IMAGES = 4
const MAX_DOCS = 5
const MAX_FILE_SIZE = 20 * 1024 * 1024 // 20 MB

/** 文件类型 → 展示标签与颜色 */
const KIND_META: Record<string, { label: string; color: string }> = {
  text: { label: 'TXT', color: '#6b7280' },
  pdf: { label: 'PDF', color: '#ef4444' },
  docx: { label: 'DOC', color: '#3b82f6' },
  xlsx: { label: 'XLS', color: '#10b981' },
  pptx: { label: 'PPT', color: '#f59e0b' },
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function getExtension(filename: string): string {
  const idx = filename.lastIndexOf('.')
  return idx >= 0 ? filename.slice(idx).toLowerCase() : ''
}

/** 上传中的文档状态 */
interface PendingUpload {
  id: number
  filename: string
  size: number
  /** 上传进度 0-100 */
  progress: number
  /** 阶段：上传中 / 解析中 */
  phase: 'uploading' | 'parsing'
  error?: string
}

/** 输入框中的图片（本地读取带进度） */
interface ImageItem {
  id: number
  dataUrl: string
  /** 读取进度 0-100 */
  progress: number
  status: 'loading' | 'done' | 'error'
}

const InputBox: React.FC<InputBoxProps> = ({
  onSend,
  onStop,
  isStreaming,
  disabled = false,
  tokenHint = '剩余 14,200 / 16,384 tokens',
  models,
  currentModelId,
  onModelChange,
  onNavigate,
  bgMode = false,
  onBgModeChange,
}) => {
  // 对话（轻量问答）模式下不显示工作区/权限/技能/连接器这一排工作相关按钮
  const chatMode = useUiStore((s) => s.chatMode)
  const [input, setInput] = useState('')
  const [images, setImages] = useState<ImageItem[]>([])
  const [attachments, setAttachments] = useState<FileAttachment[]>([])
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([])
  const [uploading, setUploading] = useState(false)
  const [readOnly, setReadOnly] = useState(false)
  const [permission, setPermission] = useState<'全部允许' | '仅询问' | '全部拒绝'>('全部允许')
  const [toast, setToast] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pendingIdRef = useRef(0)
  /** 取消上传：id → abort 函数 */
  const abortMapRef = useRef<Record<number, () => void>>({})

  // ── P0.6 语音输入：录音 → 转写 → 填入输入框 ──
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const toggleRecord = async () => {
    if (transcribing || disabled) return
    // 停止录音 → 转写
    if (recording) {
      try {
        mediaRecorderRef.current?.stop()
      } catch {
        setRecording(false)
      }
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunksRef.current = []
      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
      }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRecording(false)
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' })
        if (blob.size === 0) return
        setTranscribing(true)
        try {
          const res = await api.speech.transcribe(blob)
          const text = (res?.text || '').trim()
          if (text) {
            setInput((prev) => (prev ? prev + text : text))
            showToast('已转写，可修改后发送')
          } else {
            showToast('未识别到语音内容')
          }
        } catch (err) {
          showToast(err instanceof Error ? err.message : '语音转写失败')
        } finally {
          setTranscribing(false)
        }
      }
      mr.start()
      mediaRecorderRef.current = mr
      setRecording(true)
    } catch (err) {
      const msg = '无法访问麦克风，请在系统设置中允许颤翎子使用麦克风'
      showToast(msg)
    }
  }

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 2200)
  }

  /** 附件按钮：打开文件选择器 */
  const handleAttachClick = () => {
    if (uploading) {
      showToast('正在上传文件，请稍候…')
      return
    }
    fileInputRef.current?.click()
  }

  /** 更新单个图片项状态 */
  const updateImage = (id: number, patch: Partial<ImageItem>) => {
    setImages((prev) => prev.map((img) => (img.id === id ? { ...img, ...patch } : img)))
  }

  /** 图片文件：带进度读取为 base64 data URL */
  const addImageFile = (file: File) => {
    const id = ++pendingIdRef.current
    setImages((prev) => [
      ...prev,
      { id, dataUrl: '', progress: 0, status: 'loading' },
    ])
    const reader = new FileReader()
    reader.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) {
        updateImage(id, { progress: Math.round((e.loaded / e.total) * 100) })
      }
    }
    reader.onload = () => {
      updateImage(id, { dataUrl: reader.result as string, progress: 100, status: 'done' })
    }
    reader.onerror = () => updateImage(id, { status: 'error' })
    reader.readAsDataURL(file)
  }

  /** 处理文件列表：图片走 base64（带进度），文档走上传解析（文件选择与拖拽共用） */
  const processFiles = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList)
    if (files.length === 0) return

    let pendingImgCount = 0
    const pendingDocs: File[] = []

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      const ext = getExtension(file.name)

      // 大小校验
      if (file.size > MAX_FILE_SIZE) {
        showToast(`${file.name} 超过 20MB 上限`)
        continue
      }

      // 图片：立即加入（本地读取带进度）
      if (file.type.startsWith('image/') || IMAGE_EXTENSIONS.includes(ext)) {
        if (images.length + pendingImgCount >= MAX_IMAGES) {
          showToast(`最多附加 ${MAX_IMAGES} 张图片`)
          continue
        }
        pendingImgCount++
        addImageFile(file)
        continue
      }

      // 文档
      if (DOC_EXTENSIONS.includes(ext)) {
        if (attachments.length + pendingDocs.length + pendingUploads.length >= MAX_DOCS) {
          showToast(`最多附加 ${MAX_DOCS} 个文档`)
          continue
        }
        pendingDocs.push(file)
        continue
      }

      showToast(`不支持的文件类型：${ext || file.name}`)
    }

    // 文档上传解析
    if (pendingDocs.length > 0) {
      setUploading(true)
      try {
        for (const file of pendingDocs) {
          // 注册上传任务
          const id = ++pendingIdRef.current
          const pending: PendingUpload = {
            id,
            filename: file.name,
            size: file.size,
            progress: 0,
            phase: 'uploading',
          }
          setPendingUploads((prev) => [...prev, pending])

          // 更新单个任务状态
          const updatePending = (patch: Partial<PendingUpload>) => {
            setPendingUploads((prev) =>
              prev.map((p) => (p.id === id ? { ...p, ...patch } : p)),
            )
          }
          const removePending = () => {
            setPendingUploads((prev) => prev.filter((p) => p.id !== id))
            delete abortMapRef.current[id]
          }

          // 上传 + 解析
          try {
            const { promise, abort } = api.files.upload(file, (percent) => {
              updatePending({ progress: percent })
            })
            abortMapRef.current[id] = abort

            const res = await promise
            // 上传完成，进入解析阶段
            updatePending({ phase: 'parsing', progress: 100 })
            // 解析完成 → 移入正式附件
            setAttachments((prev) => [
              ...prev,
              {
                filename: res.filename,
                kind: res.kind,
                extracted_text: res.extracted_text,
                char_count: res.char_count,
                size: res.size,
                truncated: res.truncated,
                savedPath: res.saved_path,
              },
            ])
            removePending()
            showToast(
              res.truncated
                ? `${res.filename} 已解析，但文件较大，内容被截断（仅前 ${res.char_count.toLocaleString()} 字符）`
                : `${res.filename} 解析完成（${res.char_count.toLocaleString()} 字符）`
            )
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err)
            // 提取后端返回的 detail
            const detailMatch = msg.match(/detail["\s:]+([^}]+)/)
            const friendly = detailMatch ? detailMatch[1].trim() : msg
            if (friendly === '已取消上传') {
              removePending()
            } else {
              updatePending({ error: friendly })
              showToast(`${file.name} 解析失败：${friendly}`)
            }
          }
        }
      } finally {
        setUploading(false)
      }
    }
  }

  /** 取消某个上传任务 */
  const cancelUpload = (id: number) => {
    const abort = abortMapRef.current[id]
    if (abort) abort()
    // 立即从列表移除（onabort 回调会再次触发移除，幂等）
    setPendingUploads((prev) => prev.filter((p) => p.id !== id))
    delete abortMapRef.current[id]
  }

  /** 移除已失败的上传任务 */
  const removeFailedUpload = (id: number) => {
    setPendingUploads((prev) => prev.filter((p) => p.id !== id))
    delete abortMapRef.current[id]
  }

  /** 处理文件选择 */
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return
    e.target.value = ''
    await processFiles(files)
  }

  /** 拖拽悬停：阻止默认行为并高亮 */
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!disabled && !uploading) setDragging(true)
  }

  /** 拖拽离开 */
  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
  }

  /** 松手：处理拖入的文件 */
  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragging(false)
    if (disabled || uploading) {
      showToast(disabled ? '请先在左侧新建或选择一个会话' : '正在上传文件，请稍候…')
      return
    }
    const files = e.dataTransfer.files
    if (files.length > 0) await processFiles(files)
  }

  /** 粘贴图片：从剪贴板读取图片文件，带进度加入预览 */
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items
    if (!items) return
    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      if (item.type.startsWith('image/')) {
        e.preventDefault()
        const file = item.getAsFile()
        if (file) {
          if (images.length >= MAX_IMAGES) {
            showToast(`最多附加 ${MAX_IMAGES} 张图片`)
            return
          }
          addImageFile(file)
        }
        break
      }
    }
  }

  /** 删除指定图片 */
  const removeImage = (id: number) => {
    setImages((prev) => prev.filter((img) => img.id !== id))
  }

  /** 删除指定文档附件 */
  const removeAttachment = (idx: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== idx))
  }

  const handleSend = () => {
    if ((!input.trim() && images.length === 0 && attachments.length === 0) || isStreaming || disabled || uploading) return
    onSend(
      input.trim(),
      images.length > 0 ? images.map((img) => img.dataUrl) : undefined,
      attachments.length > 0 ? attachments : undefined
    )
    setInput('')
    setImages([])
    setAttachments([])
    setPendingUploads([])
    abortMapRef.current = {}
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    const textarea = e.target
    textarea.style.height = 'auto'
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px'
  }

  const canSend =
    (Boolean(input.trim()) || images.length > 0 || attachments.length > 0) &&
    !isStreaming &&
    !disabled &&
    !uploading  /** 功能按钮通用样式 */
  const toolBtnStyle: React.CSSProperties = {
    background: 'var(--bg-panel)',
    border: '1px solid var(--border-soft)',
    color: 'var(--text-dim)',
  }

  return (
    <div
      className="px-4 pb-3 pt-2 border-t"
      style={{
        background: 'var(--bg-panel)',
        borderTop: '1px solid var(--border-soft)',
      }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Toast 提示 */}
      {toast && (
        <div
          className="absolute left-1/2 -translate-x-1/2 -top-10 px-3 py-1.5 rounded-lg text-xs whitespace-nowrap z-10"
          style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
        >
          {toast}
        </div>
      )}

      <div className="max-w-4xl mx-auto">
        {/* 大圆角输入框 */}
        <div
          className="rounded-2xl p-3 transition-all duration-150"
          style={{
            background: 'var(--surf-input)',
            border: dragging ? '2px dashed var(--accent)' : '1px solid var(--border)',
            boxShadow: dragging ? '0 0 0 4px color-mix(in srgb, var(--accent) 18%, transparent)' : '0 2px 8px rgba(0,0,0,0.06)',
            position: 'relative',
          }}
        >
          {/* 拖拽悬停覆盖提示 */}
          {dragging && (
            <div
              className="absolute inset-0 rounded-2xl flex items-center justify-center z-10 pointer-events-none"
              style={{
                background: 'color-mix(in srgb, var(--bg-panel) 78%, transparent)',
                backdropFilter: 'blur(1px)',
              }}
            >
              <div
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: 'var(--accent)', color: 'var(--accent-ink)' }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
                松手上传文件
              </div>
            </div>
          )}
          <textarea
            ref={textareaRef}
            className="w-full bg-transparent outline-none resize-none text-sm"
            style={{
              color: 'var(--text)',
              minHeight: '52px',
              maxHeight: '200px',
              fontFamily: 'var(--sans)',
              lineHeight: '1.5',
            }}
            placeholder={
              disabled
                ? '请先在左侧新建或选择一个会话…'
                : '输入消息…（Shift+Enter 换行，Enter 发送，可拖拽/粘贴图片，回形针上传文档）'
            }
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            disabled={disabled}
            rows={1}
          />

          {/* 附件预览区：图片 + 文档 */}
          {(images.length > 0 || attachments.length > 0 || pendingUploads.length > 0) && (
            <div className="flex gap-2 mt-2 flex-wrap">
              {/* 图片预览 */}
              {images.map((img) => (
                <div
                  key={`img-${img.id}`}
                  className="relative group"
                  style={{ width: '72px', height: '72px' }}
                >
                  {/* 图片本体（读取完成后显示） */}
                  {img.dataUrl && img.status !== 'error' && (
                    <img
                      src={img.dataUrl}
                      alt="图片"
                      className="w-full h-full object-cover rounded-lg"
                      style={{ border: '1px solid var(--border-soft)' }}
                    />
                  )}

                  {/* 读取中：占位 + 进度条 */}
                  {img.status === 'loading' && (
                    <div
                      className="w-full h-full rounded-lg flex flex-col items-center justify-center gap-1.5"
                      style={{
                        background: 'var(--bg-elev)',
                        border: '1px solid var(--border-soft)',
                      }}
                    >
                      <div className="w-10 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border-soft)' }}>
                        <div
                          className="h-full rounded-full transition-all duration-150"
                          style={{
                            width: `${Math.max(img.progress, 4)}%`,
                            background: 'var(--accent)',
                          }}
                        />
                      </div>
                      <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
                        {img.progress}%
                      </span>
                    </div>
                  )}

                  {/* 读取失败 */}
                  {img.status === 'error' && (
                    <div
                      className="w-full h-full rounded-lg flex items-center justify-center text-[10px]"
                      style={{ background: 'var(--bg-elev)', border: '1px solid var(--danger)', color: 'var(--danger)' }}
                    >
                      读取失败
                    </div>
                  )}

                  {/* 成功标记：绿色 ✓（hover 显示删除按钮） */}
                  {img.status === 'done' && (
                    <span
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center"
                      style={{ background: '#22c55e', color: '#fff' }}
                      title="图片已就绪"
                    >
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    </span>
                  )}

                  {/* 删除按钮（hover 出现；读取中/失败时可直接移除） */}
                  <button
                    className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center text-xs transition-opacity opacity-0 group-hover:opacity-100"
                    style={{
                      background: 'var(--danger)',
                      color: '#fff',
                      border: 'none',
                      cursor: 'pointer',
                      opacity: img.status === 'done' ? undefined : 0.9,
                    }}
                    onClick={() => removeImage(img.id)}
                    title="移除图片"
                  >
                    ×
                  </button>
                </div>
              ))}

              {/* 文档附件卡片（上传成功） */}
              {attachments.map((att, idx) => {
                const meta = KIND_META[att.kind] || { label: 'FILE', color: '#6b7280' }
                return (
                  <div
                    key={`doc-${idx}`}
                    className="relative flex items-center gap-2 px-2.5 py-2 rounded-lg group"
                    style={{
                      background: 'var(--bg-elev)',
                      border: '1px solid var(--border-soft)',
                      minWidth: '180px',
                      maxWidth: '240px',
                    }}
                  >
                    {/* 类型标签 */}
                    <span
                      className="flex-shrink-0 w-9 h-9 rounded flex items-center justify-center text-[10px] font-bold"
                      style={{ background: `${meta.color}22`, color: meta.color }}
                    >
                      {meta.label}
                    </span>
                    {/* 文件名 + 字符数 */}
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-medium truncate" style={{ color: 'var(--text)' }} title={att.filename}>
                        {att.filename}
                      </div>
                      <div className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
                        {att.char_count.toLocaleString()} 字符{att.truncated ? '（已截断）' : ''}
                        {att.size ? ` · ${formatSize(att.size)}` : ''}
                      </div>
                    </div>
                    {/* 成功标记：绿色 ✓（hover 显示删除按钮） */}
                    <span
                      className="flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center"
                      style={{ background: '#22c55e', color: '#fff' }}
                      title="上传成功"
                    >
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    </span>
                    {/* 删除按钮（hover 出现） */}
                    <button
                      className="absolute -top-2 -right-2 w-5 h-5 rounded-full flex items-center justify-center text-xs transition-opacity opacity-0 group-hover:opacity-100"
                      style={{
                        background: 'var(--danger)',
                        color: '#fff',
                        border: 'none',
                        cursor: 'pointer',
                      }}
                      onClick={() => removeAttachment(idx)}
                      title="移除附件"
                    >
                      ×
                    </button>
                  </div>
                )
              })}

              {/* 上传中 / 解析中 / 失败 卡片（带进度条） */}
              {pendingUploads.map((p) => {
                const meta = KIND_META.text
                const kindLabel = (() => {
                  const ext = getExtension(p.filename)
                  const m = Object.entries(KIND_META).find(([k]) => ext.includes(k))
                  return m ? m[1].label.toUpperCase() : 'FILE'
                })()
                const failed = Boolean(p.error)
                const done = p.progress >= 100 && p.phase === 'parsing'
                return (
                  <div
                    key={p.id}
                    className="relative flex items-center gap-2 px-2.5 py-2 rounded-lg"
                    style={{
                      background: 'var(--bg-elev)',
                      border: failed ? '1px solid var(--danger)' : '1px solid var(--border-soft)',
                      minWidth: '200px',
                      maxWidth: '260px',
                    }}
                  >
                    {/* 类型标签 */}
                    <span
                      className="flex-shrink-0 w-9 h-9 rounded flex items-center justify-center text-[10px] font-bold"
                      style={{ background: `${meta.color}22`, color: meta.color }}
                    >
                      {kindLabel}
                    </span>
                    {/* 文件名 + 进度 */}
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-medium truncate" style={{ color: 'var(--text)' }} title={p.filename}>
                        {p.filename}
                      </div>
                      {failed ? (
                        <div className="text-[10px] truncate" style={{ color: 'var(--danger)' }} title={p.error}>
                          {p.error}
                        </div>
                      ) : done ? (
                        <div className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
                          解析中…
                        </div>
                      ) : (
                        <div className="mt-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border-soft)' }}>
                          <div
                            className="h-full rounded-full transition-all duration-200"
                            style={{
                              width: `${Math.max(p.progress, 4)}%`,
                              background: 'var(--accent)',
                            }}
                          />
                        </div>
                      )}
                      {!failed && !done && (
                        <div className="mt-0.5 text-[9px]" style={{ color: 'var(--text-faint)' }}>
                          {p.phase === 'uploading' ? `上传中 ${p.progress}% · ${formatSize(p.size)}` : '解析中…'}
                        </div>
                      )}
                    </div>
                    {/* 取消 / 移除 */}
                    <button
                      className="flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-xs"
                      style={{
                        background: failed ? 'var(--danger)' : 'var(--border-soft)',
                        color: failed ? '#fff' : 'var(--text-dim)',
                        border: 'none',
                        cursor: 'pointer',
                      }}
                      onClick={() => (failed ? removeFailedUpload(p.id) : cancelUpload(p.id))}
                      title={failed ? '移除' : '取消上传'}
                    >
                      ×
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {/* 底部工具栏：附件 + 模型选择 + 发送 */}
          <div className="flex items-center justify-between mt-1">
            {/* 左侧：附件图标 */}
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.markdown,.csv,.json,.log,.pdf,.docx,.xlsx,.pptx,image/*"
              multiple
              className="hidden"
              onChange={handleFileChange}
            />
            <button
              className="p-1.5 rounded-lg transition-colors"
              style={{ color: uploading ? 'var(--accent)' : 'var(--text-faint)' }}
              onClick={handleAttachClick}
              title="添加附件（支持图片、PDF、Word、Excel、TXT、Markdown；也可直接拖拽文件到输入框）"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>

            {/* P0.6 语音输入：按住说话 → 转写填入输入框 */}
            <button
              className="p-1.5 rounded-lg transition-colors"
              style={{
                color: recording ? 'var(--danger)' : transcribing ? 'var(--accent)' : 'var(--text-faint)',
                animation: recording ? 'pulse 1.2s ease-in-out infinite' : undefined,
              }}
              onClick={toggleRecord}
              disabled={transcribing || disabled}
              title={recording ? '正在录音，点击停止并转写' : '语音输入：点击开始录音，再点停止转写'}
            >
              {transcribing ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="16 18 22 12 16 6" />
                  <polyline points="8 6 2 12 8 18" />
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" y1="19" x2="12" y2="23" />
                  <line x1="8" y1="23" x2="16" y2="23" />
                </svg>
              )}
            </button>

            {/* 右侧：模型选择 + 发送/停止 */}
            <div className="flex items-center gap-2">
              {/* 模型选择器 */}
              <div className="relative">
                <select
                  value={currentModelId}
                  onChange={(e) => onModelChange(e.target.value)}
                  disabled={disabled || models.length === 0}
                  className="appearance-none pl-8 pr-7 py-1.5 rounded-lg text-xs font-medium cursor-pointer outline-none"
                  style={{
                    background: 'var(--bg-elev)',
                    border: '1px solid var(--border-soft)',
                    color: 'var(--text)',
                    maxWidth: '200px',
                  }}
                >
                  {models.length === 0 && <option value="">暂无模型</option>}
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name || m.id}
                    </option>
                  ))}
                </select>
                {/* 模型图标 */}
                <span className="absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: 'var(--accent)' }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
                  </svg>
                </span>
                {/* 下拉箭头 */}
                <span className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: 'var(--text-faint)' }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </span>
              </div>

              {/* P0 后台执行开关：长任务跑后台，可随时查进度/取消/续跑 */}
              <button
                className="px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors"
                style={{
                  background: bgMode ? 'var(--accent)' : 'var(--bg-elev)',
                  color: bgMode ? 'var(--accent-ink)' : 'var(--text-faint)',
                  border: '1px solid var(--border-soft)',
                }}
                onClick={() => onBgModeChange?.(!bgMode)}
                title="开启后任务在后台执行，关闭对话框也不中断；可随时查看进度、取消或续跑"
              >
                ⏳ 后台
              </button>

              {/* 发送/停止按钮 */}
              {isStreaming ? (
                <button
                  className="px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap"
                  style={{ background: 'var(--danger)', color: '#fff' }}
                  onClick={onStop}
                  title="中断当前流式响应"
                >
                  ■ 停止
                </button>
              ) : (
                <button
                  className="px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors"
                  style={{
                    background: canSend ? 'var(--accent)' : 'var(--bg-elev)',
                    color: canSend ? 'var(--accent-ink)' : 'var(--text-faint)',
                  }}
                  onClick={handleSend}
                  disabled={!canSend}
                >
                  发送
                </button>
              )}
            </div>
          </div>
        </div>

        {/* 功能按钮栏（仅工作模式显示；对话模式保持简洁） */}
        <div className="flex items-center gap-2 mt-2.5 flex-wrap">
          {chatMode === 'work' && (<>
          <WorkspaceSelector />

          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors"
            style={{
              ...toolBtnStyle,
              color: readOnly ? 'var(--warn)' : 'var(--text-dim)',
              borderColor: readOnly ? 'var(--warn)' : 'var(--border-soft)',
            }}
            onClick={() => { setReadOnly(!readOnly); showToast(readOnly ? '已切换为可编辑模式' : '已切换为只读模式') }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
            {readOnly ? '只读' : '可编辑'}
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="6 9 12 15 18 9" /></svg>
          </button>

          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors"
            style={toolBtnStyle}
            onClick={() => {
              const order: Array<typeof permission> = ['全部允许', '仅询问', '全部拒绝']
              const next = order[(order.indexOf(permission) + 1) % order.length]
              setPermission(next)
              showToast(`权限已切换为：${next}`)
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="16" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
            {permission}
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="6 9 12 15 18 9" /></svg>
          </button>

          <SkillSelector onManage={() => onNavigate?.('skills')} />

          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors relative"
            style={toolBtnStyle}
            onClick={() => onNavigate?.('connectors')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
            </svg>
            连接器
            <span
              className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full text-[10px] font-bold flex items-center justify-center"
              style={{ background: 'var(--accent)', color: 'var(--accent-ink)' }}
            >
              3
            </span>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="6 9 12 15 18 9" /></svg>
          </button>
          </>)}

          {/* Token 提示靠右 */}
          <div className="flex-1" />
          <span className="text-xs" style={{ color: 'var(--text-faint)' }}>
            {tokenHint}
          </span>
        </div>
      </div>
    </div>
  )
}

export default InputBox
