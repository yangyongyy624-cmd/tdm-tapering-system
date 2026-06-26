"""TDM Scoring Engine — Triple-track + baseline severity + drug scales"""
from dataclasses import dataclass
from typing import Optional

@dataclass
class ScreeningResult:
    total: int
    level: str  # green, yellow, red
    recommendation: str

@dataclass
class RelapseResult:
    phq9_current: int
    phq9_baseline: int
    phq9_change: int
    phq9_level: str
    gad7_current: int
    gad7_baseline: int
    gad7_change: int
    gad7_level: str
    level: str

@dataclass
class CSSRSResult:
    severity: int
    level: str
    recommendation: str

@dataclass
class BaselineSeverity:
    phq9_level: str
    gad7_level: str
    level: str  # worst of the two

@dataclass
class DrugScalesResult:
    levels: dict  # {scale_name: level}
    level: str    # worst of all

@dataclass
class TDMDecision:
    overall_decision: str
    overall_risk: str
    track1: str
    track2: str
    track3: str
    message_doctor: str
    message_patient: str
    action: str  # continue, slow_down, pause, reinstate, emergency

# ── Individual track evaluators ──

def evaluate_screening(total: int) -> ScreeningResult:
    if total <= 6:
        return ScreeningResult(total, "green", "继续减药")
    elif total <= 12:
        return ScreeningResult(total, "yellow", "减速减药，填写完整版量表")
    elif total <= 18:
        return ScreeningResult(total, "red", "暂停减药，24h内完成完整版评估")
    else:
        return ScreeningResult(total, "red", "考虑恢复剂量，48h内完成完整版评估")

def evaluate_relapse(phq9_curr: int, phq9_base: int, gad7_curr: int, gad7_base: int,
                   symptom_onset: str = '', dawss_score: int = -1) -> RelapseResult:
    """评估复燃/撤药反应。

    结合三维度鉴别撤药 vs 复燃：
    1. 分数变化（PHQ-9/GAD-7 升高幅度）
    2. 症状出现时间（<7天→撤药，>14天→复燃）
    3. DAWSS 撤药特异性症状（≥4→撤药，<4→复燃）

    冲突处理：安全优先 → 倾向撤药（减速）
    理由：撤药反应可逆（恢复剂量可缓解），复燃风险不可逆

    ┌────────────┬──────────────┬──────────────┐
    │ 时间 vs DAWSS│ ≥4(撤药)     │ <4(复燃)     │
    ├────────────┼──────────────┼──────────────┤
    │ <7天(撤药) │ ✅ 一致:撤药  │ ⚠️ 冲突:撤药  │
    │ >14天(复燃)│ ⚠️ 冲突:撤药  │ ✅ 一致:复燃  │
    │ 不确定     │ 倾向撤药      │ 倾向复燃      │
    └────────────┴──────────────┴──────────────┘
    """
    phq9_change = phq9_curr - phq9_base
    gad7_change = gad7_curr - gad7_base

    # 基础分数判断
    if phq9_change >= 5:
        phq9_level = "red"
    elif phq9_change >= 3:
        phq9_level = "yellow"
    else:
        phq9_level = "green"

    if gad7_change >= 4:
        gad7_level = "red"
    elif gad7_change >= 2:
        gad7_level = "yellow"
    else:
        gad7_level = "green"

    overall = "red" if "red" in [phq9_level, gad7_level] else ("yellow" if "yellow" in [phq9_level, gad7_level] else "green")

    if overall == "green":
        return RelapseResult(
            phq9_current=phq9_curr, phq9_baseline=phq9_base, phq9_change=phq9_change, phq9_level=phq9_level,
            gad7_current=gad7_curr, gad7_baseline=gad7_base, gad7_change=gad7_change, gad7_level=gad7_level,
            level="green"
        )

    # ── 三维度鉴别 ──
    # 1. 时间维度判断
    time_says_withdrawal = symptom_onset in ['within_3days', 'within_week']
    time_says_relapse = symptom_onset == 'after_2weeks'

    # 2. DAWSS 维度判断（≥4 倾向撤药，<4 倾向复燃）
    dawss_says_withdrawal = dawss_score >= 4
    dawss_says_relapse = 0 <= dawss_score < 4
    has_dawss = dawss_score >= 0

    # 3. 综合判断
    if has_dawss:
        # 有 DAWSS 数据 → 三维度综合
        if time_says_withdrawal and dawss_says_withdrawal:
            # ✅ 一致：高度确信撤药
            overall = "yellow"
        elif time_says_relapse and dawss_says_relapse:
            # ✅ 一致：高度确信复燃
            overall = "red"
        elif time_says_withdrawal and dawss_says_relapse:
            # ⚠️ 冲突（快起病 + 少撤药症状）→ 安全优先：倾向撤药
            overall = "yellow"
        elif time_says_relapse and dawss_says_withdrawal:
            # ⚠️ 冲突（慢起病 + 多撤药症状）→ 安全优先：倾向撤药
            overall = "yellow"
        else:
            # DAWSS 有数据但时间不确定
            if dawss_says_withdrawal:
                overall = "yellow"
            else:
                overall = "red"
    else:
        # 无 DAWSS 数据 → 仅用时间维度
        if time_says_withdrawal:
            overall = "yellow"
        # 其他情况维持原判断

    return RelapseResult(
        phq9_current=phq9_curr, phq9_baseline=phq9_base, phq9_change=phq9_change, phq9_level=phq9_level,
        gad7_current=gad7_curr, gad7_baseline=gad7_base, gad7_change=gad7_change, gad7_level=gad7_level,
        level=overall
    )

