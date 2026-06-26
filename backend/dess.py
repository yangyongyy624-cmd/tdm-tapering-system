"""DESS — Discontinuation-Emergent Signs & Symptoms (15-item short form)
15 high-frequency items, 0-3 per item. Total 0-45.
"""
from .base import ScaleDef

_QUESTIONS = [
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

_LABELS = ["0 无", "1 轻度", "2 中度", "3 重度"]

class DESSScale(ScaleDef):
    def __init__(self):
        super().__init__(
            name="dess",
            label="DESS",
            drug_category="antidepressant",
            questions=_QUESTIONS,
            option_labels=_LABELS,
            max_per_item=3,
        )

    def _interpret(self, total: int) -> tuple[str, str]:
        if total <= 8:
            return "green", "无/轻度撤药反应"
        elif total <= 20:
            return "yellow", "中度撤药反应"
        else:
            return "red", "重度撤药反应"

DESS_SCALE = DESSScale()
