---
name: "hardening-audit-verify"
description: "颤翎子客户端(v2)安全/工程加固审查、修复与回归验证。Invoke when fixing shell bypass/path sandbox/injection/approval-auth, cleaning dead code or build artifacts, or adding pytest regression after such fixes."
---

# 颤翎子客户端 安全/工程加固 → 修复 → 回归验证

适用于 v2 仓库（`/Users/caojian/Desktop/agent/workdesktop/v2`，FastAPI agent + Electron/React 双层架构）的安全与工程质量加固。核心纪律：**先取证 → 最小定点修复 → 补 pytest 锁定行为 → 全量验证**，不臆断、不扩大范围、不主动 commit。

## 一、环境与命令（固定用法，勿踩坑）

- venv python：`agent/.venv/bin/python`。**禁止直接调 `.venv/bin/pip`**，装依赖用 `agent/.venv/bin/python -m pip install <pkg> -i https://pypi.tuna.tsinghua.edu.cn/simple`。
- 后端单测：`cd agent && .venv/bin/python -m pytest tests/ -q`（测试目录 `agent/tests/`，conftest.py 已把 agent/ 加入 sys.path）。
- 后端语法：`agent/.venv/bin/python -m py_compile <改的文件...>`。
- 前端：`cd desktop && npx tsc --noEmit && npx vite build`（改 desktop/src 后必跑）。
- 后端重启：先杀端口再让 Electron 拉起——
  `pkill -f "desktop/node_modules/electron"; pkill -f "app.main"; pkill -f "私有域AI助手"; for p in $(lsof -nP -iTCP:8765 -sTCP:LISTEN -t); do kill -9 $p; done; cd desktop && npx electron .`
- 健康检查：`curl -s http://127.0.0.1:8765/healthz`。
- AGENT_TOKEN：`PID=$(lsof -nP -iTCP:8765 -sTCP:LISTEN -t|head -1); ps eww $PID | tr ' ' '\n' | grep '^AGENT_TOKEN=' | cut -d= -f2`。

## 二、异步测试写法

**不引入 pytest-asyncio**（venv 未装）。异步工具用标准库驱动：
```python
import asyncio
arun = asyncio.run
def test_x(self, tool):
    r = arun(tool.execute({...}))
    assert r["success"] is True
```

## 三、加固检查清单（本仓库历史踩点，逐项核对）

1. **Shell 命令绕过**（`agent/app/security/shell_security.py`）：分段白名单（`; | && &` 两侧每段首命令都须 basename 精确匹配，拒 `ls; rm -rf`、`lsof` 前缀伪造）；拒命令替换 `$(`、反引号、进程替换 `<(`/`>(`、换行；`find -exec/-ok`、`env VAR=v <非白名单>` 拦截；重定向目标与 cwd 必须落在 allowed_root_dirs 内。危险正则是纵深防御，注意勿误杀合法绝对路径（如"重定向到根"应只匹配裸根 `/`，用 `>\s*/(?:\s|$)`，不能写成 `>\s*/`）。
2. **文件路径沙箱**（`agent/app/tools/_foundation.py`）：`_resolve_in_roots` 多根逐一匹配 + 越权抛 PermissionError + 凭据目录（`.ssh/.gnupg/.aws/.kube/.docker`）任何动作拒绝；`_assert_writable` 禁写根下一级 dotfile/dotdir（`.zshrc/.config/...`）。新增读/写文件的工具（code、doc_to_html、新工具）都必须接 allowed_roots 并走这两个函数。
3. **审批服务鉴权**：Electron `main.ts` 本地 HTTP 审批端口须校验 `X-Approval-Secret`（crypto.randomBytes 生成、常量时间比较，env 透传后端 `chat.py`），否则本机任意进程可伪造批准。
4. **HTML/模板注入**（`agent/app/tools/doc_tools.py`）：chart 配置交 jinja2 `tojson`（自动转义 `<` 为 `\u003c`），非法 JSON 降级 `{}`；chart id 用 `re.sub(r"[^\w-]","_",id)` 清洗并**回写 section.id**（canvas 与 JS 共用，否则图表找不到画布）；custom_css 剔除 `<`/`>`。模板正文 `| safe`（left/right_html、_icons）是模型正文/固定 SVG，非注入面，勿改。
5. **硬编码可配置化**：模型路由候选走 `routing.py` 的 `load_routing_candidates()`（env `AGENT_ROUTING_CANDIDATES` JSON，非法安全回退默认），勿再把模型名写死在逻辑里。
6. **前端无界状态**：`useUiStore` messageCache、`ChatView` traces/citations 等持续累加结构须封顶（如 30 会话 / 60 条），丢弃最早项，不得影响消息正文与交付文件。
7. **死代码/产物入库**：全仓零引用的旧实现要删（删前用 Grep 对文件名、定义符号、动态 import、打包配置/脚本/文档做交叉验证，排除 `.venv/`）；`release/`、`desktop/build-staging/`、`dist*/`、`node_modules/`、`agent/data/`、`.DS_Store` 等须在 `.gitignore` 且移出 git 索引（`git rm -r --cached <path>`，磁盘文件保留）。

## 四、工具拆分纪律（`agent/app/tools/`）

大文件拆分采用**下沉叶子模块 + `__init__.py` re-export**：基类/共享 helper 放 `_foundation.py`，内聚类放子模块（如 `doc_tools.py`），`__init__.py` 顶部 `from ._foundation import ...` / `from .doc_tools import ...` 保持 `from app.tools import XxxTool` 与 `create_tools()` 零改动。拆完必须冒烟验证导入与 `create_tools(allowed_root_dirs=...)` 实例化。
**前端大组件（InputBox/ConnectorsView 等）与强交互模块不要纯静态拆**——需在运行的 Electron 里做 GUI 验证，无 GUI 条件时保持原样并在总结中说明。

## 五、执行流程

1. **取证**：Read/Grep 定位真实代码路径与调用方，记录行号；判断是否真实缺陷还是测试/断言写错。
2. **最小修复**：只改必要点，不顺手重构；改工具记得同步 `create_tools()` 注册参数（如 allowed_roots）。
3. **补回归**：在 `agent/tests/test_*.py` 加用例锁定新行为（安全用例必须包含"放行正常"和"拒绝越权"双向断言）。
4. **全量验证**：py_compile → `pytest tests/ -q`（全绿）→ 涉及前端再 tsc + vite build。后端结构性改动额外冒烟：`from app.agents.orchestrator import AgentOrchestrator; from app.tools import create_tools; create_tools(allowed_root_dirs=['/tmp'])`。
5. **收尾**：报告改了哪些文件（给可点击绝对路径）、验证结果、未做项及原因；**不主动 git commit**，需要提交时由用户明确提出。

## 六、验证清单（每次交付前自查）

- [ ] 改动文件 `py_compile` 通过
- [ ] `pytest tests/ -q` 全部 passed（新增安全用例双向断言齐全）
- [ ] 改了 desktop/src → `tsc --noEmit` exit 0 且 `vite build` 成功
- [ ] 未引入新依赖（如必须，用 `python -m pip` 并说明）
- [ ] 未 commit；死代码/产物清理已交叉验证零引用
- [ ] 需要 GUI/重启才生效的项已明确告知用户
