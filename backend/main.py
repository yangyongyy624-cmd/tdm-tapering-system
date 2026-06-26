"""TDM Tapering System - Cloud API Backend (端口 8001)

非敏感数据本地处理，敏感数据通过 data_router 转发到本地 API (8002)。
"""
import json, os, random, uuid, sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import init_db, get_db, ensure_admin_pins
from data_router import router
from security import (check_lockout, record_failed_login, clear_failed_logins,
                      create_session as create_auth_session, verify_session, destroy_session,
                      audit_log, get_audit_logs)

app = FastAPI(title="TDM 减药决策矩阵", version="2.0")
init_db()
ensure_admin_pins()

@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.endswith(('.html', '/')):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.middleware("http")
async def https_redirect(request: Request, call_next):
    """HTTP 自动重定向到 HTTPS（仅生产环境）。"""
    if request.url.scheme == "http" and request.url.port in (8890, 80):
        url = str(request.url).replace("http://", "https://", 1)
        return RedirectResponse(url, status_code=301)
    return await call_next(request)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend')
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# Admin auth
def verify_admin_pin(x_admin_pin: str = Header(None, alias="X-Admin-Pin")) -> str:
    if not x_admin_pin:
        raise HTTPException(401, "需要管理员认证")
    db = get_db()
    c = db.cursor()
    c.execute("SELECT pin, role FROM admin_pins WHERE pin=? AND is_active=1", (x_admin_pin,))
    row = c.fetchone()
    db.close()
    if not row:
        raise HTTPException(401, "管理员 PIN 错误")
    return row["role"]

CODE_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LEN = 6

def generate_access_code() -> str:
    return "".join(random.choices(CODE_CHARS, k=CODE_LEN))

# ── Pydantic models ──

class ScreeningSubmit(BaseModel):
    session_id: str
    patient_id: str
    patient_phone: Optional[str] = None
    q: list[int]

class PHQ9Submit(BaseModel):
    session_id: str
    p: list[int]
    is_baseline: bool = False
    symptom_onset: str = ''  # 症状出现时间：within_3days / within_week / within_2weeks / after_2weeks / not_sure

class GAD7Submit(BaseModel):
    session_id: str
    g: list[int]
    is_baseline: bool = False
    symptom_onset: str = ''

class CSSRSSubmit(BaseModel):
    session_id: str
    wish_dead: int = 0
    non_specific: int = 0
    with_method: int = 0
    with_intent: int = 0
    with_plan: int = 0

class DecisionRequest(BaseModel):
    session_id: str

class ScaleSubmit(BaseModel):
    session_id: str
    scale_name: str
    answers: list[int]

class ScaleAssignRequest(BaseModel):
    session_id: str
    scale_name: str

class PINValidateRequest(BaseModel):
    session_id: str

class AddPINRequest(BaseModel):
    pin: str
    doctor_name: str
    role: str = "doctor"

class DeletePINRequest(BaseModel):
    pin: str

# ── Static routes ──

@app.get("/")
async def root(code: str = ""):
    # 如果有 code 参数 → 患者评估入口
    if code:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/patient-confirm.html?code=" + code, status_code=302)
    return FileResponse(os.path.join(FRONTEND_DIR, "landing.html"))

@app.get("/patient.html")
async def patient_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "patient.html"))

@app.get("/patient-v4.html")
async def patient_v4_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "patient-v4.html"))

@app.get("/patient-confirm.html")
async def patient_confirm_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "patient-confirm.html"))

@app.get("/scale-detail.html")
async def scale_detail_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "scale-detail.html"))

@app.get("/doctor-card.html")
async def doctor_card_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "doctor-card.html"))

@app.get("/doctor")
async def doctor_dashboard():
    return FileResponse(os.path.join(FRONTEND_DIR, "doctor.html"))

@app.get("/mobile-doctor.html")
async def mobile_doctor():
    return FileResponse(os.path.join(FRONTEND_DIR, "mobile-doctor.html"))

@app.get("/admin.html")
async def admin_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin.html"))

@app.get("/doctor-qr.html")
async def doctor_qr():
    return FileResponse(os.path.join(FRONTEND_DIR, "doctor-qr.html"))

@app.get("/doctor-entry.html")
async def doctor_entry_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "doctor-entry.html"))

@app.get("/simple-login.html")
async def simple_login_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "simple-login.html"))

@app.get("/doctor-home.html")
async def doctor_home_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "doctor-home.html"))

@app.get("/admin-entry.html")
async def admin_entry_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin-entry.html"))

@app.get("/admin-home.html")
async def admin_home_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin-home.html"))

@app.get("/landing.html")
async def landing_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "landing.html"))



@app.get("/doctor-card.html")
async def doctor_card():
    return FileResponse(os.path.join(FRONTEND_DIR, "doctor-card.html"))


# ── 安全：登录 / 登出 / 审计 ──

class LoginRequest(BaseModel):
    pin: str

