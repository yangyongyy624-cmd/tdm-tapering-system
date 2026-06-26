"""数据分流路由器 — 云端调用版

敏感数据 → 通过 SSH 隧道转发到本地 API (localhost:8002)
非敏感数据 → 云端本地处理

云端部署时，SSH 反向隧道使 cloud:8002 → local:8002
"""
import httpx
import json
import os
from typing import Any, Dict, List, Optional

LOCAL_API = "http://127.0.0.1:8002"

class DataRouter:
    def __init__(self):
        self.cloud_client = httpx.Client(base_url="http://127.0.0.1:8001", timeout=10.0)
        self.local_client = httpx.Client(base_url=LOCAL_API, timeout=10.0)

    # ── 敏感数据操作（转发到本地）──

    def submit_assessment(self, session_id: str, scale_name: str, answers: list[int], is_baseline: bool = False) -> dict:
        """提交量表原始答案 → 本地 API"""
        try:
            resp = self.local_client.post("/api/assessment/submit", json={
                "session_id": session_id,
                "scale_name": scale_name,
                "answers": answers,
                "is_baseline": is_baseline,
            })
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": f"本地 API 不可达: {e}"}

    def get_session_detail(self, session_id: str) -> dict:
        """获取 session 完整详情（含原始答案）→ 本地 API"""
        try:
            resp = self.local_client.get(f"/api/assessment/{session_id}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": f"本地 API 不可达: {e}"}

    def compute_decision(self, session_id: str) -> dict:
        """计算 TDM 决策 → 本地 API"""
        try:
            resp = self.local_client.post("/api/decision/compute", json={"session_id": session_id})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": f"本地 API 不可达: {e}"}

    def delete_session(self, session_id: str) -> dict:
        """删除 session → 本地 API"""
        try:
            resp = self.local_client.delete(f"/api/assessment/{session_id}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": f"本地 API 不可达: {e}"}

    def get_patient_history(self, patient_id: str) -> dict:
        """获取患者完整历史 → 本地 API"""
        try:
            resp = self.local_client.get(f"/api/patient/history/{patient_id}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": f"本地 API 不可达: {e}"}

    def register_patient(self, patient_id: str, phone: str = None, name: str = None,
                         diagnosis: str = None, current_meds: str = None, tapering_meds: str = None) -> dict:
        """注册患者 → 本地 API"""
        try:
            resp = self.local_client.post("/api/patient/register", json={
                "patient_id": patient_id,
                "phone": phone,
                "name": name,
                "diagnosis": diagnosis,
                "current_meds": current_meds,
                "tapering_meds": tapering_meds,
            })
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": f"本地 API 不可达: {e}"}

    def sync_session(self, session_id: str, patient_id: str, access_code: str,
                     drug_categories: str = None, doctor_pin: str = None) -> dict:
        """同步 session 到本地 → 本地 API"""
        try:
            resp = self.local_client.post("/api/session/sync", json={
                "session_id": session_id,
                "patient_id": patient_id,
                "access_code": access_code,
                "drug_categories": drug_categories,
                "doctor_pin": doctor_pin,
            })
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": f"本地 API 不可达: {e}"}

    # ── 脱敏工具 ──

    @staticmethod
    def desensitize_phone(phone: Optional[str]) -> Optional[str]:
        """手机号脱敏: 13712345833 → 137****5833"""
        if not phone or len(phone) < 7:
            return phone
        return phone[:3] + "****" + phone[-4:]

    @staticmethod
    def desensitize_name(name: Optional[str]) -> Optional[str]:
        """姓名脱敏: 张三 → 张*"""
        if not name:
            return name
        return name[0] + "*" * (len(name) - 1)

# 全局实例
router = DataRouter()
