#!/usr/bin/env python3
"""Read-only Streamlit dashboard for a locally generated published bundle.

This file deliberately contains no training or data-collection code.  Use
``python streamlit_app.py --validate-data published`` for a dependency-light
bundle check in CI; use ``streamlit run streamlit_app.py`` for the dashboard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


SUPPORTED_SCHEMA_MAJOR = 1


class BundleError(RuntimeError):
    """A friendly error for a missing, incompatible or corrupt bundle."""


@dataclass
class Bundle:
    manifest: dict[str, Any]
    stock_forecasts: pd.DataFrame
    stock_paths: pd.DataFrame
    portfolio_forecast: pd.DataFrame
    portfolio_paths: pd.DataFrame
    decisions: pd.DataFrame
    components: pd.DataFrame
    stock_history: pd.DataFrame
    portfolio_history: pd.DataFrame
    model_metrics: dict[str, Any]
    data_quality: dict[str, Any]
    feature_status: dict[str, Any]
    run_summary: dict[str, Any]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise BundleError(f"필수 파일이 없습니다: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"{path.name} JSON 읽기 실패: {exc}") from exc


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise BundleError(f"필수 파일이 없습니다: {path.name}")
    try:
        return pd.read_csv(path)
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise BundleError(f"{path.name} CSV 읽기 실패: {exc}") from exc


def validate_columns(frame: pd.DataFrame, required: set[str], filename: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise BundleError(f"{filename} schema 열 누락: {', '.join(missing)}")
    if frame.empty:
        raise BundleError(f"{filename}에 표시할 행이 없습니다.")


def load_bundle_uncached(directory: Path, verify_hashes: bool = True) -> Bundle:
    """Load and validate all files without invoking Streamlit."""

    directory = directory.resolve()
    manifest = read_json(directory / "manifest.json")
    version = str(manifest.get("schema_version", "0.0.0"))
    try:
        major = int(version.split(".")[0])
    except (ValueError, IndexError) as exc:
        raise BundleError(f"manifest schema version 형식 오류: {version!r}") from exc
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise BundleError(
            f"지원하지 않는 schema version {version}. 이 앱은 major {SUPPORTED_SCHEMA_MAJOR}만 지원합니다."
        )
    if verify_hashes:
        for entry in manifest.get("files", []):
            path = directory / Path(str(entry.get("path", ""))).name
            if not path.exists():
                raise BundleError(f"manifest에 선언된 파일 없음: {path.name}")
            expected_size = entry.get("bytes")
            if expected_size is not None and path.stat().st_size != int(expected_size):
                raise BundleError(f"파일 크기 불일치: {path.name}")
            expected = entry.get("sha256")
            if expected and sha256_file(path) != expected:
                raise BundleError(f"SHA-256 불일치: {path.name}")
    stock_forecasts = read_csv(directory / "stock_forecasts.csv")
    stock_paths = read_csv(directory / "stock_paths.csv.gz")
    portfolio_forecast = read_csv(directory / "portfolio_forecast.csv")
    portfolio_paths = read_csv(directory / "portfolio_paths.csv.gz")
    decisions = read_csv(directory / "decision_levels.csv")
    components = read_csv(directory / "portfolio_components.csv")
    stock_history = read_csv(directory / "stock_history.csv.gz")
    portfolio_history = read_csv(directory / "portfolio_history.csv.gz")
    validate_columns(stock_forecasts, {"ticker", "period_years", "horizon_days", "current_price", "price_q50", "confidence_score"}, "stock_forecasts.csv")
    validate_columns(stock_paths, {"ticker", "period_years", "horizon_days", "date", "p10", "p50", "p90"}, "stock_paths.csv.gz")
    validate_columns(portfolio_forecast, {"account_scope", "period_years", "horizon_days", "current_total", "future_p50", "loss_probability"}, "portfolio_forecast.csv")
    validate_columns(portfolio_paths, {"account_scope", "period_years", "horizon_days", "date", "p10", "p50", "p90"}, "portfolio_paths.csv.gz")
    validate_columns(decisions, {"ticker", "period_years", "horizon_days", "target1", "stop_loss", "add_consideration"}, "decision_levels.csv")
    validate_columns(components, {"account_scope", "ticker", "asset_type", "current_value", "forecast_status", "valuation_method"}, "portfolio_components.csv")
    if set(portfolio_forecast["account_scope"].astype(str).unique()) != {"전체"}:
        raise BundleError("이 앱은 계좌별 분리 없이 account_scope='전체'인 통합 결과만 지원합니다.")
    for frame in (stock_paths, portfolio_paths, stock_history, portfolio_history):
        if "date" in frame:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            if frame["date"].isna().any():
                raise BundleError("그래프 파일에 잘못된 날짜가 있습니다.")
    return Bundle(
        manifest,
        stock_forecasts,
        stock_paths,
        portfolio_forecast,
        portfolio_paths,
        decisions,
        components,
        stock_history,
        portfolio_history,
        read_json(directory / "model_metrics.json"),
        read_json(directory / "data_quality.json"),
        read_json(directory / "feature_status.json"),
        read_json(directory / "run_summary.json"),
    )


def money(value: Any, currency: str) -> str:
    if value is None or not np_isfinite(value):
        return "—"
    decimals = 0 if currency in {"KRW", "JPY"} else 2
    return f"{float(value):,.{decimals}f} {currency}"


def price(value: Any, currency: str) -> str:
    return money(value, currency)


def percent(value: Any, digits: int = 1) -> str:
    if value is None or not np_isfinite(value):
        return "—"
    return f"{float(value) * 100:,.{digits}f}%"


def np_isfinite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def record_for(records: Sequence[Mapping[str, Any]], ticker: str, period: int, horizon: int) -> dict[str, Any] | None:
    for record in records:
        if str(record.get("ticker")) == ticker and int(record.get("period_years", -1)) == period and int(record.get("horizon_days", -1)) == horizon:
            return dict(record)
    return None


def validate_cli(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="게시 번들 읽기/해시/schema 검증")
    parser.add_argument("--validate-data", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        bundle = load_bundle_uncached(args.validate_data, verify_hashes=True)
    except BundleError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    combinations = bundle.stock_forecasts[["period_years", "horizon_days"]].drop_duplicates()
    print(
        json.dumps(
            {
                "valid": True,
                "schema_version": bundle.manifest["schema_version"],
                "stock_rows": len(bundle.stock_forecasts),
                "portfolio_rows": len(bundle.portfolio_forecast),
                "periods": sorted(int(v) for v in combinations["period_years"].unique()),
                "horizons": sorted(int(v) for v in combinations["horizon_days"].unique()),
                "memory_spot_used": bundle.manifest.get("memory_spot_used"),
                "memory_spot_status": bundle.manifest.get("memory_spot_status"),
                "kcs_trade_status": bundle.manifest.get("kcs_trade_status"),
                "kcs_latest_period": bundle.manifest.get("kcs_latest_period"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def run_app() -> None:
    try:
        import plotly.express as px
        import plotly.graph_objects as go
        import streamlit as st
    except ImportError as exc:
        raise SystemExit(
            "Streamlit/Plotly가 없습니다. `pip install -r requirements.txt` 후 "
            "`streamlit run streamlit_app.py`를 실행하세요."
        ) from exc

    st.set_page_config(
        page_title="메모리 주식 확률 예측",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1500px;}
        [data-testid="stMetric"] {background: rgba(120,120,120,.06); border: 1px solid rgba(120,120,120,.18); padding: .75rem; border-radius: .75rem;}
        .small-note {font-size: .82rem; color: #777;}
        @media (max-width: 700px) {.block-container {padding-left: .7rem; padding-right: .7rem;} [data-testid="stMetric"] {padding: .45rem;}}
        </style>
        """,
        unsafe_allow_html=True,
    )
    published_dir = Path(os.getenv("PUBLISHED_DIR", Path(__file__).resolve().parent / "published"))
    manifest_path = published_dir / "manifest.json"
    if not manifest_path.exists():
        st.error(
            "게시 데이터가 없습니다. 로컬에서 memory_stock_engine.py를 먼저 실행해 "
            "published/를 생성한 뒤 이 브랜치에 게시하세요."
        )
        st.stop()

    @st.cache_data(show_spinner=False)
    def cached_load(path_text: str, manifest_mtime_ns: int) -> Bundle:
        del manifest_mtime_ns
        return load_bundle_uncached(Path(path_text), verify_hashes=True)

    try:
        bundle = cached_load(str(published_dir), manifest_path.stat().st_mtime_ns)
    except BundleError as exc:
        st.error(f"게시 데이터 검증 실패: {exc}")
        st.stop()

    manifest = bundle.manifest
    generated_at = pd.to_datetime(manifest.get("generated_at"), utc=True, errors="coerce")
    age_hours = (pd.Timestamp.now(tz="UTC") - generated_at).total_seconds() / 3600 if pd.notna(generated_at) else float("inf")
    if age_hours > 72:
        st.warning(f"결과가 {age_hours / 24:.1f}일 전에 계산되었습니다. 데이터 최신성과 신뢰도를 다시 확인하세요.")
    if manifest.get("test_data"):
        st.error("테스트/합성 데이터 번들입니다. 실제 투자 판단에 사용할 수 없습니다.")
    spot_status = str(manifest.get("memory_spot_status", "UNKNOWN"))
    if spot_status == "PROXY_OPT_IN":
        st.error("⚠️ 실제 DRAM·NAND 현물가가 아니라 사용자가 명시적으로 허용한 대체지표(PROXY_OPT_IN)를 사용했습니다.")
    elif spot_status == "CACHED":
        st.warning("최신 현물가 수집/입력 실패로 마지막 검증 캐시(CACHED)를 사용했습니다.")
    kcs_status = str(manifest.get("kcs_trade_status", "UNKNOWN"))
    if kcs_status in {"MISSING_KEY", "FETCH_FAILED", "DISABLED"}:
        st.info(
            "관세청 반도체 수출입 단가 특징이 제외되었습니다. 데이터 상태 탭에서 API 키·캐시 상태를 확인하세요."
        )

    forecasts = bundle.stock_forecasts.copy()
    periods = sorted(int(v) for v in forecasts["period_years"].unique())
    st.sidebar.header("표시 조건")
    period_years = st.sidebar.select_slider("학습 기간", options=periods, value=periods[-1], format_func=lambda v: f"{v}년")
    horizons_for_period = sorted(int(v) for v in forecasts.loc[forecasts["period_years"] == period_years, "horizon_days"].unique())
    horizon = st.sidebar.select_slider("예측 기간", options=horizons_for_period, value=horizons_for_period[0], format_func=lambda v: f"{v}거래일")
    combo_forecasts = forecasts[(forecasts["period_years"] == period_years) & (forecasts["horizon_days"] == horizon)]
    account = "전체"
    combo_components = bundle.components[
        (bundle.components["period_years"] == period_years)
        & (bundle.components["horizon_days"] == horizon)
        & (bundle.components["account_scope"].astype(str) == account)
    ].copy()
    component_stocks = combo_components[combo_components["asset_type"] == "stock"]
    component_name_map = (
        component_stocks.drop_duplicates("ticker").set_index("ticker")["name"].astype(str).to_dict()
        if not component_stocks.empty
        else {}
    )
    forecast_name_map = {
        str(row.ticker): str(row.name) for row in combo_forecasts.itertuples()
    }
    ticker_options = list(
        dict.fromkeys(
            [*component_stocks["ticker"].astype(str).tolist(), *combo_forecasts["ticker"].astype(str).tolist()]
        )
    )
    if not ticker_options:
        st.error("선택 기간에 표시할 보유 주식 또는 예측 결과가 없습니다.")
        st.stop()
    forecasted_set = set(combo_forecasts["ticker"].astype(str))
    ticker_labels = {
        value: (
            f"{component_name_map.get(value, forecast_name_map.get(value, value))} ({value})"
            + (" · 예측 불가" if value not in forecasted_set else "")
        )
        for value in ticker_options
    }
    ticker = st.sidebar.selectbox("보유 종목", options=ticker_options, format_func=lambda v: ticker_labels[v])
    base_currency = st.sidebar.selectbox("기준 통화", options=[str(manifest.get("base_currency", "KRW"))])
    interval_level = st.sidebar.radio("예측구간", options=(80, 90), horizontal=True, format_func=lambda v: f"{v}%")
    graph_items = st.sidebar.multiselect(
        "그래프 표시 항목",
        options=["과거", "중앙값", "평균", "불확실성", "판단 가격선"],
        default=["과거", "중앙값", "불확실성", "판단 가격선"],
    )
    st.sidebar.caption("계좌 구분 없이 모든 보유자산을 통합 표시합니다. 이 앱은 재학습하지 않습니다.")

    portfolio_row_frame = bundle.portfolio_forecast[
        (bundle.portfolio_forecast["period_years"] == period_years)
        & (bundle.portfolio_forecast["horizon_days"] == horizon)
        & (bundle.portfolio_forecast["account_scope"].astype(str) == account)
    ]
    if portfolio_row_frame.empty:
        st.error("선택한 기간의 전체 포트폴리오 결과가 없습니다.")
        st.stop()
    portfolio_row = portfolio_row_frame.iloc[0]
    confidence_map = (
        combo_forecasts.groupby("ticker")["confidence_score"].mean().astype(float).to_dict()
    )
    if not component_stocks.empty:
        confidence_values = component_stocks["ticker"].astype(str).map(confidence_map).fillna(0.0)
        confidence_weights = pd.to_numeric(component_stocks["current_value"], errors="coerce").fillna(0.0).clip(lower=0.0)
        overall_confidence = (
            float((confidence_values * confidence_weights).sum() / confidence_weights.sum())
            if confidence_weights.sum() > 0
            else float(confidence_values.mean())
        )
    else:
        overall_confidence = 0.0
    overall_grade = "높음" if overall_confidence >= 80 else "보통" if overall_confidence >= 60 else "낮음" if overall_confidence >= 40 else "사용 주의"

    st.title("메모리 반도체 주식·보유자산 확률 예측")
    st.caption(
        f"현물가 상태: {spot_status} · 관세청 단가: {kcs_status} · 마지막 계산: {manifest.get('generated_at', '—')} · "
        "현재 수량을 그대로 보유한다고 가정한 확률적 추정이며 투자수익을 보장하지 않습니다."
    )
    unforecasted_components = component_stocks[
        component_stocks.get("forecast_status", pd.Series(index=component_stocks.index, dtype=str)).astype(str)
        != "forecasted"
    ]
    if not unforecasted_components.empty:
        missing_names = ", ".join(
            f"{row['name']}({row['ticker']})" for _, row in unforecasted_components.drop_duplicates("ticker").iterrows()
        )
        st.warning(
            f"예측이 정상 완료되지 않은 보유종목도 총자산에서 제외하지 않았습니다: {missing_names}. "
            "해당 종목의 표시 방식과 경고를 아래 보유자산 표에서 확인하세요."
        )
    cards = st.columns(3)
    cards[0].metric("현재 총자산", money(portfolio_row["current_total"], base_currency))
    cards[1].metric(f"{horizon}일 중앙값", money(portfolio_row["future_p50"], base_currency))
    cards[2].metric("예상수익률", percent(portfolio_row["expected_return"]))
    cards2 = st.columns(3)
    cards2[0].metric("손실 확률", percent(portfolio_row["loss_probability"]))
    cards2[1].metric("전체 신뢰도", f"{overall_confidence:.1f}/100", overall_grade)
    cards2[2].metric("마지막 계산", generated_at.tz_convert("Asia/Seoul").strftime("%m-%d %H:%M") if pd.notna(generated_at) else "—")

    tab_portfolio, tab_stock, tab_decision, tab_model, tab_data = st.tabs(
        ["포트폴리오", "종목별 예측", "투자 판단", "모델 검증", "데이터 상태"]
    )

    with tab_portfolio:
        st.subheader("전체 보유자산 가치와 미래 예상 자산")
        history = bundle.portfolio_history[bundle.portfolio_history["account_scope"].astype(str) == account].copy()
        future = bundle.portfolio_paths[
            (bundle.portfolio_paths["period_years"] == period_years)
            & (bundle.portfolio_paths["horizon_days"] == horizon)
            & (bundle.portfolio_paths["account_scope"].astype(str) == account)
        ].copy()
        fig = go.Figure()
        if "과거" in graph_items and not history.empty:
            fig.add_trace(go.Scatter(x=history["date"], y=history["value"], name="현재 수량 소급평가", mode="lines", line=dict(color="#8a94a6"), hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f}<extra></extra>"))
        lower = "p05" if interval_level == 90 and "p05" in future else "p10"
        upper = "p95" if interval_level == 90 and "p95" in future else "p90"
        if "불확실성" in graph_items:
            fig.add_trace(go.Scatter(x=future["date"], y=future[upper], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=future["date"], y=future[lower], name=f"{interval_level}% 구간", mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(54,162,235,.18)", hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f}<extra></extra>"))
        if "중앙값" in graph_items:
            fig.add_trace(go.Scatter(x=future["date"], y=future["p50"], name="미래 중앙값", mode="lines", line=dict(color="#0d6efd", width=3), hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f}<extra></extra>"))
        if "평균" in graph_items:
            fig.add_trace(go.Scatter(x=future["date"], y=future["mean"], name="미래 평균", mode="lines", line=dict(color="#20c997", dash="dot")))
        if not future.empty:
            current_rows = future[future["step"] == 0] if "step" in future else pd.DataFrame()
            current_date = current_rows["date"].iloc[0] if not current_rows.empty else future["date"].min() - pd.offsets.BDay(1)
            fig.add_vline(x=current_date.timestamp() * 1000, line_dash="dash", line_color="#dc3545", annotation_text="현재")
        fig.update_layout(height=520, hovermode="x unified", yaxis_title=base_currency, xaxis_title="날짜", legend_orientation="h")
        st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})
        st.caption("과거 구간은 실제 거래내역이 아니라 현재 수량을 과거 가격으로 소급평가한 참고선입니다. 미래선은 공동 block bootstrap Monte Carlo 경로의 시점별 통계입니다.")

        risk_cols = st.columns(4)
        risk_cols[0].metric("95% VaR", money(portfolio_row["var95"], base_currency))
        risk_cols[1].metric("95% Expected Shortfall", money(portfolio_row["expected_shortfall95"], base_currency))
        risk_cols[2].metric("원금 이하 확률", percent(portfolio_row["below_cost_probability"]))
        risk_cols[3].metric("Monte Carlo 경로", f"{int(portfolio_row['simulation_paths']):,}")
        components = combo_components.copy()
        st.markdown("#### 종목별 미래 가치·기여도")
        component_cols = ["name", "ticker", "asset_type", "currency", "quantity", "current_value", "invested_cost", "future_value_p10", "future_value_p50", "future_value_p90", "contribution_to_change", "forecast_status", "valuation_method", "fx_method", "warning"]
        display_components = components[[c for c in component_cols if c in components]].rename(columns={"name": "자산", "ticker": "종목코드", "asset_type": "유형", "currency": "통화", "quantity": "수량", "current_value": "현재가치", "invested_cost": "투자원금", "future_value_p10": "미래 10%", "future_value_p50": "미래 중앙값", "future_value_p90": "미래 90%", "contribution_to_change": "변화 기여", "forecast_status": "예측 상태", "valuation_method": "현재가 산정", "fx_method": "환율 처리", "warning": "경고"})
        st.dataframe(display_components, use_container_width=True, hide_index=True, column_config={c: st.column_config.NumberColumn(c, format="%,.0f") for c in ["수량", "현재가치", "투자원금", "미래 10%", "미래 중앙값", "미래 90%", "변화 기여"]})
        chart_components = components[components["asset_type"] == "stock"].sort_values("contribution_to_change")
        if not chart_components.empty:
            contribution_fig = px.bar(chart_components, x="contribution_to_change", y="name", orientation="h", labels={"contribution_to_change": f"예상 변화 기여 ({base_currency})", "name": "종목"}, color="contribution_to_change", color_continuous_scale="RdYlGn")
            st.plotly_chart(contribution_fig, use_container_width=True)

    with tab_stock:
        selected_forecast = combo_forecasts[combo_forecasts["ticker"] == ticker]
        if selected_forecast.empty:
            holding = component_stocks[component_stocks["ticker"].astype(str) == ticker]
            asset_name = component_name_map.get(ticker, ticker)
            st.subheader(f"{asset_name} ({ticker})")
            st.error(
                "이 종목은 보유자산 합계에는 포함했지만 시장 데이터 또는 최소 이력이 없어 "
                "주가 예측을 만들지 못했습니다. 평균매수가를 예측값으로 가장하지 않습니다."
            )
            if not holding.empty:
                st.dataframe(
                    holding[[c for c in ["name", "ticker", "quantity", "currency", "current_value", "forecast_status", "valuation_method", "warning"] if c in holding]],
                    use_container_width=True,
                    hide_index=True,
                )
        else:
            row = selected_forecast.iloc[0]
            st.subheader(f"{row['name']} ({ticker}) 미래 주가")
            history = bundle.stock_history[bundle.stock_history["ticker"] == ticker].copy()
            future = bundle.stock_paths[
                (bundle.stock_paths["ticker"] == ticker)
                & (bundle.stock_paths["period_years"] == period_years)
                & (bundle.stock_paths["horizon_days"] == horizon)
            ].copy()
            decision_rows = bundle.decisions[
                (bundle.decisions["ticker"] == ticker)
                & (bundle.decisions["period_years"] == period_years)
                & (bundle.decisions["horizon_days"] == horizon)
            ]
            decision = decision_rows.iloc[0] if not decision_rows.empty else None
            local_currency = component_stocks[component_stocks["ticker"].astype(str) == ticker]["currency"].iloc[0] if not component_stocks[component_stocks["ticker"].astype(str) == ticker].empty else base_currency
            if "SHORT_HISTORY_FALLBACK" in str(row.get("data_status", "")):
                st.warning(
                    "상장 이력이 짧아 정상 walk-forward 검증 대신 저신뢰 block-bootstrap 기준선을 사용했습니다. "
                    "신뢰도는 최대 25점이며 판단 보류가 우선입니다."
                )
            fig = go.Figure()
            if "과거" in graph_items and not history.empty:
                fig.add_trace(go.Scatter(x=history["date"], y=history["price"], name="과거 실제 주가", mode="lines", line=dict(color="#8a94a6"), hovertemplate="%{x|%Y-%m-%d}<br>가격 %{y:,.2f}<extra></extra>"))
            lower = "p05" if interval_level == 90 and "p05" in future else "p10"
            upper = "p95" if interval_level == 90 and "p95" in future else "p90"
            if "불확실성" in graph_items and not future.empty:
                fig.add_trace(go.Scatter(x=future["date"], y=future[upper], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
                fig.add_trace(go.Scatter(x=future["date"], y=future[lower], name=f"{interval_level}% 예측구간", mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(13,110,253,.18)"))
            if "중앙값" in graph_items and not future.empty:
                hover_return = (future["p50"] / float(row["current_price"]) - 1.0) * 100
                fig.add_trace(go.Scatter(x=future["date"], y=future["p50"], customdata=hover_return, name="미래 중앙값", mode="lines", line=dict(color="#0d6efd", width=3), hovertemplate="%{x|%Y-%m-%d}<br>가격 %{y:,.2f}<br>예상수익률 %{customdata:.2f}%<extra></extra>"))
            if "평균" in graph_items and not future.empty:
                fig.add_trace(go.Scatter(x=future["date"], y=future["mean"], name="미래 평균", line=dict(color="#20c997", dash="dot")))
            current_date = history["date"].max() if not history.empty else pd.to_datetime(row["as_of_date"])
            fig.add_vline(x=current_date.timestamp() * 1000, line_dash="dash", line_color="#dc3545", annotation_text="현재")
            if "판단 가격선" in graph_items and decision is not None:
                lines = [("1차 목표", decision["target1"], "#198754"), ("2차 목표", decision["target2"], "#20c997"), ("손절", decision["stop_loss"], "#dc3545"), ("추매 검토", decision["add_consideration"], "#fd7e14")]
                for label, level, color in lines:
                    fig.add_hline(y=float(level), line_dash="dot", line_color=color, annotation_text=label)
            fig.update_layout(height=560, hovermode="x unified", yaxis_title=str(local_currency), xaxis_title="날짜", legend_orientation="h")
            st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})
            stats = st.columns(6)
            stats[0].metric("현재가", price(row["current_price"], str(local_currency)))
            stats[1].metric("예상 중앙가격", price(row["price_q50"], str(local_currency)))
            stats[2].metric("예상수익률", percent(row["expected_return"]))
            stats[3].metric("상승확률", percent(row["up_probability"]))
            stats[4].metric("예상 변동성", percent(row["predicted_volatility"]))
            stats[5].metric("신뢰도", f"{float(row['confidence_score']):.1f}/100", str(row["confidence_grade"]))

    with tab_decision:
        st.subheader("종목별 투자 판단용 숫자")
        if not unforecasted_components.empty:
            st.markdown("#### 예측을 만들지 못했지만 총자산에는 포함된 종목")
            st.dataframe(
                unforecasted_components[
                    [c for c in ["name", "ticker", "quantity", "currency", "current_value", "forecast_status", "valuation_method", "warning"] if c in unforecasted_components]
                ].drop_duplicates("ticker"),
                use_container_width=True,
                hide_index=True,
            )
        decision_table = bundle.decisions[
            (bundle.decisions["period_years"] == period_years)
            & (bundle.decisions["horizon_days"] == horizon)
        ].merge(
            combo_forecasts[["ticker", "current_price", "price_q25", "price_q50", "price_q75", "up_probability", "expected_return", "predicted_volatility", "max_expected_downside", "model_name", "data_status"]],
            on="ticker",
            how="left",
        )
        for pct_col in ("expected_return", "up_probability", "predicted_volatility", "max_expected_downside"):
            if pct_col in decision_table:
                decision_table[pct_col] = decision_table[pct_col] * 100.0
        columns = ["name", "ticker", "current_price", "price_q25", "price_q50", "price_q75", "expected_return", "up_probability", "target1", "target2", "stop_loss", "add_consideration", "reward_risk", "predicted_volatility", "max_expected_downside", "confidence_score", "confidence_grade", "judgement_status", "model_name", "data_status", "warning"]
        st.dataframe(decision_table[columns], use_container_width=True, hide_index=True, column_config={
            "current_price": st.column_config.NumberColumn("현재가", format="%,.2f"),
            "price_q25": st.column_config.NumberColumn("보수적(q25)", format="%,.2f"),
            "price_q50": st.column_config.NumberColumn("중앙(q50)", format="%,.2f"),
            "price_q75": st.column_config.NumberColumn("낙관적(q75)", format="%,.2f"),
            "expected_return": st.column_config.NumberColumn("예상수익률", format="%.2f%%"),
            "up_probability": st.column_config.NumberColumn("상승확률", format="%.2f%%"),
            "predicted_volatility": st.column_config.NumberColumn("예상 변동성", format="%.2f%%"),
            "max_expected_downside": st.column_config.NumberColumn("최대 예상 하락", format="%.2f%%"),
            "target1": st.column_config.NumberColumn("1차 목표", format="%,.2f"),
            "target2": st.column_config.NumberColumn("2차 목표", format="%,.2f"),
            "stop_loss": st.column_config.NumberColumn("손절가", format="%,.2f"),
            "add_consideration": st.column_config.NumberColumn("추매 고려가", format="%,.2f"),
            "reward_risk": st.column_config.NumberColumn("손익비", format="%.2f"),
            "confidence_score": st.column_config.ProgressColumn("신뢰도", min_value=0, max_value=100, format="%.1f"),
        })
        st.warning("추매 고려가는 물타기 권고가 아닙니다. 해당 가격에서도 모델의 상승 기대값과 데이터 상태가 유지되는 경우에만 재검토하세요.")
        with st.expander("목표가·손절가·추매 고려가 산식"):
            sample = decision_table.iloc[0]
            st.markdown(
                f"- 1차 목표가: `{sample['formula_target1']}`\n"
                f"- 2차 목표가: `{sample['formula_target2']}`\n"
                f"- 손절가: `{sample['formula_stop']}`\n"
                f"- 추매 고려가: `{sample['formula_add']}`\n\n"
                "모든 수치는 예측 분위수, ATR, 최근 지지·저항과 신뢰도를 함께 사용합니다."
            )

    with tab_model:
        st.subheader("Walk-forward 검증과 신뢰도 근거")
        metric = record_for(bundle.model_metrics.get("records", []), ticker, period_years, horizon)
        if metric is None:
            st.info("선택 조합의 모델 검증 기록이 없습니다.")
        else:
            st.markdown(f"**선택 모델:** `{metric['selected_model']}`  \n**선정 이유:** {metric['selection_reason']}")
            if not metric.get("validation_available", True):
                st.error("검증 표본이 부족하여 walk-forward 및 격리 테스트 성능이 없습니다. 이 결과는 저신뢰 기준선입니다.")
            else:
                val, test = metric["validation"], metric["isolated_test"]
                comparison = pd.DataFrame(
                    [
                        {"구간": "Walk-forward OOF", **val},
                        {"구간": "최종 격리 테스트", **test},
                    ]
                )
                st.dataframe(comparison, use_container_width=True, hide_index=True)
                st.caption(f"기준선 대비 OOF 개선율 {metric['baseline_improvement']:.2%} · 격리 테스트 개선율 {metric['isolated_test_improvement']:.2%} · 최근/전체 RMSE 비율 {metric['recent_rmse_ratio']:.2f}")
            left, right = st.columns(2)
            with left:
                st.markdown("#### 후보 모델 비교")
                st.dataframe(pd.DataFrame(metric.get("candidates", [])), use_container_width=True, hide_index=True)
            with right:
                st.markdown("#### 신뢰도 계산(합계 100점)")
                confidence_frame = pd.DataFrame([{"요소": key, "획득점수": value} for key, value in metric.get("confidence_formula", {}).items()])
                st.dataframe(confidence_frame, use_container_width=True, hide_index=True)
                st.caption("기준선 미개선 시 59점, PROXY_OPT_IN 시 39점, 최근 RMSE 급등 시 69점으로 상한을 적용합니다.")
            folds = pd.DataFrame(metric.get("folds", []))
            if not folds.empty:
                st.markdown("#### 폴드별 성능 안정성")
                fold_fig = px.line(folds, x="fold", y=["rmse", "mae", "direction_accuracy"], markers=True)
                st.plotly_chart(fold_fig, use_container_width=True)
                st.dataframe(folds, use_container_width=True, hide_index=True)

    with tab_data:
        st.subheader("데이터·특징·폴백 상태")
        asset_coverage = bundle.data_quality.get("asset_coverage", {})
        if asset_coverage:
            st.markdown("#### 보유자산 포함 상태")
            st.write("입력 주식:", ", ".join(asset_coverage.get("input_stock_tickers", [])) or "없음")
            st.write("시장 데이터 확보:", ", ".join(asset_coverage.get("market_data_tickers", [])) or "없음")
            st.write("예측 생성:", ", ".join(asset_coverage.get("forecasted_tickers", [])) or "없음")
            if asset_coverage.get("all_holdings_in_portfolio_components"):
                st.success("assets.csv의 모든 보유자산이 전체 포트폴리오 구성표에 포함됐습니다.")
            else:
                st.error("일부 입력 자산이 포트폴리오 구성표에서 누락됐습니다. 이 결과를 사용하지 마세요.")
        spot = bundle.data_quality.get("memory_spot", {})
        info = st.columns(5)
        info[0].metric("현물가 상태", spot.get("status", "—"))
        info[1].metric("실제 현물가 사용", "예" if spot.get("memory_spot_used") else "아니오")
        info[2].metric("현물가 행", f"{int(spot.get('rows', 0)):,}")
        info[3].metric("최종 현물가 날짜", spot.get("final_date") or "—")
        info[4].metric("사용 제품", f"{len(spot.get('products', []))}개")
        st.write("사용 제품:", ", ".join(spot.get("products", [])) or "없음")
        kcs = bundle.data_quality.get("kcs_semiconductor_trade", {})
        st.markdown("#### 한국 반도체 수출입 단가")
        trade_info = st.columns(4)
        trade_info[0].metric("관세청 상태", kcs.get("status", "—"))
        trade_info[1].metric("월별 행", f"{int(kcs.get('rows', 0)):,}")
        trade_info[2].metric("최종 대상월", kcs.get("latest_period") or "—")
        trade_info[3].metric("HS 코드", ", ".join(kcs.get("hs_codes", [])) or "—")
        st.caption(
            f"단가 산식: {kcs.get('unit_value_formula', '—')} · "
            f"사용 시점: {kcs.get('release_lag', '—')}"
        )
        if kcs.get("hs_scope_note"):
            st.info(str(kcs["hs_scope_note"]))
        if kcs.get("revision_warning"):
            st.warning(str(kcs["revision_warning"]))
        feature_records = pd.DataFrame(bundle.feature_status.get("records", []))
        if not feature_records.empty:
            local = feature_records[
                ((feature_records["ticker"] == ticker) & (feature_records["period_years"] == period_years) & (feature_records["horizon_days"] == horizon))
                | (feature_records["ticker"] == "ALL")
            ].copy()
            if "coverage" in local:
                local["coverage"] = local["coverage"] * 100.0
            show_excluded = st.checkbox("제외 특징도 표시", value=True)
            if not show_excluded:
                local = local[local["used"] == True]  # noqa: E712
            st.dataframe(local[[c for c in ["name", "source", "required", "used", "coverage", "latest_date", "release_lag", "excluded_reason"] if c in local]], use_container_width=True, hide_index=True, column_config={"coverage": st.column_config.ProgressColumn("coverage", min_value=0.0, max_value=100.0, format="%.1f%%")})
        st.markdown("#### 수집·품질 경고")
        warnings = list(dict.fromkeys([*manifest.get("warnings", []), *bundle.data_quality.get("warnings", [])]))
        if warnings:
            for warning in warnings:
                st.warning(str(warning))
        else:
            st.success("기록된 데이터 경고가 없습니다.")
        failures = bundle.run_summary.get("failures", [])
        if failures:
            st.markdown("#### 실패한 종목/조합")
            st.dataframe(pd.DataFrame(failures), use_container_width=True, hide_index=True)
        fallbacks = bundle.run_summary.get("fallbacks", [])
        if fallbacks:
            st.markdown("#### 단기이력 폴백 적용")
            st.dataframe(pd.DataFrame(fallbacks), use_container_width=True, hide_index=True)
        with st.expander("메타데이터"):
            st.json({"manifest": manifest, "market_sources": bundle.data_quality.get("market_sources"), "fx_series": bundle.data_quality.get("fx_series"), "kcs_semiconductor_trade": kcs, "package_versions": bundle.run_summary.get("package_versions")}, expanded=False)


def main(argv: Sequence[str] | None = None) -> int:
    actual = list(sys.argv[1:] if argv is None else argv)
    if actual and actual[0] == "--validate-data":
        return validate_cli(actual)
    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
