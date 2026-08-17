# -*- coding: utf-8 -*-
"""
메모리 반도체 의사결정 대시보드 (memory_dashboard.py)
=====================================================
대상 종목 : SK하이닉스 · 삼성전자 · 샌디스크 · 마이크론 · 키옥시아

하는 일
  1) RSI·MACD·볼린저밴드·스토캐스틱·ADX·CCI·MFI 등 기술적 지표와
     관련 지표(SOX, NVDA, WDC, 환율, 금리 등)로 피처를 만들고
  2) 국가별 장 마감 시차를 보수적으로 반영하고, 시간순 홀드아웃으로 보정한
     향후 N거래일 "상승 확률"을 종목별 0~100 점수로 표시하며
  3) 점수를 5단계 행동(지금 매수/조정 시 매수/관망/반등 시 축소/매도)으로 번역하고
     매수 추천가 · 목표가(범위) · 손절가를 변동폭(ATR) 기반 규칙으로 자동 계산하며
  4) 다음 거래 가능 가격·거래비용을 반영한 워크포워드 백테스트로 이 모델이
     과거에 얼마나 맞았는지(방향·확률보정·수익·낙폭)를 쉬운 말로 보여주고
  5) 보유 종목(티커/수량/평단)을 입력하면 현재 자산 · 예상 자산 · 지금 행동을 계산하며
  6) TrendForce 공개 페이지에서 DRAM/NAND 현물가를 자동 수집하고,
     공개 주간 업데이트 기사로 초기 이력을 백필한 뒤 모델 피처로 반영한다.
     수집 장애 시 마지막 정상 캐시를 사용하며, 화면에서 수동 보정도 가능하다.
     포트폴리오와 수동 보정값은 브라우저 세션별로 격리된다.

실행
  pip install streamlit yfinance scikit-learn plotly pandas numpy
  streamlit run memory_dashboard.py --server.address 0.0.0.0

공개 배포
  이 파일과 함께 제공되는 README.md·requirements.txt·Dockerfile을 참고한다.
  기본값은 세션 저장이다. 로컬에서만 CSV 자동 저장을 원하면 환경변수
  MEMORY_DASH_PERSIST_LOCAL=1 을 설정한다.

주의
  - 점수는 과거 패턴 기반 '확률 추정치'다. 보장된 예측이 아니며,
    반드시 정확도 패널에서 베이스라인(무조건 상승 예측) 대비 우위를 확인하고 쓸 것.
  - 예측은 확률적 추정이며 목표가·구간은 보장이 아니다.
  - 공개 URL에는 인증이 없으므로 민감한 계좌번호나 개인정보를 입력하지 않는다.
"""

from __future__ import annotations

import concurrent.futures
import html
import io
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin

warnings.filterwarnings("ignore", category=FutureWarning)

# ──────────────────────────────────────────────────────────────
# 설정 (종목을 바꾸고 싶으면 여기만 수정)
# ──────────────────────────────────────────────────────────────
TICKERS = {
    "000660.KS": "SK하이닉스",
    "005930.KS": "삼성전자",
    "SNDK":      "샌디스크",
    "RAM":    "DRAM ETF X2",
    "MU":        "마이크론",
    "285A.T":    "키옥시아",
}

MACRO = {
    "^SOX":  "필라델피아 반도체지수",
    "NVDA":  "엔비디아 (HBM 수요 프록시)",
    "WDC":   "웨스턴디지털 (스토리지 사이클 프록시)",
    "KRW=X": "달러/원",
    "JPY=X": "달러/엔",
    "^TNX":  "미 10년물 금리",
}

DEFAULT_HORIZON = 20        # 예측 지평 (거래일)
DEFAULT_PERIOD = "10y"      # 다운로드 기간 (UI에서 1y·3y 포함 선택)
WF_STEP = 21                # 워크포워드 재학습 주기 (거래일)
MIN_TRAIN_DAYS = 500        # 첫 예측 전 최소 학습 구간 (합산 달력 기준)
MIN_TRAIN_ROWS = 300        # 최소 학습 표본 수
CALIBRATION_DAYS = 252       # 시간순 확률 보정 구간
MIN_CALIBRATION_ROWS = 150
RECENCY_HALF_LIFE_DAYS = 756 # 최근 3년(약 756거래일)에 가중치 절반
MACRO_RELEASE_LAG = 1        # 국가별 마감 시차 누수 방지용 보수적 1거래일 지연
DEFAULT_COST_BPS = 25        # 왕복 수수료+슬리피지 기본값 0.25%
VERSION = "4.1"
ENTRY_ATR = 1.0             # 조정 시 매수가 = 현재가 − 1.0 × ATR(14)
STOP_ATR = 2.0              # 손절가 = 기준가 − 2.0 × ATR(14)
TRIM_ATR = 0.5              # 반등 시 축소가 = 현재가 + 0.5 × ATR(14)
SPOT_CSV = "spot_prices.csv"
AUTO_SPOT_CACHE = os.getenv("MEMORY_AUTO_SPOT_CACHE", "auto_spot_prices.csv")
AUTO_SPOT_TTL_HOURS = 6
AUTO_SPOT_NEWS_PAGES = 12
TREND_DRAM_URL = "https://www.trendforce.com/price/dram/dram_spot"
TREND_NAND_URL = "https://www.trendforce.com/price/flash/flash_spot"
TREND_NEWS_TAG = "https://www.trendforce.com/news/tag/ddr4/"
PORTFOLIO_CSV = "portfolio.csv"
SPOT_DEFAULT_COLS = [
    "DRAM_DDR5_16Gb", "DRAM_DDR4_8Gb", "NAND_TLC_512Gb"
]  # TrendForce Session Average, USD
PORT_COLS = ["티커", "수량", "평단", "모델연동", "배수"]

# 짧은 기간을 단순히 UI에만 추가하면 MIN_TRAIN_DAYS=500 때문에 1년 모델은
# 단 한 번도 학습되지 않는다. 기간별로 첫 학습·확률 보정·최근가중 반감기를
# 함께 줄여 실제로 작동하게 한다. 표본 수는 여러 종목을 풀링해 확보한다.
PERIOD_PROFILES = {
    "1y":  {"min_train_days": 90,  "calibration_days": 42,
            "min_calibration_rows": 80,  "recency_half_life": 126,
            "label": "1년 · 최근 레짐 중심"},
    "3y":  {"min_train_days": 189, "calibration_days": 126,
            "min_calibration_rows": 120, "recency_half_life": 378,
            "label": "3년 · 최근 사이클"},
    "5y":  {"min_train_days": 315, "calibration_days": 189,
            "min_calibration_rows": 150, "recency_half_life": 504,
            "label": "5년 · 중기 사이클"},
    "10y": {"min_train_days": 500, "calibration_days": 252,
            "min_calibration_rows": 150, "recency_half_life": 756,
            "label": "10년 · 권장"},
    "15y": {"min_train_days": 500, "calibration_days": 252,
            "min_calibration_rows": 150, "recency_half_life": 756,
            "label": "15년 · 장기 스트레스"},
}

META_COLS = ("date", "ticker", "entry_px", "exit_px", "fwd_ret", "y",
             "label_known_date")

FEATURE_LABELS = {
    "ret1":        "1일 수익률",
    "ret5":        "5일 수익률",
    "ret10":       "10일 수익률",
    "ret20":       "20일 수익률",
    "ret60":       "60일 수익률",
    "mom_accel":   "모멘텀 가속도(5일-20일)",
    "ma20_gap":    "20일선 이격",
    "ma60_gap":    "60일선 이격",
    "ma120_gap":   "120일선 이격",
    "ma200_gap":   "200일선 이격",
    "ema12_gap":   "EMA(12) 이격",
    "ema26_gap":   "EMA(26) 이격",
    "ema_trend":   "EMA 12·26 추세차",
    "rsi7":        "RSI(7)",
    "rsi14":       "RSI(14)",
    "rsi28":       "RSI(28)",
    "macd":        "MACD/주가",
    "macd_signal": "MACD 시그널/주가",
    "macd_hist":   "MACD 히스토그램/주가",
    "bb_pctb":     "볼린저밴드 %B",
    "bb_width":    "볼린저밴드 폭",
    "stoch_k":     "스토캐스틱 %K",
    "stoch_d":     "스토캐스틱 %D",
    "williams_r":  "Williams %R",
    "adx14":       "ADX(14)",
    "plus_di14":   "+DI(14)",
    "minus_di14":  "-DI(14)",
    "di_spread":   "DI 방향 차이",
    "cci20":       "CCI(20)",
    "mfi14":       "MFI(14)",
    "obv_mom20":   "OBV 20일 모멘텀",
    "range_pos20": "20일 가격범위 내 위치",
    "range_pos60": "60일 가격범위 내 위치",
    "gap1":        "전일 종가 대비 시가 갭",
    "intraday_ret": "장중 수익률",
    "range_pct":   "일중 고저 변동폭",
    "positive_days20": "최근 20일 상승일 비율",
    "skew20":      "20일 수익률 왜도",
    "vol_ratio":   "단기/중기 변동성 비율",
    "vol20":       "20일 변동성(연율)",
    "vol60":       "60일 변동성(연율)",
    "down_vol20":  "20일 하방 변동성",
    "drawdown60":  "60일 고점 대비 낙폭",
    "atr_pct14":   "ATR(14)/주가",
    "volu_ratio":  "거래량 추세(5일/60일)",
    "rel_sox20":   "SOX 대비 20일 상대강도",
    "peer_rel20":  "동종 메모리주 대비 20일 초과수익률",
    "peer_rel60":  "동종 메모리주 대비 60일 초과수익률",
    "peer_rel5":   "동종 메모리주 대비 5일 초과수익률",
    "sox_ret20":   "SOX 20일 수익률",
    "sox_ret60":   "SOX 60일 수익률",
    "sox_ma60_gap":"SOX 60일선 이격",
    "sox_rsi14":   "SOX RSI(14)",
    "sox_macd_hist":"SOX MACD 히스토그램",
    "sox_vol20":   "SOX 20일 변동성",
    "nvda_ret20":  "NVDA 20일 수익률",
    "wdc_ret20":   "WDC 20일 수익률",
    "krw_chg20":   "달러/원 20일 변화",
    "jpy_chg20":   "달러/엔 20일 변화",
    "tnx_chg20":   "미 10년물 20일 변화",
}


# ──────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────
def feat_label(c: str) -> str:
    if c in FEATURE_LABELS:
        return FEATURE_LABELS[c]
    if c.startswith("spot_") and c.endswith("_chg20"):
        return f"현물가 {c[5:-6]} 20일 변화"
    if c.startswith("spot_") and c.endswith("_chg60"):
        return f"현물가 {c[5:-6]} 60일 변화"
    return c


def feat_display_value(c: str, v: float) -> float:
    """모델 내부 0~1 정규화 지표를 사람이 익숙한 0~100 눈금으로 복원."""
    pct_oscillators = {"rsi7", "rsi14", "rsi28", "stoch_k", "stoch_d",
                       "adx14", "plus_di14", "minus_di14", "mfi14",
                       "bb_pctb", "positive_days20", "sox_rsi14"}
    return float(v) * 100.0 if c in pct_oscillators else float(v)


def fmt_table(df: pd.DataFrame, fmts: dict) -> pd.DataFrame:
    """st.dataframe(df.style...)는 jinja2가 필요해 별도 설치가 든다.
    대신 표시용 문자열 DataFrame을 직접 만들어 의존성 없이 같은 효과를 낸다."""
    out = df.copy()
    for col, spec in fmts.items():
        if col not in out.columns:
            continue
        out[col] = out[col].map(
            lambda v, spec=spec: "-" if pd.isna(v) else spec.format(v))
    return out


def file_sig(path: str) -> str:
    """파일 변경 감지용 서명 (Streamlit 캐시 무효화 키)."""
    try:
        s = os.stat(path)
        return f"{s.st_mtime_ns}-{s.st_size}"
    except OSError:
        return "none"


def local_persistence_enabled() -> bool:
    """공개 배포에서는 방문자 입력을 서버 공용 파일에 쓰지 않는다."""
    return os.getenv("MEMORY_DASH_PERSIST_LOCAL", "0").strip().lower() \
        in {"1", "true", "yes", "on"}


def pchg(s: pd.Series, n: int) -> pd.Series:
    """pandas 버전에 따라 pct_change 동작이 달라서 수동 계산."""
    return s / s.shift(n) - 1.0


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    diff = close.diff()
    up = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    """0 나눗셈·무한대를 결측으로 돌려 트리 입력을 안정화한다."""
    return (num / den.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=max(3, n // 2)).mean()


def adx_components(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, ...]:
    """Wilder 방식 ADX, +DI, -DI. OHLC가 없으면 결측 시리즈를 반환한다."""
    idx = df.index
    if not {"High", "Low", "Close"} <= set(df.columns):
        empty = pd.Series(np.nan, index=idx, dtype=float)
        return empty, empty.copy(), empty.copy()
    h, l, c = df["High"], df["Low"], df["Close"]
    up_move, down_move = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0),
                                 up_move, 0.0), index=idx)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0),
                                  down_move, 0.0), index=idx)
    prev = c.shift()
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * safe_div(plus_dm.ewm(alpha=1 / n, adjust=False,
                                         min_periods=n).mean(), atr)
    minus_di = 100 * safe_div(minus_dm.ewm(alpha=1 / n, adjust=False,
                                           min_periods=n).mean(), atr)
    dx = 100 * safe_div((plus_di - minus_di).abs(), plus_di + minus_di)
    adx = dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return adx, plus_di, minus_di


