import os
from dotenv import load_dotenv
import pyupbit
import pandas as pd
import json
from openai import OpenAI
import ta
from ta.utils import dropna
import time
import requests
import base64
from PIL import Image
import io
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, ElementClickInterceptedException,
    WebDriverException, NoSuchElementException
)
import logging
from pydantic import BaseModel
import sqlite3
from datetime import datetime, timedelta
import schedule
from telegram import Bot
import asyncio
import re

# 추가: 실시간 달러지수(DXY) 조회를 위해 yfinance 라이브러리를 활용
import yfinance as yf

# .env 파일에 저장된 환경 변수를 불러오기 (API 키 등)
load_dotenv()

# 로깅 설정 - 로그 레벨을 INFO로 설정하여 중요 정보 출력
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 현재 날짜와 시간을 가져옵니다.
now_dw = datetime.now()

# 현재 연도의 12월 27일 13시를 기준 시간으로 설정합니다.
target_dw = datetime(2024, 12, 27, 13, 0, 0)

# 현재 시간이 기준 시간 이전인지 확인하고 deposit_withdrawal 값을 설정합니다.
if now_dw < target_dw:
    deposit_withdrawal = 500000
else:
    deposit_withdrawal = 0

print(f"deposit_withdrawal = {deposit_withdrawal}")

# Upbit 객체 생성
access = os.getenv("UPBIT_ACCESS_KEY")
secret = os.getenv("UPBIT_SECRET_KEY")
if not access or not secret:
    logger.error("API keys not found. Please check your .env file.")
    raise ValueError("Missing API keys. Please check your .env file.")
upbit = pyupbit.Upbit(access, secret)


class TradingDecision(BaseModel):
    decision: str
    percentage: int
    reason: str


