/**
 * DSML 兜底剥离（渲染层防御）。
 *
 * 主拦截在 Python 后端 `app/dsml` 完成：content 流里的 DSML 块会被翻译成标准
 * tool_calls 事件，前端原则上不会再收到 DSML 源码。
 *
 * 这里只做两件后端覆盖不到的事：
 * 1. 历史会话里**已经存进数据库**的 DSML 源码（老数据）；
 * 2. 流式中间帧 —— 后端按 chunk 拦截，但前端按帧合批渲染，收尾瞬间可能还剩
 *    半截标记；这里只影响「显示内容」，不影响累积 buffer，下一帧会重新计算。
 */
const FW = '\uFF5C' // 全角竖线 ｜(U+FF5C)

const START_A = `<${FW}${FW}DSML${FW}${FW}>`
const END_A = `</${FW}${FW}DSML${FW}${FW}>`
const START_B = `<${FW}DSML${FW}` // 行前缀式（DeepSeek 常见形态）
const END_B = `</${FW}DSML${FW}`

// 行尾半截起始标记：`<` + 至少一个竖线（流式中间帧）
const TAIL_PARTIAL_RE = /<[|\uFF5C]+D?S?M?L?[|\uFF5C]*$/

/** 行前缀式块的结束位置（相对 blockStart 的偏移），未结束返回 -1 */
function lineModeEnd(text: string, blockStart: number): number {
  const rest = text.slice(blockStart)
  const ei = rest.indexOf(END_B)
  if (ei >= 0) {
    const gt = rest.indexOf('>', ei)
    return gt >= 0 ? gt + 1 : rest.length
  }
  let pos = 0
  let first = true
  for (const seg of rest.split('\n')) {
    if (first) {
      first = false
      pos += seg.length
      continue
    }
    const s = seg.trim()
    if (!s) {
      pos += 1
      continue
    }
    if (!s.startsWith(START_B)) return pos
    pos += seg.length + 1
  }
  return -1
}

/** 移除文本中的 DSML 片段（含未闭合尾部），非 DSML 文本原样返回 */
export function stripDSML(text: string): string {
  if (!text) return text
  if (!text.includes('DSML') && !text.includes(FW)) return text

  let out = ''
  let i = 0
  while (i < text.length) {
    const ia = text.indexOf(START_A, i)
    const ib = text.indexOf(START_B, i)
    let start = -1
    let endMark = ''
    if (ia >= 0 && (ib < 0 || ia <= ib)) {
      start = ia
      endMark = END_A
    } else if (ib >= 0) {
      start = ib
    }
    if (start < 0) {
      out += text.slice(i)
      break
    }
    out += text.slice(i, start)
    const bodyStart = start + (endMark ? START_A.length : START_B.length)
    if (endMark) {
      const ei = text.indexOf(endMark, bodyStart)
      if (ei < 0) return stripTail(out) // 未闭合：丢弃尾部
      i = ei + endMark.length
    } else {
      const rel = lineModeEnd(text, bodyStart)
      if (rel < 0) return stripTail(out)
      i = bodyStart + rel
    }
  }
  return stripTail(out)
}

function stripTail(s: string): string {
  return s.replace(TAIL_PARTIAL_RE, '')
}
