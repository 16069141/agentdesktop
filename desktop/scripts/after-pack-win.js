/**
 * electron-builder afterPack 钩子（仅 Windows）：
 * 用 resedit（纯 JS PE 资源编辑器）把 build/icon.ico 写入 exe 图标资源。
 *
 * 为什么需要：macOS 交叉构建 Windows 时无 wine，electron-builder 配置了
 * win.signAndEditExecutable=false 会跳过 rcedit，导致 exe 保持 Electron 默认图标。
 * resedit 不依赖 wine，可直接编辑 PE 资源。
 *
 * 用法：electron-builder.yml 的 "afterPack": "scripts/after-pack-win.js"
 */
const fs = require('fs')
const path = require('path')
const { NtExecutable, NtExecutableResource, Resource, Data } = require('resedit')

module.exports = async function afterPackWin(context) {
  const { electronPlatformName, appOutDir } = context
  if (electronPlatformName !== 'win32') return

  const icoPath = path.join(__dirname, '..', 'build', 'icon.ico')
  if (!fs.existsSync(icoPath)) {
    console.warn('[afterPack-win] 未找到 build/icon.ico，跳过图标替换')
    return
  }

  const exePath = fs
    .readdirSync(appOutDir)
    .map((f) => path.join(appOutDir, f))
    .find((p) => p.endsWith('.exe') && !p.includes('uninstall'))
  if (!exePath) {
    console.warn('[afterPack-win] 未找到主 exe，跳过图标替换')
    return
  }

  try {
    const exe = NtExecutable.from(fs.readFileSync(exePath))
    const res = NtExecutableResource.from(exe)

    const groups = Resource.IconGroupEntry.fromEntries(res.entries)
    if (groups.length === 0) {
      console.warn('[afterPack-win] exe 无图标组资源，跳过')
      return
    }

    const iconFile = Data.IconFile.from(fs.readFileSync(icoPath))
    Resource.IconGroupEntry.replaceIconsForResource(
      res.entries,
      groups[0].id,
      groups[0].lang,
      iconFile.icons.map((i) => i.data),
    )
    res.outputResource(exe)

    fs.writeFileSync(exePath, Buffer.from(exe.generate()))
    console.log(`[afterPack-win] ✓ 已替换 exe 图标（${iconFile.icons.length} 个尺寸）: ${path.basename(exePath)}`)
  } catch (err) {
    // 图标替换失败不应阻塞构建，但要在日志中显眼告警
    console.warn('[afterPack-win] ✗ 图标替换失败（构建继续，exe 将保留默认图标）:', err.message)
  }
}