def evaluate_cssrs(severity: int) -> CSSRSResult:
    if severity == 0:
        return CSSRSResult(severity, "green", "无自杀风险")
    elif severity == 1:
        return CSSRSResult(severity, "yellow", "轻度风险，密切观察")
    elif severity == 2:
        return CSSRSResult(severity, "red", "恢复剂量，临床评估")
    else:
        return CSSRSResult(severity, "red", "立即急诊")

def evaluate_baseline_severity(phq9_score: int, gad7_score: int) -> BaselineSeverity:
    """首评严重度检查（不看变化量，只看绝对值）。
    PHQ-9: <15 green, 15-19 yellow, ≥20 red
    GAD-7: <10 green, 10-14 yellow, ≥15 red
    """
    if phq9_score >= 20:
        phq9_level = "red"
    elif phq9_score >= 15:
        phq9_level = "yellow"
    else:
        phq9_level = "green"

    if gad7_score >= 15:
        gad7_level = "red"
    elif gad7_score >= 10:
        gad7_level = "yellow"
    else:
        gad7_level = "green"

    overall = "red" if "red" in [phq9_level, gad7_level] else ("yellow" if "yellow" in [phq9_level, gad7_level] else "green")
    return BaselineSeverity(phq9_level=phq9_level, gad7_level=gad7_level, level=overall)

def evaluate_drug_scales(scales_data: list[dict]) -> DrugScalesResult:
    """药物量表第4轨。每个 dict 包含 {scale_name, total_score}。
    使用各量表自身的 _interpret() 判断 level。
    """
    from scales import ALL_SCALES

    levels = {}
    for s in scales_data:
        name = s["scale_name"]
        total = s["total_score"]
        scale = ALL_SCALES.get(name)
        if scale:
            level, _ = scale._interpret(total)
            levels[name] = level
        else:
            levels[name] = "green"  # unknown scale, default safe

    if "red" in levels.values():
        overall = "red"
    elif "yellow" in levels.values():
        overall = "yellow"
    else:
        overall = "green"

    return DrugScalesResult(levels=levels, level=overall)

# ── Combined decision ──

def _worst_level(*levels: str) -> str:
    """返回最严重的 level: red > yellow > green"""
    if "red" in levels:
        return "red"
    if "yellow" in levels:
        return "yellow"
    return "green"

