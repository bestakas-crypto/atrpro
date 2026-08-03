# tests/test_company_metrics_engine.py
# company_metrics_engine.py -- 고정 픽스처로 정확한 기대값 검증
# (atr_engine.py 테스트 스타일 그대로: 손계산 가능한 값으로 직접 검증).
from atrsite.services import company_metrics_engine as engine


def test_compute_derived_metrics_basic_ratios():
    metrics = {
        "revenue": 1000.0, "gross_profit": 400.0, "operating_income": 200.0,
        "net_income": 150.0, "operating_cash_flow": 180.0, "capex": 50.0,
        "cash": 300.0, "total_debt": 500.0, "interest_expense": 20.0,
    }
    d = engine.compute_derived_metrics(metrics)
    assert d["gross_margin"] == 0.4
    assert d["operating_margin"] == 0.2
    assert d["net_margin"] == 0.15
    assert d["fcf"] == 130.0
    assert abs(d["fcf_margin"] - 0.13) < 1e-9
    assert d["net_debt"] == 200.0
    assert d["interest_coverage"] == 10.0
    assert abs(d["cash_conversion"] - 1.2) < 1e-9


def test_compute_derived_metrics_missing_inputs_stay_none():
    d = engine.compute_derived_metrics({"revenue": 1000.0})
    assert d["gross_margin"] is None
    assert d["fcf"] is None
    assert d["net_debt"] is None
    assert d["interest_coverage"] is None


def test_compute_verdict_bands():
    assert engine.compute_verdict(110, 100) == engine.IMPROVING
    assert engine.compute_verdict(95, 100) == engine.DETERIORATING
    assert engine.compute_verdict(101, 100) == engine.STABLE  # 3% 이내
    assert engine.compute_verdict(None, 100) == engine.INSUFFICIENT_DATA
    assert engine.compute_verdict(100, 0) == engine.INSUFFICIENT_DATA
    # net_debt처럼 낮을수록 좋은 지표는 방향이 반대
    assert engine.compute_verdict(90, 100, higher_is_better=False) == engine.IMPROVING
    assert engine.compute_verdict(110, 100, higher_is_better=False) == engine.DETERIORATING


def _period(period_end, revenue, net_income, ocf=None, receivables=None, inventory=None, shares=None):
    return {
        "period_type": "QUARTER", "period_end": period_end,
        "metrics": {
            "revenue": revenue, "net_income": net_income, "operating_cash_flow": ocf,
            "receivables": receivables, "inventory": inventory, "shares_outstanding": shares,
        },
    }


def test_annotate_periods_computes_qoq_and_yoy():
    periods = [
        _period("2025-02-01", 1000, 100),
        _period("2025-05-01", 1100, 120),
        _period("2025-08-01", 1050, 90),
        _period("2025-11-01", 1200, 150),
        _period("2026-02-01", 1300, 200),  # YoY 비교 대상 = 인덱스 0(4개 전)
    ]
    annotated = engine.annotate_periods(periods)
    latest = annotated[-1]
    # QoQ: 1300 vs 1200 -> +8.33%(STABLE 밴드 3% 밖이므로 IMPROVING)
    assert latest["comparisons"]["revenue"]["qoq_verdict"] == engine.IMPROVING
    # YoY: 1300 vs 1000(인덱스 0) -> +30%
    assert latest["comparisons"]["revenue"]["yoy_verdict"] == engine.IMPROVING
    assert round(latest["comparisons"]["revenue"]["yoy_change_pct"], 1) == 30.0
    # 첫 기간은 비교 대상이 없어 INSUFFICIENT_DATA
    assert annotated[0]["comparisons"]["revenue"]["qoq_verdict"] == engine.INSUFFICIENT_DATA


