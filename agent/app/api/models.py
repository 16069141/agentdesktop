"""模型列表接口（Phase 1：静态清单；Phase 2 接 Provider 层真实探测）。

公网模型用 isPublic=True 标记，前端据此展示「公网」徽标与知情提示。
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/models", tags=["models"])

# 与前端 store 默认列表保持一致，前端会以本接口结果为准覆盖本地默认值
MODELS = [
    {
        "id": "qwen2.5:7b",
        "name": "Qwen2.5 7B",
        "providerId": "ollama-local",
        "isPublic": False,
        "description": "本地 Ollama，数据不出本机",
    },
    {
        "id": "deepseek-coder:6.7b",
        "name": "DeepSeek-Coder 6.7B",
        "providerId": "ollama-local",
        "isPublic": False,
        "description": "本地 Ollama，代码场景优化",
    },
    {
        "id": "enterprise-v3",
        "name": "Enterprise-Model-V3",
        "providerId": "private-api",
        "isPublic": False,
        "description": "企业私有化部署，内网可达",
    },
    {
        "id": "deepseek-chat",
        "name": "DeepSeek-Chat",
        "providerId": "cloud-deepseek",
        "isPublic": True,
        "description": "数据将发送至公网模型厂商",
    },
    {
        "id": "qwen-turbo",
        "name": "通义千问 Turbo",
        "providerId": "cloud-qwen",
        "isPublic": True,
        "description": "数据将发送至公网模型厂商",
    },
]


@router.get("")
async def list_models():
    return MODELS
