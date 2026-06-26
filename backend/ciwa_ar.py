"""CIWA-Ar — Clinical Institute Withdrawal Assessment for Alcohol, revised
10 items, 0-4 per item (some 0-7). Simplified to 0-4 for all. Total 0-40.
"""
from .base import ScaleDef

_QUESTIONS = [
    "恶心/呕吐",
    "震颤（双手伸直观察）",
    "阵发性出汗",
    "焦虑/紧张",
    "激越/不安",
    "听觉/视觉/触觉干扰（幻觉样）",
    "头痛",
    "定向力/意识模糊",
    "意识水平（嗜睡/昏迷）",
    "坐立不安/全身紧张",
]

_LABELS = ["0 无", "1 轻度", "2 中度", "3 偏重", "4 重度"]

class CIWAARScale(ScaleDef):
    def __init__(self):
        super().__init__(
            name="ciwa_ar",
            label="CIWA-Ar",
            drug_category="benzodiazepine",
            questions=_QUESTIONS,
            option_labels=_LABELS,
            max_per_item=4,
        )

    def _interpret(self, total: int) -> tuple[str, str]:
        if total <= 8:
            return "green", "轻度戒断"
        elif total <= 15:
            return "yellow", "中度戒断"
        elif total <= 20:
            return "red", "重度戒断"
        else:
            return "red", "极重度戒断（需急诊）"

CIWA_AR_SCALE = CIWAARScale()
