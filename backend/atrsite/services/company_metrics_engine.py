# backend/atrsite/services/company_metrics_engine.py
# 재무 정량 계산 엔진 -- 스펙 6절. LLM이 비율을 계산하지 않게 하는 핵심
# 원칙(analysis_service.py의 macro 브리핑과 동일한 설계)을 종목탐구에도
# 그대로 적용: 여기서 Python이 마진/FCF/경고를 전부 확정하고, LLM은
# 결과를 설명만 한다.
from __future__ import annotations

from typing import Optional

IMPROVING = "IMPROVING"
STABLE = "STABLE"
DETERIORATING = "DETERIORATING"
CAUTION = "CAUTION"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# 반도체 매출원가/재고 지표 계산을 켤 업종(스펙 7절 1순위) -- SIC 코드 기반
# 정식 분류가 아니라 알려진 티커 목록의 임시 휴리스틱이다. SEC companyfacts
# 만으로는 정교한 업종 분류가 불가능해서(회사 자체 SIC 코드는 submissions
# API의 별도 필드), 이번 MVP 범위에서는 이렇게 좁혀둔다 -- 잘못 분류돼도
# 부작용은 "반도체 전용 지표가 안 나옴" 정도라 안전한 단순화.
SEMICONDUCTOR_TICKERS = {
    "MU", "NVDA", "INTC", "AMD", "TXN", "AVGO", "QCOM", "TSM", "ASML",
    "KLAC", "LRCX", "AMAT", "MRVL", "ON", "MCHP", "SWKS", "QRVO",
}

# 스펙 6.4 대표 경고 규칙 -- 임계값은 상수로 숨기지 말고 여기 모아서 버전
# 관리 가능하게 둔다(스펙 요구사항 그대로).
WARNING_THRESHOLDS = {
    "receivables_vs_revenue_growth_gap_pct": 10.0,
    "inventory_vs_revenue_growth_gap_pct": 10.0,
    "fcf_negative_streak_periods": 2,
    "interest_coverage_floor": 3.0,
    "stable_band_pct": 3.0,
}


def _safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100


def compute_verdict(
    current: Optional[float], previous: Optional[float], *,
    higher_is_better: bool = True, stable_band_pct: float = WARNING_THRESHOLDS["stable_band_pct"],
) -> str:
    """전기 대비 변화율로 IMPROVING/STABLE/DETERIORATING 판정(스펙 6.3).
    비교할 값이 없으면(자료 부족) 임의로 추정하지 않고 INSUFFICIENT_DATA."""
    change_pct = _pct_change(current, previous)
    if change_pct is None:
        return INSUFFICIENT_DATA
    if abs(change_pct) <= stable_band_pct:
        return STABLE
    improving = (change_pct > 0) if higher_is_better else (change_pct < 0)
    return IMPROVING if improving else DETERIORATING


# metric_key -> "값이 클수록 좋은 지표인가" (판정 방향 결정용)
_HIGHER_IS_BETTER = {
    "revenue": True, "gross_profit": True, "gross_margin": True,
    "operating_income": True, "operating_margin": True,
    "net_income": True, "net_margin": True,
    "operating_cash_flow": True, "fcf": True, "fcf_margin": True,
    "cash": True, "interest_coverage": True, "cash_conversion": True,
    "net_debt": False, "total_debt": False,
    "receivables": None, "inventory": None,  # 단독으로는 좋고 나쁨을 판단 못 함(경고규칙에서 별도 처리)
    "inventory_days": False,
}


def compute_derived_metrics(metrics: dict, *, industry_template: Optional[str] = None) -> dict:
    """원본 지표(SEC/DART에서 그대로 가져온 값)에 마진/FCF/순차입금 등
    파생지표를 계산해서 추가한 새 dict를 반환한다."""
    m = dict(metrics)
    revenue = m.get("revenue")

    m["gross_margin"] = _safe_div(m.get("gross_profit"), revenue)
    m["operating_margin"] = _safe_div(m.get("operating_income"), revenue)
    m["net_margin"] = _safe_div(m.get("net_income"), revenue)

    ocf, capex = m.get("operating_cash_flow"), m.get("capex")
    m["fcf"] = (ocf - capex) if (ocf is not None and capex is not None) else None
    m["fcf_margin"] = _safe_div(m["fcf"], revenue)

    total_debt, cash = m.get("total_debt"), m.get("cash")
    m["net_debt"] = (total_debt - cash) if (total_debt is not None and cash is not None) else None

    m["interest_coverage"] = _safe_div(m.get("operating_income"), m.get("interest_expense"))
    m["cash_conversion"] = _safe_div(m.get("operating_cash_flow"), m.get("net_income"))

    if industry_template == "semiconductor":
        cogs = (revenue - m["gross_profit"]) if (revenue is not None and m.get("gross_profit") is not None) else None
        # 분기 재고일수 -- 분기 매출원가를 91일 기준 1일 평균으로 환산.
        # 연간 기간이면 365일 기준(period_type을 여기선 모르므로 호출자가
        # ANNUAL이면 이 값을 다시 계산해서 덮어써야 함 -- annotate_periods에서 처리).
        m["inventory_days"] = _safe_div(m.get("inventory"), _safe_div(cogs, 91))

    return m


def detect_industry_template(ticker: str) -> Optional[str]:
    if ticker.upper() in SEMICONDUCTOR_TICKERS:
        return "semiconductor"
    return None


