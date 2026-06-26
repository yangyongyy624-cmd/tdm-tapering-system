"""TDM Local API — 敏感数据服务（端口 8002）

只处理敏感数据：患者信息、量表原始答案、完整会话详情、决策计算。
通过 SSH 反向隧道暴露给云端 API 调用。
"""
import json, os, uuid
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import init_db, get_db
from scorer import evaluate_screening, evaluate_relapse, evaluate_cssrs, tdm_decision
from scales import get_scales_for_drugs, score_scale, ALL_SCALES

app = FastAPI(title="TDM Local API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

# ── Pydantic models ──

class PatientRegister(BaseModel):
    patient_id: str
    phone: Optional[str] = None
    name: Optional[str] = None
    diagnosis: Optional[str] = None
    current_meds: Optional[str] = None
    tapering_meds: Optional[str] = None

class AssessmentSubmit(BaseModel):
    session_id: str
    scale_name: str  # screening, phq9, gad7, cssrs, or drug-specific scale name
    answers: list[int]
    is_baseline: bool = False

class DecisionCompute(BaseModel):
    session_id: str

class SessionSync(BaseModel):
    session_id: str
    patient_id: str
    access_code: str
    drug_categories: Optional[str] = None
    doctor_pin: Optional[str] = None

# ── Patient management ──

@app.post("/api/patient/register")
async def register_patient(data: PatientRegister):
    """注册患者信息（敏感 PII）。"""
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO patients (patient_id, phone, name, diagnosis, current_meds, tapering_meds) "
        "VALUES (?,?,?,?,?,?)",
        (data.patient_id, data.phone, data.name, data.diagnosis, data.current_meds, data.tapering_meds)
    )
    db.commit()
    db.close()
    return {"success": True, "patient_id": data.patient_id}

@app.get("/api/patient/{patient_id}")
async def get_patient(patient_id: str):
    """获取患者完整信息。"""
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,))
    row = c.fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "患者不存在")
    return dict(row)

@app.get("/api/patient/history/{patient_id}")
async def patient_history(patient_id: str):
    """患者完整历史（含脱敏手机号、每题详情）。"""
    db = get_db()
    c = db.cursor()

    # 患者信息
    c.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,))
    patient = c.fetchone()

    # 所有 session
    c.execute("SELECT session_id, created_at, access_code, drug_categories, doctor_pin FROM sessions WHERE patient_id=? ORDER BY created_at DESC", (patient_id,))
    sessions = [dict(r) for r in c.fetchall()]

    # 每个 session 的完整评估数据
    assessments = []
    for s in sessions:
        sid = s["session_id"]
        detail = {"session_id": sid, "created_at": s["created_at"], "access_code": s["access_code"], "drug_categories": s["drug_categories"], "doctor_pin": s["doctor_pin"]}

        for table in ["screening", "phq9", "gad7", "cssrs", "tdm_decision"]:
            c.execute(f"SELECT * FROM {table} WHERE session_id=?", (sid,))
            r = c.fetchone()
            if r:
                detail[table] = dict(r)

        c.execute("SELECT * FROM scale_results WHERE session_id=?", (sid,))
        detail["drug_scales"] = [dict(r) for r in c.fetchall()]

        assessments.append(detail)

    db.close()
    return {
        "patient": dict(patient) if patient else None,
        "assessments": assessments,
    }

# ── Assessment submission ──

