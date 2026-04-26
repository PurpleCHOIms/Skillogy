"""Neo4j driver singleton + connection helpers.

Reads connection info from env vars:
  NEO4J_URI       (default: bolt://localhost:7687)
  NEO4J_USER      (default: neo4j)
  NEO4J_PASSWORD  (default: skillrouter)
"""

from __future__ import annotations

import os

from neo4j import Driver, GraphDatabase

_DEFAULT_URI = "bolt://localhost:7687"
_DEFAULT_USER = "neo4j"
_DEFAULT_PASS = "skillrouter"

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver  # noqa: PLW0603
    if _driver is None:
        uri = os.environ.get("NEO4J_URI", _DEFAULT_URI)
        user = os.environ.get("NEO4J_USER", _DEFAULT_USER)
        pwd = os.environ.get("NEO4J_PASSWORD", _DEFAULT_PASS)
        _driver = GraphDatabase.driver(uri, auth=(user, pwd))
    return _driver


def close_driver() -> None:
    global _driver  # noqa: PLW0603
    if _driver is not None:
        _driver.close()
        _driver = None
