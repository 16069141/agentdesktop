/**
 * SSE 解码器单元测试。
 *
 * 重点验证「粘包 / 半包」：网络分片与 SSE 事件边界不对齐时，
 * 解码器必须既不丢事件、也不产生残缺事件。
 *
 * 运行：node scripts/test-sse.mjs
 */
import { build } from 'esbuild'
import { fileURLToPath } from 'url'
import path from 'path'
import os from 'os'
import fs from 'fs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')

let passed = 0
let failed = 0

function check(name, ok, detail = '') {
  if (ok) {
    passed++
    console.log(`  ✅ ${name}`)
  } else {
    failed++
    console.log(`  ❌ ${name}${detail ? ' — ' + detail : ''}`)
  }
}

function eq(name, actual, expected) {
  const a = JSON.stringify(actual)
  const b = JSON.stringify(expected)
  check(name, a === b, `\n      实际: ${a}\n      期望: ${b}`)
}

// 用 esbuild 把 TS 源码打成 ESM，避免额外引入测试框架
const outfile = path.join(os.tmpdir(), `sse-client-${Date.now()}.mjs`)
await build({
  entryPoints: [path.join(root, 'src/sse/sseClient.ts')],
  outfile,
  bundle: true,
  format: 'esm',
  platform: 'node',
  logLevel: 'error',
})

const { SSEDecoder, parseEventBlock } = await import(`file://${outfile}`)
fs.unlinkSync(outfile)

console.log('\n=== SSE 解码器测试 ===\n')

console.log('[1] 基本解析')
{
  const d = new SSEDecoder()
  const evs = d.feed('event: text\ndata: {"delta":"你好"}\n\n')
  eq('单事件解析', evs, [{ type: 'text', data: { delta: '你好' } }])
}

console.log('\n[2] 粘包：一次 chunk 含多个事件')
{
  const d = new SSEDecoder()
  const chunk =
    'event: meta\ndata: {"message_id":"m1"}\n\n' +
    'event: text\ndata: {"delta":"A"}\n\n' +
    'event: text\ndata: {"delta":"B"}\n\n'
  const evs = d.feed(chunk)
  check('三个事件全部解析', evs.length === 3, `实际 ${evs.length}`)
  eq('类型序列', evs.map((e) => e.type), ['meta', 'text', 'text'])
  eq('内容正确', evs.map((e) => e.data), [{ message_id: 'm1' }, { delta: 'A' }, { delta: 'B' }])
}

console.log('\n[3] 半包：事件被切成两片')
{
  const d = new SSEDecoder()
  // 第一片：事件头 + 半个 data
  const first = d.feed('event: text\ndata: {"del')
  check('半包不产生事件', first.length === 0, `实际 ${first.length}`)

  // 第二片：剩余部分 + 分隔符
  const second = d.feed('ta":"你好"}\n\n')
  eq('补齐后正确解析', second, [{ type: 'text', data: { delta: '你好' } }])
}

console.log('\n[4] 半包切在分隔符中间（\\n 与 \\n 分家）')
{
  const d = new SSEDecoder()
  const a = d.feed('event: text\ndata: {"delta":"X"}\n')
  check('分隔符未完整时不吐事件', a.length === 0, `实际 ${a.length}`)
  // 补齐的这一片同时带上了后续完整事件 → 应一次性吐出两个
  const b = d.feed('\nevent: text\ndata: {"delta":"Y"}\n\n')
  eq('补齐分隔符后两个事件一并吐出', b, [
    { type: 'text', data: { delta: 'X' } },
    { type: 'text', data: { delta: 'Y' } },
  ])
  check('缓冲已清空', d.flush().length === 0)
}

console.log('\n[5] UTF-8 多字节字符跨分片')
{
  const d = new SSEDecoder()
  // 模拟「中」字的 UTF-8 字节被切成两半（解码前先按字符切，检验拼接逻辑）
  const evs = d.feed('event: text\ndata: {"delta":"中')
  check('半个中文不吐事件', evs.length === 0)
  const rest = d.feed('文"}\n\n')
  eq('拼接后中文完整', rest, [{ type: 'text', data: { delta: '中文' } }])
}

console.log('\n[6] CRLF 与 CR 换行兼容')
{
  const d1 = new SSEDecoder()
  eq(
    'CRLF 分隔',
    d1.feed('event: text\r\ndata: {"delta":"A"}\r\n\r\n'),
    [{ type: 'text', data: { delta: 'A' } }]
  )

  const d2 = new SSEDecoder()
  eq(
    'CR 分隔',
    d2.feed('event: text\rdata: {"delta":"B"}\r\r'),
    [{ type: 'text', data: { delta: 'B' } }]
  )
}

console.log('\n[7] [DONE] 哨兵')
{
  const d = new SSEDecoder()
  const evs = d.feed('event: done\ndata: [DONE]\n\n')
  eq('哨兵解析为 done 事件', evs, [{ type: 'done', data: null }])

  const d2 = new SSEDecoder()
  eq(
    '无 event 行的 [DONE] 也能识别',
    d2.feed('data: [DONE]\n\n'),
    [{ type: 'done', data: null }]
  )
}

console.log('\n[8] 心跳注释与非 JSON 降级')
{
  const d = new SSEDecoder()
  const evs = d.feed(': heartbeat\n\nevent: text\ndata: 纯文本增量\n\n')
  eq('心跳被忽略，非 JSON 降级为 text', evs, [
    { type: 'text', data: { delta: '纯文本增量' } },
  ])
}

console.log('\n[9] 多行 data 拼接')
{
  const block = 'event: text\ndata: {"delta":"第一行\\n第二行"}\n\n'
  const d = new SSEDecoder()
  const evs = d.feed(block)
  eq('JSON 内换行保持完整', evs, [
    { type: 'text', data: { delta: '第一行\n第二行' } },
  ])
}

console.log('\n[10] 逐字节喂入（最极端的半包）')
{
  const full =
    'event: meta\ndata: {"message_id":"m1"}\n\n' +
    'event: text\ndata: {"delta":"逐"}\n\n' +
    'event: text\ndata: {"delta":"字节"}\n\n' +
    'event: done\ndata: [DONE]\n\n'
  const d = new SSEDecoder()
  const all = []
  for (const ch of full) {
    all.push(...d.feed(ch))
  }
  all.push(...d.flush())
  check('事件总数正确', all.length === 4, `实际 ${all.length}`)
  eq(
    '内容逐字节拼装无损',
    all.filter((e) => e.type === 'text').map((e) => e.data.delta).join(''),
    '逐字节'
  )
  check('末事件为 done', all[all.length - 1].type === 'done')
}

console.log('\n[11] reset 清空缓冲')
{
  const d = new SSEDecoder()
  d.feed('event: text\ndata: {"delta":"丢弃')
  d.reset()
  const evs = d.feed('event: text\ndata: {"delta":"新鲜"}\n\n')
  eq('reset 后旧半包不污染新数据', evs, [{ type: 'text', data: { delta: '新鲜' } }])
}

console.log('\n[12] parseEventBlock 边界')
{
  check('空块返回 null', parseEventBlock('') === null)
  check('纯空白返回 null', parseEventBlock('   \n  ') === null)
  check('无 data 行返回 null', parseEventBlock('event: text') === null)
}

console.log(`\n=== 结果：${passed} 通过 / ${failed} 失败 ===\n`)
process.exit(failed > 0 ? 1 : 0)