def money_flow_index(df: pd.DataFrame, n: int = 14) -> pd.Series:
    if not {"High", "Low", "Close", "Volume"} <= set(df.columns):
        return pd.Series(np.nan, index=df.index, dtype=float)
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    flow = tp * df["Volume"]
    pos = flow.where(tp.diff() > 0, 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(tp.diff() < 0, 0.0).rolling(n, min_periods=n).sum()
    ratio = safe_div(pos, neg)
    return 100 - 100 / (1 + ratio)


def _naive_index(df: pd.DataFrame) -> pd.DataFrame:
    """시장별 타임존이 섞이면 비교가 깨지므로 전부 naive datetime으로 통일."""
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df = df.copy()
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


# ──────────────────────────────────────────────────────────────
# 데이터 수집 (yfinance는 실행 시점에만 import)
# ──────────────────────────────────────────────────────────────
def _retry(fn, tries: int = 2):
    """일시적 네트워크 오류 대비 단순 재시도."""
    import time
    last = None
    for k in range(tries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (k + 1))
    raise last


def download_prices(period: str = DEFAULT_PERIOD) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    symbols = list(TICKERS) + list(MACRO)
    # yfinance는 3y·15y 문자열을 공식 period로 받지 않는다. 3y는 5y를 받아
    # 절단해 불필요한 max 다운로드를 피하고, 15y만 max를 받아 절단한다.
    yf_period = "5y" if period == "3y" else (
        period if period in {"1y", "2y", "5y", "10y", "ytd", "max"} else "max")
    raw = _retry(lambda: yf.download(
        symbols, period=yf_period, auto_adjust=True,
        group_by="ticker", progress=False, threads=True,
    ))
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw[sym]
            else:  # 심볼 1개만 성공한 경우 등
                df = raw
            df = df.dropna(how="all")
            if df.empty or "Close" not in df.columns:
                continue
            keep = df[["Close"]].copy()
            for c in ("Open", "High", "Low", "Volume"):
                keep[c] = df[c] if c in df.columns else np.nan
            keep = _naive_index(keep.dropna(subset=["Close"]))
            if period.endswith("y") and period[:-1].isdigit():
                cutoff = pd.Timestamp.today().normalize() - pd.DateOffset(
                    years=int(period[:-1]))
                keep = keep[keep.index >= cutoff]
            out[sym] = keep
        except Exception:
            continue
    return out


# ──────────────────────────────
# DRAM/NAND 현물가 자동 수집
# ──────────────────────────────
_AUTO_SPOT_LOCK = threading.Lock()


def _http_text(url: str, timeout: int = 20) -> str:
    """공개 페이지만 읽는 가벼운 HTTP 클라이언트.

    외부 requests/lxml 의존성을 추가하지 않아 기존 배포환경에서도 작동한다.
    로그인·유료 다운로드 URL은 접근하지 않는다.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; MemoryStockDashboard/4.1; "
                "+https://www.trendforce.com/)"
            ),
            "Accept": "text/html,application/xhtml+xml,text/csv;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(4_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _plain_html(fragment: str) -> str:
    fragment = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>", " ", fragment,
        flags=re.IGNORECASE | re.DOTALL,
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def _number(value) -> float | None:
    text = str(value).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _normalise_spot_data(df: pd.DataFrame | None) -> pd.DataFrame:
    """날짜+숫자 컬럼 형태로 정규화한다. 사용자 커스텀 컬럼도 보존."""
    if df is None or df.empty:
        return pd.DataFrame(
            {"날짜": pd.Series(dtype="datetime64[ns]"),
             **{c: pd.Series(dtype=float) for c in SPOT_DEFAULT_COLS}}
        )
    out = df.copy()
    if "날짜" not in out.columns:
        if isinstance(out.index, pd.DatetimeIndex):
            out = out.reset_index()
        out = out.rename(columns={out.columns[0]: "날짜"})
    out["날짜"] = pd.to_datetime(out["날짜"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["날짜"])
    for col in [c for c in out.columns if c != "날짜"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return (out.sort_values("날짜").groupby("날짜", as_index=False)
               .last().reset_index(drop=True))


def merge_spot_data(*frames: pd.DataFrame | None) -> pd.DataFrame:
    """앞에서 뒤 순서로 병합하며, 뒤의 프레임이 같은 날짜·컬럼을 덮어쓴다."""
    merged: pd.DataFrame | None = None
    for frame in frames:
        clean = _normalise_spot_data(frame)
        if clean.empty:
            continue
        indexed = clean.set_index("날짜")
        merged = indexed if merged is None else indexed.combine_first(merged)
    if merged is None:
        return _normalise_spot_data(None)
    merged.index.name = "날짜"
    return _normalise_spot_data(merged.reset_index())


def _quote_from_table(page_html: str, item_pattern: str) -> dict | None:
    """TrendForce HTML 표에서 해당 품목의 Session Average를 추출."""
    table_re = re.compile(r"<table\b[^>]*>(.*?)</table>", re.I | re.S)
    row_re = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
    cell_re = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.I | re.S)
    for table_match in table_re.finditer(page_html):
        rows = []
        for row_html in row_re.findall(table_match.group(1)):
            cells = [_plain_html(x) for x in cell_re.findall(row_html)]
            if cells:
                rows.append(cells)
        if len(rows) < 2 or "Item" not in rows[0]:
            continue
        header = rows[0]
        for row in rows[1:]:
            if not row or not re.fullmatch(item_pattern, row[0], flags=re.I):
                continue
            mapped = {header[i]: row[i] for i in range(min(len(header), len(row)))}
            value = _number(mapped.get("Session Average", mapped.get("Average")))
            if value is None or value <= 0:
                continue
            before = _plain_html(page_html[max(0, table_match.start() - 30000):
                                           table_match.start()])
            dates = re.findall(
                r"Last\s*Update\s*:?[ ]*(\d{4}-\d{2}-\d{2})", before,
                flags=re.I,
            )
            if not dates:
                continue
            change_text = str(mapped.get(
                "Session Change", mapped.get("Average Change", "")))
            change = _number(change_text)
            if change is not None and "▼" in change_text and change > 0:
                change = -change
            return {
                "date": pd.Timestamp(dates[-1]).normalize(),
                "value": float(value),
                "change_pct": change,
                "item": row[0],
            }
    return None


def _current_trendforce_spot() -> tuple[pd.DataFrame, dict, list[str]]:
    """TrendForce 공개 표의 최신 현물가와 직전 세션 가격을 읽는다."""
    pages: dict[str, str] = {}
    errors: list[str] = []

    def get_page(name_url):
        name, url = name_url
        return name, _retry(lambda: _http_text(url), tries=1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(get_page, item): item[0]
            for item in (("dram", TREND_DRAM_URL), ("nand", TREND_NAND_URL))
        }
        for future, name in futures.items():
            try:
                key, value = future.result()
                pages[key] = value
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name} 페이지: {type(exc).__name__}")

    specs = (
        ("DRAM_DDR5_16Gb", "dram", r"DDR5\s+16Gb\s+\(2Gx8\)\s+4800/5600"),
        ("DRAM_DDR4_8Gb", "dram", r"DDR4\s+8Gb\s+\(1Gx8\)\s+3200"),
        ("NAND_TLC_512Gb", "nand", r"512Gb\s+TLC"),
    )
    records: list[dict] = []
    updates: dict[str, str] = {}
    for col, page_key, pattern in specs:
        quote = _quote_from_table(pages.get(page_key, ""), pattern)
        if quote is None:
            errors.append(f"{col} 표 파싱 실패")
            continue
        date = quote["date"]
        records.append({"날짜": date, col: quote["value"]})
        updates[col] = date.strftime("%Y-%m-%d")
        chg = quote.get("change_pct")
        if chg is not None and -95 < chg < 500:
            previous = quote["value"] / (1.0 + chg / 100.0)
            records.append({"날짜": date - pd.offsets.BDay(1), col: previous})
    return _normalise_spot_data(pd.DataFrame(records)), updates, errors


def _trendforce_article_urls(page_count: int) -> tuple[list[str], list[str]]:
    """DRAM 태그 페이지의 공개 Memory Spot Price Update 기사 URL."""
    page_urls = [TREND_NEWS_TAG] + [
        urllib.parse.urljoin(TREND_NEWS_TAG, f"page/{page}/")
        for page in range(2, page_count + 1)
    ]
    errors: list[str] = []

    def parse_page(url: str) -> list[str]:
        page_html = _retry(lambda: _http_text(url), tries=1)
        found: list[str] = []
        for href in re.findall(r'''href=["']([^"']+)["']''', page_html, re.I):
            absolute = urllib.parse.urljoin(url, html.unescape(href))
            absolute = absolute.split("#", 1)[0].split("?", 1)[0]
            if "/news/20" not in absolute:
                continue
            if not any(token in absolute for token in (
                "spot-price-update", "spot-market-update", "weekly-price-update"
            )):
                continue
            if absolute not in found:
                found.append(absolute)
        return found

    collected: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(parse_page, url): url for url in page_urls}
        for future, url in futures.items():
            try:
                collected.extend(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"기사 목록 {url.rstrip('/').rsplit('/', 1)[-1]}: "
                    f"{type(exc).__name__}"
                )
    return list(dict.fromkeys(collected)), errors


def _spot_from_article(url: str) -> dict | None:
    """공개 주간 기사 본문의 명시적인 USD 현물가만 추출."""
    match = re.search(r"/news/(\d{4})/(\d{2})/(\d{2})/", url)
    if not match:
        return None
    page_html = _retry(lambda: _http_text(url), tries=1)
    paragraphs = [
        _plain_html(block)
        for block in re.findall(r"<p\b[^>]*>(.*?)</p>", page_html, re.I | re.S)
    ]
    dram_values: list[float] = []
    nand_values: list[float] = []
    for paragraph in paragraphs:
        usd_values = [
            float(x) for x in re.findall(
                r"US\$+\s*([0-9]+(?:\.[0-9]+)?)", paragraph, re.I
            )
        ]
        if not usd_values:
            continue
        is_mainstream_dram = bool(
            re.search(r"DDR4\s*(?:1Gx8|8Gb).*?3200", paragraph, re.I)
            or re.search(r"average spot price of mainstream chips", paragraph, re.I)
        )
        if is_mainstream_dram:
            dram_values.append(usd_values[-1])
        if re.search(r"512Gb\s*TLC", paragraph, re.I):
            nand_values.append(usd_values[-1])
    record: dict = {
        "날짜": pd.Timestamp(
            year=int(match.group(1)), month=int(match.group(2)), day=int(match.group(3))
        )
    }
    if dram_values and 0.05 < dram_values[-1] < 500:
        record["DRAM_DDR4_8Gb"] = dram_values[-1]
    if nand_values and 0.05 < nand_values[-1] < 500:
        record["NAND_TLC_512Gb"] = nand_values[-1]
    return record if len(record) > 1 else None


def _trendforce_public_history() -> tuple[pd.DataFrame, list[str]]:
    """공개 주간 기사로 초기 학습용 이력을 백필(유료 이력 우회 안 함)."""
    try:
        page_count = int(os.getenv("MEMORY_SPOT_NEWS_PAGES", AUTO_SPOT_NEWS_PAGES))
    except ValueError:
        page_count = AUTO_SPOT_NEWS_PAGES
    page_count = min(20, max(1, page_count))
    urls, errors = _trendforce_article_urls(page_count)
    records: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_spot_from_article, url): url for url in urls[:80]}
        for future, url in futures.items():
            try:
                record = future.result()
                if record:
                    records.append(record)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"기사 {url.rstrip('/').rsplit('/', 1)[-1][:32]}: "
                    f"{type(exc).__name__}"
                )
    return _normalise_spot_data(pd.DataFrame(records)), errors


def _read_auto_spot_cache(path: str = AUTO_SPOT_CACHE) -> pd.DataFrame:
    try:
        if os.path.exists(path):
            return _normalise_spot_data(pd.read_csv(path))
    except Exception:
        pass
    return _normalise_spot_data(None)


def _save_auto_spot_cache(df: pd.DataFrame, path: str = AUTO_SPOT_CACHE) -> bool:
    """공개 시세 캐시는 개인정보가 아니므로 세션 간 공유해 요청을 최소화."""
    if df.empty:
        return False
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with _AUTO_SPOT_LOCK:
            _normalise_spot_data(df).to_csv(tmp, index=False)
            os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _remote_spot_csv() -> tuple[pd.DataFrame, str | None]:
    """선택: 라이선스 내역/Google Sheets 등 게시 CSV URL을 자동 병합."""
    url = os.getenv("MEMORY_SPOT_CSV_URL", "").strip()
    if not url:
        return _normalise_spot_data(None), None
    try:
        csv_text = _retry(lambda: _http_text(url), tries=1)
        return _normalise_spot_data(pd.read_csv(io.StringIO(csv_text))), None
    except Exception as exc:  # noqa: BLE001
        return _normalise_spot_data(None), f"원격 CSV: {type(exc).__name__}"


def _history_is_sufficient(df: pd.DataFrame) -> bool:
    return all(
        col in df.columns and pd.to_numeric(df[col], errors="coerce").notna().sum() >= 12
        for col in ("DRAM_DDR4_8Gb", "NAND_TLC_512Gb")
    )


def fetch_auto_spot_prices(force: bool = False) -> tuple[pd.DataFrame, dict]:
    """
    최신값 → 공개 기사 백필 → 캐시 폴백을 하나의 DataFrame으로 만든다.

    MEMORY_SPOT_CSV_URL을 설정하면 사용자가 정상적으로 구매/공유받은
    이력 CSV가 가장 높은 우선순위로 덮어쓴다.
    """
    cache = _read_auto_spot_cache()
    try:
        age_hours = (time.time() - os.path.getmtime(AUTO_SPOT_CACHE)) / 3600
    except OSError:
        age_hours = np.inf
    remote_url_set = bool(os.getenv("MEMORY_SPOT_CSV_URL", "").strip())
    if (not force and not remote_url_set and not cache.empty
            and age_hours <= AUTO_SPOT_TTL_HOURS):
        latest = pd.to_datetime(cache["날짜"]).max()
        return cache, {
            "state": "cached", "rows": len(cache), "latest_date": latest,
            "history_ready": _history_is_sufficient(cache), "errors": [],
            "message": f"{AUTO_SPOT_TTL_HOURS}시간 이내 자동 캐시",
        }

    errors: list[str] = []
    remote, remote_error = _remote_spot_csv()
    if remote_error:
        errors.append(remote_error)
    seed = merge_spot_data(cache, remote)

    history = _normalise_spot_data(None)
    if not _history_is_sufficient(seed):
        history, history_errors = _trendforce_public_history()
        errors.extend(history_errors)

    current = _normalise_spot_data(None)
    updates: dict[str, str] = {}
    try:
        current, updates, current_errors = _current_trendforce_spot()
        errors.extend(current_errors)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"최신 현물가: {type(exc).__name__}")

    merged = merge_spot_data(cache, history, current, remote)
    fetched_any = not current.empty or not history.empty or not remote.empty
    saved = _save_auto_spot_cache(merged) if fetched_any else False

    if not current.empty:
        state = "live"
        message = "TrendForce 공개 현물가 갱신 완료"
    elif not cache.empty:
        state = "stale"
        message = "수집 장애로 마지막 정상 캐시 사용"
    elif not merged.empty:
        state = "partial"
        message = "공개 이력만 부분 수집"
    else:
        state = "failed"
        message = "자동 현물가를 받지 못함"
    latest = pd.to_datetime(merged["날짜"]).max() if not merged.empty else None
    return merged, {
        "state": state, "rows": len(merged), "latest_date": latest,
        "history_ready": _history_is_sufficient(merged),
        "updates": updates, "saved": saved,
        "errors": errors[:8], "message": message,
    }


# ──────────────────────────────────────────────────────────────
# 피처 생성
# ──────────────────────────────────────────────────────────────
def build_peer_features(prices: dict, master: pd.DatetimeIndex) -> pd.DataFrame:
    """같은 메모리 그룹 내 상대강도 — 그날 또래 5종목 평균 대비 초과수익률.

    기존 rel_sox20(광범위 반도체지수 대비 강도)보다 좁고 실전적인 비교다.
    '어느 메모리주가 더 강한가'를 직접 겨냥하는 신호이며, 대시보드의
    '상대 최강/최약' 카드와도 취지가 맞다. 교차시장 시차 누수를 막기 위해
    매크로 피처와 동일하게 1거래일 지연한다."""
    syms = [s for s in TICKERS if s in prices]
    if len(syms) < 2:
        return pd.DataFrame(index=master)
    out = {}
    for h, tag in ((5, "5"), (20, "20"), (60, "60")):
        wide = pd.DataFrame(
            {s: pchg(prices[s]["Close"].reindex(master).ffill(limit=3), h)
             for s in syms}, index=master)
        group_mean = wide.mean(axis=1, skipna=True)  # 그날 살아있는 종목들의 평균
        for s in syms:
            out[f"__peer{tag}__{s}"] = wide[s] - group_mean
    return pd.DataFrame(out, index=master).shift(MACRO_RELEASE_LAG)


def build_macro_features(prices: dict, master: pd.DatetimeIndex) -> pd.DataFrame:
    f = pd.DataFrame(index=master)

    def closes(sym):
        if sym not in prices:
            return None
        return prices[sym]["Close"].reindex(master).ffill(limit=7)

    sox = closes("^SOX")
    if sox is not None:
        f["sox_ret20"] = pchg(sox, 20)
        f["sox_ret60"] = pchg(sox, 60)
        f["sox_ma60_gap"] = sox / sox.rolling(60).mean() - 1
        f["sox_rsi14"] = rsi(sox, 14) / 100.0
        sox_macd = ema(sox, 12) - ema(sox, 26)
        f["sox_macd_hist"] = safe_div(sox_macd - ema(sox_macd, 9), sox)
        f["sox_vol20"] = sox.pct_change(fill_method=None).rolling(20).std() \
            * np.sqrt(252)
    for sym, col in (("NVDA", "nvda_ret20"), ("WDC", "wdc_ret20")):
        c = closes(sym)
        if c is not None:
            f[col] = pchg(c, 20)
    for sym, col in (("KRW=X", "krw_chg20"), ("JPY=X", "jpy_chg20")):
        c = closes(sym)
        if c is not None:
            f[col] = pchg(c, 20)
    tnx = closes("^TNX")
    if tnx is not None:
        f["tnx_chg20"] = tnx.diff(20)
    # 날짜만 정규화한 글로벌 데이터는 한국 장 마감 시점에 아직 확정되지 않은
    # 같은 날짜의 미국 종가를 포함할 수 있다. 모든 외생 변수는 1거래일 지연해
    # 이 교차시장 시차 누수를 보수적으로 제거한다.
    return f.shift(MACRO_RELEASE_LAG)


def load_spot_features(master: pd.DatetimeIndex,
                       spot_data: pd.DataFrame | None = None,
                       path: str = SPOT_CSV):
    """DRAM/NAND 현물가를 20/60일 변화율 피처로 병합.

    주간 공개 자료도 쓸 수 있게 70거래일까지 유지하고, 해당 일자의
    한국장이 닫힌 후 게시될 수 있으므로 1거래일 지연해 누수를 막는다.
    """
    try:
        if spot_data is not None and not spot_data.empty:
            df = _normalise_spot_data(spot_data).set_index("날짜")
        elif local_persistence_enabled() and os.path.exists(path):
            df = _normalise_spot_data(pd.read_csv(path)).set_index("날짜")
        else:
            return None
        df = df.apply(pd.to_numeric, errors="coerce")
        df = (_naive_index(df).reindex(master).ffill(limit=70)
              .shift(MACRO_RELEASE_LAG))
    except Exception:
        return None
    out = pd.DataFrame(index=master)
    for c in df.columns:
        if df[c].notna().sum() < 3:
            continue
        out[f"spot_{c}_chg20"] = pchg(df[c], 20)
        out[f"spot_{c}_chg60"] = pchg(df[c], 60)
    return out if len(out.columns) else None


def build_ticker_features(df: pd.DataFrame) -> pd.DataFrame:
    """가격·추세·모멘텀·변동성·거래량을 다섯 축으로 기술적 피처화.

    절대 가격 대신 비율/오실레이터를 사용해 원화·달러·엔 종목을 한 모델로
    풀링해도 스케일이 섞이지 않게 한다. 모든 지표는 해당 일 종가까지만 사용한다.
    """
    c = df["Close"].astype(float)
    o = df["Open"].astype(float) if "Open" in df.columns else c
    h = df["High"].astype(float) if "High" in df.columns else c
    l = df["Low"].astype(float) if "Low" in df.columns else c
    v = df["Volume"].astype(float) if "Volume" in df.columns else None
    f = pd.DataFrame(index=df.index)
    r = c.pct_change(fill_method=None)

    # 수익률·추세
    f["ret1"] = pchg(c, 1)
    f["ret5"] = pchg(c, 5)
    f["ret10"] = pchg(c, 10)
    f["ret20"] = pchg(c, 20)
    f["ret60"] = pchg(c, 60)
    f["mom_accel"] = f["ret5"] - f["ret20"] / 4.0
    for n in (20, 60, 120, 200):
        f[f"ma{n}_gap"] = c / c.rolling(n).mean() - 1
    e12, e26 = ema(c, 12), ema(c, 26)
    f["ema12_gap"] = safe_div(c, e12) - 1.0
    f["ema26_gap"] = safe_div(c, e26) - 1.0
    f["ema_trend"] = safe_div(e12, e26) - 1.0

    # RSI·MACD·볼린저·스토캐스틱 등 모멘텀/과열 신호
    f["rsi7"] = rsi(c, 7) / 100.0
    f["rsi14"] = rsi(c, 14) / 100.0
    f["rsi28"] = rsi(c, 28) / 100.0
    macd_line = e12 - e26
    macd_signal = ema(macd_line, 9)
    f["macd"] = safe_div(macd_line, c)
    f["macd_signal"] = safe_div(macd_signal, c)
    f["macd_hist"] = safe_div(macd_line - macd_signal, c)
    ma20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
    bb_upper, bb_lower = ma20 + 2 * sd20, ma20 - 2 * sd20
    f["bb_pctb"] = safe_div(c - bb_lower, bb_upper - bb_lower)
    f["bb_width"] = safe_div(bb_upper - bb_lower, ma20)
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    f["stoch_k"] = safe_div(c - lo14, hi14 - lo14)
    f["stoch_d"] = f["stoch_k"].rolling(3).mean()
    f["williams_r"] = -safe_div(hi14 - c, hi14 - lo14)

    adx, plus_di, minus_di = adx_components(df, 14)
    f["adx14"] = adx / 100.0
    f["plus_di14"] = plus_di / 100.0
    f["minus_di14"] = minus_di / 100.0
    f["di_spread"] = (plus_di - minus_di) / 100.0
    typical = (h + l + c) / 3.0
    tp_ma = typical.rolling(20).mean()
    mean_dev = typical.rolling(20).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    f["cci20"] = safe_div(typical - tp_ma, 0.015 * mean_dev) / 100.0

    # 변동성·캔들 구조·가격 범위 내 위치
    f["gap1"] = safe_div(o, c.shift()) - 1.0
    f["intraday_ret"] = safe_div(c, o) - 1.0
    f["range_pct"] = safe_div(h - l, c)
    f["vol20"] = r.rolling(20).std() * np.sqrt(252)
    f["vol60"] = r.rolling(60).std() * np.sqrt(252)
    f["vol_ratio"] = safe_div(r.rolling(5).std(), r.rolling(20).std())
    f["down_vol20"] = r.clip(upper=0).rolling(20).std() * np.sqrt(252)
    f["drawdown60"] = c / c.rolling(60).max() - 1.0
    f["atr_pct14"] = atr_series(df, 14) / c
    f["positive_days20"] = (r > 0).rolling(20).mean()
    f["skew20"] = r.rolling(20).skew()
    for n in (20, 60):
        low_n, high_n = l.rolling(n).min(), h.rolling(n).max()
        f[f"range_pos{n}"] = safe_div(c - low_n, high_n - low_n)

    # 거래량·자금흐름
    if v is not None and v.notna().sum() > 60:
        f["volu_ratio"] = safe_div(v.rolling(5).mean(), v.rolling(60).mean())
        f["mfi14"] = money_flow_index(df, 14) / 100.0
        obv = (np.sign(c.diff()).fillna(0.0) * v.fillna(0.0)).cumsum()
        f["obv_mom20"] = safe_div(obv.diff(20), obv.abs().rolling(60).mean())
    else:
        f["volu_ratio"] = np.nan
        f["mfi14"] = np.nan
        f["obv_mom20"] = np.nan
    return f.replace([np.inf, -np.inf], np.nan)


def assemble_dataset(prices: dict, horizon: int,
                     spot_data: pd.DataFrame | None = None):
    """(날짜 × 종목) long 형태 데이터셋과 피처 컬럼 목록을 만든다."""
    tick_syms = [s for s in TICKERS if s in prices]
    if not tick_syms:
        raise RuntimeError("종목 가격 데이터를 하나도 받지 못했습니다.")

    master = pd.DatetimeIndex(
        sorted(set().union(*[set(prices[s].index) for s in prices]))
    )
    macro = build_macro_features(prices, master)
    spot = load_spot_features(master, spot_data=spot_data)
    peer = build_peer_features(prices, master)

    frames = []
    for sym in tick_syms:
        df = prices[sym]
        X = build_ticker_features(df)
        X = pd.concat([X, macro.reindex(df.index)], axis=1)
        if spot is not None:
            X = pd.concat([X, spot.reindex(df.index)], axis=1)
        if "sox_ret20" in X.columns:
            X["rel_sox20"] = X["ret20"] - X["sox_ret20"]
        for h, tag in ((5, "5"), (20, "20"), (60, "60")):
            col = f"__peer{tag}__{sym}"
            if col in peer.columns:
                X[f"peer_rel{tag}"] = peer[col].reindex(df.index)

        c = df["Close"]
        # 신호는 t일 종가가 확정된 뒤 계산되므로 실제 진입은 t+1일에만 가능하다.
        # 다음 날 시가가 없을 때만 다음 날 종가를 보수적 대체값으로 쓴다.
        if "Open" in df.columns and df["Open"].notna().sum() > horizon + 20:
            entry = df["Open"].shift(-1)
        else:
            entry = c.shift(-1)
        exit_px = c.shift(-horizon)
        X["entry_px"] = entry
        X["exit_px"] = exit_px
        X["fwd_ret"] = exit_px / entry - 1.0
        X["y"] = np.where(X["fwd_ret"].isna(), np.nan,
                          (X["fwd_ret"] > 0).astype(float))
        # 이 행의 정답(라벨)이 '확정되는 날짜' = 해당 종목 달력 기준 horizon일 뒤.
        # 학습 시점 t에는 label_known_date <= t 인 행만 쓰면 미래 정보 누수가 없다.
        X["label_known_date"] = pd.Series(df.index, index=df.index).shift(-horizon)
        X["ticker"] = sym
        X.index.name = "date"
        frames.append(X.reset_index())

    data = pd.concat(frames, ignore_index=True).sort_values("date")
    for sym in TICKERS:  # 종목 원핫 (풀링 학습용)
        data[f"tk_{sym}"] = (data["ticker"] == sym).astype(int)

    data = data[data["ret20"].notna()].reset_index(drop=True)
    candidate_cols = [c for c in data.columns if c not in META_COLS]
    # 다운로드 실패·상장이력 부족으로 사실상 비어 있는 지표는 제거한다.
    # 결측 자체는 HistGradientBoosting이 분기 정보로 안전하게 처리한다.
    min_present = max(30, int(len(data) * 0.03))
    feat_cols = [c for c in candidate_cols
                 if data[c].notna().sum() >= min_present
                 and data[c].nunique(dropna=True) >= 2]
    return data, feat_cols


# ──────────────────────────────────────────────────────────────
# 모델 · 워크포워드 백테스트
# ──────────────────────────────────────────────────────────────
def make_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    kwargs = dict(
        max_iter=240, learning_rate=0.045, max_leaf_nodes=15,
        min_samples_leaf=45, l2_regularization=2.0,
        early_stopping=False, random_state=42,
    )
    try:
        # 매 분할마다 후보 피처를 70%만 보게 해, 저잡음·고자기상관 매크로 지표
        # (예: SOX 60일 수익률) 하나가 모든 트리 분기를 독식하는 것을 막는다.
        # 이 독식은 실제 성능 저하의 대표 원인이라 사이킷런 버전이 지원하면 항상 켠다.
        return HistGradientBoostingClassifier(max_features=0.7, **kwargs)
    except TypeError:
        return HistGradientBoostingClassifier(**kwargs)  # 구버전 sklearn 대비


def training_weights(train: pd.DataFrame,
                     half_life_days: int = RECENCY_HALF_LIFE_DAYS) -> np.ndarray:
    """최근 레짐을 더 반영하되 특정 클래스가 압도하지 않도록 완만히 보정."""
    ordered_dates = pd.Series(pd.to_datetime(train["date"]).unique()).sort_values()
    date_rank = {d: i for i, d in enumerate(ordered_dates)}
    ranks = pd.to_datetime(train["date"]).map(date_rank).to_numpy(dtype=float)
    age = ranks.max() - ranks
    recency = np.power(0.5, age / max(20, int(half_life_days)))
    y = train["y"].to_numpy(dtype=int)
    up = float(np.mean(y == 1))
    if 0.02 < up < 0.98:
        balance = np.where(y == 1, 0.5 / up, 0.5 / (1.0 - up))
    else:
        balance = np.ones(len(y))
    return np.clip(recency * balance, 0.20, 4.0)


@dataclass
class ProbabilityModel(ClassifierMixin, BaseEstimator):
    """시간순 홀드아웃으로 확률을 보정한 분류기 래퍼.

    BaseEstimator·ClassifierMixin 상속으로 permutation_importance 등 sklearn
    도구의 estimator 인터페이스 검증(fit 존재, __sklearn_tags__/_estimator_type)을
    구버전·신버전 모두에서 통과한다."""

    estimator: object
    calibrator: object | None
    base_rate: float
    calibration_rows: int = 0

    classes_ = np.array([0, 1])

    def fit(self, X, y=None, sample_weight=None):
        """sklearn 도구(permutation_importance 등)의 estimator 인터페이스 검증용.

        permutation_importance는 fit의 '존재'만 검사하고 실제로 재학습하지 않지만,
        만약 호출되더라도 안전하게 내부 estimator에 위임한다."""
        if y is not None:
            self.estimator.fit(X, y, sample_weight=sample_weight)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        raw = np.clip(self.estimator.predict_proba(X)[:, 1], 1e-4, 1 - 1e-4)
        if self.calibrator is not None:
            logit = np.log(raw / (1.0 - raw)).reshape(-1, 1)
            p = self.calibrator.predict_proba(logit)[:, 1]
        else:
            # 보정 표본이 없을 때 과도한 확신만 약하게 축소한다.
            p = 0.90 * raw + 0.10 * self.base_rate
        p = np.clip(p, 0.01, 0.99)
        return np.column_stack([1.0 - p, p])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


def fit_probability_model(train: pd.DataFrame,
                          feat_cols: list[str],
                          calibration_days: int = CALIBRATION_DAYS,
                          min_calibration_rows: int = MIN_CALIBRATION_ROWS,
                          recency_half_life: int = RECENCY_HALF_LIFE_DAYS,
                          horizon: int = DEFAULT_HORIZON) -> ProbabilityModel:
    """과거→최근 순서를 지킨 홀드아웃으로 sigmoid 확률 보정 후 전체 재학습.

    일반 K-fold 보정은 시계열에서 미래 레짐을 과거 모델에 섞을 수 있으므로 쓰지 않는다.
    """
    from sklearn.linear_model import LogisticRegression

    tr = train.sort_values("date").copy()
    base_rate = float(tr["y"].mean())
    unique_dates = np.array(sorted(tr["date"].unique()))
    calibrator = None
    cal_rows = 0

    min_core_dates = max(50, int(horizon) * 3)
    if len(unique_dates) > int(calibration_days) + min_core_dates:
        cut = unique_dates[-int(calibration_days)]
        # 보정구간 첫날 당시에 이미 정답이 확정된 행만 base 학습에 사용한다.
        # feature date만 자르면 horizon만큼 뒤에 확정될 라벨이 섞일 수 있다.
        core = tr[(tr["date"] < cut) & (tr["label_known_date"] < cut)]
        cal = tr[tr["date"] >= cut]
        if (len(core) >= MIN_TRAIN_ROWS and len(cal) >= min_calibration_rows
                and core["y"].nunique() == 2 and cal["y"].nunique() == 2):
            core_model = make_model()
            core_model.fit(core[feat_cols], core["y"],
                           sample_weight=training_weights(core, recency_half_life))
            raw = np.clip(core_model.predict_proba(cal[feat_cols])[:, 1],
                          1e-4, 1 - 1e-4)
            logits = np.log(raw / (1 - raw)).reshape(-1, 1)
            calibrator = LogisticRegression(C=0.5, max_iter=500,
                                            random_state=42)
            calibrator.fit(logits, cal["y"],
                           sample_weight=training_weights(cal, recency_half_life))
            cal_rows = len(cal)

    estimator = make_model()
    estimator.fit(tr[feat_cols], tr["y"],
                  sample_weight=training_weights(tr, recency_half_life))
    return ProbabilityModel(estimator, calibrator, base_rate, cal_rows)


def walk_forward(data: pd.DataFrame, feat_cols: list[str], horizon: int,
                 step: int = WF_STEP, min_train_days: int = MIN_TRAIN_DAYS,
                 calibration_days: int = CALIBRATION_DAYS,
                 min_calibration_rows: int = MIN_CALIBRATION_ROWS,
                 recency_half_life: int = RECENCY_HALF_LIFE_DAYS):
    """step 거래일마다 '그 시점까지 확정된 라벨'로만 재학습 → 다음 구간 예측.
    반환되는 예측은 전부 아웃오브샘플(모델이 그 시점엔 정답을 몰랐던 구간)이다."""
    dates = np.array(sorted(data["date"].unique()))
    chunks = []
    for i in range(min_train_days, len(dates) - 1, step):
        t = dates[i]
        t_next = dates[min(i + step, len(dates) - 1)]
        train = data[(data["label_known_date"].notna())
                     & (data["label_known_date"] <= t)
                     & (data["y"].notna())]
        if len(train) < MIN_TRAIN_ROWS:
            continue
        test = data[(data["date"] > t) & (data["date"] <= t_next)]
        if test.empty:
            continue
        mdl = fit_probability_model(
            train, feat_cols, calibration_days=calibration_days,
            min_calibration_rows=min_calibration_rows,
            recency_half_life=recency_half_life, horizon=horizon)
        p = mdl.predict_proba(test[feat_cols])[:, 1]
        chunk = test[["date", "ticker", "entry_px", "exit_px",
                      "fwd_ret", "y"]].copy()
        chunk["score"] = p * 100.0
        chunks.append(chunk)

    oos = (pd.concat(chunks, ignore_index=True)
           if chunks else pd.DataFrame(columns=["date", "ticker", "fwd_ret", "y", "score"]))

    # 최종 모델: 지금까지 확정된 모든 라벨로 학습 → 오늘의 점수 산출용
    final_train = data[(data["label_known_date"].notna()) & (data["y"].notna())]
    final_model = fit_probability_model(
        final_train, feat_cols, calibration_days=calibration_days,
        min_calibration_rows=min_calibration_rows,
        recency_half_life=recency_half_life, horizon=horizon) \
        if len(final_train) >= MIN_TRAIN_ROWS else None
    return oos, final_model


def current_scores(data: pd.DataFrame, feat_cols: list[str], final_model):
    if final_model is None:
        return pd.DataFrame()
    latest = data.sort_values("date").groupby("ticker").tail(1).copy()
    latest["score"] = final_model.predict_proba(latest[feat_cols])[:, 1] * 100.0
    latest["calibration_rows"] = getattr(final_model, "calibration_rows", 0)
    return latest[["date", "ticker", "score", "calibration_rows"]].reset_index(drop=True)


def compute_metrics(oos: pd.DataFrame, thr: int = 55, horizon: int = 20):
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

    m = oos.dropna(subset=["y"]).sort_values("date").copy()
    if m.empty:
        return None
    p = np.clip(m["score"].to_numpy(dtype=float) / 100.0, 1e-4, 1 - 1e-4)
    y = m["y"].to_numpy(dtype=int)
    m["pred_up"] = m["score"] >= 50
    m["hit"] = (m["pred_up"] == (m["y"] > 0.5)).astype(float)
    m["actual_up"] = (m["y"] > 0.5).astype(float)

    overall = float(m["hit"].mean())
    base = float(m["actual_up"].mean())          # '무조건 상승' 예측의 적중률
    naive = max(base, 1 - base)                   # 다수 클래스 베이스라인
    brier = float(brier_score_loss(y, p))
    brier_base = float(brier_score_loss(y, np.full(len(y), base)))
    brier_skill = 1.0 - brier / brier_base if brier_base > 0 else np.nan
    ll = float(log_loss(y, p, labels=[0, 1]))
    auc = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else np.nan

    # Expected Calibration Error: 같은 확률대에서 말한 확률과 실제 빈도의 차이.
    cal_bin = pd.cut(p, bins=np.linspace(0, 1, 11), include_lowest=True)
    cal_df = (pd.DataFrame({"bin": cal_bin, "p": p, "y": y})
              .groupby("bin", observed=True)
              .agg(예측확률=("p", "mean"), 실제상승률=("y", "mean"), 표본수=("y", "size")))
    ece = float(np.average((cal_df["예측확률"] - cal_df["실제상승률"]).abs(),
                           weights=cal_df["표본수"])) if len(cal_df) else np.nan

    # horizon 중첩 라벨의 자기상관을 반영한 날짜 블록 부트스트랩 95% 구간.
    date_rank = pd.Series(pd.factorize(m["date"], sort=True)[0], index=m.index)
    m["_block"] = (date_rank // max(1, int(horizon))).to_numpy()
    block_stats = m.groupby("_block")["hit"].agg(["sum", "count"])
    if len(block_stats) >= 5:
        rng = np.random.default_rng(42)
        draws = []
        arr = block_stats[["sum", "count"]].to_numpy(dtype=float)
        for _ in range(800):
            picked = arr[rng.integers(0, len(arr), len(arr))]
            draws.append(float(picked[:, 0].sum() / picked[:, 1].sum()))
        acc_lo, acc_hi = np.quantile(draws, [0.025, 0.975])
    else:
        n_obs, z = len(m), 1.96
        den = 1 + z * z / n_obs
        centre = (overall + z * z / (2 * n_obs)) / den
        half = z * np.sqrt(overall * (1 - overall) / n_obs
                           + z * z / (4 * n_obs * n_obs)) / den
        acc_lo, acc_hi = max(0.0, centre - half), min(1.0, centre + half)

    per_ticker = (m.groupby("ticker")
                    .agg(적중률=("hit", "mean"),
                         실제상승비율=("actual_up", "mean"),
                         평균점수=("score", "mean"),
                         표본수=("hit", "size"))
                    .reindex(list(TICKERS)).dropna(how="all"))

    m["band"] = pd.cut(m["score"], [0, 40, 60, 100],
                       labels=["약세 (≤40)", "중립 (40~60)", "강세 (≥60)"],
                       include_lowest=True)
    band = (m.groupby("band", observed=True)
              .agg(적중률=("hit", "mean"),
                   실제상승비율=("actual_up", "mean"),
                   표본수=("hit", "size")))

    up_calls = m[m["score"] >= thr]
    dn_calls = m[m["score"] <= 100 - thr]
    prec_up = float(up_calls["actual_up"].mean()) if len(up_calls) else np.nan
    prec_dn = float(1 - dn_calls["actual_up"].mean()) if len(dn_calls) else np.nan

    roll = m[["date"]].copy()
    roll["모델 적중률"] = m["hit"].rolling(250, min_periods=100).mean().values
    roll["무조건 상승 적중률"] = m["actual_up"].rolling(250, min_periods=100).mean().values
    roll = roll.dropna()

    return {
        "overall": overall, "base": base, "naive": naive, "n": int(len(m)),
        "accuracy_ci": (float(acc_lo), float(acc_hi)),
        "brier": brier, "brier_base": brier_base, "brier_skill": brier_skill,
        "log_loss": ll, "auc": auc, "ece": ece, "calibration": cal_df,
        "per_ticker": per_ticker, "band": band,
        "prec_up": prec_up, "n_up": int(len(up_calls)),
        "prec_dn": prec_dn, "n_dn": int(len(dn_calls)),
        "rolling": roll,
    }


def score_evidence(oos: pd.DataFrame, ticker: str, score: float,
                   width: float = 10.0, horizon: int = 20) -> dict:
    """현재 점수 근처의 과거 실제 상승률과 Wilson 구간을 반환."""
    candidates = oos[oos["y"].notna()].sort_values(["ticker", "date"])
    # 같은 향후구간을 여러 번 세지 않도록 종목별 비중복 표본으로 근사한다.
    independent = pd.concat(
        [x.iloc[::max(1, int(horizon))] for _, x in candidates.groupby("ticker")],
        ignore_index=True) if len(candidates) else candidates
    g = independent[(independent["ticker"] == ticker)
                    & independent["score"].between(score - width, score + width)]
    source = "종목별"
    if len(g) < 30:
        g = independent[independent["score"].between(score - width, score + width)]
        source = "전체 종목 풀링"
    n = len(g)
    if n == 0:
        return {"n": 0, "rate": np.nan, "lo": np.nan, "hi": np.nan,
                "source": source}
    rate = float(g["y"].mean())
    z = 1.645  # 행동판단용 90% 구간
    den = 1 + z * z / n
    centre = (rate + z * z / (2 * n)) / den
    half = z * np.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / den
    return {"n": n, "rate": rate, "lo": max(0.0, centre - half),
            "hi": min(1.0, centre + half), "source": source}


def equity_curve(oos: pd.DataFrame, ticker: str, horizon: int, thr: int = 55,
                 cost_bps: int = DEFAULT_COST_BPS):
    """비중복 구간 백테스트: t일 신호→t+1일 시가 진입, 비용 차감."""
    g = (oos[oos["ticker"] == ticker]
         .dropna(subset=["fwd_ret"]).sort_values("date"))
    g = g.iloc[::horizon]
    if len(g) < 6:
        return None
    cost = float(cost_bps) / 10_000.0
    active = g["score"] >= thr
    strategy_ret = pd.Series(np.where(active, g["fwd_ret"] - cost, 0.0),
                             index=g.index).clip(lower=-0.99)
    benchmark_ret = g["fwd_ret"].clip(lower=-0.99)
    curve = pd.DataFrame({
        "date": g["date"].values,
        "시그널 추종": strategy_ret.add(1).cumprod().values,
        "단순 보유": benchmark_ret.add(1).cumprod().values,
    })

    def perf(r: pd.Series) -> dict:
        periods = len(r)
        years = max(periods * horizon / 252.0, 1 / 252)
        total = float(np.prod(1 + r) - 1)
        cagr = float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
        nav = (1 + r).cumprod()
        mdd = float((nav / nav.cummax() - 1).min())
        vol = float(r.std(ddof=1))
        sharpe = (float(r.mean()) / vol * np.sqrt(252 / horizon)
                  if vol > 0 else np.nan)
        return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe}

    stats = {"strategy": perf(strategy_ret), "benchmark": perf(benchmark_ret),
             "trades": int(active.sum()), "exposure": float(active.mean()),
             "win_rate": float((g.loc[active, "fwd_ret"] - cost > 0).mean())
             if active.any() else np.nan}
    return curve, stats


def feature_importance(final_model, data: pd.DataFrame, feat_cols: list[str],
                       n_rows: int = 1000):
    if final_model is None:
        return None
    from sklearn.inspection import permutation_importance
    lab = data.dropna(subset=["y"]).sort_values("date").tail(n_rows)
    if len(lab) < 200:
        return None
    r = permutation_importance(final_model, lab[feat_cols], lab["y"],
                               n_repeats=3, random_state=0, scoring="accuracy")
    imp = pd.Series(r.importances_mean, index=feat_cols)
    imp.index = [feat_label(c) for c in imp.index]
    return imp.sort_values(ascending=False)


def horizon_return_stats(data: pd.DataFrame) -> dict:
    """종목별 H일 수익률의 상승/하락 조건부 평균 → 점수를 기대수익률로 변환할 때 사용.
    E[r] = P(상승)·E[r|상승] + (1-P(상승))·E[r|하락]"""
    out = {}
    for sym, g in data.dropna(subset=["fwd_ret"]).groupby("ticker"):
        # 극단치 한 건이 목표가를 지배하지 않도록 1~99%로 윈저라이즈한다.
        raw = g["fwd_ret"].astype(float)
        lo, hi = raw.quantile([0.01, 0.99])
        clipped = raw.clip(lo, hi)
        pos = clipped[clipped > 0]
        neg = clipped[clipped <= 0]
        out[sym] = {
            "mu_up": float(pos.mean()) if len(pos) else 0.0,
            "mu_dn": float(neg.mean()) if len(neg) else 0.0,
            "sd": float(clipped.std()) if len(g) > 2 else 0.0,
            "median": float(clipped.median()) if len(g) else 0.0,
            "q10": float(clipped.quantile(0.10)) if len(g) else 0.0,
            "q90": float(clipped.quantile(0.90)) if len(g) else 0.0,
        }
    return out


def expected_return(score: float, stats: dict | None) -> float:
    if stats is None or score is None or np.isnan(score):
        return 0.0
    p = float(score) / 100.0
    return p * stats["mu_up"] + (1 - p) * stats["mu_dn"]


def model_quality_weight(metrics: dict | None) -> float:
    """검증력이 약할수록 50점 쪽으로 신호를 축소하는 경험적 베이지안 가중치."""
    if not metrics or metrics.get("n", 0) < 300:
        return 0.35
    brier_skill = float(metrics.get("brier_skill", 0.0))
    auc = float(metrics.get("auc", 0.5))
    if not np.isfinite(brier_skill):
        brier_skill = 0.0
    if not np.isfinite(auc):
        auc = 0.5
    # Brier skill 5%, AUC 0.55 수준에서 대략 100% 반영한다.
    quality = 0.35 + 0.40 * np.clip(brier_skill / 0.05, 0, 1) \
        + 0.25 * np.clip((auc - 0.5) / 0.05, 0, 1)
    return float(np.clip(quality, 0.35, 1.0))


# ──────────────────────────────────────────────────────────────
# 행동 플랜 — 점수를 "지금 할 일 + 가격"으로 번역 (순수 계산부)
# ──────────────────────────────────────────────────────────────
ACTION_META = {
    "STRONG_BUY": ("🟢", "지금 매수", "상승 확률이 강하게 우위"),
    "BUY_DIP":    ("🔵", "조정 시 매수", "우위는 있으나 눌림을 기다림"),
    "HOLD":       ("⚪", "관망 · 보유 유지", "방향성이 뚜렷하지 않음"),
    "TRIM":       ("🟠", "반등 시 비중 축소", "하락 확률 우위"),
    "SELL":       ("🔴", "매도 (리스크 회피)", "하락 확률이 강하게 우위"),
}


def atr_series(pdf: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR(14). 고가/저가가 없으면 종가 변동폭으로 근사."""
    c = pdf["Close"]
    if {"High", "Low"} <= set(pdf.columns) and pdf["High"].notna().sum() > n:
        h, l, pc = pdf["High"], pdf["Low"], c.shift()
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    else:
        tr = c.diff().abs()
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def px_round(v, ccy: str):
    if v is None or not np.isfinite(v):
        return None
    return float(round(v)) if ccy in ("KRW", "JPY") else float(round(v, 2))


def fmt_px(v, ccy: str) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    return f"{v:,.0f}" if ccy in ("KRW", "JPY") else f"{v:,.2f}"


def make_action_plan(pdf: pd.DataFrame, score: float, stats: dict | None,
                     horizon: int, thr: int, ccy: str,
                     quality: float = 1.0,
                     evidence: dict | None = None) -> dict:
    """점수 → 5단계 행동 + 매수/목표/손절/축소 가격 (ATR·모델 기대수익 기반 규칙).
      지금 매수(≥thr+10) / 조정 시 매수(≥thr) / 관망 / 반등 시 축소 / 매도(≤100-thr-10)"""
    c = float(pdf["Close"].iloc[-1])
    a = float(atr_series(pdf).iloc[-1])
    if not np.isfinite(a) or a <= 0:
        a = c * 0.02  # 안전장치: 대략 일변동 2% 가정
    raw_er = expected_return(score, stats)
    median = float(stats.get("median", 0.0)) if stats else 0.0
    er = median + (raw_er - median) * quality
    sd = float(stats.get("sd", 0.0)) if stats else 0.0
    decision_score = 50.0 + (float(score) - 50.0) * quality
    strong_b = min(thr + 10, 90)
    weak_b = 100 - thr
    strong_s = max(weak_b - 10, 10)

    shift = er - median
    q_lo = float(stats.get("q10", -sd)) + shift if stats else er - sd
    q_hi = float(stats.get("q90", sd)) + shift if stats else er + sd
    plan = {"score": float(score), "decision_score": decision_score,
            "quality": quality, "evidence": evidence or {},
            "price": c, "atr": a, "ccy": ccy, "er": er,
            "target": px_round(c * (1 + er), ccy),
            "t_lo": px_round(max(c * 0.05, c * (1 + q_lo)), ccy),
            "t_hi": px_round(c * (1 + q_hi), ccy),
            "buy": None, "stop": None, "trim": None, "reentry": None}

    if decision_score >= strong_b:
        code = "STRONG_BUY"
        plan["buy"] = px_round(c, ccy)
        plan["stop"] = px_round(c - STOP_ATR * a, ccy)
    elif decision_score >= thr:
        code = "BUY_DIP"
        b = c - ENTRY_ATR * a
        plan["buy"] = px_round(b, ccy)
        plan["stop"] = px_round(b - STOP_ATR * a, ccy)
    elif decision_score > weak_b:
        code = "HOLD"
        plan["buy"] = px_round(c - 1.5 * a, ccy)      # 강한 조정이 오면 관심
        plan["stop"] = px_round(c - STOP_ATR * a, ccy)  # 보유자용 손절선
    elif decision_score > strong_s:
        code = "TRIM"
        plan["trim"] = px_round(c + TRIM_ATR * a, ccy)
        plan["stop"] = px_round(c - 1.0 * a, ccy)      # 보유자용 타이트 손절
        plan["reentry"] = px_round(c - 2.0 * a, ccy)
    else:
        code = "SELL"
        plan["trim"] = px_round(c, ccy)                 # 지금 정리
        plan["reentry"] = px_round(c - 2.5 * a, ccy)

    plan["code"] = code
    plan["emoji"], plan["label"], plan["why_short"] = ACTION_META[code]
    return plan


def action_for_holding(plan: dict | None, cur: float | None = None,
                       is_self: bool = False) -> str:
    """보유 중인 포지션에 대한 '지금 행동' 문구."""
    if plan is None:
        return "⚪ 모델 신호 없음"
    if is_self and cur is not None and np.isfinite(cur):
        if plan.get("stop") and cur <= plan["stop"]:
            return "🔴 손절선 이탈 — 규칙대로 축소"
        if (plan["code"] in ("STRONG_BUY", "BUY_DIP")
                and plan.get("target") and cur >= plan["target"]):
            return "💰 목표가 도달 — 부분 익절 검토"
    return {"STRONG_BUY": "🟢 보유 지속 (추가 매수 여지)",
            "BUY_DIP":    "🟢 보유 지속 · 추가는 조정 시",
            "HOLD":       "⚪ 보유 유지",
            "TRIM":       "🟠 반등 시 비중 축소",
            "SELL":       "🔴 비중 축소/정리 검토"}[plan["code"]]


_REASON_RULES = [
    # (피처, 상방 임계, 상방 문구, 하방 임계, 하방 문구)
    ("ret20",       0.03,  "최근 1개월 상승세",      -0.03, "최근 1개월 하락세"),
    ("ma60_gap",    0.005, "60일 평균선 위",         -0.005, "60일 평균선 아래"),
    ("rel_sox20",   0.02,  "업종 대비 강함",         -0.02, "업종 대비 약함"),
    ("peer_rel20",  0.02,  "동종 메모리주 대비 강세", -0.02, "동종 메모리주 대비 약세"),
    ("sox_ret20",   0.03,  "반도체 업종 전반 강세",   -0.03, "반도체 업종 전반 약세"),
    ("macd_hist",   0.001, "MACD 상승 모멘텀",       -0.001, "MACD 하락 모멘텀"),
    ("di_spread",   0.08,  "+DI 우위 추세",           -0.08,  "-DI 우위 추세"),
]


def plain_reasons(sym: str, last_row: pd.Series, max_n: int = 3) -> list[str]:
    """비전문가용 한 줄 근거 — 대표 지표를 쉬운 말로."""
    out: list[str] = []
    for col, hi, pos, lo, neg in _REASON_RULES:
        v = last_row.get(col)
        if v is None or pd.isna(v):
            continue
        if v >= hi:
            out.append(pos)
        elif v <= lo:
            out.append(neg)
    rsi = last_row.get("rsi14")
    if rsi is not None and not pd.isna(rsi):
        if rsi >= 0.70:
            out.append("단기 과열 구간(RSI 70↑)")
        elif rsi <= 0.32:
            out.append("과매도 구간(RSI 32↓)")
    if sym.endswith((".KS", ".KQ")):
        k = last_row.get("krw_chg20")
        if k is not None and not pd.isna(k) and k >= 0.015:
            out.append("원화 약세(수출주 우호)")
    spot_vals = [last_row[c] for c in last_row.index
                 if c.startswith("spot_") and c.endswith("_chg20")
                 and not pd.isna(last_row[c])]
    if spot_vals:
        m = float(np.mean(spot_vals))
        if m >= 0.02:
            out.append("메모리 현물가 상승 중")
        elif m <= -0.02:
            out.append("메모리 현물가 하락 중")
    return out[:max_n]


def technical_snapshot(pdf: pd.DataFrame) -> dict:
    """상세 차트 상단에 보여줄 최신 기술적 상태."""
    f = build_ticker_features(pdf).iloc[-1]

    def val(name: str, scale: float = 1.0) -> float:
        x = f.get(name, np.nan)
        return float(x) * scale if pd.notna(x) else np.nan

    rsi_v, adx_v = val("rsi14", 100), val("adx14", 100)
    macd_v, bb_v, stoch_v = val("macd_hist"), val("bb_pctb", 100), val("stoch_k", 100)
    return {
        "rsi": rsi_v,
        "rsi_state": ("과열" if rsi_v >= 70 else "과매도" if rsi_v <= 30 else "중립")
        if np.isfinite(rsi_v) else "-",
        "macd": macd_v,
        "macd_state": ("상승 모멘텀" if macd_v > 0 else "하락 모멘텀")
        if np.isfinite(macd_v) else "-",
        "adx": adx_v,
        "adx_state": ("강한 추세" if adx_v >= 25 else "약한 추세")
        if np.isfinite(adx_v) else "-",
        "bb": bb_v,
        "bb_state": ("상단 돌파" if bb_v > 100 else "하단 이탈" if bb_v < 0 else "밴드 내부")
        if np.isfinite(bb_v) else "-",
        "stoch": stoch_v,
    }


def reliability_summary(metrics: dict | None) -> dict:
    """정확도 수치를 비전문가용 등급·문장으로 번역."""
    if metrics is None:
        return {"grade": "검증 전", "emoji": "⚪",
                "lines": ["아직 검증 표본이 부족해 신뢰도를 매길 수 없습니다."],
                "advice": "신호는 참고만 하고 행동은 보수적으로."}
    edge = metrics["overall"] - metrics["naive"]
    brier_skill = metrics.get("brier_skill", np.nan)
    auc = metrics.get("auc", np.nan)
    n = metrics["n"]
    if n < 300:
        grade, emoji = "표본 부족", "⚪"
        advice = "검증 횟수가 적어 아직 믿고 쓰기 이릅니다."
    elif (np.isfinite(brier_skill) and brier_skill >= 0.03
          and np.isfinite(auc) and auc >= 0.54 and edge >= 0.01):
        grade, emoji = "양호", "🟢"
        advice = "신호에 무게를 둘 만하지만, 손절 규칙은 항상 지키세요."
    elif (np.isfinite(brier_skill) and brier_skill > 0
          and np.isfinite(auc) and auc > 0.51):
        grade, emoji = "보통", "🟡"
        advice = "신호는 보조 지표로 쓰고, 분할 매매로 대응하세요."
    else:
        grade, emoji = "낮음", "🔴"
        advice = "현재 이 신호의 실질 우위가 거의 없습니다. 행동보다 관망을 권장."
    lines = [
        f"과거 {n:,}번의 완전 아웃오브샘플 검증에서 방향을 10번 중 "
        f"{metrics['overall'] * 10:.1f}번 맞았습니다.",
        f"아무 판단 없이 '오른다'고만 했어도 10번 중 {metrics['base'] * 10:.1f}번 맞는 "
        f"시장이었으므로, 이 모델의 실질 우위는 {edge * 100:+.1f}%p입니다.",
        f"확률 품질(Brier skill)은 {brier_skill:+.1%}, 순위 판별력(AUC)은 "
        f"{auc:.3f}입니다.",
    ]
    return {"grade": grade, "emoji": emoji, "lines": lines, "advice": advice,
            "quality": model_quality_weight(metrics)}


def ladder_fig(plan: dict, go):
    """손절–매수–현재–목표를 한 줄에 놓은 가격 사다리."""
    cc = plan["ccy"]
    pts = []
    if plan.get("stop"):
        pts.append(("손절", plan["stop"], "#d62728", "triangle-down"))
    if plan.get("reentry"):
        pts.append(("재진입 관찰", plan["reentry"], "#9467bd", "triangle-down"))
    if plan.get("buy"):
        pts.append(("매수 추천", plan["buy"], "#2ca02c", "triangle-up"))
    pts.append(("현재가", plan["price"], "#111111", "diamond"))
    if plan.get("trim"):
        pts.append(("축소/매도", plan["trim"], "#ff7f0e", "triangle-down"))
    if plan.get("target"):
        pts.append(("목표", plan["target"], "#1f77b4", "star"))
    pts.sort(key=lambda x: x[1])

    fig = go.Figure()
    if plan.get("t_lo") and plan.get("t_hi"):
        fig.add_shape(type="rect", x0=plan["t_lo"], x1=plan["t_hi"],
                      y0=-0.35, y1=0.35, line_width=0,
                      fillcolor="rgba(31,119,180,0.12)")
    xs = [p[1] for p in pts]
    fig.add_scatter(x=[min(xs), max(xs)], y=[0, 0], mode="lines",
                    line=dict(color="#bbbbbb", width=2),
                    hoverinfo="skip", showlegend=False)
    for k, (name, v, color, symmark) in enumerate(pts):
        pos = "top center" if k % 2 == 0 else "bottom center"
        fig.add_scatter(x=[v], y=[0], mode="markers+text",
                        marker=dict(size=13, color=color, symbol=symmark),
                        text=[f"{name}<br>{fmt_px(v, cc)}"],
                        textposition=pos, textfont=dict(size=11),
                        showlegend=False, hoverinfo="skip")
    fig.update_yaxes(visible=False, range=[-1, 1])
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_layout(height=170, margin=dict(t=10, b=10, l=10, r=10),
                      title=None)
    return fig


def technical_chart(pdf: pd.DataFrame, score_hist: pd.DataFrame, go,
                    make_subplots):
    """캔들·이평/확률·RSI·MACD를 한 화면에 정렬한 분석 차트."""
    d = pdf.tail(756).copy()
    c = d["Close"]
    e12, e26 = ema(c, 12), ema(c, 26)
    macd_line = e12 - e26
    macd_signal = ema(macd_line, 9)
    macd_hist = macd_line - macd_signal
    rsi14 = rsi(c, 14)

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.035,
        row_heights=[0.56, 0.22, 0.22],
        specs=[[{"secondary_y": True}], [{}], [{}]],
    )
    if {"Open", "High", "Low", "Close"} <= set(d.columns) \
            and d[["Open", "High", "Low"]].notna().all(axis=1).any():
        fig.add_trace(go.Candlestick(
            x=d.index, open=d["Open"], high=d["High"], low=d["Low"],
            close=d["Close"], name="OHLC", increasing_line_color="#16a34a",
            decreasing_line_color="#ef4444"), row=1, col=1, secondary_y=False)
    else:
        fig.add_trace(go.Scatter(x=d.index, y=c, name="종가",
                                 line=dict(width=1.8, color="#2563eb")),
                      row=1, col=1, secondary_y=False)
    for n, color in ((20, "#2563eb"), (60, "#f59e0b"), (120, "#7c3aed")):
        fig.add_trace(go.Scatter(
            x=d.index, y=c.rolling(n).mean(), name=f"MA{n}",
            line=dict(width=1.1, color=color)), row=1, col=1, secondary_y=False)
    hist = score_hist.sort_values("date")
    if not hist.empty:
        fig.add_trace(go.Scatter(
            x=hist["date"], y=hist["score"], name="상승확률",
            line=dict(width=1.2, color="#db2777"), opacity=0.75),
            row=1, col=1, secondary_y=True)

    fig.add_trace(go.Scatter(x=d.index, y=rsi14, name="RSI(14)",
                             line=dict(width=1.5, color="#0f766e")), row=2, col=1)
    fig.add_hrect(y0=70, y1=100, fillcolor="rgba(239,68,68,.08)",
                  line_width=0, row=2, col=1)
    fig.add_hrect(y0=0, y1=30, fillcolor="rgba(37,99,235,.08)",
                  line_width=0, row=2, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#ef4444", row=2, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#2563eb", row=2, col=1)

    colors = np.where(macd_hist >= 0, "rgba(22,163,74,.65)",
                      "rgba(239,68,68,.65)")
    fig.add_trace(go.Bar(x=d.index, y=macd_hist, name="MACD Hist",
                         marker_color=colors), row=3, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=macd_line, name="MACD",
                             line=dict(width=1.3, color="#2563eb")), row=3, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=macd_signal, name="Signal",
                             line=dict(width=1.1, color="#f59e0b")), row=3, col=1)

    fig.update_yaxes(title_text="가격", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="확률", range=[0, 100], ticksuffix="%",
                     showgrid=False, row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    fig.update_yaxes(title_text="MACD", row=3, col=1)
    fig.update_layout(
        height=690, margin=dict(t=30, b=20, l=20, r=20), hovermode="x unified",
        legend=dict(orientation="h", y=1.04, x=0),
        xaxis_rangeslider_visible=False, bargap=0,
    )
    return fig


# ──────────────────────────────────────────────────────────────
# 포트폴리오 (순수 계산부 — UI와 분리)
# ──────────────────────────────────────────────────────────────
def ticker_currency(t: str) -> str:
    t = str(t).upper().strip()
    if t.endswith((".KS", ".KQ")):
        return "KRW"
    if t.endswith(".T"):
        return "JPY"
    return "USD"


def fetch_quotes(tickers: tuple[str, ...]) -> dict[str, tuple[float, float]]:
    """포트폴리오 종목 + 환율의 (최근 종가, 직전 종가). yfinance 지연 시세."""
    import yfinance as yf

    syms = sorted({*(str(t).strip() for t in tickers if str(t).strip()),
                   "KRW=X", "JPY=X"})
    raw = _retry(lambda: yf.download(syms, period="1mo", auto_adjust=True,
                                     group_by="ticker", progress=False,
                                     threads=True))
    out: dict[str, tuple[float, float]] = {}
    for s in syms:
        try:
            df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            c = df["Close"].dropna()
            if len(c) == 0:
                continue
            last = float(c.iloc[-1])
            prev = float(c.iloc[-2]) if len(c) >= 2 else np.nan
            out[s] = (last, prev)
        except Exception:
            continue
    return out


def default_portfolio() -> pd.DataFrame:
    rows = [{"티커": s, "수량": 0.0, "평단": 0.0, "모델연동": "자동", "배수": 1.0}
            for s in TICKERS]
    return pd.DataFrame(rows, columns=PORT_COLS)


def load_portfolio(path: str = PORTFOLIO_CSV) -> pd.DataFrame:
    if not os.path.exists(path):
        return default_portfolio()
    try:
        df = pd.read_csv(path)
    except Exception:
        return default_portfolio()
    for c in PORT_COLS:
        if c not in df.columns:
            df[c] = {"모델연동": "자동", "배수": 1.0}.get(c, 0.0)
    df["티커"] = df["티커"].astype(str).str.strip()
    for c in ("수량", "평단", "배수"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["배수"] = df["배수"].fillna(1.0)
    return df[PORT_COLS]


def save_portfolio(df: pd.DataFrame, path: str = PORTFOLIO_CSV) -> None:
    df[PORT_COLS].to_csv(path, index=False)


def resolve_link(ticker: str, link: str | None) -> str | None:
    """모델연동 결정: '자동'이면 자기 자신이 유니버스일 때만 연결."""
    link = (link or "자동").strip()
    if link == "없음":
        return None
    if link in TICKERS:
        return link
    return ticker if ticker in TICKERS else None  # '자동'


def build_portfolio_view(pf: pd.DataFrame, quotes: dict,
                         scores_map: dict[str, float], ret_stats: dict,
                         horizon: int, plans: dict | None = None):
    """보유 목록 → 포지션별 평가/예상 테이블과 원화 합계.
    환산은 현재 환율 일괄 적용, 예상은 모델 기대수익률 × 배수(레버리지) 반영."""
    usdkrw = quotes.get("KRW=X", (np.nan, np.nan))[0]
    usdjpy = quotes.get("JPY=X", (np.nan, np.nan))[0]

    def to_krw(v: float, ccy: str) -> float:
        if ccy == "KRW":
            return v
        if ccy == "USD":
            return v * usdkrw
        if ccy == "JPY":
            return v * usdkrw / usdjpy
        return np.nan

    rows, failed = [], []
    for _, r in pf.iterrows():
        t = str(r["티커"]).strip()
        qty = float(r["수량"]) if pd.notna(r["수량"]) else 0.0
        avg = float(r["평단"]) if pd.notna(r["평단"]) else 0.0
        if not t or t.lower() == "nan" or qty <= 0:
            continue
        last, prev = quotes.get(t, (np.nan, np.nan))
        if np.isnan(last):
            failed.append(t)
            continue
        ccy = ticker_currency(t)
        value = to_krw(last * qty, ccy)
        cost = to_krw(avg * qty, ccy) if avg > 0 else np.nan
        day = last / prev - 1 if prev and not np.isnan(prev) else np.nan

        link = resolve_link(t, r.get("모델연동"))
        mult = float(r["배수"]) if pd.notna(r["배수"]) else 1.0
        score = scores_map.get(link, np.nan) if link else np.nan
        plan = plans.get(link) if (plans and link) else None
        er_base = (float(plan["er"]) if plan is not None
                   else expected_return(score, ret_stats.get(link))) if link else 0.0
        er = er_base * mult
        act = action_for_holding(plan, cur=last if t == link else None,
                                 is_self=(t == link))

        rows.append({
            "종목": TICKERS.get(t, t), "지금 행동": act, "티커": t, "통화": ccy,
            "수량": qty, "평단": avg, "현재가": last, "당일": day,
            "매입액(₩)": cost, "평가액(₩)": value,
            "손익(₩)": value - cost if cost == cost else np.nan,
            "수익률": value / cost - 1 if cost == cost and cost > 0 else np.nan,
            "연동": TICKERS.get(link, "-") if link else "-",
            "점수": score, "배수": mult,
            f"예상수익률({horizon}일)": er,
            "예상평가액(₩)": value * (1 + er),
        })

    view = pd.DataFrame(rows)
    totals = None
    if not view.empty:
        tv = float(view["평가액(₩)"].sum())
        known = view["매입액(₩)"].notna()
        tc = float(view.loc[known, "매입액(₩)"].sum()) if known.any() else np.nan
        tv_known = float(view.loc[known, "평가액(₩)"].sum()) if known.any() else np.nan
        te = float(view["예상평가액(₩)"].sum())
        totals = {
            "value": tv, "cost": tc,
            "pnl": tv_known - tc if tc == tc else np.nan,
            "ret": tv_known / tc - 1 if tc == tc and tc > 0 else np.nan,
            "exp_value": te, "exp_pnl": te - tv,
            "exp_ret": te / tv - 1 if tv > 0 else np.nan,
            "usdkrw": usdkrw,
        }
    return view, totals, failed


# ──────────────────────────────────────────────────────────────
# 현물가 수동 보정 파일 입출력 (UI용)
# ──────────────────────────────────────────────────────────────
def load_spot_editor(path: str = SPOT_CSV) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            df = pd.read_csv(path)
            df = df.rename(columns={df.columns[0]: "날짜"})
            df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
            return (df.dropna(subset=["날짜"]).sort_values("날짜")
                      .reset_index(drop=True))
        except Exception:
            pass
    return pd.DataFrame({"날짜": pd.Series(dtype="datetime64[ns]"),
                         **{c: pd.Series(dtype=float) for c in SPOT_DEFAULT_COLS}})


def save_spot_editor(df: pd.DataFrame, path: str = SPOT_CSV) -> None:
    d = df.copy()
    d["날짜"] = pd.to_datetime(d["날짜"], errors="coerce")
    d = d.dropna(subset=["날짜"]).sort_values("날짜")
    for c in [c for c in d.columns if c != "날짜"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d.to_csv(path, index=False)


# ──────────────────────────────────────────────────────────────
# 파이프라인 (Streamlit 캐시 대상)
# ──────────────────────────────────────────────────────────────
def run_pipeline(horizon: int, period: str,
                 spot_data: pd.DataFrame | None = None,
                 refresh_token: int = 0,
                 force_spot: bool = False):
    auto_spot, spot_status = fetch_auto_spot_prices(force=force_spot)
    # 자동값이 기본이고, 사용자 세션의 수동 값이 같은 날짜·품목만 덮어쓴다.
    merged_spot = merge_spot_data(auto_spot, spot_data)
    del refresh_token  # 캐시 키만 바꾸기 위한 사용자 세션별 토큰
    profile = PERIOD_PROFILES.get(period, PERIOD_PROFILES[DEFAULT_PERIOD])
    prices = download_prices(period)
    missing = [s for s in list(TICKERS) + list(MACRO) if s not in prices]
    data, feat_cols = assemble_dataset(prices, horizon, spot_data=merged_spot)
    oos, final_model = walk_forward(
        data, feat_cols, horizon,
        min_train_days=profile["min_train_days"],
        calibration_days=profile["calibration_days"],
        min_calibration_rows=profile["min_calibration_rows"],
        recency_half_life=profile["recency_half_life"])
    scores = current_scores(data, feat_cols, final_model)
    imp = feature_importance(final_model, data, feat_cols)
    ret_stats = horizon_return_stats(data)
    spot_used = sorted(c for c in feat_cols if c.startswith("spot_"))
    return {"prices": prices, "data": data, "feat_cols": feat_cols,
            "oos": oos, "scores": scores, "importance": imp,
            "missing": missing, "ret_stats": ret_stats,
            "spot_used": spot_used, "spot_data": merged_spot,
            "spot_status": spot_status, "profile": profile}


# ──────────────────────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────────────────────
def main():
    import io

    import plotly.graph_objects as go
    import streamlit as st
    from plotly.subplots import make_subplots

    st.set_page_config(page_title="MEMORY STOCK PREDICT", page_icon="🧠",
                       layout="wide", initial_sidebar_state="expanded")
    st.markdown("""
    <style>
    .block-container {padding-top: 1.15rem; padding-bottom: 3.5rem; max-width: 1540px;}
    .hero {padding: 1.35rem 1.55rem; border-radius: 1.15rem; margin-bottom: 1rem;
      background: linear-gradient(125deg, rgba(15,23,42,.98), rgba(30,64,175,.90));
      color: white; box-shadow: 0 14px 34px rgba(15,23,42,.16);}
    .hero-badge {display:inline-block; font-size:.74rem; letter-spacing:.08em;
      font-weight:750; padding:.25rem .55rem; border-radius:999px;
      background:rgba(255,255,255,.15); margin-bottom:.55rem;}
    .hero h1 {font-size:2rem; margin:0 0 .25rem 0; line-height:1.15;}
    .hero p {margin:0; opacity:.78; font-size:.93rem;}
    div[data-testid="stMetric"] {background: rgba(127,127,127,.055); border: 1px solid
      rgba(127,127,127,.15); padding: .9rem 1rem; border-radius: .9rem;
      box-shadow: 0 4px 14px rgba(15,23,42,.04);}
    div[data-testid="stMetricLabel"] {font-weight: 700;}
    div[data-testid="stTabs"] button {font-weight:680; padding-left:.95rem; padding-right:.95rem;}
    div[data-testid="stDataFrame"] {border:1px solid rgba(127,127,127,.14);
      border-radius:.75rem; overflow:hidden;}
    .section-kicker {font-size:.74rem; font-weight:800; letter-spacing:.08em;
      color:#2563eb; margin-bottom:.15rem;}
    .small-note {color: #777; font-size: .86rem;}
    </style>
    """, unsafe_allow_html=True)

    if "portfolio_df" not in st.session_state:
        st.session_state.portfolio_df = (load_portfolio()
                                         if local_persistence_enabled()
                                         else default_portfolio())
    if "spot_df" not in st.session_state:
        st.session_state.spot_df = (load_spot_editor()
                                    if local_persistence_enabled()
                                    else load_spot_editor(path="__session_only__"))
    if "refresh_token" not in st.session_state:
        st.session_state.refresh_token = 0
    if "spot_editor_version" not in st.session_state:
        st.session_state.spot_editor_version = 0

    st.markdown(f"""
    <div class="hero">
      <div class="hero-badge">MEMORY CYCLE DECISION ENGINE · v{VERSION}</div>
      <h1>Memory Stock Predict</h1>
      <p>기술적 흐름 · 반도체 사이클 · 확률 보정 · 실행 가격을 한 화면에서 봅니다.</p>
    </div>
    """, unsafe_allow_html=True)

    with st.sidebar:
        st.header("분석 설정")
        horizon = st.selectbox("예측 지평", [10, 20, 40], index=1,
                               format_func=lambda x: f"{x}거래일")
        thr = st.slider("행동 신호 기준", 52, 70, 55,
                        help="검증력으로 축소한 실행점수가 이 값 이상이면 매수 우위입니다.")
        period_options = list(PERIOD_PROFILES)
        period = st.selectbox(
            "학습 이력", period_options, index=period_options.index(DEFAULT_PERIOD),
            format_func=lambda x: PERIOD_PROFILES[x]["label"])
        if period == "1y":
            st.caption("⚡ 최근 장세 반영은 빠르지만 검증 표본과 확률 안정성이 가장 낮습니다.")
        elif period == "3y":
            st.caption("최근 메모리 사이클에 집중합니다. 장기 레짐 비교는 제한됩니다.")
        else:
            st.caption("10년은 표본 안정성과 최근 레짐 반영의 균형이 가장 좋습니다.")
        cost_bps = st.number_input("왕복 거래비용 (bp)", min_value=0,
                                   max_value=200, value=DEFAULT_COST_BPS, step=5,
                                   help="25bp = 0.25%. 백테스트에만 차감됩니다.")
        if st.button("주가·DRAM·NAND 즉시 새로고침", use_container_width=True):
            with st.spinner("DRAM·NAND 최신 공개 시세를 확인 중..."):
                fetch_auto_spot_prices(force=True)
            st.session_state.refresh_token += 1
            st.rerun()
        st.caption("첫 실행은 현물가 공개 이력 백필과 워크포워드 "
                   "재학습으로 1~3분 걸릴 수 있습니다.")
        st.divider()
        st.caption("공개 배포 안전 기본값: 사용자 입력은 세션 격리 · XSRF/CORS 보호 · "
                   "공개 현물가 캐시만 공유")

    run_cached = st.cache_data(
        ttl=3600, show_spinner="DRAM·NAND 현물가와 워크포워드 모델을 계산 중..."
    )(run_pipeline)
    try:
        out = run_cached(horizon, period, st.session_state.spot_df,
                         st.session_state.refresh_token)
    except Exception as e:
        st.error(f"데이터 준비 실패: {e}")
        st.info("네트워크·티커 상태를 확인한 뒤 사이드바의 새로고침을 눌러보세요.")
        st.stop()

    prices, oos, scores = out["prices"], out["oos"], out["scores"]
    metrics = compute_metrics(oos, thr=thr, horizon=horizon)
    rel = reliability_summary(metrics)
    plans: dict[str, dict] = {}
    avail: list[str] = []
    if not scores.empty:
        smap = scores.set_index("ticker")
        avail = [s for s in TICKERS if s in smap.index and s in prices]
        for sym in avail:
            raw_score = float(smap.loc[sym, "score"])
            evidence = score_evidence(oos, sym, raw_score, horizon=horizon)
            plans[sym] = make_action_plan(
                prices[sym], raw_score, out["ret_stats"].get(sym), horizon, thr,
                ticker_currency(sym), quality=rel.get("quality", 0.35),
                evidence=evidence)

    latest_date = pd.to_datetime(scores["date"]).max() if not scores.empty else None
    best = max(plans, key=lambda s: plans[s]["decision_score"]) if plans else None
    worst = min(plans, key=lambda s: plans[s]["decision_score"]) if plans else None
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("모델 신뢰도", f"{rel['emoji']} {rel['grade']}",
              f"신호 반영 {rel.get('quality', .35):.0%}", delta_color="off")
    h2.metric("상대 최강", TICKERS[best] if best else "-",
              f"실행점수 {plans[best]['decision_score']:.0f}" if best else None,
              delta_color="off")
    h3.metric("상대 최약", TICKERS[worst] if worst else "-",
              f"실행점수 {plans[worst]['decision_score']:.0f}" if worst else None,
              delta_color="off")
    h4.metric("기준일", latest_date.strftime("%Y-%m-%d") if latest_date is not None else "-",
              f"{period.upper()} 학습 · {horizon}일 전망", delta_color="off")
    st.caption(f"기술·거시·상대강도 포함 {len(out['feat_cols'])}개 유효 피처 · "
               f"완전 OOS 예측 {len(oos):,}건 · "
               f"최근가중 반감기 {out['profile']['recency_half_life']}거래일")

    if out["missing"]:
        names = {**TICKERS, **MACRO}
        st.warning("일부 데이터 누락: " + ", ".join(
            f"{names.get(s, s)}({s})" for s in out["missing"]))

    tab_today, tab_chart, tab_pf, tab_validation, tab_data, tab_method = st.tabs(
        ["① 오늘의 결론", "② 기술적 차트", "③ 내 포트폴리오",
         "④ 검증·백테스트", "⑤ 현물가·데이터", "⑥ 모델 설명"])

    with tab_today:
        st.markdown('<div class="section-kicker">TODAY\'S DECISION</div>',
                    unsafe_allow_html=True)
        st.subheader("오늘 무엇을 할 것인가")
        st.info(f"{rel['emoji']} {rel['advice']}")
        if not plans:
            st.warning("신호를 계산할 수 없습니다.")
        else:
            board = []
            for sym in avail:
                p, cc = plans[sym], plans[sym]["ccy"]
                mine = out["data"][out["data"]["ticker"] == sym].sort_values("date")
                reasons = plain_reasons(sym, mine.iloc[-1]) if len(mine) else []
                ev = p["evidence"]
                evidence_text = (f"{ev['rate']:.0%} ({ev['lo']:.0%}~{ev['hi']:.0%}, n={ev['n']})"
                                 if ev.get("n", 0) else "표본 없음")
                board.append({
                    "종목": TICKERS[sym], "행동": f"{p['emoji']} {p['label']}",
                    "상승확률": f"{p['score']:.0f}%", "실행점수": f"{p['decision_score']:.0f}",
                    "과거 유사점수 실제상승": evidence_text,
                    "현재가": fmt_px(p["price"], cc), "매수 기준": fmt_px(p["buy"], cc),
                    f"목표({horizon}일)": fmt_px(p["target"], cc),
                    "예상범위(10~90%)": f"{fmt_px(p['t_lo'], cc)} ~ {fmt_px(p['t_hi'], cc)}",
                    "손절": fmt_px(p["stop"], cc),
                    "핵심 근거": " · ".join(reasons) if reasons else p["why_short"],
                })
            st.dataframe(pd.DataFrame(board), use_container_width=True, hide_index=True,
                         height=min(390, 38 * (len(board) + 1)))
            st.caption("상승확률은 시간순 홀드아웃으로 보정한 값입니다. 실행점수는 검증력이 "
                       "약할 때 확률을 50점 쪽으로 축소한 보수적 행동 점수입니다.")

            pick_detail = st.selectbox("상세 종목", avail, key="detail_pick",
                                       format_func=lambda s: TICKERS[s])
            p = plans[pick_detail]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("현재 행동", f"{p['emoji']} {p['label']}")
            c2.metric("보정 상승확률", f"{p['score']:.1f}%")
            c3.metric("기대수익률", f"{p['er']:+.1%}")
            c4.metric("ATR 위험폭", f"{p['atr'] / p['price']:.1%}")
            st.plotly_chart(ladder_fig(p, go), use_container_width=True)

    with tab_chart:
        st.markdown('<div class="section-kicker">TECHNICAL COCKPIT</div>',
                    unsafe_allow_html=True)
        st.subheader("가격과 기술적 신호를 함께 확인")
        if not avail:
            st.info("차트를 만들 수 있는 종목 데이터가 없습니다.")
        else:
            chart_pick = st.selectbox("차트 종목", avail, key="chart_pick",
                                      format_func=lambda s: TICKERS[s])
            snap = technical_snapshot(prices[chart_pick])
            t1, t2, t3, t4, t5 = st.columns(5)
            t1.metric("RSI(14)", f"{snap['rsi']:.1f}" if np.isfinite(snap['rsi']) else "-",
                      snap["rsi_state"], delta_color="off")
            t2.metric("MACD 모멘텀", f"{snap['macd']:+.3%}"
                      if np.isfinite(snap['macd']) else "-",
                      snap["macd_state"], delta_color="off")
            t3.metric("ADX(14)", f"{snap['adx']:.1f}" if np.isfinite(snap['adx']) else "-",
                      snap["adx_state"], delta_color="off")
            t4.metric("볼린저 %B", f"{snap['bb']:.0f}%" if np.isfinite(snap['bb']) else "-",
                      snap["bb_state"], delta_color="off")
            t5.metric("스토캐스틱 %K", f"{snap['stoch']:.0f}"
                      if np.isfinite(snap['stoch']) else "-", delta_color="off")
            chart_hist = oos[oos["ticker"] == chart_pick].tail(756)
            st.plotly_chart(
                technical_chart(prices[chart_pick], chart_hist, go, make_subplots),
                use_container_width=True)
            st.caption("차트 지표는 모두 해당 거래일 종가까지의 값만 사용합니다. 미국·환율·"
                       "금리 등 외생 데이터는 교차시장 시차 누수를 막기 위해 1거래일 늦춰 "
                       "학습됩니다.")

    with tab_pf:
        st.markdown('<div class="section-kicker">PORTFOLIO</div>',
                    unsafe_allow_html=True)
        st.subheader("내 포트폴리오")
        st.caption("현재 브라우저 세션 전용입니다. 영구 보관은 아래 CSV 다운로드를 사용하세요.")
        uploaded_pf = st.file_uploader("포트폴리오 CSV 불러오기", type=["csv"],
                                       key="pf_upload")
        if uploaded_pf is not None and st.button("불러온 포트폴리오 적용"):
            try:
                loaded = pd.read_csv(uploaded_pf)
                for col in PORT_COLS:
                    if col not in loaded.columns:
                        loaded[col] = {"모델연동": "자동", "배수": 1.0}.get(col, 0.0)
                st.session_state.portfolio_df = loaded[PORT_COLS]
                st.session_state.pop("pf_editor", None)
                st.rerun()
            except Exception as e:
                st.error(f"CSV 형식을 읽지 못했습니다: {e}")

        pf = st.data_editor(
            st.session_state.portfolio_df, num_rows="dynamic", hide_index=True,
            use_container_width=True, key="pf_editor",
            column_config={
                "티커": st.column_config.TextColumn("티커", required=True),
                "수량": st.column_config.NumberColumn("수량", min_value=0.0, format="%.6g"),
                "평단": st.column_config.NumberColumn("평단(현지통화)", min_value=0.0,
                                                     format="%.8g"),
                "모델연동": st.column_config.SelectboxColumn(
                    "모델연동", options=["자동", *TICKERS, "없음"], default="자동"),
                "배수": st.column_config.NumberColumn("배수", min_value=-5.0,
                                                     max_value=5.0, format="%.1f"),
            })
        st.session_state.portfolio_df = pf.copy()
        if local_persistence_enabled():
            save_portfolio(pf)
        st.download_button("포트폴리오 CSV 다운로드", pf.to_csv(index=False).encode("utf-8-sig"),
                           file_name="memory_portfolio.csv", mime="text/csv")

        qty_num = pd.to_numeric(pf["수량"], errors="coerce").fillna(0)
        held = tuple(sorted({str(t).strip() for t in pf.loc[qty_num > 0, "티커"]
                             if str(t).strip() and str(t).lower() != "nan"}))
        if not held:
            st.info("수량을 입력하면 평가액·손익·모델 반영 예상 자산이 표시됩니다.")
        else:
            quotes_cached = st.cache_data(ttl=300, show_spinner="보유 종목 시세 조회 중...")(
                fetch_quotes)
            try:
                quotes = quotes_cached(held)
            except Exception as e:
                quotes = {}
                st.error(f"시세 조회 실패: {e}")
            scores_map = ({} if scores.empty else dict(zip(scores["ticker"], scores["score"])))
            view, totals, failed = build_portfolio_view(
                pf, quotes, scores_map, out["ret_stats"], horizon, plans)
            if failed:
                st.warning("시세를 못 받은 티커: " + ", ".join(failed))
            if totals:
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("총 평가액", f"₩{totals['value']:,.0f}",
                          f"{totals['ret']:+.1%}" if np.isfinite(totals['ret']) else None)
                k2.metric(f"모델 예상자산({horizon}일)", f"₩{totals['exp_value']:,.0f}",
                          f"{totals['exp_ret']:+.1%}")
                k3.metric("예상 증감", f"₩{totals['exp_pnl']:+,.0f}")
                k4.metric("USD/KRW", f"₩{totals['usdkrw']:,.0f}"
                          if np.isfinite(totals['usdkrw']) else "-")
                fmt = {"수량": "{:,.6g}", "평단": "{:,.6g}", "현재가": "{:,.2f}",
                       "당일": "{:+.2%}", "매입액(₩)": "₩{:,.0f}",
                       "평가액(₩)": "₩{:,.0f}", "손익(₩)": "₩{:+,.0f}",
                       "수익률": "{:+.1%}", "점수": "{:.0f}", "배수": "{:.1f}",
                       f"예상수익률({horizon}일)": "{:+.1%}",
                       "예상평가액(₩)": "₩{:,.0f}"}
                st.dataframe(fmt_table(view, fmt), use_container_width=True, hide_index=True)
                st.caption("야후 지연시세·현재 환율 기준. 레버리지 배수는 경로 의존성을 "
                           "반영하지 못하므로 장기 예상치로 해석하면 안 됩니다.")

    with tab_validation:
        st.markdown('<div class="section-kicker">OUT-OF-SAMPLE AUDIT</div>',
                    unsafe_allow_html=True)
        st.subheader("검증 결과 — 확률과 수익을 따로 확인")
        if metrics is None:
            st.info("검증 표본이 없습니다.")
        else:
            st.markdown(f"{rel['emoji']} **{rel['grade']}** — {rel['advice']}")
            for line in rel["lines"]:
                st.markdown(f"- {line}")
            c1, c2, c3, c4, c5 = st.columns(5)
            lo, hi = metrics["accuracy_ci"]
            c1.metric("방향 적중률", f"{metrics['overall']:.1%}",
                      f"95% CI {lo:.1%}~{hi:.1%}", delta_color="off")
            c2.metric("베이스라인 대비", f"{metrics['overall']-metrics['naive']:+.1%}p")
            c3.metric("ROC-AUC", f"{metrics['auc']:.3f}")
            c4.metric("Brier skill", f"{metrics['brier_skill']:+.1%}")
            c5.metric("확률 보정오차(ECE)", f"{metrics['ece']:.1%}")

            left, right = st.columns(2)
            with left:
                cal = metrics["calibration"]
                fig = go.Figure()
                fig.add_scatter(x=[0, 1], y=[0, 1], name="완전 보정",
                                line=dict(dash="dash", color="gray"))
                fig.add_scatter(x=cal["예측확률"], y=cal["실제상승률"],
                                name="모델", mode="lines+markers",
                                marker=dict(size=np.clip(cal["표본수"] / 10, 7, 22)))
                fig.update_layout(title="확률 보정도", xaxis_tickformat=".0%",
                                  yaxis_tickformat=".0%", height=340,
                                  margin=dict(t=45, b=10))
                st.plotly_chart(fig, use_container_width=True)
            with right:
                pt_fmt = fmt_table(metrics["per_ticker"].rename(index=TICKERS),
                                   {"적중률": "{:.1%}", "실제상승비율": "{:.1%}",
                                    "평균점수": "{:.1f}", "표본수": "{:,.0f}"})
                st.markdown("**종목별 완전 아웃오브샘플 결과**")
                st.dataframe(pt_fmt, use_container_width=True)
                st.caption("상장 이력이 짧은 샌디스크·키옥시아는 표본수부터 확인하세요.")

            roll = metrics["rolling"]
            if not roll.empty:
                fig = go.Figure()
                fig.add_scatter(x=roll["date"], y=roll["모델 적중률"], name="모델")
                fig.add_scatter(x=roll["date"], y=roll["무조건 상승 적중률"],
                                name="무조건 상승", line=dict(dash="dot"))
                fig.update_layout(title="최근 250개 예측의 이동 적중률",
                                  yaxis_tickformat=".0%", height=320,
                                  margin=dict(t=45, b=10),
                                  legend=dict(orientation="h", y=1.12))
                st.plotly_chart(fig, use_container_width=True)

            st.markdown("#### 실행 가능한 백테스트")
            st.caption(f"t일 종가로 신호 계산 → t+1일 시가 진입 → {horizon}일 뒤 종가 청산 · "
                       f"비중복 구간 · 왕복비용 {cost_bps}bp")
            avail_bt = [s for s in TICKERS if s in oos["ticker"].unique()]
            if avail_bt:
                bt_pick = st.selectbox("백테스트 종목", avail_bt, key="bt_pick",
                                       format_func=lambda s: TICKERS[s])
                bt = equity_curve(oos, bt_pick, horizon, thr, int(cost_bps))
                if bt is None:
                    st.info("표본이 부족합니다.")
                else:
                    ec, bs = bt
                    b1, b2, b3, b4, b5 = st.columns(5)
                    b1.metric("누적수익", f"{bs['strategy']['total']:+.1%}")
                    b2.metric("CAGR", f"{bs['strategy']['cagr']:+.1%}")
                    b3.metric("MDD", f"{bs['strategy']['mdd']:.1%}")
                    b4.metric("Sharpe", f"{bs['strategy']['sharpe']:.2f}"
                              if np.isfinite(bs['strategy']['sharpe']) else "-")
                    b5.metric("거래/노출", f"{bs['trades']}회 / {bs['exposure']:.0%}")
                    fig = go.Figure()
                    fig.add_scatter(x=ec["date"], y=ec["시그널 추종"], name="시그널 추종")
                    fig.add_scatter(x=ec["date"], y=ec["단순 보유"], name="동일구간 보유",
                                    line=dict(dash="dot"))
                    fig.update_layout(height=360, yaxis_title="누적 배수",
                                      margin=dict(t=25, b=10),
                                      legend=dict(orientation="h", y=1.12))
                    st.plotly_chart(fig, use_container_width=True)

    with tab_data:
        st.markdown('<div class="section-kicker">MEMORY SPOT DATA</div>',
                    unsafe_allow_html=True)
        st.subheader("DRAM·NAND 현물가 자동 수집")
        st.caption(
            "TrendForce 공개 표의 최신 Session Average를 6시간마다 갱신하고, "
            "공개 주간 업데이트 기사로 학습 이력을 자동 백필합니다. "
            "현물가는 게시 다음 거래일부터만 모델이 보도록 지연합니다."
        )
        spot_status = out["spot_status"]
        status_text = spot_status.get("message", "-")
        if spot_status.get("state") in {"live", "cached"}:
            st.success(f"자동 수집 정상 · {status_text}")
        elif spot_status.get("state") in {"stale", "partial"}:
            st.warning(status_text)
        else:
            st.error(status_text)

        spot_loaded = out["spot_data"]
        spot_names = {
            "DRAM_DDR5_16Gb": "DRAM DDR5 16Gb",
            "DRAM_DDR4_8Gb": "DRAM DDR4 8Gb",
            "NAND_TLC_512Gb": "NAND TLC 512Gb",
        }
        quote_cols = st.columns(3)
        for box, col in zip(quote_cols, SPOT_DEFAULT_COLS):
            if col in spot_loaded:
                valid = pd.to_numeric(spot_loaded[col], errors="coerce").dropna()
            else:
                valid = pd.Series(dtype=float)
            if valid.empty:
                box.metric(spot_names[col], "-")
            else:
                idx = valid.index[-1]
                quote_date = pd.to_datetime(spot_loaded.loc[idx, "날짜"])
                box.metric(spot_names[col], f"US${valid.iloc[-1]:,.3f}",
                           quote_date.strftime("%Y-%m-%d"), delta_color="off")

        if out["spot_used"]:
            st.success("모델 실제 반영 중: "
                       + " · ".join(feat_label(c) for c in out["spot_used"]))
        elif spot_status.get("history_ready"):
            st.info("현물가 이력은 수집됐지만 현재 학습 기간에서 유효 피처가 아직 없습니다.")
        else:
            st.warning("자동 이력이 12개 이상 쌓여야 20/60일 변화율이 안정적으로 반영됩니다.")

        spot_cols = [c for c in spot_loaded.columns if c != "날짜"]
        if spot_cols and len(spot_loaded) >= 2:
            fig = go.Figure()
            for col in spot_cols:
                s = pd.to_numeric(spot_loaded.set_index("날짜")[col], errors="coerce").dropna()
                if len(s) >= 2 and s.iloc[0] != 0:
                    fig.add_scatter(x=s.index, y=s / s.iloc[0] * 100,
                                    name=col, mode="lines+markers")
            fig.update_layout(title="현물가 상대 추이 (첫 수집값=100)", height=330,
                              margin=dict(t=45, b=10),
                              legend=dict(orientation="h", y=1.12))
            st.plotly_chart(fig, use_container_width=True)

        st.download_button(
            "자동+수동 병합 현물가 CSV 다운로드",
            spot_loaded.to_csv(index=False).encode("utf-8-sig"),
            file_name="memory_spot_prices.csv", mime="text/csv",
        )
        if spot_status.get("errors"):
            with st.expander("수집 경고 상세"):
                st.code("\n".join(spot_status["errors"]))

        with st.expander("선택 사항 · 수동 보정/CSV 불러오기"):
            st.caption("자동값이 틀린 날짜만 입력하면 해당 셀이 우선 반영됩니다. "
                       "공개 배포에서는 현재 방문자 세션에만 저장됩니다.")
            spot_edit = st.data_editor(
                st.session_state.spot_df, num_rows="dynamic", hide_index=True,
                use_container_width=True,
                key=f"spot_editor_{st.session_state.spot_editor_version}",
                column_config={"날짜": st.column_config.DateColumn(
                    "날짜", required=True)},
            )
            sc1, sc2 = st.columns(2)
            if sc1.button("수동 보정 적용·재학습", use_container_width=True):
                clean = _normalise_spot_data(spot_edit)
                st.session_state.spot_df = clean
                if local_persistence_enabled():
                    save_spot_editor(clean)
                st.session_state.refresh_token += 1
                st.rerun()
            if sc2.button("수동 보정 초기화", use_container_width=True):
                st.session_state.spot_df = _normalise_spot_data(None)
                if local_persistence_enabled():
                    save_spot_editor(st.session_state.spot_df)
                st.session_state.spot_editor_version += 1
                st.session_state.refresh_token += 1
                st.rerun()

            uploaded_spot = st.file_uploader("현물가 CSV 불러오기", type=["csv"],
                                             key="spot_upload")
            if uploaded_spot is not None and st.button("불러온 현물가를 수동 보정으로 적용"):
                try:
                    loaded = pd.read_csv(io.BytesIO(uploaded_spot.getvalue()))
                    st.session_state.spot_df = _normalise_spot_data(loaded)
                    st.session_state.spot_editor_version += 1
                    st.session_state.refresh_token += 1
                    st.rerun()
                except Exception as e:
                    st.error(f"현물가 CSV를 읽지 못했습니다: {e}")

    with tab_method:
        st.markdown('<div class="section-kicker">MODEL & GOVERNANCE</div>',
                    unsafe_allow_html=True)
        st.subheader("모델이 무엇을 학습하는가")
        st.markdown(
            f"- **기술적 지표 확장:** RSI(7/14/28), MACD·시그널·히스토그램, "
            f"볼린저 %B·폭, 스토캐스틱, Williams %R, ADX·DI, CCI, MFI, OBV, "
            f"갭·캔들 변동폭 등 총 {len(out['feat_cols'])}개 유효 피처를 사용합니다.\n"
            "- **기간별 학습 프로파일:** 1년·3년·5년·10년·15년마다 첫 학습일, "
            "확률 보정 구간, 최근가중 반감기를 함께 바꿔 짧은 구간도 실제 학습됩니다.\n"
            "- **DRAM·NAND 자동 학습:** TrendForce 공개 표의 최신 Session Average와 "
            "공개 주간 업데이트의 DRAM DDR4 8Gb·NAND TLC 512Gb 이력을 "
            "20/60일 변화율로 바꿔 학습합니다.\n"
            "- **교차시장 시차 누수 차단:** 미국·환율·금리·현물가 피처를 "
            "1거래일 늦춰 한국 장에서 같은 날짜의 미확정/장 마감 후 "
            "게시 값을 보지 않게 했습니다.\n"
            "- **확률 보정:** 매 재학습 시 최근 252거래일을 시간순 홀드아웃으로 두고 "
            "sigmoid 보정을 한 뒤, Brier skill과 보정오차를 공개합니다.\n"
            f"- **레짐 적응:** 현재 {period.upper()} 설정에서는 최근 "
            f"{out['profile']['recency_half_life']}거래일을 가중 반감기로 사용하고 클래스 "
            "불균형을 완만히 보정합니다.\n"
            "- **실행 가능한 검증:** 신호 다음 날 시가 진입, 비중복 구간, 왕복 거래비용, "
            "CAGR·MDD·Sharpe를 적용합니다.\n"
            "- **불확실성 노출:** 현재 점수 근처의 과거 실제 상승률·표본수·Wilson 구간과 "
            "향후 수익률 10~90% 범위를 함께 보여줍니다.\n"
            "- **공개 배포 데이터 격리:** 공개 현물가 캐시만 공유하고, 포트폴리오와 "
            "수동 보정값은 방문자 세션에 저장합니다.\n"
            "- **동종그룹 상대강도:** 광범위 SOX 대비 강도 외에, 그날 다른 메모리 5종목 "
            "평균 대비 초과수익률(peer_rel20/60)을 추가했습니다. '어느 메모리주가 더 "
            "강한가'를 더 직접 겨냥합니다.\n"
            "- **피처 독식 방지:** 트리 분기마다 후보 피처의 70%만 보게 해(max_features), "
            "자기상관이 큰 매크로 지표 하나가 모든 분기를 독식해 다른 신호를 가리는 것을 "
            "막습니다.\n"
            "- **정직한 참고:** 가격·매크로만으로 20거래일 방향을 맞히는 문제는 원래 "
            "AUC 0.52~0.56 정도가 현실적인 상한권입니다. '신뢰도 낮음'이 뜨는 건 버그가 "
            "아니라 이 모델이 스스로의 한계를 숨기지 않는다는 뜻이며, 그 상태에서는 행동 "
            "점수를 50점 쪽으로 자동 축소합니다.")
        if out["importance"] is not None:
            imp = out["importance"].head(15).iloc[::-1]
            fig = go.Figure(go.Bar(x=imp.values, y=imp.index, orientation="h"))
            fig.update_layout(title="최종 모델 변수 중요도(순열 방식)", height=440,
                              margin=dict(t=50, b=10))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("최종 학습표본 기준 설명 도구이며 인과관계를 뜻하지 않습니다.")
        with st.expander("현재 지표 스냅샷"):
            snap_pick = st.selectbox("종목", [s for s in TICKERS if s in prices],
                                     key="snap_pick", format_func=lambda s: TICKERS[s])
            mine = out["data"][out["data"]["ticker"] == snap_pick]
            if not mine.empty:
                last = mine.sort_values("date").iloc[-1]
                rows = []
                for col in [c for c in out["feat_cols"] if not c.startswith("tk_")]:
                    if col in mine and pd.notna(last[col]):
                        histv = mine[col].dropna()
                        rows.append({"지표": feat_label(col),
                                     "현재값": round(feat_display_value(col, last[col]), 5),
                                     "역사적 백분위": f"{(histv < last[col]).mean():.0%}"})
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.warning("이 모델은 가격·기술적 지표·거시·자동 DRAM/NAND 현물가를 사용합니다. "
                   "실적 컨센서스 변경, 공급계약, "
                   "CAPEX, 재고, 지정학적 사건을 자동으로 읽지 않으므로 최종 투자결정을 "
                   "대체하지 않습니다.")

    st.divider()
    st.caption("데이터: Yahoo Finance 수정주가·지연시세 · TrendForce 공개 DRAM/NAND 현물가. "
               "확률·목표가·예상자산은 "
               "과거 패턴의 통계 추정치이며 수익을 보장하지 않습니다. 투자 판단과 결과의 "
               "책임은 사용자에게 있습니다.")


if __name__ == "__main__":
    main()
