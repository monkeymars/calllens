import sqlite3
_conn = sqlite3.connect(":memory:")

def query(sql, *args):
    return _conn.execute(sql, args).fetchall()

def query_one(sql, *args):
    return _conn.execute(sql, args).fetchone()

def execute(sql, *args):
    cur = _conn.execute(sql, args)
    _conn.commit()
    return cur.lastrowid