def test_check_warning_rules_receivables_and_inventory_outpace_revenue():
    periods = [
        _period("2025-11-01", 1000, 100, ocf=100, receivables=200, inventory=200),
        _period("2026-02-01", 1050, 110, ocf=110, receivables=260, inventory=250),  # 매출+5%, 매출채권+30%, 재고+25%
    ]
    annotated = engine.annotate_periods(periods)
    warnings = engine.check_warning_rules(annotated)
    rules = {w["rule"] for w in warnings}
    assert "receivables_growth_exceeds_revenue" in rules
    assert "inventory_growth_exceeds_revenue" in rules


def test_check_warning_rules_net_income_up_cash_flow_down():
    periods = [
        _period("2025-11-01", 1000, 100, ocf=150),
        _period("2026-02-01", 1050, 130, ocf=90),  # 순이익 +30%, 영업현금흐름 -40%
    ]
    annotated = engine.annotate_periods(periods)
    warnings = engine.check_warning_rules(annotated)
    assert any(w["rule"] == "net_income_up_cash_flow_down" for w in warnings)


def test_check_warning_rules_fcf_negative_streak():
    periods = [
        {"period_type": "QUARTER", "period_end": "2025-08-01",
         "metrics": {"revenue": 1000, "operating_cash_flow": 50, "capex": 100}},  # fcf -50
        {"period_type": "QUARTER", "period_end": "2025-11-01",
         "metrics": {"revenue": 1000, "operating_cash_flow": 40, "capex": 100}},  # fcf -60
    ]
    annotated = engine.annotate_periods(periods)
    warnings = engine.check_warning_rules(annotated)
    assert any(w["rule"] == "fcf_negative_streak" for w in warnings)


def test_check_warning_rules_interest_coverage_low():
    periods = [
        {"period_type": "QUARTER", "period_end": "2025-11-01",
         "metrics": {"revenue": 1000, "operating_income": 100, "interest_expense": 10}},
        {"period_type": "QUARTER", "period_end": "2026-02-01",
         "metrics": {"revenue": 1000, "operating_income": 20, "interest_expense": 10}},  # 이자보상배율 2.0
    ]
    annotated = engine.annotate_periods(periods)
    warnings = engine.check_warning_rules(annotated)
    assert any(w["rule"] == "interest_coverage_low" for w in warnings)


def test_check_warning_rules_share_dilution_outpacing_income():
    periods = [
        _period("2025-11-01", 1000, 100, shares=1000),
        _period("2026-02-01", 1000, 105, shares=1200),  # 순이익 +5%, 주식수 +20%
    ]
    annotated = engine.annotate_periods(periods)
    warnings = engine.check_warning_rules(annotated)
    assert any(w["rule"] == "share_dilution_outpacing_income" for w in warnings)


def test_check_warning_rules_clean_case_no_warnings():
    periods = [
        _period("2025-11-01", 1000, 100, ocf=110, receivables=200, inventory=200, shares=1000),
        _period("2026-02-01", 1050, 110, ocf=120, receivables=205, inventory=205, shares=1005),
    ]
    annotated = engine.annotate_periods(periods)
    warnings = engine.check_warning_rules(annotated)
    assert warnings == []


def test_check_warning_rules_needs_at_least_two_periods():
    periods = [_period("2025-11-01", 1000, 100)]
    annotated = engine.annotate_periods(periods)
    assert engine.check_warning_rules(annotated) == []


def test_detect_industry_template():
    assert engine.detect_industry_template("MU") == "semiconductor"
    assert engine.detect_industry_template("mu") == "semiconductor"  # 대소문자 무관
    assert engine.detect_industry_template("KO") is None


def test_annotate_periods_semiconductor_inventory_days():
    periods = [{
        "period_type": "QUARTER", "period_end": "2026-02-01",
        "metrics": {"revenue": 9100.0, "gross_profit": 3500.0, "inventory": 8000.0},
    }]
    annotated = engine.annotate_periods(periods, industry_template="semiconductor")
    cogs = 9100.0 - 3500.0
    expected_days = 8000.0 / (cogs / 91)
    assert abs(annotated[0]["metrics"]["inventory_days"] - expected_days) < 1e-6