@app.post("/api/auth/login")
async def login(data: LoginRequest, request: Request):
    """PIN 登录 → 返回 session token。限号 + 锁定。"""
    ip = request.client.host if request.client else "unknown"
    lock_msg = check_lockout(ip, data.pin)
    if lock_msg:
        return {"valid": False, "error": lock_msg}
    db = get_db()
    c = db.cursor()
    c.execute("SELECT pin, doctor_name, role, is_active FROM admin_pins WHERE pin=?", (data.pin,))
    row = c.fetchone()
    source = "tdm"
    if not row:
        try:
            cssrs_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                         'cssrs-system', 'data', 'cssrs.db')
            cssrs_db = sqlite3.connect(cssrs_db_path)
            cssrs_db.row_factory = sqlite3.Row
            c2 = cssrs_db.cursor()
            c2.execute("SELECT pin, doctor_name, 'doctor' as role, is_active FROM cssrs_doctor_pins WHERE pin=? AND is_active=1", (data.pin,))
            row = c2.fetchone()
            cssrs_db.close()
            source = "cssrs"
        except Exception:
            pass
    if row and row["is_active"]:
        clear_failed_logins(ip, data.pin)
        token = create_auth_session(data.pin, row["role"], row["doctor_name"])
        audit_log(data.pin, "login", ip=ip, detail=f"source={source}")
        db.close()
        return {"valid": True, "token": token, "name": row["doctor_name"], "role": row["role"]}
    record_failed_login(ip, data.pin)
    db.close()
    return {"valid": False, "error": "PIN 错误"}

@app.post("/api/auth/logout")
async def logout(token: str = Header(None, alias="X-Session-Token")):
    if token:
        destroy_session(token)
    return {"success": True}

@app.get("/api/admin/audit", dependencies=[Depends(verify_admin_pin)])
async def admin_audit_logs(limit: int = 100):
    return {"logs": get_audit_logs(limit)}

def verify_session_token(x_session_token: str = Header(None, alias="X-Session-Token")) -> dict:
    if not x_session_token:
        raise HTTPException(401, "需要登录")
    user = verify_session(x_session_token)
    if not user:
        raise HTTPException(401, "Session 已过期")
    return user

# ── Session management (non-sensitive) ─

@app.post("/api/session/create")
async def create_session(patient_id: str, phone: Optional[str] = None, drug_categories: Optional[str] = None, doctor_pin: Optional[str] = None):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT patient_id FROM patients WHERE patient_id=?", (patient_id,))
    if not c.fetchone():
        c.execute("INSERT INTO patients (patient_id, phone) VALUES (?, ?)", (patient_id, phone))
    session_id = str(uuid.uuid4())
    access_code = generate_access_code()
    while c.execute("SELECT 1 FROM sessions WHERE access_code = ?", (access_code,)).fetchone():
        access_code = generate_access_code()
    c.execute("INSERT INTO sessions (session_id, patient_id, access_code, drug_categories, doctor_pin) VALUES (?, ?, ?, ?, ?)",
              (session_id, patient_id, access_code, drug_categories, doctor_pin))
    db.commit()
    db.close()

    # 同步患者信息和 session 到本地
    # router.register_patient(patient_id, phone)
    # router.sync_session(session_id, patient_id, access_code, drug_categories, doctor_pin)

    from scales import get_scales_for_drugs, DRUG_TO_SCALES
    scales = ["phq9", "gad7", "cssrs"]
    # 空字符串或"all"时返回所有量表
    if drug_categories == "" or drug_categories == "all":
        cats = list(DRUG_TO_SCALES.keys())
        drug_scales = get_scales_for_drugs(cats)
        scales.extend([s.name for s in drug_scales])
    elif drug_categories:
        try:
            cats = json.loads(drug_categories)
            drug_scales = get_scales_for_drugs(cats)
            scales.extend([s.name for s in drug_scales])
        except Exception:
            pass

    return {"session_id": session_id, "access_code": access_code, "scales": scales}

@app.post("/api/session/update")
async def update_session(session_id: str, drug_categories: Optional[str] = None):
    db = get_db()
    db.execute("UPDATE sessions SET drug_categories=? WHERE session_id=?", (drug_categories, session_id))
    db.commit()
    db.close()
    return {"success": True}

# ── Assessment submission → forward to local API ──

@app.post("/api/screening/submit")
async def submit_screening(data: ScreeningSubmit):
    # 只算总分，原始答案存本地
    total = sum(data.q)
    result = router.submit_assessment(data.session_id, "screening", data.q)
    if not result.get("success"):
        raise HTTPException(502, result.get("error", "本地服务不可用"))

    # 云端也存一份（含每题原始答案，供医生端查看）
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO screening (session_id, q1,q2,q3,q4,q5,q6,q7,q8,q9,q10, total_score) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (data.session_id, data.q[0], data.q[1], data.q[2], data.q[3], data.q[4],
         data.q[5], data.q[6], data.q[7], data.q[8], data.q[9], total))
    db.commit()
    db.close()

    from scorer import evaluate_screening
    r = evaluate_screening(total)
    return {"total": total, "level": r.level, "recommendation": r.recommendation}

