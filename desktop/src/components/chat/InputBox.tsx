import React, { useState, useRef } from 'react'

interface InputBoxProps {
  onSend: (message: string) => void
  onStop: () => void
  isStreaming: boolean
  /** 未选中会话时禁用输入 */
  disabled?: boolean
  /** 底部 token 余量提示 */
  tokenHint?: string
}

const InputBox: React.FC<InputBoxProps> = ({
  onSend,
  onStop,
  isStreaming,
  disabled = false,
  tokenHint = '剩余 14,200 / 16,384 tokens',
}) => {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = () => {
    if (!input.trim() || isStreaming || disabled) return
    onSend(input.trim())
    setInput('')
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

  const canSend = Boolean(input.trim()) && !isStreaming && !disabled

  return (
    <div
      className="p-4 border-t"
      style={{
        background: 'var(--bg-panel)',
        backdropFilter: 'var(--glass)',
        WebkitBackdropFilter: 'var(--glass)',
        borderTop: '1px solid var(--border-soft)',
      }}
    >
      <div className="max-w-4xl mx-auto">
        <div
          className="flex items-end gap-2 rounded-xl p-3"
          style={{ background: 'var(--surf-input)', border: '1px solid var(--border)' }}
        >
          <textarea
            ref={textareaRef}
            className="flex-1 bg-transparent outline-none resize-none text-sm"
            style={{
              color: 'var(--text)',
              minHeight: '44px',
              maxHeight: '200px',
              fontFamily: 'var(--sans)',
            }}
            placeholder={
              disabled ? '请先在左侧新建或选择一个会话…' : '输入消息…（Shift+Enter 换行，Enter 发送）'
            }
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            rows={1}
          />

          {isStreaming ? (
            <button
              className="px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap"
              style={{ background: 'var(--danger)', color: '#fff' }}
              onClick={onStop}
              title="中断当前流式响应"
            >
              ■ 停止生成
            </button>
          ) : (
            <button
              className="px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors"
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

        <div className="mt-2 text-xs text-center" style={{ color: 'var(--text-faint)' }}>
          {tokenHint}
        </div>
      </div>
    </div>
  )
}

export default InputBox