# ---------------------- DB 초기화 및 관련 함수 ---------------------- #
def init_db():
    """SQLite 데이터베이스 초기화 (테이블 생성)"""
    conn = sqlite3.connect('bitcoin_trades.db')
    c = conn.cursor()

    # trades 테이블: 매매 이력 저장
    c.execute('''CREATE TABLE IF NOT EXISTS trades
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  decision TEXT,
                  percentage INTEGER,
                  reason TEXT,
                  btc_balance REAL,
                  krw_balance REAL,
                  btc_avg_buy_price REAL,
                  btc_krw_price REAL,
                  reflection TEXT)''')
    
    # transactions 테이블: (예시) 입출금 이력 관리용
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  type TEXT,
                  amount REAL,
                  currency TEXT,
                  reason TEXT)''')

    # orderbook_snapshots 테이블: 1시간 간격의 오더북 스냅샷 저장
    # - snapshot_time: 스냅샷 생성 시각 (ISO8601)
    # - orderbook_json: 실제 오더북 데이터(JSON)를 문자열로 저장
    c.execute('''CREATE TABLE IF NOT EXISTS orderbook_snapshots
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  snapshot_time TEXT,
                  orderbook_json TEXT)''')

    conn.commit()
    return conn


def log_trade(conn, decision, percentage, reason,
              btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price,
              reflection=''):
    """거래 기록을 trades 테이블에 저장"""
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute("""INSERT INTO trades 
                 (timestamp, decision, percentage, reason, 
                  btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price, reflection) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (timestamp, decision, percentage, reason,
               btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price, reflection))
    conn.commit()


def get_recent_trades(conn, days=7):
    """최근 N일(days) 동안의 trades 이력 조회 -> Pandas DataFrame 반환"""
    c = conn.cursor()
    cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("SELECT * FROM trades WHERE timestamp > ? ORDER BY timestamp DESC", (cutoff_time,))
    columns = [column[0] for column in c.description]
    return pd.DataFrame.from_records(data=c.fetchall(), columns=columns)


def calculate_performance(trades_df):
    """최근 투자 기록을 기반으로 퍼포먼스 계산 (초기 잔고 대비 최종 잔고 비율)"""
    if trades_df.empty:
        return 0  # 기록이 없을 경우 0%로 설정
    # trades_df는 최신 순으로 정렬되어 있으므로
    initial_balance = (
        trades_df.iloc[-1]['krw_balance']
        + trades_df.iloc[-1]['btc_balance'] * trades_df.iloc[-1]['btc_krw_price']
        + deposit_withdrawal
    )
    final_balance = (
        trades_df.iloc[0]['krw_balance']
        + trades_df.iloc[0]['btc_balance'] * trades_df.iloc[0]['btc_krw_price']
    )
    return (final_balance - initial_balance) / initial_balance * 100


# ---------------------- 오더북 스냅샷 관련 함수 ---------------------- #
def store_orderbook_snapshot():
    """
    1시간마다 호출되어 오더북 스냅샷을 DB에 저장하는 함수.
    schedule 등을 이용해 주기적으로 실행.
    """
    conn = sqlite3.connect('bitcoin_trades.db')
    c = conn.cursor()

    try:
        ob = pyupbit.get_orderbook("KRW-BTC")
        if ob is None:
            logger.warning("Failed to get orderbook from Upbit.")
            return
        snapshot_time = datetime.now().isoformat()
        # JSON 직렬화해서 문자열로 저장
        ob_str = json.dumps(ob, ensure_ascii=False)
        c.execute(""" 
            INSERT INTO orderbook_snapshots (snapshot_time, orderbook_json)
            VALUES (?, ?)
        """, (snapshot_time, ob_str))
        conn.commit()
        logger.info(f"[store_orderbook_snapshot] Successfully stored snapshot at {snapshot_time}")
    except Exception as e:
        logger.error(f"Error storing orderbook snapshot: {e}")
    finally:
        conn.close()


def get_recent_orderbook_snapshots(conn, hours=8):
    """
    최근 X시간 동안 저장된 오더북 스냅샷을 모두 조회.
    hours=8로 하면 8시간치 스냅샷(1시간 간격 * 최대 8개 예상) 반환.
    """
    c = conn.cursor()
    cutoff_time = (datetime.now() - timedelta(hours=hours)).isoformat()
    c.execute("""
        SELECT snapshot_time, orderbook_json 
        FROM orderbook_snapshots
        WHERE snapshot_time >= ?
        ORDER BY snapshot_time ASC
    """, (cutoff_time,))
    rows = c.fetchall()
    # 각 row -> [{'snapshot_time': ..., 'orderbook': ...}, ...] 형태로 파싱
    snapshots = []
    for row in rows:
        stime, ob_json = row
        try:
            ob_data = json.loads(ob_json)
        except json.JSONDecodeError:
            ob_data = None
        snapshots.append({
            "snapshot_time": stime,
            "orderbook": ob_data
        })
    return snapshots


# ---------------------- AI 분석(Reflection) 관련 함수 ---------------------- #
def generate_reflection(trades_df, current_market_data):
    """
    AI 모델을 사용하여 최근 투자 기록과 시장 데이터를 기반으로 반성(Reflection)을 생성
    """
    performance = calculate_performance(trades_df)
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if not client.api_key:
        logger.error("OpenAI API key is missing or invalid.")
        return None

    # GPT 호출
    try:
        response = client.chat.completions.create(
            model="o3-mini-2025-01-31",
            messages=[
                {
                    "role": "developer",
                    "content": "You are an AI trading assistant tasked with analyzing recent trading performance and current market conditions to generate insights and improvements for future trading decisions."
                },
                {
                    "role": "user",
                    "content": f"""
Recent trading data:
{trades_df.to_json(orient='records')}

Current market data:
{current_market_data}

Overall performance in the last 7 days: {performance:.2f}%

Please analyze this data and provide:
1. A brief reflection on the recent trading decisions
2. Insights on what worked well and what didn't
3. Suggestions for improvement in future trading decisions
4. Any patterns or trends you notice in the market data

When describing each of the above, be sure to mention the data numbers that are important to your judgment and explain why.

Limit your response to 250 words or less.
"""
                }
            ], reasoning_effort= "high"
        )
    except Exception as e:
        logger.error(f"Error generating reflection from GPT: {e}")
        return None

    try:
        response_content = response.choices[0].message.content
        return response_content
    except (IndexError, AttributeError) as e:
        logger.error(f"Error extracting response content: {e}")
        return None


# ---------------------- 지표 계산 함수(예시) ---------------------- #
def add_indicators(df):
    """ta 라이브러리를 활용하여 각종 지표를 추가"""
    indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_bbm'] = indicator_bb.bollinger_mavg()
    df['bb_bbh'] = indicator_bb.bollinger_hband()
    df['bb_bbl'] = indicator_bb.bollinger_lband()

    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
    macd = ta.trend.MACD(close=df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = macd.macd_diff()

    df['sma_20'] = ta.trend.SMAIndicator(close=df['close'], window=20).sma_indicator()
    df['ema_12'] = ta.trend.EMAIndicator(close=df['close'], window=12).ema_indicator()

    stoch = ta.momentum.StochasticOscillator(
        high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
    df['stoch_k'] = stoch.stoch()
    df['stoch_d'] = stoch.stoch_signal()

    df['atr'] = ta.volatility.AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()

    df['obv'] = ta.volume.OnBalanceVolumeIndicator(
        close=df['close'], volume=df['volume']).on_balance_volume()
    return df


# ---------------------- 외부 API 예시 함수(공포탐욕지수, 뉴스) ---------------------- #
def get_fear_and_greed_index():
    url = "https://api.alternative.me/fng/"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data['data'][0]
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching Fear and Greed Index: {e}")
        return None


last_news_fetch_time = None
cached_news = []


def get_bitcoin_news():
    """SERPAPI 사용 예시 (비트코인 관련 최신 뉴스 검색)"""
    global last_news_fetch_time, cached_news
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    if not serpapi_key:
        logger.error("SERPAPI API key is missing.")
        return []

    now = datetime.now()
    current_period = "morning" if now.hour < 12 else "afternoon"
    last_period = None
    if last_news_fetch_time:
        last_period = "morning" if last_news_fetch_time.hour < 12 else "afternoon"

    # 같은 반나절에는 캐시 재활용
    if last_news_fetch_time and current_period == last_period:
        logger.info("Using cached news data:")
        log_news_to_console(cached_news)
        return cached_news

    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_news",
        "q": "bitcoin OR btc",
        "api_key": serpapi_key
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        news_results = data.get("news_results", [])
        cached_news = [
            {"title": item.get("title", ""), "date": item.get("date", "")}
            for item in news_results[:15]
        ]
        last_news_fetch_time = now

        logger.info("Fetched new news data:")
        log_news_to_console(cached_news)
        return cached_news

    except requests.RequestException as e:
        logger.error(f"Error fetching news: {e}")
        return cached_news


def log_news_to_console(news):
    if not news:
        logger.info("No news available.")
    else:
        logger.info("\n".join([f"- {item['title']} ({item['date']})" for item in news]))


# ---------------------- (추가) 달러지수(DXY) 실시간 조회 함수 ---------------------- #
def get_dollar_index():
    """
    DX-Y.NYB 데이터를 yfinance로 받아,
    최종적으로 인덱스를 KST(Asia/Seoul)로 변환한 뒤,
    DataFrame 형태로 반환 (Close, + timestamp_kst).
    """
    try:
        ticker = yf.Ticker("DX-Y.NYB")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        # 7일치 1시간봉 데이터
        df = ticker.history(start=start_date, end=end_date, interval="1h")
        if df.empty:
            return None
        
        # 인덱스 TZ 확인
        current_tz = df.index.tz
        
        # Naive면 'America/New_York'로 간주
        if current_tz is None:
            df.index = df.index.tz_localize('America/New_York')
            print("DEBUG: localized to US/Eastern.")
            
        # KST 변환
        df.index = df.index.tz_convert('Asia/Seoul')
        
        # 1) Close만 추출 + 복사
        df_kst = df[["Close"]].copy()
        # 2) 인덱스를 KST 문자열로 만들어 별도 컬럼 'timestamp_kst'에 저장
        df_kst["timestamp_kst"] = df_kst.index.strftime('%Y-%m-%d %H:%M:%S %Z%z')
        # 3) 인덱스는 더 이상 필요 없으므로 reset_index (drop=True)
        df_kst.reset_index(drop=True, inplace=True)
        
        return df_kst
    
    except Exception as e:
        logger.error(f"Error fetching DXY from yfinance: {e}")
        return None


# ---------------------- (추가) 채권 수익률 실시간 조회 함수 ---------------------- #
def get_bond_yield():
    """
    미국 10년물 채권 수익률 데이터를 yfinance로 받아,
    최종적으로 인덱스를 KST(Asia/Seoul)로 변환한 뒤,
    DataFrame 형태로 반환 (Close, + timestamp_kst).
    """
    try:
        # 미국 10년물 채권 수익률 티커 (^TNX)를 사용
        ticker = yf.Ticker("^TNX")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        # 7일치 1시간봉 데이터
        df = ticker.history(start=start_date, end=end_date, interval="1h")
        if df.empty:
            return None
        
        current_tz = df.index.tz
        if current_tz is None:
            df.index = df.index.tz_localize('America/New_York')
        
        df.index = df.index.tz_convert('Asia/Seoul')
        
        df_kst = df[["Close"]].copy()
        df_kst["timestamp_kst"] = df_kst.index.strftime('%Y-%m-%d %H:%M:%S %Z%z')
        df_kst.reset_index(drop=True, inplace=True)
        
        return df_kst
    
    except Exception as e:
        logger.error(f"Error fetching Bond Yield from yfinance: {e}")
        return None


# ---------------------- 메인 AI 트레이딩 로직 ---------------------- #
def ai_trading():
    """8시간 간격으로 실행되는 메인 트레이딩 로직"""
    global upbit

    # 현재 잔고 조회
    all_balances = upbit.get_balances()
    filtered_balances = [balance for balance in all_balances if balance['currency'] in ['BTC', 'KRW']]

    # 시간봉/일봉 차트 데이터 수집 + 지표 계산
    df_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=180)
    df_daily = dropna(df_daily)
    df_daily = add_indicators(df_daily)

    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=168)
    df_hourly = dropna(df_hourly)
    df_hourly = add_indicators(df_hourly)

    df_daily_recent = df_daily.tail(60)
    df_hourly_recent = df_hourly.tail(48)

    # 외부 데이터 (공포탐욕지수, 뉴스)
    fear_greed_index = get_fear_and_greed_index()
    news_headlines = get_bitcoin_news()

    # (추가) 달러지수(DXY) 실시간 가져오기
    dxy_series = get_dollar_index()
    if dxy_series is not None:
        dxy_value_json = dxy_series.to_json(date_format='iso', orient='index')
    else:
        dxy_value_json = None

    # (추가) 채권 수익률 실시간 가져오기
    bond_yield_series = get_bond_yield()
    if bond_yield_series is not None:
        bond_yield_json = bond_yield_series.to_json(date_format='iso', orient='index')
    else:
        bond_yield_json = None

    # --- 최근 8시간의 오더북 스냅샷 불러오기 --- #
    with sqlite3.connect('bitcoin_trades.db') as conn:
        recent_orderbooks = get_recent_orderbook_snapshots(conn, hours=8)

    # (추가) 현재 오더북(실시간)도 한 번 가져오고 싶다면:
    current_orderbook = pyupbit.get_orderbook("KRW-BTC")
    
    # ----------------- 여기까지 데이터 준비 완료 ----------------- #
    # AI가 참고할 시장 데이터 구성
    current_market_data = {
        "fear_greed_index": fear_greed_index,
        "dollar_index(DXY)": dxy_value_json,
        "U.S. 10-Year Treasury Yield(^TNX)": bond_yield_json,
        "news_headlines": news_headlines,
        "orderbook_history": recent_orderbooks,
        "daily_ohlcv": df_daily_recent.to_dict(),
        "hourly_ohlcv": df_hourly_recent.to_dict(),
    }

    # DB 연결해서 최근 트레이드 이력과 reflection 생성
    try:
        with sqlite3.connect('bitcoin_trades.db') as conn:
            recent_trades = get_recent_trades(conn)
            reflection = generate_reflection(recent_trades, current_market_data)

            # AI에게 "매수/매도/홀드" 의사결정 요청
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            if not client.api_key:
                logger.error("OpenAI API key is missing or invalid.")
                return None

            response = client.chat.completions.create(
                model="o3-mini-2025-01-31",
                messages=[
                    {
                        "role": "developer",
                        "content": f"""You are an expert in Bitcoin trading strategies. This analysis is performed every 4 hours. 

You have already produced a factual reflection of recent trading performance (see {reflection}) in the previous step. Now, based on that reflection plus the latest market data provided, decide whether to BUY, SELL, or HOLD at this exact moment. 

Please follow these instructions:

1. Incorporate the prior Reflection’s insights (factual performance review, lessons learned, etc.).
2. Analyze current technical indicators (OHLCV with indicators, past 8 hours orderbook data), recent headlines, the Fear and Greed Index, 7-days recent Dollar Index (DXY), and 7-days recent U.S. 10-Year Treasury Yield (TNX))
3. Include the overall market sentiment, including any short-term or long-term signals you see.
4. Recommend a single decision: “buy,” “sell,” or “hold.”
5. For “buy” or “sell,” provide an integer percentage (1-100) that reflects how strongly you believe in that position size relative to available capital. For “hold,” the percentage must be 0.
6. Give a clear, data-driven explanation: cite specific numbers (RSI levels, support/resistance, etc.) or events (headline summaries, fear/greed values, volume spikes, DXY trend) that influenced your reasoning.

Your goal is to generate a well-grounded yet potentially creative or aggressive strategy based on the data. If strong signals point to an opportunity, you may propose a larger percentage, but justify it with numbers.

Ensure that the percentage is an integer between 1 and 100 for buy/sell decisions, and exactly 0 for hold decisions.
Your percentage should reflect the strength of your conviction in the decision based on the analyzed data.
"""
                    },
                    {
                        "role": "user",
                        "content": f"""Recent trading reflection from the previous step:
{reflection}

Current investment status: {json.dumps(filtered_balances)}
Orderbook snapshots (last 8 hours): {json.dumps(recent_orderbooks)}
Daily OHLCV (recent 60 days) with indicators: {df_daily_recent.to_json()}
Hourly OHLCV (recent 48 hours) with indicators: {df_hourly_recent.to_json()}
Recent news headlines: {json.dumps(news_headlines)}
Fear and Greed Index: {json.dumps(fear_greed_index)}
Dollar Index (DXY, recent 7 days): {dxy_value_json}
U.S. 10-Year Treasury Yield (^TNX, recent 7 days): {bond_yield_json}
"""
                    }
                ], response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "trading_decision",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "decision": {"type": "string", "enum": ["buy", "sell", "hold"]},
                                "percentage": {"type": "integer"},
                                "reason": {"type": "string"}
                            },
                            "required": ["decision", "percentage", "reason"],
                            "additionalProperties": False
                        }
                    }
                }, reasoning_effort="high"
            )

            response_text = response.choices[0].message.content

            # AI 응답(JSON) 직접 파싱
            try:
                parsed_response = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing error: {e}")
                return

            decision = parsed_response.get('decision')
            percentage = parsed_response.get('percentage')
            reason = parsed_response.get('reason')

            logger.info(f"AI Decision: {decision.upper()}")
            logger.info(f"Percentage: {percentage}")
            logger.info(f"Decision Reason: {reason}")

            order_executed = False

            # ---------------------- 주문 실행 로직 ---------------------- #
            if decision == "buy":
                my_krw = upbit.get_balance("KRW")
                if my_krw is None:
                    logger.error("Failed to retrieve KRW balance.")
                    return
                buy_amount = my_krw * (percentage / 100) * 0.9995  # 수수료 고려
                if buy_amount > 5000:
                    logger.info(f"Buy Order Executed: {percentage}% of available KRW")
                    try:
                        order = upbit.buy_market_order("KRW-BTC", buy_amount)
                        if order:
                            logger.info(f"Buy order executed successfully: {order}")
                            order_executed = True
                        else:
                            logger.error("Buy order failed.")
                    except Exception as e:
                        logger.error(f"Error executing buy order: {e}")
                else:
                    logger.warning("Buy Order Failed: Insufficient KRW (less than 5000 KRW).")
            
            elif decision == "sell":
                my_btc = upbit.get_balance("KRW-BTC")
                if my_btc is None:
                    logger.error("Failed to retrieve BTC balance.")
                    return
                sell_amount = my_btc * (percentage / 100)
                current_price = pyupbit.get_current_price("KRW-BTC")
                if sell_amount * current_price > 5000:
                    logger.info(f"Sell Order Executed: {percentage}% of held BTC")
                    try:
                        order = upbit.sell_market_order("KRW-BTC", sell_amount)
                        if order:
                            order_executed = True
                        else:
                            logger.error("Sell order failed.")
                    except Exception as e:
                        logger.error(f"Error executing sell order: {e}")
                else:
                    logger.warning("Sell Order Failed: Insufficient BTC (less than 5000 KRW worth).")
            
            elif decision == "hold":
                logger.info("Decision is to hold. No action taken.")
            
            else:
                logger.error("Invalid decision received from AI.")
                return

            # 거래 실행 후 잔고 갱신
            time.sleep(2)  # API호출 제한 고려
            balances = upbit.get_balances()
            btc_balance = next((float(b['balance']) for b in balances if b['currency'] == 'BTC'), 0)
            krw_balance = next((float(b['balance']) for b in balances if b['currency'] == 'KRW'), 0)
            btc_avg_buy_price = next((float(b['avg_buy_price']) for b in balances if b['currency'] == 'BTC'), 0)
            current_btc_price = pyupbit.get_current_price("KRW-BTC")

            # 거래 기록 DB 삽입
            log_trade(conn,
                      decision,
                      percentage if order_executed else 0,
                      reason,
                      btc_balance,
                      krw_balance,
                      btc_avg_buy_price,
                      current_btc_price,
                      reflection)

    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        return


# ---------------------- 메인 실행 (스케줄 설정) ---------------------- #
if __name__ == "__main__":
    init_db()

    trading_in_progress = False

    def job_ai_trading():
        """8시간 간격으로 실행되는 트레이딩 스케줄 함수"""
        global trading_in_progress
        if trading_in_progress:
            logger.warning("Trading job is already in progress, skipping this run.")
            return
        try:
            trading_in_progress = True
            ai_trading()
        except Exception as e:
            logger.error(f"An error occurred: {e}")
        finally:
            trading_in_progress = False

    def job_orderbook_snapshot():
        """1시간 간격으로 오더북 스냅샷을 저장하는 스케줄 함수"""
        store_orderbook_snapshot()

    job_ai_trading()

    # 스케줄 등록
    # 1) 오더북 스냅샷: 매 정시(:29/59)에 30분 간격으로 실행
    schedule.every().hour.at(":29").do(job_orderbook_snapshot)
    schedule.every().hour.at(":59").do(job_orderbook_snapshot)

    # 2) 8시간마다 트레이딩 실행 (예: 04:30, 12:30, 20:30)
    schedule.every().day.at("04:30").do(job_ai_trading)
    schedule.every().day.at("12:30").do(job_ai_trading)
    schedule.every().day.at("20:30").do(job_ai_trading)
    schedule.every().day.at("08:30").do(job_ai_trading)
    schedule.every().day.at("16:30").do(job_ai_trading)
    schedule.every().day.at("00:30").do(job_ai_trading)
    
    # 메인 루프
    while True:
        schedule.run_pending()
        time.sleep(1)