@app.post("/api/phq9/submit")
async def submit_phq9(data: PHQ9Submit):
    total = sum(data.p)
    result = router.submit_assessment(data.session_id, "phq9", data.p, data.is_baseline)
    if not result.get("success"):
        raise HTTPException(502, result.get("error", "本地服务不可用"))

    db = get_db()
    db.execute("INSERT OR REPLACE INTO phq9 (session_id, p1, p2, p3, p4, p5, p6, p7, p8, p9, total_score, baseline, symptom_onset) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (data.session_id, data.p[0], data.p[1], data.p[2], data.p[3], data.p[4], data.p[5], data.p[6], data.p[7], data.p[8], total, 1 if data.is_baseline else 0, getattr(data, 'symptom_onset', '')))
    db.commit()
    db.close()
    return {"total": total, "is_baseline": data.is_baseline}

@app.post("/api/gad7/submit")
async def submit_gad7(data: GAD7Submit):
    total = sum(data.g)
    result = router.submit_assessment(data.session_id, "gad7", data.g, data.is_baseline)
    if not result.get("success"):
        raise HTTPException(502, result.get("error", "本地服务不可用"))

    db = get_db()
    db.execute("INSERT OR REPLACE INTO gad7 (session_id, g1, g2, g3, g4, g5, g6, g7, total_score, baseline, symptom_onset) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (data.session_id, data.g[0], data.g[1], data.g[2], data.g[3], data.g[4], data.g[5], data.g[6], total, 1 if data.is_baseline else 0, getattr(data, 'symptom_onset', '')))
    db.commit()
    db.close()
    return {"total": total, "is_baseline": data.is_baseline}

@app.post("/api/cssrs/submit")
async def submit_cssrs(data: CSSRSSubmit):
    severity = 0
    if data.with_plan: severity = 5
    elif data.with_intent: severity = 4
    elif data.with_method: severity = 3
    elif data.non_specific: severity = 2
    elif data.wish_dead: severity = 1

    answers = [data.wish_dead, data.non_specific, data.with_method, data.with_intent, data.with_plan]
    result = router.submit_assessment(data.session_id, "cssrs", answers)
    if not result.get("success"):
        raise HTTPException(502, result.get("error", "本地服务不可用"))

    # 云端也存一份（含每题原始答案，供医生端查看）
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO cssrs (session_id, wish_dead, non_specific, with_method, with_intent, with_plan, severity_score) "
        "VALUES (?,?,?,?,?,?,?)",
        (data.session_id, data.wish_dead, data.non_specific, data.with_method,
         data.with_intent, data.with_plan, severity))
    db.commit()
    db.close()

    from scorer import evaluate_cssrs
    r = evaluate_cssrs(severity)
    return {"severity": severity, "level": r.level, "recommendation": r.recommendation}

@app.post("/api/scale/submit")
async def submit_scale(data: ScaleSubmit):
    from scales import ALL_SCALES
    scale = ALL_SCALES.get(data.scale_name)
    if not scale:
        raise HTTPException(400, f"Unknown scale: {data.scale_name}")

    result = scale.score(data.answers)
    total = result["total"]

    # 原始答案存本地
    local_result = router.submit_assessment(data.session_id, data.scale_name, data.answers)
    if not local_result.get("success"):
        raise HTTPException(502, local_result.get("error", "本地服务不可用"))

    # 云端也存一份（含原始答案，供医生端查看）
    db = get_db()
    scale_id = f"{data.session_id}_{data.scale_name}"
    db.execute("""INSERT OR REPLACE INTO scale_results
                  (id, session_id, scale_name, drug_category, status, raw_answers, total_score)
                  VALUES (?,?,?,?,?,?,?)""",
               (scale_id, data.session_id, data.scale_name, scale.drug_category, "completed",
                json.dumps(data.answers), total))
    db.commit()
    db.close()
    return {"scale": data.scale_name, "score": total, "level": result["level"], "label": result["label"]}

@app.post("/api/scale/assign")
async def assign_scale(data: ScaleAssignRequest):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT status FROM scale_results WHERE session_id=? AND scale_name=?",
              (data.session_id, data.scale_name))
    row = c.fetchone()
    if row and row["status"] == "completed":
        db.close()
        raise HTTPException(400, "该量表已完成")

    temp_code = uuid.uuid4().hex[:8].upper()
    scale_id = f"{data.session_id}_{data.scale_name}"

    if row:
        c.execute("UPDATE scale_results SET id=?, status='pending', assigned_at=datetime('now') WHERE session_id=? AND scale_name=?",
                  (temp_code, data.session_id, data.scale_name))
    else:
        c.execute("INSERT INTO scale_results (id, session_id, scale_name, status, assigned_at) VALUES (?,?,?,?,datetime('now'))",
                  (temp_code, data.session_id, data.scale_name, "pending"))
    db.commit()
    db.close()
    return {"temp_code": temp_code}

@app.get("/api/scale/results/{session_id}")
async def get_scale_results(session_id: str):
    """返回量表状态和总分，不含原始答案（原始答案通过本地 API 获取）。"""
    db = get_db()
    c = db.cursor()

    c.execute("SELECT drug_categories FROM sessions WHERE session_id=?", (session_id,))
    row = c.fetchone()
    drug_cats = []
    if row and row["drug_categories"]:
        try:
            drug_cats = json.loads(row["drug_categories"])
        except Exception:
            pass

    from scales import get_scales_for_drugs, DRUG_TO_SCALES
    expected = ["phq9", "gad7", "cssrs"]
    if drug_cats:
        drug_scales = get_scales_for_drugs(drug_cats)
        expected.extend([s.name for s in drug_scales])

    c.execute("SELECT scale_name, status, total_score, completed_at FROM scale_results WHERE session_id=?", (session_id,))
    results = []
    for r in c.fetchall():
        d = dict(r)
        d["raw_answers"] = None  # 不返回原始答案
        results.append(d)

    # 补齐所有量表（基础 + 药物相关）
    completed_names = [r["scale_name"] for r in results]
    for name in expected:
        if name not in completed_names:
            # 确定字段名
            if name == "cssrs":
                col = "severity_score"
            elif name in ["phq9", "gad7", "dess", "ciwa_b", "ap_withdrawal_short", "ap_withdrawal", "ymrs", "madrs"]:
                col = "total_score"
            else:
                col = "total_score"
            try:
                c.execute(f"SELECT {col} FROM {name} WHERE session_id=?", (session_id,))
                row = c.fetchone()
                results.append({
                    "scale_name": name,
                    "status": "completed" if row else "pending",
                    "total_score": row[col] if row else None,
                    "raw_answers": None,
                    "completed_at": None,
                })
            except Exception:
                results.append({
                    "scale_name": name,
                    "status": "pending",
                    "total_score": None,
                    "raw_answers": None,
                    "completed_at": None,
                })

    db.close()
    return {"scales": results}

