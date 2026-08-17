"""Streamlit Cloud 전용 뷰어.

모델 학습·yfinance·sklearn·외부 HTTP 호출을 하지 않는다. 로컬 계산기가 Git에
게시한 published_data의 CSV/JSON만 읽어 빠르고 재현 가능하게 표시한다.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots


BASE_DIR = Path(__file__).resolve().parent
PUBLISHED_ROOT = BASE_DIR / "published_data"
EXPECTED_SCHEMA = 1

# Streamlit에서 마우스로 선택할 수 있는 항목
PERIOD_OPTIONS = {
    "1y": "1년 · 최근 레짐 중심",
    "3y": "3년 · 최근 사이클",
    "5y": "5년 · 중기 사이클",
    "10y": "10년 · 권장",
    "15y": "15년 · 장기 스트레스",
}
HORIZON_OPTIONS = [10, 20, 40]
DEFAULT_PERIOD = "10y"
DEFAULT_HORIZON = 20
SPOT_NAMES = {
    "DRAM_DDR5_16Gb": "DRAM DDR5 16Gb",
    "DRAM_DDR4_8Gb": "DRAM DDR4 8Gb",
    "NAND_TLC_512Gb": "NAND TLC 512Gb",
}
MODEL_NAMES = {
    "boost_interaction": "비선형 Boosting",
    "boost_smooth": "강규제 Boosting",
    "extra_trees": "Extra Trees",
    "linear_shrinkage": "선형 Shrinkage",
}


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def read_csv(data_dir: Path, name: str, **kwargs) -> pd.DataFrame:
    path = data_dir / name
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except Exception:
        return pd.DataFrame()


def parse_date_column(frame: pd.DataFrame, column: str = "date") -> pd.DataFrame:
    if column in frame:
        frame = frame.copy()
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


@st.cache_data(show_spinner=False)
def load_bundle(period: str, horizon: int, generation_id: str) -> dict:
    del generation_id  # 캐시 무효화 키
    data_dir = PUBLISHED_ROOT / period / f"{horizon}d"
    manifest = read_json(data_dir / "manifest.json")
    prices = {}
    for ticker, relative in manifest.get("ticker_files", {}).items():
        path = data_dir / relative
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, compression="gzip")
            frame = parse_date_column(frame)
            prices[ticker] = frame.dropna(subset=["date"]).sort_values("date")
        except Exception:
            continue
    return {
        "manifest": manifest,
        "plans": read_json(data_dir / "plans.json"),
        "metrics": read_json(data_dir / "metrics_summary.json"),
        "reliability": read_json(data_dir / "reliability.json"),
        "model_info": read_json(data_dir / "model_info.json"),
        "spot_status": read_json(data_dir / "spot_status.json"),
        "board": read_csv(data_dir, "decision_board.csv"),
        "scores": parse_date_column(read_csv(data_dir, "scores.csv")),
        "oos": parse_date_column(
            read_csv(data_dir, "oos.csv.gz", compression="gzip")
        ),
        "spot": parse_date_column(
            read_csv(data_dir, "spot_prices.csv"), "날짜"
        ),
        "importance": read_csv(data_dir, "feature_importance.csv"),
        "per_ticker": read_csv(data_dir, "metrics_per_ticker.csv"),
        "calibration": read_csv(data_dir, "calibration.csv"),
        "rolling": parse_date_column(
            read_csv(data_dir, "rolling_accuracy.csv")
        ),
        "prices": prices,
    }


def finite(value, fallback=np.nan) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else fallback
    except (TypeError, ValueError):
        return fallback


def ticker_currency(ticker: str) -> str:
    if ticker.endswith((".KS", ".KQ")):
        return "KRW"
    if ticker.endswith(".T"):
        return "JPY"
    return "USD"


def fmt_price(value, currency: str) -> str:
    number = finite(value)
    if not math.isfinite(number):
        return "-"
    return f"{number:,.0f}" if currency in ("KRW", "JPY") else f"{number:,.2f}"


def fmt_pct(value, digits: int = 1) -> str:
    number = finite(value)
    return "-" if not math.isfinite(number) else f"{number:+.{digits}%}"


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=max(3, n // 2)).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    diff = series.diff()
    up = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def technical_figure(price: pd.DataFrame, score_history: pd.DataFrame):
    data = price.tail(756).copy()
    close = pd.to_numeric(data["Close"], errors="coerce")
    e12, e26 = ema(close, 12), ema(close, 26)
    macd = e12 - e26
    signal = ema(macd, 9)
    hist = macd - signal
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.035,
        row_heights=[0.56, 0.22, 0.22],
        specs=[[{"secondary_y": True}], [{}], [{}]],
    )
    ohlc = {"Open", "High", "Low", "Close"} <= set(data.columns)
    if ohlc:
        fig.add_trace(go.Candlestick(
            x=data["date"], open=data["Open"], high=data["High"],
            low=data["Low"], close=data["Close"], name="OHLC",
            increasing_line_color="#16a34a", decreasing_line_color="#ef4444"),
            row=1, col=1, secondary_y=False)
    else:
        fig.add_trace(go.Scatter(x=data["date"], y=close, name="종가"),
                      row=1, col=1, secondary_y=False)
    for n, color in ((20, "#2563eb"), (60, "#f59e0b"), (120, "#7c3aed")):
        fig.add_trace(go.Scatter(
            x=data["date"], y=close.rolling(n).mean(), name=f"MA{n}",
            line=dict(width=1.1, color=color)), row=1, col=1, secondary_y=False)
    if not score_history.empty:
        fig.add_trace(go.Scatter(
            x=score_history["date"], y=score_history["score"], name="상승확률",
            line=dict(width=1.2, color="#db2777"), opacity=0.75),
            row=1, col=1, secondary_y=True)
    rsi14 = rsi(close, 14)
    fig.add_trace(go.Scatter(x=data["date"], y=rsi14, name="RSI(14)",
                             line=dict(color="#0f766e")), row=2, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#ef4444", row=2, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#2563eb", row=2, col=1)
    colors = np.where(hist >= 0, "rgba(22,163,74,.65)", "rgba(239,68,68,.65)")
    fig.add_trace(go.Bar(x=data["date"], y=hist, name="MACD Hist",
                         marker_color=colors), row=3, col=1)
    fig.add_trace(go.Scatter(x=data["date"], y=macd, name="MACD"), row=3, col=1)
    fig.add_trace(go.Scatter(x=data["date"], y=signal, name="Signal"), row=3, col=1)
    fig.update_yaxes(title_text="가격", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="확률", range=[0, 100], ticksuffix="%",
                     row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    fig.update_layout(height=690, hovermode="x unified", bargap=0,
                      margin=dict(t=30, b=20, l=20, r=20),
                      legend=dict(orientation="h", y=1.04),
                      xaxis_rangeslider_visible=False)
    return fig


def ladder_figure(plan: dict):
    currency = plan.get("ccy", "USD")
    points = []
    specs = [
        ("stop", "손절", "#dc2626", "triangle-down"),
        ("reentry", "재진입", "#7c3aed", "triangle-up"),
        ("buy", "매수", "#16a34a", "triangle-up"),
        ("price", "현재", "#111827", "diamond"),
        ("trim", "축소", "#f59e0b", "triangle-down"),
        ("target", "목표", "#2563eb", "star"),
    ]
    for key, label, color, marker in specs:
        value = finite(plan.get(key))
        if math.isfinite(value):
            points.append((value, label, color, marker))
    points.sort()
    fig = go.Figure()
    if points:
        fig.add_scatter(x=[points[0][0], points[-1][0]], y=[0, 0], mode="lines",
                        line=dict(color="#cbd5e1", width=2), showlegend=False)
        for i, (value, label, color, marker) in enumerate(points):
            fig.add_scatter(
                x=[value], y=[0], mode="markers+text",
                marker=dict(size=13, color=color, symbol=marker),
                text=[f"{label}<br>{fmt_price(value, currency)}"],
                textposition="top center" if i % 2 == 0 else "bottom center",
                showlegend=False)
    fig.update_yaxes(visible=False, range=[-1, 1])
    fig.update_layout(height=180, margin=dict(t=10, b=10, l=10, r=10))
    return fig


def equity_curve(oos: pd.DataFrame, ticker: str, horizon: int,
                 threshold: int, cost_bps: int):
    group = oos[oos["ticker"] == ticker].dropna(subset=["fwd_ret"]).sort_values("date")
    group = group.iloc[::max(1, horizon)].copy()
    if len(group) < 4:
        return None, None
    returns = pd.to_numeric(group["fwd_ret"], errors="coerce").fillna(0).clip(lower=-0.99)
    active = pd.to_numeric(group["score"], errors="coerce") >= threshold
    strategy = pd.Series(np.where(active, returns - cost_bps / 10_000.0, 0.0))
    curve = pd.DataFrame({
        "date": group["date"].to_numpy(),
        "시그널 추종": (1 + strategy).cumprod(),
        "단순 보유": (1 + returns.reset_index(drop=True)).cumprod(),
    })

    def perf(series: pd.Series):
        nav = (1 + series).cumprod()
        total = float(nav.iloc[-1] - 1)
        years = max(len(series) * horizon / 252.0, 1 / 252)
        cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1
        mdd = float((nav / nav.cummax() - 1).min())
        vol = float(series.std(ddof=1))
        sharpe = float(series.mean() / vol * np.sqrt(252 / horizon)) if vol > 0 else np.nan
        return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe}

    return curve, {"strategy": perf(strategy), "benchmark": perf(returns.reset_index(drop=True)),
                   "trades": int(active.sum()), "exposure": float(active.mean())}


def portfolio_view(manifest: dict, plans: dict, ticker_names: dict):
    if "portfolio" not in st.session_state:
        st.session_state.portfolio = pd.DataFrame([
            {"티커": ticker, "수량": 0.0, "평단": 0.0}
            for ticker in ticker_names
        ])
    edited = st.data_editor(
        st.session_state.portfolio, num_rows="dynamic", hide_index=True,
        use_container_width=True,
        column_config={
            "티커": st.column_config.SelectboxColumn(
                "티커", options=list(ticker_names), required=True),
            "수량": st.column_config.NumberColumn("수량", min_value=0.0, format="%.6g"),
            "평단": st.column_config.NumberColumn("평단(현지통화)", min_value=0.0,
                                                   format="%.8g"),
        },
    )
    st.session_state.portfolio = edited.copy()
    quotes = manifest.get("latest_quotes", {})
    usdkrw = finite(quotes.get("KRW=X", {}).get("close"))
    usdjpy = finite(quotes.get("JPY=X", {}).get("close"))

    def to_krw(value: float, currency: str):
        if currency == "KRW":
            return value
        if currency == "USD" and math.isfinite(usdkrw):
            return value * usdkrw
        if currency == "JPY" and math.isfinite(usdkrw) and math.isfinite(usdjpy):
            return value * usdkrw / usdjpy
        return np.nan

    rows = []
    for _, row in edited.iterrows():
        ticker = str(row.get("티커", ""))
        quantity = finite(row.get("수량"), 0.0)
        average = finite(row.get("평단"), 0.0)
        quote = finite(quotes.get(ticker, {}).get("close"))
        if quantity <= 0 or not math.isfinite(quote):
            continue
        currency = ticker_currency(ticker)
        value = to_krw(quantity * quote, currency)
        cost = to_krw(quantity * average, currency) if average > 0 else np.nan
        expected = finite(plans.get(ticker, {}).get("er"), 0.0)
        rows.append({
            "종목": ticker_names.get(ticker, ticker), "티커": ticker,
            "수량": quantity, "현재가": fmt_price(quote, currency),
            "평가액(₩)": value, "수익률": value / cost - 1 if cost > 0 else np.nan,
            "모델 예상수익률": expected, "예상평가액(₩)": value * (1 + expected),
            "행동": plans.get(ticker, {}).get("label", "신호 없음"),
        })
    view = pd.DataFrame(rows)
    if view.empty:
        st.info("수량을 입력하면 마지막 로컬 게시 시세 기준 평가액이 표시됩니다.")
        return
    total = float(view["평가액(₩)"].sum())
    expected_total = float(view["예상평가액(₩)"].sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("총 평가액", f"₩{total:,.0f}")
    c2.metric("모델 예상자산", f"₩{expected_total:,.0f}", fmt_pct(expected_total / total - 1))
    c3.metric("예상 증감", f"₩{expected_total-total:+,.0f}")
    display = view.copy()
    for col in ("평가액(₩)", "예상평가액(₩)"):
        display[col] = display[col].map(lambda x: f"₩{x:,.0f}")
    for col in ("수익률", "모델 예상수익률"):
        display[col] = display[col].map(fmt_pct)
    st.dataframe(display, use_container_width=True, hide_index=True)
    st.caption("실시간 조회가 아니라 마지막 로컬 계산 시세 기준입니다.")


def main():
    st.set_page_config(page_title="Memory Stock Predict", page_icon="🧠",
                       layout="wide", initial_sidebar_state="expanded")
    st.markdown("""
    <style>
    .block-container {padding-top:1.15rem; padding-bottom:3rem; max-width:1540px;}
    .hero {padding:1.3rem 1.55rem; border-radius:1.1rem; margin-bottom:1rem;
      background:linear-gradient(125deg,#0f172a,#1d4ed8); color:white;}
    .hero h1 {font-size:2rem; margin:0 0 .25rem 0;}
    .hero p {margin:0; opacity:.8;}
    div[data-testid="stMetric"] {background:rgba(127,127,127,.055);
      border:1px solid rgba(127,127,127,.15); padding:.85rem 1rem; border-radius:.85rem;}
    </style>
    """, unsafe_allow_html=True)

    # 사용자가 Streamlit 화면에서 마우스로 학습기간/예측기간을 선택한다.
    with st.sidebar:
        st.header("분석 설정")
        period = st.selectbox(
            "학습 기간",
            options=list(PERIOD_OPTIONS),
            index=list(PERIOD_OPTIONS).index(DEFAULT_PERIOD),
            format_func=lambda x: PERIOD_OPTIONS[x],
        )
        horizon = st.selectbox(
            "거래일 전망",
            options=HORIZON_OPTIONS,
            index=HORIZON_OPTIONS.index(DEFAULT_HORIZON),
            format_func=lambda x: f"{x}거래일",
        )
        st.divider()

    data_dir = PUBLISHED_ROOT / period / f"{horizon}d"
    manifest = read_json(data_dir / "manifest.json")

    if not manifest:
        st.error(
            f"{PERIOD_OPTIONS[period]} / {horizon}거래일 결과가 게시되어 있지 않습니다."
        )
        st.info(
            "로컬 계산기가 "
            f"published_data/{period}/{horizon}d/ "
            "폴더에 해당 결과를 게시해야 합니다."
        )
        st.stop()

    if manifest.get("schema_version") != EXPECTED_SCHEMA:
        st.error("Cloud 앱과 게시 데이터의 스키마 버전이 맞지 않습니다.")
        st.stop()

    bundle = load_bundle(
        period,
        horizon,
        str(manifest.get("generation_id", "unknown")),
    )
    manifest = bundle["manifest"]
    ticker_names = manifest.get("ticker_names", {})
    plans = bundle["plans"]
    metrics = bundle["metrics"]
    reliability = bundle["reliability"]

    st.markdown("""
    <div class="hero"><h1>Memory Stock Predict</h1>
    <p>로컬 워크스테이션 계산 결과를 읽는 경량 Streamlit Cloud 대시보드</p></div>
    """, unsafe_allow_html=True)

    generated = pd.to_datetime(manifest.get("generated_at_utc"), utc=True, errors="coerce")
    age_hours = ((pd.Timestamp.now(tz="UTC") - generated).total_seconds() / 3600
                 if pd.notna(generated) else np.nan)
    if math.isfinite(age_hours) and age_hours > 24:
        st.warning(f"마지막 게시 후 {age_hours:.1f}시간이 지났습니다. 로컬 계산기를 확인하세요.")

    with st.sidebar:
        st.header("게시 상태")
        st.metric("기준일", str(manifest.get("latest_market_date", "-"))[:10])
        st.metric("로컬 계산 시각", generated.tz_convert("Asia/Seoul").strftime("%m-%d %H:%M")
                  if pd.notna(generated) else "-")
        st.metric("OOS 예측", f"{int(manifest.get('oos_rows', 0)):,}건")
        st.caption(
            f"{str(manifest.get('period', period)).upper()} 학습 · "
            f"{manifest.get('horizon', horizon)}거래일 전망 · "
            f"엔진 v{manifest.get('engine_version', '-')}"
        )
        st.divider()
        st.caption("이 앱은 학습·외부 시세 요청을 하지 않습니다. Git 브랜치의 마지막 "
                   "검증 완료 결과만 읽습니다.")

    available = [ticker for ticker in ticker_names if ticker in plans]
    best = max(available, key=lambda x: finite(plans[x].get("decision_score"), -np.inf)) \
        if available else None
    worst = min(available, key=lambda x: finite(plans[x].get("decision_score"), np.inf)) \
        if available else None
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("모델 신뢰도", f"{reliability.get('emoji', '⚪')} "
              f"{reliability.get('grade', '검증 전')}")
    h2.metric("상대 최강", ticker_names.get(best, "-") if best else "-",
              f"실행점수 {finite(plans[best].get('decision_score')):.0f}" if best else None)
    h3.metric("상대 최약", ticker_names.get(worst, "-") if worst else "-",
              f"실행점수 {finite(plans[worst].get('decision_score')):.0f}" if worst else None)
    h4.metric("ROC-AUC", f"{finite(metrics.get('auc')):.3f}"
              if math.isfinite(finite(metrics.get("auc"))) else "-")

    tab_today, tab_chart, tab_portfolio, tab_validation, tab_spot, tab_model = st.tabs([
        "① 오늘의 결론", "② 기술적 차트", "③ 내 포트폴리오",
        "④ 검증·백테스트", "⑤ 현물가", "⑥ 모델",
    ])

    with tab_today:
        st.subheader("오늘 무엇을 할 것인가")
        st.info(f"{reliability.get('emoji', '⚪')} {reliability.get('advice', '-')}")
        board = bundle["board"].copy()
        if board.empty:
            st.warning("게시된 의사결정 데이터가 없습니다.")
        else:
            for col in ("상승확률", "유사점수_실제상승률", "예상수익률"):
                if col in board:
                    board[col] = pd.to_numeric(board[col], errors="coerce").map(
                        lambda x: "-" if pd.isna(x) else f"{x:.1%}")
            for col in ("현재가", "매수기준", "목표가", "예상하단", "예상상단", "손절가"):
                if col in board:
                    board[col] = [fmt_price(v, c) for v, c in zip(board[col], board["통화"])]
            show_cols = [c for c in [
                "종목", "행동", "상승확률", "실행점수", "유사점수_실제상승률",
                "유사점수_표본수", "현재가", "매수기준", "목표가", "손절가", "핵심근거"
            ] if c in board]
            st.dataframe(board[show_cols], use_container_width=True, hide_index=True)
        if available:
            selected = st.selectbox("상세 종목", available,
                                    format_func=lambda x: ticker_names.get(x, x))
            plan = plans[selected]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("현재 행동", f"{plan.get('emoji', '')} {plan.get('label', '-')}")
            c2.metric("보정 상승확률", f"{finite(plan.get('score')):.1f}%")
            c3.metric("기대수익률", fmt_pct(plan.get("er")))
            atr_risk = finite(plan.get("atr")) / finite(plan.get("price")) \
                if finite(plan.get("price"), 0) > 0 else np.nan
            c4.metric("ATR 위험폭", fmt_pct(atr_risk))
            st.plotly_chart(ladder_figure(plan), use_container_width=True)

    with tab_chart:
        st.subheader("가격·기술적 지표·과거 OOS 확률")
        price_choices = [t for t in ticker_names if t in bundle["prices"]]
        if not price_choices:
            st.info("게시된 가격 데이터가 없습니다.")
        else:
            selected = st.selectbox("차트 종목", price_choices, key="chart_ticker",
                                    format_func=lambda x: ticker_names.get(x, x))
            history = bundle["oos"]
            score_history = history[history["ticker"] == selected] \
                if not history.empty and "ticker" in history else pd.DataFrame()
            st.plotly_chart(technical_figure(bundle["prices"][selected], score_history),
                            use_container_width=True)

    with tab_portfolio:
        st.subheader("내 포트폴리오")
        st.caption("입력값은 현재 브라우저 세션에만 있으며 GitHub에 저장되지 않습니다.")
        portfolio_view(manifest, plans, ticker_names)

    with tab_validation:
        st.subheader("완전 아웃오브샘플 검증")
        if not metrics:
            st.info("검증 결과가 없습니다.")
        else:
            lo_hi = metrics.get("accuracy_ci") or [np.nan, np.nan]
            v1, v2, v3, v4, v5 = st.columns(5)
            v1.metric("방향 적중률", f"{finite(metrics.get('overall')):.1%}",
                      f"95% CI {finite(lo_hi[0]):.1%}~{finite(lo_hi[1]):.1%}")
            v2.metric("베이스라인 대비",
                      f"{finite(metrics.get('overall'))-finite(metrics.get('naive')):+.1%}p")
            v3.metric("ROC-AUC", f"{finite(metrics.get('auc')):.3f}")
            v4.metric("Brier skill", f"{finite(metrics.get('brier_skill')):+.1%}")
            v5.metric("보정오차 ECE", f"{finite(metrics.get('ece')):.1%}")
            left, right = st.columns(2)
            calibration = bundle["calibration"]
            with left:
                if not calibration.empty:
                    fig = go.Figure()
                    fig.add_scatter(x=[0, 1], y=[0, 1], name="완전 보정",
                                    line=dict(dash="dash", color="gray"))
                    fig.add_scatter(x=calibration["예측확률"], y=calibration["실제상승률"],
                                    name="모델", mode="lines+markers")
                    fig.update_layout(title="확률 보정도", height=340,
                                      xaxis_tickformat=".0%", yaxis_tickformat=".0%")
                    st.plotly_chart(fig, use_container_width=True)
            with right:
                per_ticker = bundle["per_ticker"].copy()
                if not per_ticker.empty:
                    per_ticker["ticker"] = per_ticker["ticker"].map(
                        lambda x: ticker_names.get(x, x))
                    st.dataframe(per_ticker, use_container_width=True, hide_index=True)
            rolling = bundle["rolling"]
            if not rolling.empty:
                fig = go.Figure()
                fig.add_scatter(x=rolling["date"], y=rolling["모델 적중률"], name="모델")
                fig.add_scatter(x=rolling["date"], y=rolling["무조건 상승 적중률"],
                                name="무조건 상승", line=dict(dash="dot"))
                fig.update_layout(title="최근 250개 예측 이동 적중률", height=330,
                                  yaxis_tickformat=".0%")
                st.plotly_chart(fig, use_container_width=True)
            oos = bundle["oos"]
            if not oos.empty:
                bt_ticker = st.selectbox(
                    "백테스트 종목", sorted(oos["ticker"].dropna().unique()),
                    format_func=lambda x: ticker_names.get(x, x))
                curve, stats = equity_curve(
                    oos, bt_ticker, int(manifest["horizon"]),
                    int(manifest["threshold"]), int(manifest["cost_bps"]))
                if curve is not None:
                    b1, b2, b3, b4 = st.columns(4)
                    b1.metric("누적수익", fmt_pct(stats["strategy"]["total"]))
                    b2.metric("CAGR", fmt_pct(stats["strategy"]["cagr"]))
                    b3.metric("MDD", fmt_pct(stats["strategy"]["mdd"]))
                    b4.metric("Sharpe", f"{finite(stats['strategy']['sharpe']):.2f}")
                    fig = go.Figure()
                    fig.add_scatter(x=curve["date"], y=curve["시그널 추종"], name="시그널")
                    fig.add_scatter(x=curve["date"], y=curve["단순 보유"], name="보유",
                                    line=dict(dash="dot"))
                    fig.update_layout(height=360, yaxis_title="누적 배수")
                    st.plotly_chart(fig, use_container_width=True)

    with tab_spot:
        st.subheader("DRAM·NAND 현물가")
        status = bundle["spot_status"]
        st.caption(status.get("message", "마지막 로컬 계산 결과"))
        spot = bundle["spot"]
        if spot.empty:
            st.info("게시된 현물가가 없습니다.")
        else:
            product_series = []
            for col in [c for c in spot.columns if c != "날짜"]:
                series = pd.to_numeric(spot[col], errors="coerce")
                valid = pd.DataFrame({"date": spot["날짜"], "value": series}).dropna()
                if len(valid) >= 2:
                    product_series.append((col, valid))
            if product_series:
                columns = st.columns(len(product_series), gap="small")
                for box, (col, values) in zip(columns, product_series):
                    label = SPOT_NAMES.get(col, col.replace("_", " "))
                    latest = values.iloc[-1]
                    box.metric(label, f"US${latest['value']:,.3f}",
                               pd.Timestamp(latest["date"]).strftime("%Y-%m-%d"))
                    fig = go.Figure(go.Scatter(
                        x=values["date"], y=values["value"], mode="lines+markers",
                        line=dict(width=2.2, color="#2563eb"), marker=dict(size=5)))
                    fig.update_layout(title=label, height=310, showlegend=False,
                                      margin=dict(t=45, b=25, l=40, r=10),
                                      yaxis_title="USD", hovermode="x unified")
                    box.plotly_chart(fig, use_container_width=True)

    with tab_model:
        st.subheader("최종 앙상블과 변수 중요도")
        model_info = bundle["model_info"]
        weights = model_info.get("weights", {})
        losses = model_info.get("validation_losses", {})
        if weights:
            model_table = pd.DataFrame([
                {"모델": MODEL_NAMES.get(name, name), "가중치": weight,
                 "검증 Log-loss": losses.get(name)}
                for name, weight in weights.items()
            ]).sort_values("가중치", ascending=False)
            st.dataframe(model_table, use_container_width=True, hide_index=True)
            st.caption(f"확률 보정 표본 {int(model_info.get('calibration_rows', 0)):,}행")
        importance = bundle["importance"].head(20).sort_values("importance")
        if not importance.empty:
            fig = go.Figure(go.Bar(
                x=importance["importance"], y=importance["feature"], orientation="h"))
            fig.update_layout(height=560, title="순열 변수 중요도")
            st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"게시 ID {manifest.get('generation_id')} · 피처 {manifest.get('feature_count')}개 · "
            f"워크포워드 {manifest.get('wf_step')}거래일 간격"
        )

    st.divider()
    st.caption("본 대시보드는 마지막 로컬 계산 시점의 통계적 추정치이며 수익을 보장하지 않습니다.")


if __name__ == "__main__":
    main()