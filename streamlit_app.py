import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pyupbit
import numpy as np
import math
from datetime import datetime, timedelta
from streamlit_autorefresh import st_autorefresh

deposit_withdrawal = 0

# 항상 wide 모드 활성화, 제목 및 사이드바 설정
st.set_page_config(
    layout="wide",
    page_title="AI BTC (o3-mini)",
    page_icon="📈",
    initial_sidebar_state="collapsed"
)

# 사용자 정의 CSS를 주입하여 여백 줄이기 및 제목 아래에 밑줄 추가
st.markdown(
    """
    <style>
    /* 메인 컨테이너의 상단 패딩 줄이기 */
    .block-container {
        padding-top: 1rem;
    }
    /* 제목 위의 여백 제거 및 텍스트 아래에 밑줄 추가 */
    h1 {
        margin-top: 0;
        margin-bottom: 0.5rem;
        text-decoration: underline;
        text-decoration-color: currentColor;
        text-decoration-thickness: 2px;
        font-size: 30px !important;
    }
    /* 모든 h3 요소에 일관된 스타일 적용 */
    h3 {
        margin-top: 0.5rem;
        margin-bottom: 0.5rem;
        font-size: 20px;
    }
    /* 추가적인 여백 제거 (필요 시) */
    .css-18e3th9 {
        padding-top: 1rem;
    }
    .stPlotlyChart {
        padding-top: 0 !important;
        padding-bottom: 0 !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

def get_connection():
    """SQLite 데이터베이스에 연결합니다."""
    return sqlite3.connect('bitcoin_trades.db')

def load_data():
    """트레이드 데이터를 데이터베이스에서 로드하고 타임스탬프를 datetime 형식으로 변환합니다."""
    conn = get_connection()
    query = "SELECT * FROM trades ORDER BY timestamp ASC"  # 시간 순서대로 정렬
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def calculate_initial_investment(df):
    """초기 투자 금액(투자 시작 시점의 평가금액 + 예치금/출금액 반영)을 계산합니다."""
    initial_krw_balance = df.iloc[0]['krw_balance']
    initial_btc_balance = df.iloc[0]['btc_balance']
    initial_btc_price = df.iloc[0]['btc_krw_price']
    return initial_krw_balance + (initial_btc_balance * initial_btc_price) + deposit_withdrawal

def calculate_current_investment(df):
    """현재 투자 금액(마지막 보유 KRW + 마지막 보유 BTC * 현재 BTC 시세)을 계산합니다."""
    current_krw_balance = df.iloc[-1]['krw_balance']
    current_btc_balance = df.iloc[-1]['btc_balance']
    current_btc_price = pyupbit.get_current_price("KRW-BTC")
    if current_btc_price is None:
        # pyupbit API 실패 시 마지막 btc_krw_price 사용
        current_btc_price = df.iloc[-1]['btc_krw_price']
    return current_krw_balance + (current_btc_balance * current_btc_price)

def add_buy_sell_markers(fig, df, x_col, y_col, border_color='black'):
    """
    BUY와 SELL 마커를 Plotly 그래프에 추가합니다.
    테두리 색상을 매개변수로 받아 설정합니다.
    """
    buy_points = df[df['decision'] == 'buy']
    sell_points = df[df['decision'] == 'sell']

    if not buy_points.empty:
        fig.add_trace(go.Scatter(
            x=buy_points[x_col],
            y=buy_points[y_col],
            mode='markers',
            marker=dict(
                size=10,
                color='red',
                symbol='triangle-up',
                line=dict(width=1.5, color=border_color)
            ),
            name='Buy',
            hovertemplate="<b>Buy</b><br>Time: %{x}<br>Price: %{y:,} KRW"
        ))

    if not sell_points.empty:
        fig.add_trace(go.Scatter(
            x=sell_points[x_col],
            y=sell_points[y_col],
            mode='markers',
            marker=dict(
                size=10,
                color='blue',
                symbol='triangle-down',
                line=dict(width=1.5, color=border_color)
            ),
            name='Sell',
            hovertemplate="<b>Sell</b><br>Time: %{x}<br>Price: %{y:,} KRW"
        ))

    return fig

def resample_portfolio_daily(df):
    """
    내 포트폴리오를 하루 단위로 리샘플하여
    - total_assets (마지막 값)
    - 일간 수익률, 누적수익률 계산
    """
    if 'total_assets' not in df.columns:
        df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
    
    df_daily = df.set_index('timestamp').resample('D').last().dropna(subset=['total_assets'])
    df_daily['daily_return'] = df_daily['total_assets'].pct_change().fillna(0)
    df_daily['cum_return'] = (1 + df_daily['daily_return']).cumprod()
    return df_daily

def get_mdd(cum_return_series):
    """
    최대 낙폭(MDD)을 계산.
    """
    peak = cum_return_series.cummax()
    drawdown = (cum_return_series - peak) / peak
    mdd = drawdown.min()
    return mdd

def get_sharpe_ratio(return_series, freq=365, rf=0.0):
    """
    샤프 지수 = (평균수익률 - 무위험수익률) / 표준편차 * sqrt(freq)
    """
    mean_return = return_series.mean()
    std_return = return_series.std()
    if std_return == 0:
        return 0
    sharpe = ((mean_return - rf) / std_return) * math.sqrt(freq)
    return sharpe

def load_market_data_from_timestamp(start_timestamp):
    """
    PyUpbit로 start_timestamp부터 현재까지(일봉) BTC 데이터를 불러와
    일간 수익률, 누적수익률 계산하여 반환.
    """
    now = pd.Timestamp.now()
    day_diff = (now - start_timestamp).days + 5
    if day_diff < 1:
        day_diff = 10

    ohlcv = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=day_diff)
    if ohlcv is None or ohlcv.empty:
        return pd.DataFrame()

    ohlcv = ohlcv.reset_index()
    ohlcv = ohlcv[ohlcv['index'] >= start_timestamp.normalize()]
    ohlcv = ohlcv.sort_values(by='index').set_index('index')
    ohlcv['daily_return'] = ohlcv['close'].pct_change().fillna(0)
    ohlcv['cum_return'] = (1 + ohlcv['daily_return']).cumprod()
    return ohlcv

def main():
    # 페이지 자동 리프레시 (5분마다 재실행)
    st_autorefresh(interval=300000, limit=None, key="auto_refresh")

    # 테마 선택
    theme = st.sidebar.radio("테마 선택", ("light", "dark"))
    if theme == 'dark':
        plotly_template = 'plotly_dark'
        marker_border_color = 'white'
    else:
        plotly_template = 'plotly_white'
        marker_border_color = 'black'

    # 데이터 로드
    df = load_data()
    if df.empty:
        st.warning("No trade data available.")
        return

    # 최초 거래 시점
    start_timestamp = df.iloc[0]['timestamp']

    # 내 포트폴리오 초기 / 현재 평가금액
    initial_investment = calculate_initial_investment(df)
    current_investment = calculate_current_investment(df)
    my_return_rate = ((current_investment - initial_investment) / initial_investment) * 100

    # 시장 데이터(일봉) 불러오기
    market_df = load_market_data_from_timestamp(start_timestamp)
    
    # ★ 시장 수익률을 '실시간 시세' 기준으로 계산 ★
    if not market_df.empty:
        market_start_price = df.iloc[0]['btc_krw_price']
        current_btc_price_realtime = pyupbit.get_current_price("KRW-BTC")
        if current_btc_price_realtime is not None:
            market_return_rate = ((current_btc_price_realtime - market_start_price) / market_start_price) * 100
        else:
            market_current_price = market_df['close'].iloc[-1]
            market_return_rate = ((market_current_price - market_start_price) / market_start_price) * 100
    else:
        market_return_rate = 0.0

    # 내 포트폴리오 일간 수익률 → MDD, 샤프지수
    df_daily = resample_portfolio_daily(df)
    portfolio_mdd = get_mdd(df_daily['cum_return']) if not df_daily.empty else 0
    portfolio_sharpe = get_sharpe_ratio(df_daily['daily_return']) if not df_daily.empty else 0

    # 레이아웃
    st.title("AI BTC Dashboard (o3-mini)")

    col1, col2 = st.columns([1, 3])
    config = {'displayModeBar': False}

    with col1:
        st.markdown("<h3>⚡ Performance Metrics</h3>", unsafe_allow_html=True)

        # 내 수익률
        if my_return_rate > 0:
            color_my = "red"
            sign_my = "+"
        elif my_return_rate < 0:
            color_my = "blue"
            sign_my = ""
        else:
            color_my = "black"
            sign_my = ""
        my_return_html = f"<span style='color:{color_my}; font-weight:bold;'>{sign_my}{my_return_rate:.2f}%</span>"

        # 시장 수익률
        if market_return_rate > 0:
            color_mkt = "red"
            sign_mkt = "+"
        elif market_return_rate < 0:
            color_mkt = "blue"
            sign_mkt = ""
        else:
            color_mkt = "black"
            sign_mkt = ""
        mkt_return_html = f"<span style='color:{color_mkt}; font-weight:bold;'>{sign_mkt}{market_return_rate:.2f}%</span>"

        st.markdown(f"**My Return:** {my_return_html}", unsafe_allow_html=True)
        st.markdown(f"**Market Return:** {mkt_return_html}", unsafe_allow_html=True)

        # MDD, Sharpe Ratio
        mdd_html = f"<span style='font-weight:bold;'>{portfolio_mdd*100:.2f}%</span>"
        sharpe_html = f"<span style='font-weight:bold;'>{portfolio_sharpe:.2f}</span>"
        st.markdown(
            f"**MDD:** {mdd_html} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"**Sharpe Ratio:** {sharpe_html}",
            unsafe_allow_html=True
        )

        # 내 현재 총 자산
        if current_investment > initial_investment:
            assets_color = "red"
            assets_symbol = "+"
        elif current_investment < initial_investment:
            assets_color = "blue"
            assets_symbol = ""
        else:
            assets_color = "black"
            assets_symbol = ""
        formatted_assets = f"<span style='color:{assets_color}; font-weight:bold;'>{assets_symbol}{current_investment:,.0f} KRW</span>"
        st.markdown(f"**Total Assets (KRW):** {formatted_assets}", unsafe_allow_html=True)

        # 현재 BTC 시세
        current_btc_price = pyupbit.get_current_price("KRW-BTC")
        if current_btc_price is not None:
            latest_time = df.iloc[-1]['timestamp']
            one_day_ago = latest_time - timedelta(days=1)
            prev_data = df[df['timestamp'] <= one_day_ago]
            if not prev_data.empty:
                prev_btc_price = prev_data.iloc[-1]['btc_krw_price']
            else:
                prev_btc_price = df.iloc[-1]['btc_krw_price']
            if current_btc_price > prev_btc_price:
                btc_color = "red"
                btc_symbol = "↑"
            elif current_btc_price < prev_btc_price:
                btc_color = "blue"
                btc_symbol = "↓"
            else:
                btc_color = "black"
                btc_symbol = ""
            btc_price_html = f"<span style='color:{btc_color}; font-weight:bold;'>{btc_symbol}{current_btc_price:,.0f} KRW</span>"
        else:
            btc_price_html = "N/A"
        st.markdown(f"**Current BTC Price (KRW):** {btc_price_html}", unsafe_allow_html=True)

        # 내 Total Assets 그래프
        st.markdown("<h3>💵 Total Assets</h3>", unsafe_allow_html=True)
        df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
        y_min = df['total_assets'].min()
        y_max = df['total_assets'].max()
        padding = (y_max - y_min) * 0.05
        y_range = [y_min - padding, y_max + padding]

        fig_assets = px.area(
            df,
            x='timestamp',
            y='total_assets',
            template=plotly_template,
            hover_data={'total_assets': ':.0f'}
        )
        fig_assets.update_traces(
            line=dict(color='green', width=2),
            fillcolor='rgba(0,128,0,0.3)',
            marker=dict(size=4, symbol='circle', color='green')
        )
        fig_assets.add_hline(
            y=initial_investment,
            line_dash="dash",
            line_color="gray",
            annotation_text="Initial Investment",
            annotation_position="bottom right"
        )
        fig_assets.update_layout(
            xaxis=dict(
                title="Time",
                rangeslider=dict(visible=True),
                type="date"
            ),
            yaxis=dict(
                title="Total Assets (KRW)",
                range=y_range,
                tickprefix="₩"
            ),
            margin=dict(l=20, r=20, t=0, b=50),
            height=250,
            hovermode="x unified",
            showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        st.plotly_chart(fig_assets, use_container_width=True, config=config)

    # 오른쪽 열(col2) 탭 영역
    with col2:
        st.markdown("<h3>📈 Trade-Related Charts</h3>", unsafe_allow_html=True)
        
        tab1, tab2, tab3, tab4 = st.tabs([
            "BTC Price Chart (5min)",
            "Portfolio vs. Market",
            "BTC/KRW Balance Ratio Pie Chart",
            "1-Year BTC Price (Daily)"
        ])
        
        # tab1: 최근 7일 BTC 5분봉
        with tab1:
            ohlc_5m = pyupbit.get_ohlcv("KRW-BTC", interval="minute5", count=2016)
            if ohlc_5m is not None and not ohlc_5m.empty:
                ohlc_5m = ohlc_5m.reset_index()
                min_time_7d = ohlc_5m['index'].min()
                df_7d = df[df['timestamp'] >= min_time_7d]
                fig_5m = go.Figure(data=[go.Candlestick(
                    x=ohlc_5m['index'],
                    open=ohlc_5m['open'],
                    high=ohlc_5m['high'],
                    low=ohlc_5m['low'],
                    close=ohlc_5m['close'],
                    name='BTC 5min',
                    increasing=dict(line=dict(color='#FF9999'), fillcolor='#FF9999'),
                    decreasing=dict(line=dict(color='#9999FF'), fillcolor='#9999FF')
                )])
                fig_5m = add_buy_sell_markers(fig_5m, df_7d, 'timestamp', 'btc_krw_price', marker_border_color)
                fig_5m.update_layout(
                    xaxis=dict(title="Time", rangeslider=dict(visible=False)),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=0, b=0),
                    dragmode=None,
                    height=450,
                    template=plotly_template,
                    showlegend=False
                )
                st.plotly_chart(fig_5m, use_container_width=True, config=config)
        
        # tab2: "Portfolio vs. Market" 누적수익률 비교 (거래 시점 기준)
        with tab2:
            if df.empty:
                st.warning("거래 데이터가 없습니다.")
            else:
                df = df.sort_values(by='timestamp')
                # 포트폴리오 누적수익률 계산 (거래 시점의 총 자산 기준)
                df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
                df['portfolio_return'] = df['total_assets'].pct_change().fillna(0)
                df['portfolio_cum_return'] = (1 + df['portfolio_return']).cumprod()
                # 시장 누적수익률 계산 (거래 시점의 BTC 가격 기준)
                df['market_return'] = df['btc_krw_price'].pct_change().fillna(0)
                df['market_cum_return'] = (1 + df['market_return']).cumprod()
                
                fig_compare = go.Figure()
                fig_compare.add_trace(
                    go.Scatter(
                        x=df['timestamp'],
                        y=df['portfolio_cum_return'],
                        mode='lines',
                        name='Portfolio Cumulative Return',
                        line=dict(color='blue')
                    )
                )
                fig_compare.add_trace(
                    go.Scatter(
                        x=df['timestamp'],
                        y=df['market_cum_return'],
                        mode='lines',
                        name='Market Cumulative Return',
                        line=dict(color='orange')
                    )
                )
                fig_compare.update_layout(
                    title="Portfolio vs. Market (BTC) Cumulative Return (Trade Timestamps)",
                    xaxis_title="Time",
                    yaxis_title="Cumulative Return",
                    template=plotly_template,
                    height=450,
                    hovermode="x unified",
                    legend=dict(x=0.01, y=0.99)
                )
                st.plotly_chart(fig_compare, use_container_width=True, config=config)
        
        # tab3: BTC/KRW Balance Ratio 파이차트
        with tab3:
            current_btc_balance = df.iloc[-1]['btc_balance']
            current_btc_price = pyupbit.get_current_price("KRW-BTC")
            if current_btc_price is None:
                current_btc_price = df.iloc[-1]['btc_krw_price']
            btc_balance_krw = current_btc_balance * current_btc_price
            current_krw_balance = df.iloc[-1]['krw_balance']
            labels = ['BTC Balance (KRW)', 'KRW Balance']
            values = [btc_balance_krw, current_krw_balance]
            fig_pie = px.pie(
                names=labels,
                values=values,
                title="Current BTC/KRW Balance Ratio",
                template=plotly_template,
                hole=0.4
            )
            fig_pie.update_traces(
                marker=dict(colors=['#ADD8E6', '#90EE90']),
                textinfo='percent+label'
            )
            fig_pie.update_layout(
                margin=dict(l=20, r=20, t=50, b=20),
                height=450,
                showlegend=True,
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)'
            )
            st.plotly_chart(fig_pie, use_container_width=True, config=config)
        
        # tab4: 최근 1년 BTC 일봉
        with tab4:
            ohlc_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=365)
            if ohlc_daily is not None and not ohlc_daily.empty:
                ohlc_daily = ohlc_daily.reset_index()
                fig_daily = go.Figure(data=[go.Candlestick(
                    x=ohlc_daily['index'],
                    open=ohlc_daily['open'],
                    high=ohlc_daily['high'],
                    low=ohlc_daily['low'],
                    close=ohlc_daily['close'],
                    name='BTC Daily',
                    increasing=dict(line=dict(color='#FF9999'), fillcolor='#FF9999'),
                    decreasing=dict(line=dict(color='#9999FF'), fillcolor='#9999FF')
                )])
                fig_daily = add_buy_sell_markers(fig_daily, df, 'timestamp', 'btc_krw_price', marker_border_color)
                fig_daily.update_layout(
                    xaxis=dict(title="Date", rangeslider=dict(visible=True)),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=0, b=0),
                    height=450,
                    template=plotly_template,
                    showlegend=False
                )
                st.plotly_chart(fig_daily, use_container_width=True, config=config)

    # 하단: 거래내역 표
    with st.container():
        st.markdown("<h3>📋 Trade History</h3>", unsafe_allow_html=True)
        df['timestamp_display'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M')
        displayed_df = df.copy()
        displayed_df['timestamp'] = displayed_df['timestamp_display']
        displayed_df = displayed_df.drop(columns=['id', 'timestamp_display'], errors='ignore')
        displayed_df = displayed_df.rename(columns={
            'reason': '이유',
            'reflection': '관점'
        })
        if 'total_assets' not in displayed_df.columns:
            displayed_df['total_assets'] = (
                displayed_df['krw_balance'] + displayed_df['btc_balance'] * displayed_df['btc_krw_price']
            )
        for col in ['total_assets','krw_balance','btc_avg_buy_price','btc_krw_price']:
            if col in displayed_df.columns:
                displayed_df[col] = displayed_df[col].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else x)
        krw_btc_columns = ['krw_balance', 'btc_balance', 'btc_avg_buy_price', 'btc_krw_price']
        non_krw_btc_columns = [col for col in displayed_df.columns if col not in krw_btc_columns]
        final_columns = non_krw_btc_columns + krw_btc_columns
        displayed_df = displayed_df[final_columns]
        styled_df = displayed_df.style.applymap(
            lambda x: 'background-color: red; color: white;' if x == 'buy' else
                      'background-color: blue; color: white;' if x == 'sell' else '',
            subset=['decision']
        ).set_properties(**{'text-align': 'center'})\
         .set_table_styles([
            {'selector': 'th','props': [('text-align','center')]},
            {'selector': 'td:not(.col-이유):not(.col-관점)',
             'props': [('text-align','center')]}
         ])
        st.dataframe(styled_df, use_container_width=True, height=300)

if __name__ == "__main__":
    main()