# ── Decision → call local API ──

@app.post("/api/decision")
async def compute_decision(data: DecisionRequest):
    from scorer import evaluate_screening, evaluate_relapse, evaluate_cssrs, tdm_decision
    import sqlite3

    DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'tapering.db')
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # ── Track 1: 撤药筛查 ──
    c.execute("SELECT total_score FROM screening WHERE session_id=?", (data.session_id,))
    scr = c.fetchone()
    has_screening_data = scr is not None
    scr_total = scr["total_score"] if scr else 0

    # ── Track 2: 复燃监测（PHQ-9 + GAD-7）──
    c.execute("SELECT total_score, baseline FROM phq9 WHERE session_id=? ORDER BY baseline DESC LIMIT 2", (data.session_id,))
    phq9_rows = c.fetchall()
    phq9_curr = phq9_rows[0]["total_score"] if phq9_rows else 0
    phq9_base = phq9_rows[1]["total_score"] if len(phq9_rows) > 1 else 10
    if phq9_curr and phq9_base == 0:
        phq9_base = 10

    c.execute("SELECT total_score, baseline FROM gad7 WHERE session_id=? ORDER BY baseline DESC LIMIT 2", (data.session_id,))
    gad7_rows = c.fetchall()
    gad7_curr = gad7_rows[0]["total_score"] if gad7_rows else 0
    gad7_base = gad7_rows[1]["total_score"] if len(gad7_rows) > 1 else 10
    if gad7_curr and gad7_base == 0:
        gad7_base = 10

    # ── Track 3: C-SSRS ──
    c.execute("SELECT severity_score FROM cssrs WHERE session_id=?", (data.session_id,))
    cssrs = c.fetchone()
    cssrs_sev = cssrs["severity_score"] if cssrs else 0

    # ── 是否首评：只有 baseline 记录、无 follow-up ──
    c.execute("SELECT COUNT(*) as cnt FROM phq9 WHERE session_id=? AND baseline=0", (data.session_id,))
    phq9_followup = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM gad7 WHERE session_id=? AND baseline=0", (data.session_id,))
    gad7_followup = c.fetchone()["cnt"]
    is_baseline = (phq9_followup == 0 and gad7_followup == 0)

    # ── 第4轨：药物量表 ──
    c.execute("SELECT scale_name, total_score FROM scale_results WHERE session_id=? AND status='completed'", (data.session_id,))
    drug_scales_data = [{"scale_name": r["scale_name"], "total_score": r["total_score"]} for r in c.fetchall()]
    if not drug_scales_data:
        drug_scales_data = None

    # 获取症状出现时间
    c.execute('SELECT symptom_onset FROM phq9 WHERE session_id=? ORDER BY baseline DESC LIMIT 1', (data.session_id,))
    phq9_row = c.fetchone()
    symptom_onset = phq9_row['symptom_onset'] if phq9_row and phq9_row['symptom_onset'] else ''

    # 获取 DAWSS 分数（如果有）
    c.execute('SELECT total_score FROM scale_results WHERE session_id=? AND scale_name=?', (data.session_id, 'dawss'))
    dawss_row = c.fetchone()
    dawss_score = dawss_row['total_score'] if dawss_row and dawss_row['total_score'] is not None else -1

    conn.close()

    # ── 计算决策 ──
    screening = evaluate_screening(scr_total)
    relapse = evaluate_relapse(phq9_curr, phq9_base, gad7_curr, gad7_base, symptom_onset, dawss_score)
    cssrs_result = evaluate_cssrs(cssrs_sev)
    decision = tdm_decision(screening, relapse, cssrs_result, is_baseline=is_baseline, drug_scales_data=drug_scales_data, has_screening=has_screening_data)

    # ── 药物量表等级 ──
    from scorer import evaluate_drug_scales
    if drug_scales_data:
        drug_result = evaluate_drug_scales(drug_scales_data)
        drug_scales_level = drug_result.level
    else:
        drug_scales_level = None

    # ── 写入 tdm_decision 表 ──
    conn2 = sqlite3.connect(DB)
    # 迁移：确保 drug_scales_level 和 baseline_flag 列存在
    try:
        conn2.execute("ALTER TABLE tdm_decision ADD COLUMN drug_scales_level TEXT")
    except Exception:
        pass
    try:
        conn2.execute("ALTER TABLE tdm_decision ADD COLUMN baseline_flag INTEGER DEFAULT 0")
    except Exception:
        pass
    conn2.execute("""INSERT OR REPLACE INTO tdm_decision
        (session_id, withdrawal_score, withdrawal_level, phq9_change, gad7_change,
         phq9_level, gad7_level, cssrs_severity, cssrs_level,
         overall_decision, overall_risk_level, cssrs_override, drug_scales_level, baseline_flag)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data.session_id, scr_total, screening.level,
         relapse.phq9_change, relapse.gad7_change,
         "red" if relapse.phq9_change >= 5 else ("yellow" if relapse.phq9_change >= 3 else "green"),
         "red" if relapse.gad7_change >= 4 else ("yellow" if relapse.gad7_change >= 2 else "green"),
         cssrs_sev, cssrs_result.level,
         decision.overall_decision, decision.overall_risk,
         1 if cssrs_result.level == "red" else 0,
         drug_scales_level,
         1 if is_baseline else 0))
    conn2.commit()
    conn2.close()

    # ── 返回结果 ──
    tracks = {
        "withdrawal": screening.level if scr_total > 0 else ("yellow (未填写)", "yellow")[0],
        "relapse": relapse.level,
        "cssrs": cssrs_result.level,
    }
    if drug_scales_data:
        tracks["drug_scales"] = drug_scales_level

    return {
        "decision": decision.overall_decision,
        "risk": decision.overall_risk,
        "action": decision.action,
        "tracks": tracks,
        "is_baseline": is_baseline,
        "cssrs_override": cssrs_result.level == "red",
        "message_doctor": decision.message_doctor,
        "message_patient": decision.message_patient,
        "details": {
            "screening_total": scr_total,
            "phq9_change": relapse.phq9_change,
            "gad7_change": relapse.gad7_change,
            "cssrs_severity": cssrs_sev,
            "phq9_current": phq9_curr,
            "gad7_current": gad7_curr,
            "drug_scales": drug_scales_level,
        }
    }

# ── Patient history → desensitized ─

@app.post("/api/session/update")
async def update_session_drugs(session_id: str, drug_categories: str = ""):
    """更新 session 的药物类别（患者选药后调用）。"""
    db = get_db()
    db.execute("UPDATE sessions SET drug_categories=? WHERE session_id=?", (drug_categories, session_id))
    db.commit()
    db.close()
    return {"success": True}


@app.post("/api/patient/update")
async def update_patient(patient_id: str, name: str = "", phone: str = ""):
    """更新患者信息（姓名/手机）。"""
    db = get_db()
    db.execute("UPDATE patients SET name=?, phone=? WHERE patient_id=?", (name, phone, patient_id))
    db.commit()
    db.close()
    return {"success": True}


@app.get("/api/patient/history/{patient_id}")
async def patient_history(patient_id: str, doctor_pin: Optional[str] = None):
    """患者历史（脱敏）：手机号打码，不含每题详情。"""
    db = get_db()
    c = db.cursor()
    where = "WHERE s.patient_id = ?"
    params: list = [patient_id]
    if doctor_pin:
        where += " AND s.doctor_pin = ?"
        params.append(doctor_pin)
    c.execute(f"""SELECT s.session_id, s.created_at, s.access_code, s.drug_categories, s.doctor_pin,
                 pt.phone,
                 scr.total_score as screening_score,
                 phq9.total_score as phq9_score, gad7.total_score as gad7_score,
                 css.severity_score as cssrs_severity,
                 td.overall_decision, td.overall_risk_level, td.cssrs_override
                 FROM sessions s
                 LEFT JOIN patients pt ON s.patient_id = pt.patient_id
                 LEFT JOIN screening scr ON s.session_id = scr.session_id
                 LEFT JOIN phq9 ON s.session_id = phq9.session_id
                 LEFT JOIN gad7 ON s.session_id = gad7.session_id
                 LEFT JOIN cssrs css ON s.session_id = css.session_id
                 LEFT JOIN tdm_decision td ON s.session_id = td.session_id
                 {where} ORDER BY s.created_at DESC""", params)
    rows = [dict(r) for r in c.fetchall()]

    # 手机号脱敏
    for r in rows:
        if r.get("phone"):
            r["phone"] = router.desensitize_phone(r["phone"])

    db.close()
    return {"patient_id": patient_id, "assessments": rows}

@app.get("/api/doctor/detail/{session_id}")
async def doctor_session_detail(session_id: str, doctor_pin: str = "", request: Request = None):
    """医生查看 session 完整详情（含敏感数据）。需验证医生 PIN。"""
    # 验证医生 PIN
    db = get_db()
    c = db.cursor()
    c.execute("SELECT pin FROM admin_pins WHERE pin=? AND role IN ('doctor','admin') AND is_active=1", (doctor_pin,))
    if not c.fetchone():
        # Fallback to C-SSRS
        try:
            cssrs_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                         'cssrs-system', 'data', 'cssrs.db')
            cssrs_db = sqlite3.connect(cssrs_db_path)
            cssrs_db.row_factory = sqlite3.Row
            c2 = cssrs_db.cursor()
            c2.execute("SELECT pin FROM cssrs_doctor_pins WHERE pin=? AND is_active=1", (doctor_pin,))
            if not c2.fetchone():
                db.close()
                cssrs_db.close()
                raise HTTPException(401, "医生 PIN 错误")
            cssrs_db.close()
        except HTTPException:
            raise
        except Exception:
            db.close()
            raise HTTPException(401, "医生 PIN 错误")
    db.close()

    # 验证 session 归属（医生只能看自己的患者）
    db = get_db()
    c = db.cursor()
    c.execute("SELECT doctor_pin FROM sessions WHERE session_id=?", (session_id,))
    row = c.fetchone()
    db.close()
    if row and row["doctor_pin"] and row["doctor_pin"] != doctor_pin:
        # Admin can see all
        db = get_db()
        c = db.cursor()
        c.execute("SELECT role FROM admin_pins WHERE pin=? AND is_active=1", (doctor_pin,))
        r = c.fetchone()
        db.close()
        if not r or r["role"] != "admin":
            raise HTTPException(403, "无权查看此患者")

    # 审计日志
    audit_log(doctor_pin, "view_detail", session_id=session_id, ip=request.client.host if request.client else None)

    # 直接从云端数据库查询全部量表和答题内容
    db = get_db()
    c = db.cursor()

    c.execute("SELECT s.patient_id, p.phone, p.name, p.diagnosis FROM sessions s LEFT JOIN patients p ON s.patient_id=p.patient_id WHERE s.session_id=?", (session_id,))
    patient = c.fetchone()

    scales = {}

    # ── 基础量表：screening / phq9 / gad7（每题答案在独立列中）──
    base_scales = {
        "screening": {
            "questions": ["头晕/眩晕","头痛","脑电击感","恶心/呕吐","入睡困难","焦虑/紧张","情绪低落","注意力不集中","手抖/出汗","肌肉酸痛"],
            "cols": ["q1","q2","q3","q4","q5","q6","q7","q8","q9","q10"],
        },
        "phq9": {
            "questions": ["做事提不起劲","心情低落","入睡困难","疲倦没活力","食欲不振","觉得自己失败","专注困难","动作缓慢/烦躁","自杀念头"],
            "cols": ["p1","p2","p3","p4","p5","p6","p7","p8","p9"],
        },
        "gad7": {
            "questions": ["紧张焦虑","不能控制担忧","担忧过多","很难放松","坐立不安","容易烦恼","可怕事情发生"],
            "cols": ["g1","g2","g3","g4","g5","g6","g7"],
        },
    }

    for table_name, cfg in base_scales.items():
        try:
            c.execute("SELECT * FROM " + table_name + " WHERE session_id=?", (session_id,))
            row = c.fetchone()
            if row:
                d = dict(row)
                answers = []
                for i, (col, q) in enumerate(zip(cfg["cols"], cfg["questions"])):
                    if col in d and d[col] is not None:
                        answers.append({"question": q, "answer": d[col]})
                d["answers"] = answers
                scales[table_name] = d
        except Exception:
            pass

    # ── C-SSRS：列名不是 a1-a5 格式，需要特殊处理 ──
    try:
        c.execute("SELECT * FROM cssrs WHERE session_id=?", (session_id,))
        row = c.fetchone()
        if row:
            d = dict(row)
            cssrs_items = [
                ("渴望死亡", "wish_dead"),
                ("非特定自杀意念", "non_specific"),
                ("有方法的自杀想法", "with_method"),
                ("有意图的自杀想法", "with_intent"),
                ("有计划的自杀想法", "with_plan"),
            ]
            answers = []
            for q_label, col in cssrs_items:
                if col in d and d[col] is not None:
                    answers.append({"question": q_label, "answer": d[col]})
            d["answers"] = answers
            scales["cssrs"] = d
    except Exception:
        pass

    # ── 药物特定量表：从 scale_results 读取 raw_answers (JSON) ──
    try:
        c.execute("SELECT * FROM scale_results WHERE session_id=?", (session_id,))
        for row in c.fetchall():
            d = dict(row)
            raw = d.get("raw_answers")
            if raw:
                answers = json.loads(raw) if isinstance(raw, str) else raw
                # Use real question text from scale definitions
            from scales import ALL_SCALES
            scale_def = ALL_SCALES.get(d.get("scale_name"))
            questions = scale_def.questions if scale_def else [f"第{i+1}题" for i in range(len(answers))]
            d["answers"] = [{"question": questions[i] if i < len(questions) else f"第{i+1}题", "answer": v} for i, v in enumerate(answers)]
            scales[d["scale_name"]] = d
    except Exception:
        pass

    c.execute("SELECT * FROM tdm_decision WHERE session_id=?", (session_id,))
    decision = c.fetchone()
    db.close()

    result = {"patient": dict(patient) if patient else None, "tdm_decision": dict(decision) if decision else None}
    result.update(scales)
    return result

# ── Admin / PIN ──

@app.post("/api/admin/validate")
async def admin_validate(data: PINValidateRequest):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT pin, doctor_name, role, is_active FROM admin_pins WHERE pin=?", (data.session_id,))
    row = c.fetchone()
    source = "tdm"

    if not row:
        try:
            cssrs_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                         'cssrs-system', 'data', 'cssrs.db')
            cssrs_db = sqlite3.connect(cssrs_db_path)
            cssrs_db.row_factory = sqlite3.Row
            c2 = cssrs_db.cursor()
            c2.execute("SELECT pin, doctor_name, 'doctor' as role, is_active FROM cssrs_doctor_pins WHERE pin=? AND is_active=1",
                       (data.session_id,))
            row = c2.fetchone()
            cssrs_db.close()
            source = "cssrs"
        except Exception:
            pass

    if row and row["is_active"]:
        get_db().cursor().execute("UPDATE admin_pins SET last_used=datetime('now') WHERE pin=?", (data.session_id,)).connection.commit()
        return {"valid": True, "name": row["doctor_name"], "role": row["role"], "source": source}
    return {"valid": False}

@app.get("/api/admin/pins", dependencies=[Depends(verify_admin_pin)])
async def list_pins():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT pin, doctor_name, role, is_active, created_at, last_used FROM admin_pins ORDER BY created_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    db.close()
    return {"pins": rows}

@app.post("/api/admin/pins/generate", dependencies=[Depends(verify_admin_pin)])
async def generate_pin(doctor_name: str, role: str = "doctor"):
    db = get_db()
    for _ in range(100):
        pin = "".join(random.choices("0123456789", k=4))
        if not db.execute("SELECT 1 FROM admin_pins WHERE pin=?", (pin,)).fetchone():
            db.execute("INSERT INTO admin_pins (pin, doctor_name, role) VALUES (?,?,?)",
                       (pin, doctor_name, role))
            db.commit()
            db.close()
            return {"success": True, "pin": pin}
    db.close()
    return {"success": False, "error": "无法生成唯一 PIN"}

@app.post("/api/admin/pins/add", dependencies=[Depends(verify_admin_pin)])
async def add_pin(data: AddPINRequest):
    try:
        db = get_db()
        db.execute("INSERT INTO admin_pins (pin, doctor_name, role) VALUES (?,?,?)",
                   (data.pin, data.doctor_name, data.role))
        db.commit()
        db.close()
        return {"success": True}
    except Exception as e:
        if "UNIQUE" in str(e):
            return {"success": False, "already_exists": True, "error": "PIN 已存在"}
        return {"success": False, "error": str(e)}

@app.post("/api/admin/pins/delete", dependencies=[Depends(verify_admin_pin)])
async def delete_pin(data: DeletePINRequest):
    db = get_db()
    db.execute("DELETE FROM admin_pins WHERE pin=?", (data.pin,))
    db.commit()
    db.close()
    return {"success": True}

@app.post("/api/admin/pins/toggle", dependencies=[Depends(verify_admin_pin)])
async def toggle_pin(data: DeletePINRequest):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT is_active FROM admin_pins WHERE pin=?", (data.pin,))
    row = c.fetchone()
    new_val = None
    if row:
        new_val = 1 - row["is_active"]
        db.execute("UPDATE admin_pins SET is_active=? WHERE pin=?", (new_val, data.pin))
        db.commit()
    db.close()
    return {"success": True, "is_active": new_val}

# ── Code resolution ──

@app.get("/api/code/{code}")
def get_session_by_code(code: str):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT s.session_id, s.patient_id FROM sessions s WHERE s.access_code=?", (code,))
    row = c.fetchone()
    if row:
        db.close()
        return {"session_id": row["session_id"], "patient_id": row["patient_id"]}
    c2 = db.cursor()
    c2.execute("SELECT session_id, scale_name FROM scale_results WHERE id=? AND status='pending'", (code,))
    r2 = c2.fetchone()
    db.close()
    if r2:
        return {"session_id": r2["session_id"], "scale_name": r2["scale_name"]}
    raise HTTPException(404, "Invalid access code")

# ── Admin: Assessment management ──

class ChangePasswordRequest(BaseModel):
    old_pin: str
    new_pin: str

class BulkDeleteRequest(BaseModel):
    session_ids: list[str]

@app.get("/api/admin/report", dependencies=[Depends(verify_admin_pin)])
async def admin_report(q: str = ""):
    """管理报表（脱敏）：不返回姓名和完整手机号。"""
    db = get_db()
    c = db.cursor()

    base_sql = """SELECT s.session_id, s.patient_id, s.created_at, s.access_code, s.drug_categories, s.doctor_pin,
                 pt.phone_masked as phone, pt.name_masked as name,
                 scr.total_score as screening_score,
                 phq9.total_score as phq9_score, gad7.total_score as gad7_score,
                 css.severity_score as cssrs_severity,
                 td.overall_decision, td.overall_risk_level, td.cssrs_override,
                 CASE WHEN td.session_id IS NOT NULL THEN 'completed' ELSE 'pending' END as completion_status
                 FROM sessions s
                 LEFT JOIN patients pt ON s.patient_id = pt.patient_id
                 LEFT JOIN screening scr ON s.session_id = scr.session_id
                 LEFT JOIN phq9 ON s.session_id = phq9.session_id
                 LEFT JOIN gad7 ON s.session_id = gad7.session_id
                 LEFT JOIN cssrs css ON s.session_id = css.session_id
                 LEFT JOIN tdm_decision td ON s.session_id = td.session_id"""

    if q:
        pattern = f"%{q}%"
        c.execute(base_sql + " WHERE s.patient_id LIKE ? OR pt.name LIKE ? OR pt.phone LIKE ? ORDER BY s.created_at DESC", (pattern, pattern, pattern))
    else:
        c.execute(base_sql + " ORDER BY s.created_at DESC")

    rows = [dict(r) for r in c.fetchall()]

    # 脱敏
    for r in rows:
        if r.get("phone"):
            r["phone"] = router.desensitize_phone(r["phone"])
        if r.get("name"):
            r["name"] = router.desensitize_name(r["name"])

    db.close()
    return {"assessments": rows}

@app.get("/api/admin/report/{session_id}", dependencies=[Depends(verify_admin_pin)])
async def admin_report_detail(session_id: str, request: Request = None, x_admin_pin: str = Header(None, alias="X-Admin-Pin")):
    """管理详情 — 从云端数据库直接查询完整数据。"""
    audit_log(x_admin_pin, "admin_view_detail", session_id=session_id,
              ip=request.client.host if request else None)

    db = get_db()
    c = db.cursor()

    # Session info
    c.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,))
    session = c.fetchone()
    if not session:
        db.close()
        raise HTTPException(404, "Session not found")
    result = dict(session)

    # Patient info
    c.execute("SELECT * FROM patients WHERE patient_id=?", (result["patient_id"],))
    patient = c.fetchone()
    result["patient"] = dict(patient) if patient else None

    # Base scales with answers
    scale_cols = {
        "screening": (["头晕/眩晕","头痛","脑电击感","恶心/呕吐","入睡困难","焦虑/紧张","情绪低落","注意力不集中","手抖/出汗","肌肉酸痛"], ["q1","q2","q3","q4","q5","q6","q7","q8","q9","q10"]),
        "phq9": (["做事提不起劲","心情低落","入睡困难","疲倦没活力","食欲不振","觉得自己失败","专注困难","动作缓慢/烦躁","自杀念头"], ["p1","p2","p3","p4","p5","p6","p7","p8","p9"]),
        "gad7": (["紧张焦虑","不能控制担忧","担忧过多","很难放松","坐立不安","容易烦恼","可怕事情发生"], ["g1","g2","g3","g4","g5","g6","g7"]),
    }

    cssrs_items = [("渴望死亡","wish_dead"),("非特定自杀意念","non_specific"),("有方法","with_method"),("有意图","with_intent"),("有计划","with_plan")]

    for table in ["screening", "phq9", "gad7"]:
        c.execute("SELECT * FROM " + table + " WHERE session_id=?", (session_id,))
        row = c.fetchone()
        if row:
            d = dict(row)
            questions, cols = scale_cols[table]
            d["answers"] = [{"question": q, "answer": d[col]} for q, col in zip(questions, cols) if col in d]
            result[table] = d

    c.execute("SELECT * FROM cssrs WHERE session_id=?", (session_id,))
    row = c.fetchone()
    if row:
        d = dict(row)
        d["answers"] = [{"question": q, "answer": d[col]} for q, col in cssrs_items if col in d]
        result["cssrs"] = d

    # Drug scales from scale_results
    c.execute("SELECT * FROM scale_results WHERE session_id=?", (session_id,))
    drug_scales = []
    for row in c.fetchall():
        d = dict(row)
        raw = d.get("raw_answers")
        if raw:
            answers = json.loads(raw) if isinstance(raw, str) else raw
            # Use real question text from scale definitions
            from scales import ALL_SCALES
            scale_def = ALL_SCALES.get(d.get("scale_name"))
            questions = scale_def.questions if scale_def else [f"第{i+1}题" for i in range(len(answers))]
            d["answers"] = [{"question": questions[i] if i < len(questions) else f"第{i+1}题", "answer": v} for i, v in enumerate(answers)]
        drug_scales.append(d)
    result["drug_scales"] = drug_scales

    # TDM decision
    c.execute("SELECT * FROM tdm_decision WHERE session_id=?", (session_id,))
    row = c.fetchone()
    result["decision"] = dict(row) if row else None

    db.close()
    return result

@app.delete("/api/admin/report/{session_id}", dependencies=[Depends(verify_admin_pin)])
async def admin_delete_session(session_id: str):
    """删除 → 本地 API。"""
    result = router.delete_session(session_id)
    if "error" in result:
        raise HTTPException(502, result["error"])
    # 云端也清理
    db = get_db()
    for t in ["screening", "phq9", "gad7", "cssrs", "tdm_decision", "sessions"]:
        db.execute(f"DELETE FROM {t} WHERE session_id=?", (session_id,))
    db.commit()
    db.close()
    return {"success": True, "deleted": session_id}

@app.post("/api/admin/report/delete", dependencies=[Depends(verify_admin_pin)])
async def admin_bulk_delete(data: BulkDeleteRequest):
    result = {"success": True, "deleted_count": 0}
    for sid in data.session_ids:
        router.delete_session(sid)
        db = get_db()
        for t in ["screening", "phq9", "gad7", "cssrs", "tdm_decision", "sessions"]:
            db.execute(f"DELETE FROM {t} WHERE session_id=?", (sid,))
        db.commit()
        db.close()
        result["deleted_count"] += 1
    return result

@app.post("/api/admin/pins/set", dependencies=[Depends(verify_admin_pin)])
async def admin_change_password(data: ChangePasswordRequest):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT pin FROM admin_pins WHERE pin=? AND is_active=1", (data.old_pin,))
    if not c.fetchone():
        db.close()
        return {"success": False, "error": "原密码错误"}
    c.execute("UPDATE admin_pins SET pin=? WHERE pin=?", (data.new_pin, data.old_pin))
    db.commit()
    db.close()
    return {"success": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
