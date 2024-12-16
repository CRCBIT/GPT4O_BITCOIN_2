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
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, WebDriverException, NoSuchElementException
import logging
from pydantic import BaseModel
import sqlite3
from datetime import datetime, timedelta
import schedule
from telegram import Bot
import asyncio
import re

# .env 파일에 저장된 환경 변수를 불러오기 (API 키 등)
load_dotenv()

# 로깅 설정 - 로그 레벨을 INFO로 설정하여 중요 정보 출력
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Upbit 객체 생성
access = os.getenv("UPBIT_ACCESS_KEY")
secret = os.getenv("UPBIT_SECRET_KEY")
if not access or not secret:
    logger.error("API keys not found. Please check your .env file.")
    raise ValueError("Missing API keys. Please check your .env file.")
upbit = pyupbit.Upbit(access, secret)

# 텔레그램 봇 설정
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    logger.error("Telegram bot configuration is missing. Check your .env file.")
    raise ValueError("Telegram bot configuration is missing.")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# 텔레그램 메시지 전송 함수
def send_telegram_message(message):
    async def _send_message():
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            logger.info(f"Message sent successfully: {message}")
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

# SQLite 데이터베이스 초기화 함수 - 거래 내역을 저장할 테이블을 생성
def init_db():
    conn = sqlite3.connect('bitcoin_trades.db')
    c = conn.cursor()
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
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  type TEXT,
                  amount REAL,
                  currency TEXT,
                  reason TEXT)''')
    conn.commit()
    return conn

# 거래 기록을 DB에 저장하는 함수
def log_trade(conn, decision, percentage, reason, btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price, reflection=''):
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute("""INSERT INTO trades 
                 (timestamp, decision, percentage, reason, btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price, reflection) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (timestamp, decision, percentage, reason, btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price, reflection))
    conn.commit()

# 입출금 내역을 DB에 저장하는 함수
def log_transaction(conn, transaction_type, amount, currency, reason=''):
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute("""INSERT INTO transactions 
                 (timestamp, type, amount, currency, reason) 
                 VALUES (?, ?, ?, ?, ?)""",
              (timestamp, transaction_type, amount, currency, reason))
    conn.commit()


# 최근 투자 기록 조회
def get_recent_trades(conn, days=7):
    c = conn.cursor()
    seven_days_ago = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("SELECT * FROM trades WHERE timestamp > ? ORDER BY timestamp DESC", (seven_days_ago,))
    columns = [column[0] for column in c.description]
    return pd.DataFrame.from_records(data=c.fetchall(), columns=columns)

# 최근 입출금 내역 조회
def get_recent_transactions(conn, days=7):
    c = conn.cursor()
    seven_days_ago = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("SELECT * FROM transactions WHERE timestamp > ? ORDER BY timestamp DESC", (seven_days_ago,))
    columns = [column[0] for column in c.description]
    return pd.DataFrame.from_records(data=c.fetchall(), columns=columns)

# 최근 투자 기록을 기반으로 퍼포먼스 계산 (초기 잔고 대비 최종 잔고)
def calculate_performance(trades_df):
    if trades_df.empty:
        return 0  # 기록이 없을 경우 0%로 설정
    # 초기 잔고 계산 (KRW + BTC * 현재 가격)
    initial_balance = trades_df.iloc[-1]['krw_balance'] + trades_df.iloc[-1]['btc_balance'] * trades_df.iloc[-1]['btc_krw_price']
    # 최종 잔고 계산
    final_balance = trades_df.iloc[0]['krw_balance'] + trades_df.iloc[0]['btc_balance'] * trades_df.iloc[0]['btc_krw_price']
    return (final_balance - initial_balance) / initial_balance * 100

# AI 모델을 사용하여 최근 투자 기록과 시장 데이터를 기반으로 분석 및 반성을 생성하는 함수
def generate_reflection(trades_df, current_market_data):
    performance = calculate_performance(trades_df)  # 투자 퍼포먼스 계산

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if not client.api_key:
        logger.error("OpenAI API key is missing or invalid.")
        return None
    # OpenAI API 호출로 AI의 반성 일기 및 개선 사항 생성 요청
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are an AI trading assistant tasked with analyzing in detail recent trading performance and current market conditions to generate insights and improvements for future trading decisions."
            },
            {
                "role": "user",
                "content": f"""
Recent trading data:
{trades_df.to_json(orient='records')}

Current market data:
{current_market_data}

Overall performance in the last 7 days: {performance:.2f}%

Please analyze in detail this data and provide:
1. A brief reflection on the recent trading decisions
2. Insights on what worked well and what didn't
3. Suggestions for improvement in future trading decisions
4. Any patterns or trends you notice in the market data

Limit your response to 250 words or less.
"""
            }
        ]
    )

    try:
        response_content = response.choices[0].message.content
        return response_content
    except (IndexError, AttributeError) as e:
        logger.error(f"Error extracting response content: {e}")
        return None

# 데이터프레임에 보조 지표를 추가하는 함수
def add_indicators(df):
    # 볼린저 밴드 추가
    indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_bbm'] = indicator_bb.bollinger_mavg()
    df['bb_bbh'] = indicator_bb.bollinger_hband()
    df['bb_bbl'] = indicator_bb.bollinger_lband()

    # RSI (Relative Strength Index) 추가
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()

    # MACD (Moving Average Convergence Divergence) 추가
    macd = ta.trend.MACD(close=df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = macd.macd_diff()

    # 이동평균선 (단기, 장기)
    df['sma_20'] = ta.trend.SMAIndicator(close=df['close'], window=20).sma_indicator()
    df['ema_12'] = ta.trend.EMAIndicator(close=df['close'], window=12).ema_indicator()

    # Stochastic Oscillator 추가
    stoch = ta.momentum.StochasticOscillator(
        high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
    df['stoch_k'] = stoch.stoch()
    df['stoch_d'] = stoch.stoch_signal()

    # Average True Range (ATR) 추가
    df['atr'] = ta.volatility.AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()

    # On-Balance Volume (OBV) 추가
    df['obv'] = ta.volume.OnBalanceVolumeIndicator(
        close=df['close'], volume=df['volume']).on_balance_volume()

    return df

# 공포 탐욕 지수 조회
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

# 글로벌 변수로 마지막 뉴스 요청 시간 저장
last_news_fetch_time = None
cached_news = []

# 뉴스 데이터 가져오기 (오전 한 번, 오후 한 번만 실행)
def get_bitcoin_news():
    global last_news_fetch_time, cached_news
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    if not serpapi_key:
        logger.error("SERPAPI API key is missing.")
        return []  # 빈 리스트 반환

    # 현재 시간
    now = datetime.now()

    # 오전/오후 시간 체크 (오전: 00:00~11:59, 오후: 12:00~23:59)
    current_period = "morning" if now.hour < 12 else "afternoon"
    last_period = None
    if last_news_fetch_time:
        last_period = "morning" if last_news_fetch_time.hour < 12 else "afternoon"

    # 같은 기간(오전 또는 오후)에 요청된 경우 캐싱된 뉴스 반환
    if last_news_fetch_time and current_period == last_period:
        logger.info("Using cached news data:")
        log_news_to_console(cached_news)
        return cached_news

    # 새로운 뉴스 요청
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
        last_news_fetch_time = now  # 뉴스 요청 시간 갱신

        # 콘솔에 뉴스 로그 출력
        logger.info("Fetched new news data:")
        log_news_to_console(cached_news)

        return cached_news
    except requests.RequestException as e:
        logger.error(f"Error fetching news: {e}")
        return cached_news  # 실패 시 캐시된 뉴스 반환

# 뉴스 헤드라인 로그 포맷팅 함수
def log_news_to_console(news):
    if not news:
        logger.info("No news available.")
    else:
        logger.info("\n".join([f"- {item['title']} ({item['date']})" for item in news]))




### 메인 AI 트레이딩 로직
def ai_trading():
    global upbit
    ### 데이터 가져오기
    # 1. 현재 투자 상태 조회
    all_balances = upbit.get_balances()
    filtered_balances = [balance for balance in all_balances if balance['currency'] in ['BTC', 'KRW']]

    # 2. 오더북(호가 데이터) 조회
    orderbook = pyupbit.get_orderbook("KRW-BTC")

    # 3. 차트 데이터 조회 및 보조지표 추가
    df_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=180)
    df_daily = dropna(df_daily)
    df_daily = add_indicators(df_daily)

    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=168)  # 7 days of hourly data
    df_hourly = dropna(df_hourly)
    df_hourly = add_indicators(df_hourly)

    # 최근 데이터만 사용하도록 설정 (메모리 절약)
    df_daily_recent = df_daily.tail(120)
    df_hourly_recent = df_hourly.tail(48)

    # 4. 공포 탐욕 지수 가져오기
    fear_greed_index = get_fear_and_greed_index()

    # 5. 뉴스 헤드라인 가져오기
    news_headlines = get_bitcoin_news()

    # 6. 전략 추가

    f = open("strategy.txt", "r", encoding="utf-8")
    strategy = f.read()
    f.close()
    ### AI에게 데이터 제공하고 판단 받기
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if not client.api_key:
        logger.error("OpenAI API key is missing or invalid.")
        return None
    try:
        # 데이터베이스 연결
        with sqlite3.connect('bitcoin_trades.db') as conn:
            # 최근 거래 내역 가져오기
            recent_trades = get_recent_trades(conn)

            # 최근 입출금 내역 가져오기
            recent_transactions = get_recent_transactions(conn)

            # 현재 시장 데이터 수집 (기존 코드에서 가져온 데이터 사용)
            current_market_data = {
                "fear_greed_index": fear_greed_index,
                "news_headlines": news_headlines,
                "orderbook": orderbook,
                "daily_ohlcv": df_daily_recent.to_dict(),
                "hourly_ohlcv": df_hourly_recent.to_dict(),
                "transactions": recent_transactions.to_dict()
            }

            # 반성 및 개선 내용 생성
            reflection = generate_reflection(recent_trades, current_market_data)

            # AI 모델에 반성 내용 제공
            # Few-shot prompting으로 JSON 예시 추가
            examples = """
Example Response 1:
{
  "decision": "buy",
  "percentage": 50,
  "reason": "Based on the current market indicators and positive news, it's a good opportunity to invest."
}

Example Response 2:
{
  "decision": "sell",
  "percentage": 30,
  "reason": "Due to negative trends in the market and high fear index, it is advisable to reduce holdings."
}

Example Response 3:
{
  "decision": "hold",
  "percentage": 0,
  "reason": "Market indicators are neutral; it's best to wait for a clearer signal."
}
"""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": f"""You are an expert in Bitcoin investing. This analysis is performed every 1 hours. Analyze in detail the provided data and determine whether to buy, sell, or hold at the current moment. Consider the following in your analysis step by step:

- Technical indicators and market data
- Recent news headlines and their potential impact on Bitcoin price
- The Fear and Greed Index and its implications
- Overall market sentiment
- Recent trading performance and reflection

Recent trading reflection:
{reflection}

Here's the strategy of an anonymous successful Bitcoin investor in South Korea. you should keep in mind:

{strategy}

Based on your analysis, make a decision and provide your reasoning.

Please provide your response in the following JSON format:

{examples}

Ensure that the percentage is an integer between 1 and 100 for buy/sell decisions, and exactly 0 for hold decisions.
Your percentage should reflect the strength of your conviction in the decision based on the analyzed data.
"""
                    },
                    {
                        "role": "user",
                        "content": f"""Current investment status: {json.dumps(filtered_balances)}
Orderbook: {json.dumps(orderbook)}
Daily OHLCV with indicators (recent 60 days): {df_daily_recent.to_json()}
Hourly OHLCV with indicators (recent 48 hours): {df_hourly_recent.to_json()}
Recent news headlines: {json.dumps(news_headlines)}
Fear and Greed Index: {json.dumps(fear_greed_index)}
Transactions: {recent_transactions.to_json()}
"""
                    }
                ]
            )

            response_text = response.choices[0].message.content

            # AI 응답 파싱
            def parse_ai_response(response_text):
                try:
                    # Extract JSON part from the response
                    json_match = re.search(r'\{.*?\}', response_text, re.DOTALL)
                    if json_match:
                        json_str = json_match.group(0)
                        # Parse JSON
                        parsed_json = json.loads(json_str)
                        decision = parsed_json.get('decision')
                        percentage = parsed_json.get('percentage')
                        reason = parsed_json.get('reason')
                        return {'decision': decision, 'percentage': percentage, 'reason': reason}
                    else:
                        logger.error("No JSON found in AI response.")
                        return None
                except json.JSONDecodeError as e:
                    logger.error(f"JSON parsing error: {e}")
                    return None

            parsed_response = parse_ai_response(response_text)
            if not parsed_response:
                logger.error("Failed to parse AI response.")
                return

            decision = parsed_response.get('decision')
            percentage = parsed_response.get('percentage')
            reason = parsed_response.get('reason')

            if not decision or reason is None:
                logger.error("Incomplete data in AI response.")
                return

            logger.info(f"AI Decision: {decision.upper()}")
            logger.info(f"Percentage: {percentage}")
            logger.info(f"Decision Reason: {reason}")

            order_executed = False

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
                            send_telegram_message(f"\u2705 비트코인 구매 완료\n구매 금액: {buy_amount:.2f} KRW\n잔액: {my_krw - buy_amount:.2f} KRW\n이유: {reason}")
                        else:
                            logger.error("Buy order failed.")
                    except Exception as e:
                        logger.error(f"Error executing buy order: {e}")
                else:
                    logger.warning("Buy Order Failed: Insufficient KRW (less than 5000 KRW)")
            elif decision == "sell":
                my_btc = upbit.get_balance("KRW-BTC")
                if my_btc is None:
                    logger.error("Failed to retrieve BTC balance.")
                    return
                sell_amount = my_btc * (percentage / 100)
                current_price = pyupbit.get_current_price("KRW-BTC")
                if sell_amount * current_price > 5000:

                    try:
                        order = upbit.sell_market_order("KRW-BTC", sell_amount)
                        if order:
                            logger.info(f"Sell Order Executed: {percentage}% of held BTC")
                            order_executed = True
                            send_telegram_message(f"\u2705 비트코인 판매 완료\n판매 수량: {sell_amount:.4f} BTC\n판매 금액: {sell_amount * current_price:.2f} KRW\n이유: {reason}")
                        else:
                            logger.error("Sell order failed.")
                    except Exception as e:
                        logger.error(f"Error executing sell order: {e}")
                else:
                    logger.warning("Sell Order Failed: Insufficient BTC (less than 5000 KRW worth)")
            elif decision == "hold":
                logger.info("Decision is to hold. No action taken.")
                send_telegram_message(f"\u2705 비트코인 홀드 결정\n현재 상태 유지\n이유: {reason}")
            else:
                logger.error("Invalid decision received from AI.")
                return

            # 거래 실행 여부와 관계없이 현재 잔고 조회
            time.sleep(2)  # API 호출 제한을 고려하여 잠시 대기
            balances = upbit.get_balances()
            btc_balance = next((float(balance['balance']) for balance in balances if balance['currency'] == 'BTC'), 0)
            krw_balance = next((float(balance['balance']) for balance in balances if balance['currency'] == 'KRW'), 0)
            btc_avg_buy_price = next((float(balance['avg_buy_price']) for balance in balances if balance['currency'] == 'BTC'), 0)
            current_btc_price = pyupbit.get_current_price("KRW-BTC")

            # 거래 기록을 DB에 저장하기
            log_trade(conn, decision, percentage if order_executed else 0, reason,
                      btc_balance, krw_balance, btc_avg_buy_price, current_btc_price, reflection)
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        return

if __name__ == "__main__":
    # 데이터베이스 초기화
    init_db()

    # 중복 실행 방지를 위한 변수
    trading_in_progress = False

    # 트레이딩 작업을 수행하는 함수
    def job():
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

    job()

    # 매 1시간마다 실행
    schedule.every().day.at("01:30").do(job)
    schedule.every().day.at("02:30").do(job)
    schedule.every().day.at("03:30").do(job)
    schedule.every().day.at("04:30").do(job)
    schedule.every().day.at("05:30").do(job)
    schedule.every().day.at("06:30").do(job)
    schedule.every().day.at("07:30").do(job)
    schedule.every().day.at("08:30").do(job)
    schedule.every().day.at("09:30").do(job)
    schedule.every().day.at("10:30").do(job)
    schedule.every().day.at("11:30").do(job)
    schedule.every().day.at("12:30").do(job)
    schedule.every().day.at("13:30").do(job)
    schedule.every().day.at("14:30").do(job)
    schedule.every().day.at("15:30").do(job)
    schedule.every().day.at("16:30").do(job)
    schedule.every().day.at("17:30").do(job)
    schedule.every().day.at("18:30").do(job)
    schedule.every().day.at("19:30").do(job)
    schedule.every().day.at("20:30").do(job)
    schedule.every().day.at("21:30").do(job)
    schedule.every().day.at("22:30").do(job)
    schedule.every().day.at("23:30").do(job)
    schedule.every().day.at("00:30").do(job)    

    while True:
        schedule.run_pending()
        time.sleep(1)
                                        





