import React, { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import sql from 'highlight.js/lib/languages/sql'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'
import java from 'highlight.js/lib/languages/java'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import cpp from 'highlight.js/lib/languages/cpp'
import plaintext from 'highlight.js/lib/languages/plaintext'

// 按需注册常用语言（bash 自带 sh/shell/zsh 等别名），控制包体积
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('java', java)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('plaintext', plaintext)

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** 代码块：语言标签 + 复制按钮 + 语法高亮（highlight.js，仅输出转义后的安全 HTML） */
const CodeBlock: React.FC<{ language: string; code: string }> = ({ language, code }) => {
  const [copied, setCopied] = useState(false)

  const highlighted = useMemo(() => {
    try {
      const lang = language && hljs.getLanguage(language) ? language : 'plaintext'
      return hljs.highlight(code, { language: lang }).value
    } catch {
      return escapeHtml(code)
    }
  }, [code, language])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = code
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      try {
        document.execCommand('copy')
      } catch {
        /* ignore */
      }
      document.body.removeChild(ta)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="md-code-block">
      <div className="md-code-head">
        <span className="md-code-lang">{language || 'text'}</span>
        <button className="md-code-copy" onClick={copy}>
          {copied ? '✓ 已复制' : '复制'}
        </button>
      </div>
      <pre>
        {/* dangerouslySetInnerHTML 的输入来自 hljs 转义输出，不含原始 HTML */}
        <code dangerouslySetInnerHTML={{ __html: highlighted }} />
      </pre>
    </div>
  )
}

interface MarkdownContentProps {
  content: string
}

/**
 * AI 回复正文的 Markdown 渲染器：
 * - remark-gfm：表格 / 任务列表 / 删除线等 GFM 扩展
 * - 自定义 code：行内代码与代码块（语法高亮 + 复制）
 * - 自定义 table：窄屏下横向滚动，不撑破布局
 * - 自定义 a：外部链接走 Electron shell.openExternal（仅 http/https）
 * - 未开启 rehypeRaw，原始 HTML 一律转义，防 XSS
 */
const MarkdownContent: React.FC<MarkdownContentProps> = ({ content }) => {
  const remarkPlugins = useMemo(() => [remarkGfm], [])

  const components = useMemo(
    () => ({
      // 代码块由 code 渲染器自包含 <pre>，去掉 markdown 默认的 <pre> 包裹层
      pre: ({ children }: any) => <>{children}</>,
      code: (props: any) => {
        const { className, children } = props
        const match = /language-([\w-]+)/.exec(className || '')
        const text = String(children)
        // 无语言声明且不含换行 → 行内代码
        if (!match && !text.includes('\n')) {
          return <code className="md-code-inline">{children}</code>
        }
        return <CodeBlock language={match ? match[1] : ''} code={text.replace(/\n$/, '')} />
      },
      table: ({ children }: any) => (
        <div className="md-table-wrap">
          <table>{children}</table>
        </div>
      ),
      a: (props: any) => {
        const { href, children } = props
        return (
          <a
            href={href}
            className="md-link"
            onClick={(e) => {
              if (href && /^https?:\/\//i.test(href)) {
                e.preventDefault()
                window.electronAPI?.openExternal(href)
              }
            }}
          >
            {children}
          </a>
        )
      },
      img: (props: any) => {
        const { src, alt } = props
        return <img src={src} alt={alt || ''} className="md-image" loading="lazy" />
      },
    }),
    []
  )

  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

export default React.memo(MarkdownContent)
