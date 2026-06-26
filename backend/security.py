"""TDM Security — 限流 + 审计 + Session Token"""
import time, uuid, json
from database import get_db

# ── 配置 ──
MAX_LOGIN_ATTEMPTS = 5       # 5次失败
LOCKOUT_MINUTES = 30         # 锁定30分钟
SESSION_EXPIRY_MINUTES = 60  # Session 60分钟过期
AUDIT_LOG_MAX_AGE_DAYS = 90  # 审计日志保留90天

def check_lockout(ip: str, pin: str) -> str | None:
    """检查是否被锁定。返回 None = 可以登录，否则返回错误信息。"""
    db = get_db()
    c = db.cursor()
    # 清理过期锁定
    c.execute("DELETE FROM login_attempts WHERE locked_until < datetime('now')")
    db.commit()

    # 检查当前IP+PIN的失败次数
    c.execute("""SELECT COUNT(*) as cnt, MAX(failed_at) as last_fail
                 FROM login_attempts WHERE ip=? AND pin=?
                 AND failed_at > datetime('now', '-30 minutes')""", (ip, pin))
    row = c.fetchone()
    db.close()

    if row["cnt"] >= MAX_LOGIN_ATTEMPTS:
        return f"登录失败次数过多，请{LOCKOUT_MINUTES}分钟后再试"
    return None

def record_failed_login(ip: str, pin: str):
    """记录失败登录。"""
    db = get_db()
    locked_until = f"datetime('now', '+{LOCKOUT_MINUTES} minutes')"
    db.execute("INSERT INTO login_attempts (ip, pin, locked_until) VALUES (?,?,?)",
               (ip, pin, locked_until))
    db.commit()
    db.close()

def clear_failed_logins(ip: str, pin: str):
    """登录成功后清除失败记录。"""
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE ip=? AND pin=?", (ip, pin))
    db.commit()
    db.close()

# ── Session Token ──
_sessions = {}  # 内存存储: token -> {pin, role, name, expires_at}

def create_session(pin: str, role: str, name: str) -> str:
    """创建 session token。"""
    token = uuid.uuid4().hex
    _sessions[token] = {
        "pin": pin,
        "role": role,
        "name": name,
        "expires_at": time.time() + SESSION_EXPIRY_MINUTES * 60,
    }
    # 清理过期 session
    now = time.time()
    expired = [k for k, v in _sessions.items() if v["expires_at"] < now]
    for k in expired:
        del _sessions[k]
    return token

def verify_session(token: str) -> dict | None:
    """验证 session token。返回 user info 或 None。"""
    session = _sessions.get(token)
    if not session or session["expires_at"] < time.time():
        if token in _sessions:
            del _sessions[token]
        return None
    return session

def destroy_session(token: str):
    """销毁 session。"""
    _sessions.pop(token, None)

# ── 审计日志 ──
def audit_log(user_pin: str, action: str, session_id: str = None,
              patient_id: str = None, ip: str = None, detail: str = None):
    """写入审计日志。"""
    db = get_db()
    db.execute("""INSERT INTO audit_logs (user_pin, action, session_id, patient_id, ip, detail)
                  VALUES (?,?,?,?,?,?)""",
               (user_pin, action, session_id, patient_id, ip, detail))
    # 清理旧日志
    db.execute(f"DELETE FROM audit_logs WHERE timestamp < datetime('now', '-{AUDIT_LOG_MAX_AGE_DAYS} days')")
    db.commit()
    db.close()

def get_audit_logs(limit: int = 100, user_pin: str = None) -> list:
    """查询审计日志。"""
    db = get_db()
    c = db.cursor()
    if user_pin:
        c.execute("SELECT * FROM audit_logs WHERE user_pin=? ORDER BY timestamp DESC LIMIT ?",
                  (user_pin, limit))
    else:
        c.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    db.close()
    return rows
