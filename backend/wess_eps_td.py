"""抗精神病药撤药评估 — DESS + 运动症状标准量表
DESS (15 items) + SAS (7 items) + BARS (4 items) = 26 items total.
Based on: Rosenbaum 1998, Simpson & Angus 1970, Barnes 1989
"""
from .base import ScaleDef

# DESS items (Discontinuation-Emergent Signs & Symptoms)
_DESS_QUESTIONS = [
    "头晕/眩晕",
    "头痛",
    '"脑电击感"（像过电一样）',
    "恶心/呕吐/腹泻",
    "入睡困难/半夜醒来",
    "焦虑/紧张/坐不住",
    "情绪低落/哭泣",
    "注意力不集中",
    "手抖/出汗/心慌",
    "肌肉酸痛/流感样不适",
    "疲劳/乏力",
    "食欲改变",
    "易怒/情绪波动",
    "感觉异常（皮肤刺痛等）",
    "平衡感差/走路不稳",
]

# SAS items (Simpson-Angus Scale - 锥体外系症状)
_SAS_QUESTIONS = [
    "肌肉僵硬（胳膊弯曲时感觉阻力）",
    "震颤（静止时手抖）",
    "动作迟缓/笨拙",
    "流口水增多",
    "面部表情减少（面具脸）",
    "步态异常（拖步/小步）",
    "肌肉痉挛/抽搐",
]

# BARS items (Barnes Akathisia Scale - 静坐不能)
_BARS_QUESTIONS = [
    "坐立不安（无法安静坐着）",
    "双腿不自主活动（来回走动/抖腿）",
    "内心烦躁感（感觉必须动）",
    "因不安导致痛苦/困扰",
]

class APWithdrawalScale(ScaleDef):
    """抗精神病药撤药量表 — 完整版"""
    def __init__(self):
        all_q = _DESS_QUESTIONS + _SAS_QUESTIONS + _BARS_QUESTIONS
        super().__init__(
            name="ap_withdrawal",
            label="抗精神病药撤药评估",
            drug_category="antipsychotic",
            questions=all_q,
            option_labels=["0 无", "1 轻度", "2 中度", "3 重度"],
            max_per_item=3,
        )
    
    def _interpret(self, total: int) -> tuple[str, str]:
        max_score = len(self.questions) * 3
        pct = total / max_score if max_score > 0 else 0
        if pct <= 0.2:
            return "green", "无/轻度撤药症状"
        elif pct <= 0.5:
            return "yellow", "中度撤药症状"
        else:
            return "red", "重度撤药/运动症状"

# 短版（保持兼容）
class APWithdrawalShortScale(ScaleDef):
    """抗精神病药撤药量表 — 短版（患者自评）"""
    def __init__(self):
        short_q = _DESS_QUESTIONS[:8] + _SAS_QUESTIONS[:3] + _BARS_QUESTIONS[:2]
        super().__init__(
            name="ap_withdrawal_short",
            label="抗精神病药撤药评估（短版）",
            drug_category="antipsychotic",
            questions=short_q,
            option_labels=["0 无", "1 轻度", "2 中度", "3 重度"],
            max_per_item=3,
        )
    
    def _interpret(self, total: int) -> tuple[str, str]:
        max_score = len(self.questions) * 3
        pct = total / max_score if max_score > 0 else 0
        if pct <= 0.2:
            return "green", "无/轻度撤药症状"
        elif pct <= 0.5:
            return "yellow", "中度撤药症状"
        else:
            return "red", "重度撤药/运动症状"

AP_WITHDRAWAL_SCALE = APWithdrawalScale()
AP_WITHDRAWAL_SHORT_SCALE = APWithdrawalShortScale()