@app.post("/api/assessment/submit")
async def submit_assessment(data: AssessmentSubmit):
    """提交任意量表原始答案。"""
    db = get_db()

    if data.scale_name == "screening":
        total = sum(data.answers)
        db.execute(
            "INSERT OR REPLACE INTO screening (session_id, q1,q2,q3,q4,q5,q6,q7,q8,q9,q10, total_score) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (data.session_id, *data.answers, total)
        )
    elif data.scale_name == "phq9":
        total = sum(data.answers)
        db.execute(
            "INSERT OR REPLACE INTO phq9 (session_id, p1,p2,p3,p4,p5,p6,p7,p8,p9, total_score, baseline) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (data.session_id, *data.answers, total, 1 if data.is_baseline else 0)
        )
    elif data.scale_name == "gad7":
        total = sum(data.answers)
        db.execute(
            "INSERT OR REPLACE INTO gad7 (session_id, g1,g2,g3,g4,g5,g6,g7, total_score, baseline) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (data.session_id, *data.answers, total, 1 if data.is_baseline else 0)
        )
    elif data.scale_name == "cssrs":
        answers = data.answers
        severity = 0
        if len(answers) >= 5:
            if answers[4]: severity = 5        # with_plan
            elif answers[3]: severity = 4      # with_intent
            elif answers[2]: severity = 3      # with_method
            elif answers[1]: severity = 2      # non_specific
            elif answers[0]: severity = 1      # wish_dead
        db.execute(
            "INSERT OR REPLACE INTO cssrs (session_id, wish_dead, non_specific, with_method, with_intent, with_plan, severity_score) "
            "VALUES (?,?,?,?,?,?,?)",
            (data.session_id, answers[0] if len(answers) > 0 else 0,
             answers[1] if len(answers) > 1 else 0,
             answers[2] if len(answers) > 2 else 0,
             answers[3] if len(answers) > 3 else 0,
             answers[4] if len(answers) > 4 else 0,
             severity)
        )
    elif data.scale_name in ALL_SCALES:
        scale = ALL_SCALES[data.scale_name]
        result = scale.score(data.answers)
        scale_id = f"{data.session_id}_{data.scale_name}"
        db.execute(
            "INSERT OR REPLACE INTO scale_results (id, session_id, scale_name, drug_category, status, raw_answers, total_score) "
            "VALUES (?,?,?,?,?,?,?)",
            (scale_id, data.session_id, data.scale_name, scale.drug_category, "completed", json.dumps(data.answers), result["total"])
        )
    elif data.scale_name in ["ap_withdrawal", "ap_withdrawal_short", "ymrs", "ymrs_full"]:
        # 新量表：直接存到 scale_results
        result = score_scale(data.scale_name, data.answers)
        scale_id = f"{data.session_id}_{data.scale_name}"
        db.execute(
            "INSERT OR REPLACE INTO scale_results (id, session_id, scale_name, drug_category, status, raw_answers, total_score) "
            "VALUES (?,?,?,?,?,?,?)",
            (scale_id, data.session_id, data.scale_name, "antipsychotic" if "ap_" in data.scale_name else "mood_stabilizer", "completed", json.dumps(data.answers), result["total"])
        )
    else:
        db.close()
        raise HTTPException(400, f"未知量表: {data.scale_name}")

    db.commit()
    db.close()
    return {"success": True, "scale": data.scale_name}

# ── Session sync ──

@app.post("/api/session/sync")
async def sync_session(data: SessionSync):
    """云端创建 session 后同步到本地。"""
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO sessions (session_id, patient_id, access_code, drug_categories, doctor_pin) "
        "VALUES (?,?,?,?,?)",
        (data.session_id, data.patient_id, data.access_code, data.drug_categories, data.doctor_pin)
    )
    db.commit()
    db.close()
    
    # 检查是否所有量表都已完成，并通知云端更新状态
    try:
        import httpx
        db2 = get_db()
        c = db2.cursor()
        
        # 获取 session 信息
        c.execute("SELECT drug_categories FROM sessions WHERE session_id=?", (data.session_id,))
        row = c.fetchone()
        drug_cats = []
        if row and row["drug_categories"]:
            import json
            drug_cats = json.loads(row["drug_categories"])
        
        from scales import get_scales_for_drugs
        expected = ["screening", "phq9", "gad7", "cssrs"]
        if drug_cats:
            drug_scales = get_scales_for_drugs(drug_cats)
            expected.extend([s.name for s in drug_scales])
        
        c.execute("SELECT scale_name FROM scale_results WHERE session_id=? AND status='completed'", (data.session_id,))
        completed = [r["scale_name"] for r in c.fetchall()]
        db2.close()
        
        # 如果所有量表都完成了，通知云端更新 completion_status
        if set(expected).issubset(set(completed)):
            # 通过 SSH 隧道调用云端 API
            cloud_api = "http://127.0.0.1:8001"
            try:
                httpx.post(f"{cloud_api}/api/session/complete?session_id={data.session_id}")
            except:
                pass  # 忽略云端更新失败
    except:
        pass  # 忽略检查失败
    
    return {"success": True}

# ── Session detail ──

@app.get("/api/assessment/{session_id}")
async def get_session_detail(session_id: str):
    """获取 session 完整详情（含原始答案）。"""
    db = get_db()
    c = db.cursor()

    # Session info
    c.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,))
    session = c.fetchone()
    if not session:
        db.close()
        raise HTTPException(404, "Session 不存在")
    result = dict(session)

    # Patient info
    c.execute("SELECT * FROM patients WHERE patient_id=?", (result["patient_id"],))
    patient = c.fetchone()
    if patient:
        result["patient"] = dict(patient)

    # Base scales
    for table in ["screening", "phq9", "gad7", "cssrs", "tdm_decision"]:
        c.execute(f"SELECT * FROM {table} WHERE session_id=?", (session_id,))
        r = c.fetchone()
        if r:
            result[table] = dict(r)

    # Drug-specific scales
    c.execute("SELECT * FROM scale_results WHERE session_id=?", (session_id,))
    result["drug_scales"] = [dict(r) for r in c.fetchall()]
    
    # 添加基础量表状态到 drug_scales
    base_scales = [
        ("screening", "total_score"),
        ("phq9", "total_score"),
        ("gad7", "total_score"),
        ("cssrs", "severity_score")
    ]
    for scale, score_col in base_scales:
        c.execute(f"SELECT {score_col} FROM {scale} WHERE session_id=?", (session_id,))
        r = c.fetchone()
        if r:
            result["drug_scales"].append({
                "scale_name": scale,
                "status": "completed",
                "total_score": r[score_col]
            })
        else:
            result["drug_scales"].append({
                "scale_name": scale,
                "status": "pending",
                "total_score": None
            })

    db.close()
    return result

