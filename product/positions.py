"""Pipeline positions — the persistent object that flows through the six-step
investment loop: candidate → watch → held → exited.

Each row is one name with its written thesis, target entry price, disqualifiers,
and a factor snapshot frozen at add-time (which doubles as a point-in-time record
of what the screen said when the name was considered).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "data/prediction_markets.sqlite"
STATUSES = ("candidate", "watch", "held", "exited")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  market       TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  name         TEXT,
  status       TEXT NOT NULL DEFAULT 'candidate',
  lynch_type   TEXT,
  thesis       TEXT,
  target_price REAL,
  target_basis TEXT,
  disqualifiers TEXT,
  snapshot     TEXT,
  source       TEXT,
  added_at     TEXT NOT NULL,
  updated_at   TEXT,
  UNIQUE(market, ticker)
);
"""


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_out(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("snapshot", "disqualifiers"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def list_positions(market: str | None = None) -> dict:
    con = _conn()
    try:
        if market:
            rows = con.execute(
                "SELECT * FROM positions WHERE market=? ORDER BY "
                "CASE status WHEN 'held' THEN 0 WHEN 'watch' THEN 1 WHEN 'candidate' THEN 2 ELSE 3 END, added_at DESC",
                (market,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM positions ORDER BY added_at DESC").fetchall()
        return {"status": "ready", "positions": [_row_out(r) for r in rows]}
    finally:
        con.close()


def add_position(market: str, ticker: str, *, name: str | None = None,
                 source: str | None = None, lynch_type: str | None = None,
                 snapshot: dict | None = None) -> dict:
    """Idempotent: re-adding an existing name refreshes its snapshot but never
    downgrades status or overwrites a written thesis."""
    ticker = ticker.strip().upper() if market != "cn" else ticker.strip()
    if not ticker:
        return {"status": "error", "error": "No ticker."}
    con = _conn()
    try:
        existing = con.execute("SELECT * FROM positions WHERE market=? AND ticker=?",
                               (market, ticker)).fetchone()
        if existing:
            con.execute("UPDATE positions SET snapshot=COALESCE(?, snapshot), "
                        "name=COALESCE(?, name), lynch_type=COALESCE(?, lynch_type), updated_at=? "
                        "WHERE id=?",
                        (json.dumps(snapshot) if snapshot else None, name, lynch_type,
                         _now(), existing["id"]))
            con.commit()
            row = con.execute("SELECT * FROM positions WHERE id=?", (existing["id"],)).fetchone()
            return {"status": "ready", "position": _row_out(row), "existed": True}
        con.execute("INSERT INTO positions (market,ticker,name,status,lynch_type,snapshot,source,added_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (market, ticker, name, "candidate", lynch_type,
                     json.dumps(snapshot) if snapshot else None, source, _now()))
        con.commit()
        row = con.execute("SELECT * FROM positions WHERE market=? AND ticker=?",
                          (market, ticker)).fetchone()
        return {"status": "ready", "position": _row_out(row), "existed": False}
    finally:
        con.close()


_EDITABLE = {"status", "thesis", "target_price", "target_basis", "disqualifiers",
             "lynch_type", "name"}


def update_position(pid: int, fields: dict) -> dict:
    fields = {k: v for k, v in fields.items() if k in _EDITABLE}
    if not fields:
        return {"status": "error", "error": "No editable fields given."}
    if "status" in fields and fields["status"] not in STATUSES:
        return {"status": "error", "error": f"Bad status '{fields['status']}'."}
    if "disqualifiers" in fields and isinstance(fields["disqualifiers"], (list, dict)):
        fields["disqualifiers"] = json.dumps(fields["disqualifiers"])
    if "target_price" in fields and fields["target_price"] is not None:
        try:
            fields["target_price"] = float(fields["target_price"])
        except (TypeError, ValueError):
            return {"status": "error", "error": "target_price must be a number."}
    con = _conn()
    try:
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [_now(), pid]
        cur = con.execute(f"UPDATE positions SET {sets}, updated_at=? WHERE id=?", vals)
        con.commit()
        if cur.rowcount == 0:
            return {"status": "error", "error": f"No position id {pid}."}
        row = con.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
        return {"status": "ready", "position": _row_out(row)}
    finally:
        con.close()


def delete_position(pid: int) -> dict:
    con = _conn()
    try:
        cur = con.execute("DELETE FROM positions WHERE id=?", (pid,))
        con.commit()
        return {"status": "ready", "deleted": cur.rowcount}
    finally:
        con.close()
