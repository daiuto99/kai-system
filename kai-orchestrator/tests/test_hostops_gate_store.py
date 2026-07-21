import json
import sqlite3

import engine as engine_module
from engine import Engine


def test_persistent_hostops_gate_is_bound_and_single_use(tmp_path, monkeypatch):
    database = tmp_path / "gates.db"
    def connection():
        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE IF NOT EXISTS gates (id TEXT PRIMARY KEY, gate_type TEXT, brief TEXT, status TEXT, resolution TEXT)")
        return conn
    monkeypatch.setattr(engine_module, "get_conn", connection)
    conn = connection()
    conn.execute("INSERT INTO gates VALUES (?,?,?,?,?)", ("approved", "hostops_place_secret", json.dumps({"hostops_operation": "place_secret", "site": "site-a"}), "resolved", json.dumps({"approved": True})))
    conn.commit()
    conn.close()
    gate_engine = Engine()
    assert not gate_engine.consume_hostops_gate("forged", "place_secret", "site-a")
    assert not gate_engine.consume_hostops_gate("approved", "deploy_plugin", "site-a")
    assert not gate_engine.consume_hostops_gate("approved", "place_secret", "site-b")
    assert gate_engine.consume_hostops_gate("approved", "place_secret", "site-a")
    assert not gate_engine.consume_hostops_gate("approved", "place_secret", "site-a")