@app.delete("/api/assessment/{session_id}")
async def delete_session(session_id: str):
    """删除 session 及所有关联数据。"""
    db = get_db()
    for t in ["scale_results", "screening", "phq9", "gad7", "cssrs", "tdm_decision", "sessions"]:
        db.execute(f"DELETE FROM {t} WHERE session_id=?", (session_id,))
    db.commit()
    db.close()
    return {"success": True, "deleted": session_id}

# ── Decision engine ──

@app.post("/api/decision/compute")
async def compute_decision(data: DecisionCompute):
    """读取本地原始答案 → 计算 TDM 决策。"""
    db = get_db()
    c = db.cursor()

    c.execute("SELECT * FROM screening WHERE session_id=?", (data.session_id,))
    scr = c.fetchone()
    scr_total = scr["total_score"] if scr else 0

    c.execute("SELECT * FROM phq9 WHERE session_id=?", (data.session_id,))
    phq9_curr = c.fetchone()
    c.execute("""SELECT p.total_score FROM phq9 p JOIN sessions s ON p.session_id=s.session_id
                 WHERE s.patient_id=(SELECT patient_id FROM sessions WHERE session_id=?) AND p.baseline=1
                 ORDER BY s.created_at DESC LIMIT 1""", (data.session_id,))
    phq9_base = c.fetchone()
    if not phq9_base:
        # 没有历史基线，用临床阈值判断
        phq9_base = {"total_score": 10}  # PHQ9 >= 10 为中度抑郁阈值
    if phq9_curr and phq9_curr.get("baseline"):
        # 当前是基线评估，用临床阈值
        phq9_base = {"total_score": 10}

    c.execute("SELECT * FROM gad7 WHERE session_id=?", (data.session_id,))
    gad7_curr = c.fetchone()
    c.execute("""SELECT g.total_score FROM gad7 g JOIN sessions s ON g.session_id=s.session_id
                 WHERE s.patient_id=(SELECT patient_id FROM sessions WHERE session_id=?) AND g.baseline=1
                 ORDER BY s.created_at DESC LIMIT 1""", (data.session_id,))
    gad7_base = c.fetchone()
    if not gad7_base:
        gad7_base = {"total_score": 10}  # GAD7 >= 10 为中度焦虑阈值
    if gad7_curr and gad7_curr.get("baseline"):
        gad7_base = {"total_score": 10}

    c.execute("SELECT * FROM cssrs WHERE session_id=?", (data.session_id,))
    cssrs = c.fetchone()
    db.close()

    screening = evaluate_screening(scr_total)
    relapse = evaluate_relapse(
        phq9_curr["total_score"] if phq9_curr else 0, phq9_base["total_score"],
        gad7_curr["total_score"] if gad7_curr else 0, gad7_base["total_score"]
    )
    cssrs_result = evaluate_cssrs(cssrs["severity_score"] if cssrs else 0)
    decision = tdm_decision(screening, relapse, cssrs_result)
    cssrs_override = 1 if cssrs_result.severity >= 1 else 0

    # 写回决策结果
    db = get_db()
    db.execute("""INSERT OR REPLACE INTO tdm_decision (session_id, withdrawal_score, withdrawal_level,
                  phq9_change, gad7_change, phq9_level, gad7_level,
                  cssrs_severity, cssrs_level, overall_decision, overall_risk_level, cssrs_override)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
               (data.session_id, scr_total, screening.level,
                relapse.phq9_change, relapse.gad7_change, relapse.phq9_level, relapse.gad7_level,
                cssrs_result.severity, cssrs_result.level, decision.overall_decision, decision.overall_risk,
                cssrs_override))
    db.commit()
    db.close()

    return {
        "decision": decision.overall_decision,
        "risk": decision.overall_risk,
        "action": decision.action,
        "tracks": {"withdrawal": screening.level, "relapse": relapse.level, "cssrs": cssrs_result.level},
        "cssrs_override": bool(cssrs_override),
        "message_doctor": decision.message_doctor,
        "message_patient": decision.message_patient,
        "details": {
            "screening_total": scr_total,
            "phq9_change": relapse.phq9_change,
            "gad7_change": relapse.gad7_change,
            "cssrs_severity": cssrs_result.severity,
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
