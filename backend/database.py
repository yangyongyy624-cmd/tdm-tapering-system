"""TDM Tapering System - SQLite Database Layer"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'tapering.db')

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # ─ Core tables ──
    c.executescript('''
    CREATE TABLE IF NOT EXISTS patients (
        patient_id TEXT PRIMARY KEY, phone TEXT, name TEXT, diagnosis TEXT,
        current_meds TEXT, tapering_meds TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, access_code TEXT UNIQUE,
        drug_categories TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS screening (
        session_id TEXT PRIMARY KEY,
        q1 INTEGER,q2 INTEGER,q3 INTEGER,q4 INTEGER,q5 INTEGER,
        q6 INTEGER,q7 INTEGER,q8 INTEGER,q9 INTEGER,q10 INTEGER,
        total_score INTEGER, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS phq9 (
        session_id TEXT PRIMARY KEY,
        p1 INTEGER,p2 INTEGER,p3 INTEGER,p4 INTEGER,p5 INTEGER,
        p6 INTEGER,p7 INTEGER,p8 INTEGER,p9 INTEGER,
        total_score INTEGER, baseline INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS gad7 (
        session_id TEXT PRIMARY KEY,
        g1 INTEGER,g2 INTEGER,g3 INTEGER,g4 INTEGER,g5 INTEGER,g6 INTEGER,g7 INTEGER,
        total_score INTEGER, baseline INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS cssrs (
        session_id TEXT PRIMARY KEY,
        wish_dead INTEGER DEFAULT 0, non_specific INTEGER DEFAULT 0,
        with_method INTEGER DEFAULT 0, with_intent INTEGER DEFAULT 0,
        with_plan INTEGER DEFAULT 0, severity_score INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS tdm_decision (
        session_id TEXT PRIMARY KEY,
        withdrawal_score REAL, withdrawal_level TEXT,
        phq9_change REAL, gad7_change REAL,
        phq9_level TEXT, gad7_level TEXT,
        cssrs_severity INTEGER, cssrs_level TEXT,
        overall_decision TEXT, overall_risk_level TEXT,
        cssrs_override INTEGER DEFAULT 0,
        doctor_notes TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS dess (
        session_id TEXT PRIMARY KEY,
        a_score INTEGER,b_score INTEGER,c_score INTEGER,
        d_score INTEGER,e_score INTEGER,f_score INTEGER,
        total_score INTEGER, standardized_score REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );
    ''')

    # ── Dynamic scale results ──
    c.execute('''CREATE TABLE IF NOT EXISTS scale_results (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        scale_name TEXT NOT NULL,
        drug_category TEXT,
        status TEXT DEFAULT 'pending',
        raw_answers TEXT,
        total_score INTEGER,
        assigned_at TEXT,
        completed_at TEXT DEFAULT (datetime('now')),
        created_at TEXT DEFAULT (datetime('now'))
    )''')

    # ── 审计日志 ──
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT DEFAULT (datetime('now')),
        user_pin TEXT,
        action TEXT,
        session_id TEXT,
        patient_id TEXT,
        ip TEXT,
        detail TEXT
    )''')

    # ── 登录失败锁定 ──
    c.execute('''CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT,
        pin TEXT,
        failed_at TEXT DEFAULT (datetime('now')),
        locked_until TEXT
    )''')

    # ─ Migrate existing columns if missing ──
    try:
        c.execute("ALTER TABLE sessions ADD COLUMN drug_categories TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE tdm_decision ADD COLUMN cssrs_override INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE sessions ADD COLUMN doctor_pin TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE tdm_decision ADD COLUMN drug_scales_level TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE tdm_decision ADD COLUMN baseline_flag INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized at:", DB_PATH)

def ensure_admin_pins():
    """Ensure admin_pins table exists and has default admin PIN"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS admin_pins (
        pin TEXT PRIMARY KEY,
        doctor_name TEXT NOT NULL,
        role TEXT DEFAULT 'doctor',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        last_used TEXT
    )''')
    c.execute("SELECT pin FROM admin_pins WHERE pin='888888'")
    if not c.fetchone():
        c.execute("INSERT INTO admin_pins (pin, doctor_name, role) VALUES ('888888', '管理员', 'admin')")
    c.execute("SELECT pin FROM admin_pins WHERE pin='7655'")
    if not c.fetchone():
        c.execute("INSERT INTO admin_pins (pin, doctor_name, role) VALUES ('7655', '医生', 'doctor')")
    conn.commit()
    conn.close()