def tdm_decision(
    screening: ScreeningResult,
    relapse: RelapseResult,
    cssrs: CSSRSResult,
    is_baseline: bool = False,
    drug_scales_data: Optional[list[dict]] = None,
    has_screening: bool = True,
) -> TDMDecision:
    """三轨 + 首评严重度 + 药物量表 → 最终决策。

    C-SSRS 一票否决：severity ≥ 1 → 立即停止减药（安全红线）。
    首评时 PHQ-9/GAD-7 看绝对值，不看变化量。
    药物量表（DESS/CIWA-Ar 等）作为第4轨参与决策。
    筛查缺失 → withdrawal 默认 yellow（不安全）。
    """
    # C-SSRS 一票否决
    if cssrs.severity >= 1:
        return TDMDecision(
            overall_decision="停止减药", overall_risk="自杀风险",
            track1=screening.level, track2=relapse.level, track3=cssrs.level,
            message_doctor=f"C-SSRS 阳性（严重度={cssrs.severity}），立即停止减药并临床评估。此为安全红线，覆盖其他所有评分。",
            message_patient="请立即联系医生，暂停减药。您的安全是最重要的。",
            action="pause"
        )

    # 第4轨：药物量表
    drug_level = "green"
    drug_reasons = []
    if drug_scales_data:
        drug_result = evaluate_drug_scales(drug_scales_data)
        drug_level = drug_result.level
        from scales import ALL_SCALES
        for name, level in drug_result.levels.items():
            scale = ALL_SCALES.get(name)
            label = scale.label if scale else name
            if level == "red":
                drug_reasons.append(f"{label} 重度")
            elif level == "yellow":
                drug_reasons.append(f"{label} 中度")

    # 首评：用绝对值严重度替代变化量
    relapse_level = relapse.level
    if is_baseline:
        baseline = evaluate_baseline_severity(relapse.phq9_current, relapse.gad7_current)
        relapse_level = baseline.level

    # 汇总所有轨道
    withdrawal_level = screening.level
    # 筛查缺失（无数据）→ 不安全，要求补测
    if not has_screening:
        withdrawal_level = "yellow"

    any_red = withdrawal_level == "red" or relapse_level == "red" or drug_level == "red"
    any_yellow = withdrawal_level == "yellow" or relapse_level == "yellow" or drug_level == "yellow"

    if any_red:
        reasons = []
        if withdrawal_level == "red":
            reasons.append(f"撤药筛查分={screening.total}（重度）")
        if relapse_level == "red":
            if is_baseline:
                if relapse.phq9_current >= 20:
                    reasons.append(f"PHQ-9={relapse.phq9_current}（首次评估，重度抑郁）")
                if relapse.gad7_current >= 15:
                    reasons.append(f"GAD-7={relapse.gad7_current}（首次评估，重度焦虑）")
            else:
                if relapse.phq9_level == "red":
                    reasons.append(f"PHQ-9 较基线增加 {relapse.phq9_change} 分")
                if relapse.gad7_level == "red":
                    reasons.append(f"GAD-7 较基线增加 {relapse.gad7_change} 分")
        if drug_level == "red":
            reasons.extend(drug_reasons)

        return TDMDecision(
            overall_decision="暂停减药", overall_risk="中高风险",
            track1=withdrawal_level, track2=relapse_level, track3=cssrs.level,
            message_doctor=f"{' / '.join(reasons)}。建议暂停减药，维持当前剂量，1周后复评。结合患者主观体感、症状持续时间综合判断。",
            message_patient="您的评估结果显示需要暂停减药。请维持当前剂量，医生会在一周内联系您。",
            action="pause"
        )

    if any_yellow:
        reasons = []
        if withdrawal_level == "yellow":
            if not has_screening:
                reasons.append("撤药筛查未填写（无法评估撤药反应）")
            else:
                reasons.append(f"撤药筛查分={screening.total}")
        if relapse_level == "yellow":
            if is_baseline:
                if relapse.phq9_current >= 15:
                    reasons.append(f"PHQ-9={relapse.phq9_current}（首次评估，中重度抑郁）")
                if relapse.gad7_current >= 10:
                    reasons.append(f"GAD-7={relapse.gad7_current}（首次评估，中度焦虑）")
            else:
                if relapse.phq9_level == "yellow":
                    reasons.append(f"PHQ-9 较基线增加 {relapse.phq9_change} 分")
                if relapse.gad7_level == "yellow":
                    reasons.append(f"GAD-7 较基线增加 {relapse.gad7_change} 分")
        if drug_level == "yellow":
            reasons.extend(drug_reasons)

        return TDMDecision(
            overall_decision="减速减药", overall_risk="轻度风险",
            track1=withdrawal_level, track2=relapse_level, track3=cssrs.level,
            message_doctor=f"{' / '.join(reasons)}。建议减速减药（减量幅度减半，间隔延长一倍），2周后复评。",
            message_patient="您的评估结果总体良好，但有些指标需要关注。我们会减慢减药速度，请在两周后再次评估。",
            action="slow_down"
        )

    return TDMDecision(
        overall_decision="继续减药", overall_risk="低风险",
        track1=withdrawal_level, track2=relapse_level, track3=cssrs.level,
        message_doctor="三轨全绿。按原计划继续减药。",
        message_patient="您的评估结果很好！请按原计划继续减药。记得按时填写下次评估问卷。",
        action="continue"
    )