def annotate_periods(periods: list[dict], *, industry_template: Optional[str] = None) -> list[dict]:
    """과거->최신 순으로 정렬된 기간 리스트를 받아 각 기간에 파생지표를
    채우고, QoQ(직전 기간 대비)/YoY(4개 기간 전 대비, 분기 기준) 판정을
    추가한다. 입력/출력 모두 과거->최신 순서를 유지한다."""
    annotated = []
    period_type = periods[0]["period_type"] if periods else "QUARTER"
    yoy_lag = 4 if period_type == "QUARTER" else 1

    derived_list = [
        compute_derived_metrics(p["metrics"], industry_template=industry_template) for p in periods
    ]
    if industry_template == "semiconductor" and period_type == "ANNUAL":
        for d in derived_list:
            revenue, gp, inv = d.get("revenue"), d.get("gross_profit"), d.get("inventory")
            cogs = (revenue - gp) if (revenue is not None and gp is not None) else None
            d["inventory_days"] = _safe_div(inv, _safe_div(cogs, 365))

    for i, period in enumerate(periods):
        metrics = derived_list[i]
        prev_q = derived_list[i - 1] if i - 1 >= 0 else {}
        prev_y = derived_list[i - yoy_lag] if i - yoy_lag >= 0 else {}

        comparisons = {}
        for key, value in metrics.items():
            higher_is_better = _HIGHER_IS_BETTER.get(key)
            if higher_is_better is None:
                continue
            comparisons[key] = {
                "value": value,
                "qoq_change_pct": _pct_change(value, prev_q.get(key)),
                "qoq_verdict": compute_verdict(value, prev_q.get(key), higher_is_better=higher_is_better),
                "yoy_change_pct": _pct_change(value, prev_y.get(key)),
                "yoy_verdict": compute_verdict(value, prev_y.get(key), higher_is_better=higher_is_better),
            }

        annotated.append({
            **period,
            "metrics": metrics,
            "comparisons": comparisons,
        })
    return annotated


def check_warning_rules(annotated_periods: list[dict]) -> list[dict]:
    """스펙 6.4 경고 규칙 -- 계산 가능한 것만 구현. 단기차입금 급증(장단기
    구분 데이터 없음)/CAPEX급증+가동률(가동률 데이터 없음)/일회성이익 비중
    (비경상 항목 구분 데이터 없음)은 이번 MVP 데이터로는 계산 불가능해서
    구현하지 않음(임의로 추정하지 않는다는 원칙)."""
    if len(annotated_periods) < 2:
        return []

    latest = annotated_periods[-1]
    prev = annotated_periods[-2]
    warnings: list[dict] = []
    th = WARNING_THRESHOLDS

    rev_growth = _pct_change(latest["metrics"].get("revenue"), prev["metrics"].get("revenue"))
    recv_growth = _pct_change(latest["metrics"].get("receivables"), prev["metrics"].get("receivables"))
    inv_growth = _pct_change(latest["metrics"].get("inventory"), prev["metrics"].get("inventory"))

    if rev_growth is not None and recv_growth is not None:
        if recv_growth - rev_growth > th["receivables_vs_revenue_growth_gap_pct"]:
            warnings.append({
                "rule": "receivables_growth_exceeds_revenue",
                "message": f"매출채권 증가율({recv_growth:.1f}%)이 매출 증가율({rev_growth:.1f}%)보다 "
                           f"{recv_growth - rev_growth:.1f}%p 높습니다 -- 매출 인식/회수 품질 확인 필요.",
            })
    if rev_growth is not None and inv_growth is not None:
        if inv_growth - rev_growth > th["inventory_vs_revenue_growth_gap_pct"]:
            warnings.append({
                "rule": "inventory_growth_exceeds_revenue",
                "message": f"재고자산 증가율({inv_growth:.1f}%)이 매출 증가율({rev_growth:.1f}%)보다 "
                           f"{inv_growth - rev_growth:.1f}%p 높습니다 -- 수요 둔화/재고 적체 가능성.",
            })

    ni_verdict = latest["comparisons"].get("net_income", {}).get("qoq_verdict")
    ocf_verdict = latest["comparisons"].get("operating_cash_flow", {}).get("qoq_verdict")
    if ni_verdict == IMPROVING and ocf_verdict == DETERIORATING:
        warnings.append({
            "rule": "net_income_up_cash_flow_down",
            "message": "순이익은 증가했지만 영업현금흐름은 감소했습니다 -- 이익의 질 확인 필요"
                       "(매출채권/재고 증가, 일회성 이익 등 가능성).",
        })

    fcf_values = [p["metrics"].get("fcf") for p in annotated_periods[-th["fcf_negative_streak_periods"]:]]
    if len(fcf_values) == th["fcf_negative_streak_periods"] and all(v is not None and v < 0 for v in fcf_values):
        warnings.append({
            "rule": "fcf_negative_streak",
            "message": f"최근 {th['fcf_negative_streak_periods']}개 기간 연속 잉여현금흐름(FCF)이 적자입니다.",
        })

    ic = latest["metrics"].get("interest_coverage")
    if ic is not None and ic < th["interest_coverage_floor"]:
        warnings.append({
            "rule": "interest_coverage_low",
            "message": f"이자보상배율이 {ic:.1f}배로 낮습니다(기준 {th['interest_coverage_floor']}배 미만) "
                       "-- 이자 부담 대비 영업이익 여력 확인 필요.",
        })

    shares_growth = _pct_change(latest["metrics"].get("shares_outstanding"), prev["metrics"].get("shares_outstanding"))
    ni_growth = _pct_change(latest["metrics"].get("net_income"), prev["metrics"].get("net_income"))
    if shares_growth is not None and ni_growth is not None and shares_growth > ni_growth:
        warnings.append({
            "rule": "share_dilution_outpacing_income",
            "message": f"발행주식수 증가율({shares_growth:.1f}%)이 순이익 증가율({ni_growth:.1f}%)보다 높습니다 "
                       "-- 희석 효과로 주당 지표가 실제 이익 개선보다 덜 좋아 보일 수 있습니다.",
        })

    return warnings
