"""模型路由候选可配置化回归测试。

锁定 load_routing_candidates：
- 未设环境变量时用内置默认表
- AGENT_ROUTING_CANDIDATES(JSON) 可覆盖
- 非法 JSON / 非对象 / 缺 general 键时安全回退
"""
import pytest

from app.providers.routing import DEFAULT_ROUTING_CANDIDATES, load_routing_candidates


def test_default_when_env_absent(monkeypatch):
    monkeypatch.delenv("AGENT_ROUTING_CANDIDATES", raising=False)
    assert load_routing_candidates() == DEFAULT_ROUTING_CANDIDATES
    assert "general" in load_routing_candidates()
    assert "coding" in load_routing_candidates()


def test_env_override(monkeypatch):
    monkeypatch.setenv(
        "AGENT_ROUTING_CANDIDATES",
        '{"general": ["my-model-a", "my-model-b"], "coding": ["my-coder"]}',
    )
    c = load_routing_candidates()
    assert c["general"] == ["my-model-a", "my-model-b"]
    assert c["coding"] == ["my-coder"]


def test_invalid_json_falls_back(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_CANDIDATES", "not-json{")
    assert load_routing_candidates() == DEFAULT_ROUTING_CANDIDATES


def test_non_object_falls_back(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_CANDIDATES", '["a", "b"]')
    assert load_routing_candidates() == DEFAULT_ROUTING_CANDIDATES


def test_missing_general_gets_default(monkeypatch):
    # 只给 coding，general 缺失 → 自动补默认 general，coding 采用自定义
    monkeypatch.setenv("AGENT_ROUTING_CANDIDATES", '{"coding": ["coder-x"]}')
    c = load_routing_candidates()
    assert c["general"] == DEFAULT_ROUTING_CANDIDATES["general"]
    assert c["coding"] == ["coder-x"]


def test_malformed_entries_dropped(monkeypatch):
    # 空数组 / 非字符串元素的键被丢弃，不污染结果
    monkeypatch.setenv(
        "AGENT_ROUTING_CANDIDATES",
        '{"general": ["ok-model"], "bad": [], "alsobad": [123, ""]}',
    )
    c = load_routing_candidates()
    assert c["general"] == ["ok-model"]
    assert "bad" not in c
    assert "alsobad" not in c
