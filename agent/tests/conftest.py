"""pytest 引导：把 agent/ 目录加入 sys.path，使 `import app...` 可用。"""
import os
import sys

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)
