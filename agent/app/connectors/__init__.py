"""连接器抽象层与接入方式（需求 §2.1 / §2.2）。"""
from .base import (
    ConnectorBase,
    ConnectorRegistry,
    get_connector_registry,
)
from .enterprise import (
    EnterpriseConnector,
    ERPConnector,
    CRMConnector,
    OAConnector,
    CONNECTOR_TYPES,
    create_connector,
    list_connector_types,
    sync_connector_registry,
)
from .db_ro import (
    query_db,
    query_sqlite,
    test_sqlite,
    validate_read_only_sql,
    ReadOnlyViolation,
)
from .rpa import rpa_execute

__all__ = [
    "ConnectorBase",
    "ConnectorRegistry",
    "get_connector_registry",
    "EnterpriseConnector",
    "ERPConnector",
    "CRMConnector",
    "OAConnector",
    "CONNECTOR_TYPES",
    "create_connector",
    "list_connector_types",
    "sync_connector_registry",
    "query_db",
    "query_sqlite",
    "test_sqlite",
    "validate_read_only_sql",
    "ReadOnlyViolation",
    "rpa_execute",
]
