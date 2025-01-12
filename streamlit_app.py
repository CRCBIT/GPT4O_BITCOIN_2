import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pyupbit
import numpy as np
import math
from streamlit_autorefresh import st_autorefresh

deposit_withdrawal = 0

# 항상 wide 모드 활성화, 제목 및 사이드바 설정
st.set_page_config(
    layout="wide",
    page_title="AI BTC",
    page_icon="📈",
    initial_sidebar_state="collapsed"
)

# 사용자 정의 CSS를 주입하여 여백 줄이기 및 제목 아래에 밑줄 추가
st.markdown(
    """
    <style>
    /* 메인 컨테이너의 상단 패딩 줄이기 */
    .block-container {
        padding-top: 1rem;  /* 기본값보다 작은 패딩으로 조정 */
    }

    /* 제목 위의 여백 제거 및 텍스트 아래에 밑줄 추가 */
    h1 {
        margin-top: 0;
        margin-bottom: 0.5rem; /* 제목과 섹션 사이 간격 조정 */
        text-decoration: underline; /* 실제 텍스트 아래에 밑줄 추가 */
        text-decoration-color: currentColor; /* 밑줄 색상을 텍스트 색상과 동일하게 설정 */
        text-decoration-thickness: 2px; /* 밑줄 두께 설정 */
        font-size: 30px !important; /* 글자 크기 약간 축소 */
    }

    /* 모든 h3 요소에 일관된 스타일 적용 */
    h3 {
        margin-top: 0.5rem; /* 상단 여백 조정 */
        margin-bottom: 0.5rem; /* 하단 여백 조정 */
        font-size: 20px; /* 일관된 글자 크기 약간 축소 */
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
    """초기 투자 금액을 계산합니다."""
    initial_krw_balance = df.iloc[0]['krw_balance']
    initial_btc_balance = df.iloc[0]['btc_balance']
    initial_btc_price = df.iloc[0]['btc_krw_price']
    return initial_krw_balance + (initial_btc_balance * initial_btc_price) + deposit_withdrawal

def calculate_current_investment(df):
    """현재 투자 금액을 계산합니다."""
    current_krw_balance = df.iloc[-1]['krw_balance']
    current_btc_balance = df.iloc[-1]['btc_balance']
    current_btc_price = pyupbit.get_current_price("KRW-BTC")
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
                size=10,  # 마커 크기
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
                size=10,  # 마커 크기
                color='blue',
                symbol='triangle-down',
                line=dict(width=1.5, color=border_color)
            ),
            name='Sell',
            hovertemplate="<b>Sell</b><br>Time: %{x}<br>Price: %{y:,} KRW"
        ))

    return fig

def compute_portfolio_daily_returns(df):
    """
    df['timestamp'] 기준으로 1일 단위 resample을 한 뒤
    total_assets의 마지막 값을 사용해 일간 수익률을 계산.
    """
    # total_assets 컬럼이 없으면 생성
    if 'total_assets' not in df.columns:
        df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
    
    # 일단위 resample
    df_daily = df.set_index('timestamp').resample('D').last().dropna(subset=['total_assets'])
    
    # 일간 수익률
    df_daily['portfolio_return'] = df_daily['total_assets'].pct_change().fillna(0)
    
    # 누적수익률
    df_daily['portfolio_cum_return'] = (1 + df_daily['portfolio_return']).cumprod()
    
    return df_daily

def compute_market_daily_returns(df_daily):
    """
    시장 수익률(=BTC를 단순 보유했을 경우)을 계산하기 위해,
    동일 날짜 범위에 대해 BTC 종가 데이터를 이용해 일간 수익률을 구함.
    pyupbit에서 일별 데이터(count=len(df_daily))를 받아 사용.
    """
    start_date = df_daily.index.min().strftime('%Y-%m-%d')
    end_date = df_daily.index.max().strftime('%Y-%m-%d')
    
    # pyupbit에서 일봉 데이터 조회 (종가 기준 사용)
    # count를 넉넉히 잡고, 이후 날짜 필터링
    # count = (df_daily row 수) + 여유분
    count_for_upbit = len(df_daily) + 5
    ohlc_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=count_for_upbit)
    
    # 날짜 필터링
    if ohlc_daily is None or ohlc_daily.empty:
        # 데이터 조회 실패 시, 포트폴리오와 동일한 인덱스로 0% 수익률 처리
        df_daily['market_return'] = 0
        df_daily['market_cum_return'] = 1
        return df_daily
    
    ohlc_daily = ohlc_daily.reset_index()
    # timestamp를 date 형태로 변환
    ohlc_daily['date'] = ohlc_daily['index'].dt.date
    ohlc_daily = ohlc_daily[(ohlc_daily['date'] >= pd.to_datetime(start_date).date()) & 
                            (ohlc_daily['date'] <= pd.to_datetime(end_date).date())]
    
    # df_daily index를 date로 사용하기 위해 별도 컬럼 생성
    temp = df_daily.copy()
    temp['date'] = temp.index.date
    
    # 병합
    merged = pd.merge(temp, ohlc_daily[['date','close']], on='date', how='left', sort=True)
    merged = merged.sort_values(by='date')
    merged = merged.set_index('date')
    
    # BTC 일간 수익률 계산
    merged['market_return'] = merged['close'].pct_change().fillna(0)
    merged['market_cum_return'] = (1 + merged['market_return']).cumprod()
    
    # 다시 timestamp 기반으로 재배치
    merged = merged.sort_values(by='timestamp')
    
    return merged

def get_mdd(series):
    """누적수익률 시리즈에서 MDD(최대낙폭)을 계산하여 반환"""
    # series: 예) 누적수익률(예: 1.00 -> 1.05 -> 1.02 ...)
    peak = series.cummax()
    drawdown = (series - peak) / peak
    mdd = drawdown.min()
    return mdd

def get_sharpe_ratio(return_series, freq=252, rf=0.0):
    """
    샤프지수 = (평균수익률 - 무위험수익률) / 표준편차 * sqrt(freq)
    - freq=252: 주식 기준(1년=252 거래일),  
      크립토는 365일 24시간이지만 편의상 금융시장 표준 사용
    - rf: 무위험수익률(기본 0%)
    """
    mean_return = return_series.mean()
    std_return = return_series.std()
    if std_return == 0:
        return 0
    sharpe = ((mean_return - rf) / std_return) * math.sqrt(freq)
    return sharpe

def main():
    # 페이지 자동 리프레시 (80초마다 재실행)
    st_autorefresh(interval=80000, limit=None, key="auto_refresh")

    # 사용자에게 테마 선택을 요청
    theme = st.sidebar.radio("테마 선택", ("light", "dark"))
    
    # Plotly 템플릿 설정 based on user-selected theme
    if theme == 'dark':
        plotly_template = 'plotly_dark'
        marker_border_color = 'white'
    else:
        plotly_template = 'plotly_white'
        marker_border_color = 'black'

    # 데이터 로드
    df = load_data()

    if df.empty:
        st.warning('No trade data available.')
        return

    # 초기/현재 투자금액, 수익률
    initial_investment = calculate_initial_investment(df)
    current_investment = calculate_current_investment(df)
    profit_rate = ((current_investment - initial_investment) / initial_investment) * 100
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # total_assets 컬럼 추가
    df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])

    # 일간 포트폴리오 수익률 계산
    df_daily = compute_portfolio_daily_returns(df)
    # 시장(BTC) 수익률 계산 (df_daily와 병합)
    df_merged = compute_market_daily_returns(df_daily)

    # 포트폴리오 MDD
    portfolio_mdd = get_mdd(df_merged['portfolio_cum_return'])
    # 샤프 지수(포트폴리오 기준)
    portfolio_sharpe = get_sharpe_ratio(df_merged['portfolio_return'], freq=252, rf=0.0)

    # 레이아웃 구성
    st.title("AI BTC Dashboard")

    # 상단: 수익률, 총 자산, MDD/샤프지수
    col1, col3 = st.columns([1, 3])

    # Plotly Configuration (모드바 숨기기 등)
    config = {
        'displayModeBar': False
    }

    with col1:
        st.markdown("<h3>⚡ Performance Metrics</h3>", unsafe_allow_html=True)
        
        # Current Profit Rate
        if profit_rate > 0:
            formatted_profit = f"<span style='color:red; font-weight:bold;'>+{profit_rate:.2f}%</span>"
        elif profit_rate < 0:
            formatted_profit = f"<span style='color:blue; font-weight:bold;'>{profit_rate:.2f}%</span>"
        else:
            formatted_profit = f"{profit_rate:.2f}%"
        st.markdown(f"**Current Profit Rate:** {formatted_profit}", unsafe_allow_html=True)
        
        # Total Assets (KRW)
        if current_investment > initial_investment:
            assets_color = "red"
            assets_symbol = "+"
        elif current_investment < initial_investment:
            assets_color = "blue"
            assets_symbol = "-"
        else:
            assets_color = "black"
            assets_symbol = ""
        formatted_assets = f"<span style='color:{assets_color}; font-weight:bold;'>{assets_symbol}{current_investment:,.0f} KRW</span>"
        st.markdown(f"**Total Assets (KRW):** {formatted_assets}", unsafe_allow_html=True)
        
        # Current BTC Price (KRW)
        latest_time = df.iloc[-1]['timestamp']
        one_day_ago_time = latest_time - pd.Timedelta(days=1)
        previous_data = df[df['timestamp'] <= one_day_ago_time]
        
        if not previous_data.empty:
            previous_btc_price = previous_data.iloc[-1]['btc_krw_price']
        else:
            previous_btc_price = df.iloc[-1]['btc_krw_price']
        
        if current_btc_price > previous_btc_price:
            btc_color = "red"
            btc_symbol = "↑"
        elif current_btc_price < previous_btc_price:
            btc_color = "blue"
            btc_symbol = "↓"
        else:
            btc_color = "black"
            btc_symbol = ""

        formatted_btc_price = f"<span style='color:{btc_color}; font-weight:bold;'>{btc_symbol}{current_btc_price:,.0f} KRW</span>"
        st.markdown(f"**Current BTC Price (KRW):** {formatted_btc_price}", unsafe_allow_html=True)

        # MDD, Sharpe Ratio
        st.markdown("---")
        st.markdown("<h3>📊 Risk Metrics</h3>", unsafe_allow_html=True)
        st.markdown(f"**MDD (Max Drawdown):** {portfolio_mdd * 100:.2f}%")
        st.markdown(f"**Sharpe Ratio:** {portfolio_sharpe:.2f}")

        # Total Assets 그래프
        st.markdown("<h3>💵 Total Assets</h3>", unsafe_allow_html=True)
        y_min = df['total_assets'].min()
        y_max = df['total_assets'].max()
        padding = (y_max - y_min) * 0.05
        y_range = [y_min - padding, y_max + padding]

        total_assets_fig = px.area(
            df, 
            x='timestamp', 
            y='total_assets',
            template=plotly_template, 
            hover_data={'total_assets': ':.0f'}
        )
        total_assets_fig.update_traces(
            line=dict(color='green', width=2),
            fillcolor='rgba(0, 128, 0, 0.3)',
            marker=dict(size=4, symbol='circle', color='green')
        )
        
        # 초기 투자 기준선
        total_assets_fig.add_hline(
            y=initial_investment,
            line_dash="dash",
            line_color="gray",
            annotation_text="Initial Investment",
            annotation_position="bottom right"
        )
        total_assets_fig.update_layout(
            xaxis=dict(
                title="Time",
                rangeslider=dict(visible=True),
                type="date"
            ),
            yaxis=dict(
                title="Total Assets (KRW)", 
                tickprefix="₩",
                range=y_range
            ),
            margin=dict(l=20, r=20, t=0, b=50),
            height=350,
            hovermode="x unified",
            showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        
        st.plotly_chart(total_assets_fig, use_container_width=True, config=config)

    with col3:
        st.markdown("<h3>📈 Trade-Related Charts</h3>", unsafe_allow_html=True)
        
        # 탭 생성 (기존 tab4, tab5 삭제 후 재구성)
        tab1, tab2, tab3, tab4 = st.tabs([
            "BTC Price Chart",
            "1-Year BTC Price (Daily)",
            "BTC/KRW Balance Ratio Pie Chart",
            "Portfolio vs Market Return"
        ])

        # tab1: BTC Price Chart (5분봉, 최근 7일만 표시)
        with tab1:
            ohlc = pyupbit.get_ohlcv("KRW-BTC", interval="minute5", count=2016)  # 5분봉*2016=7일
            if ohlc is not None and not ohlc.empty:
                ohlc = ohlc.reset_index()
                fig = go.Figure(data=[go.Candlestick(
                    x=ohlc['index'],
                    open=ohlc['open'],
                    high=ohlc['high'],
                    low=ohlc['low'],
                    close=ohlc['close'],
                    name='BTC',
                    increasing=dict(
                        line=dict(color='#FF9999'),
                        fillcolor='#FF9999'
                    ),
                    decreasing=dict(
                        line=dict(color='#9999FF'),
                        fillcolor='#9999FF'
                    )
                )])
                # BUY/SELL 마커
                fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_krw_price', border_color=marker_border_color)

                fig.update_layout(
                    xaxis=dict(
                        title="Time",
                        rangeslider=dict(visible=False),  # 범위 슬라이더 비활성화
                    ),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=0, b=0),
                    dragmode=None,
                    height=450,
                    template=plotly_template,
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True, config=config)

        # tab2: 1-Year BTC Price (Daily)
        with tab2:
            ohlc_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=365)
            if ohlc_daily is not None and not ohlc_daily.empty:
                ohlc_daily = ohlc_daily.reset_index()
                fig = go.Figure(data=[go.Candlestick(
                    x=ohlc_daily['index'],
                    open=ohlc_daily['open'],
                    high=ohlc_daily['high'],
                    low=ohlc_daily['low'],
                    close=ohlc_daily['close'],
                    name='BTC Daily',
                    increasing=dict(
                        line=dict(color='#FF9999'),
                        fillcolor='#FF9999'
                    ),
                    decreasing=dict(
                        line=dict(color='#9999FF'),
                        fillcolor='#9999FF'
                    )
                )])
                fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_krw_price', border_color=marker_border_color)
                fig.update_layout(
                    xaxis=dict(title="Date", rangeslider=dict(visible=True)),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=0, b=0),
                    height=450,
                    template=plotly_template,
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True, config=config)

        # tab3: BTC/KRW Balance Ratio Pie Chart
        with tab3:
            current_btc_balance = df.iloc[-1]['btc_balance']
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

        # **새로운 tab4: 포트폴리오 vs 시장(BTC) 수익률 비교**
        with tab4:
            # df_merged 안에 portfolio_cum_return, market_cum_return 존재
            fig_compare = go.Figure()
            fig_compare.add_trace(
                go.Scatter(
                    x=df_merged['timestamp'],
                    y=df_merged['portfolio_cum_return'],
                    mode='lines',
                    name='Portfolio Cumulative Return',
                    line=dict(color='blue')
                )
            )
            fig_compare.add_trace(
                go.Scatter(
                    x=df_merged['timestamp'],
                    y=df_merged['market_cum_return'],
                    mode='lines',
                    name='Market (BTC) Cumulative Return',
                    line=dict(color='orange')
                )
            )
            fig_compare.update_layout(
                title="Portfolio vs. BTC Market Cumulative Return (Daily)",
                xaxis_title="Date",
                yaxis_title="Cumulative Return",
                template=plotly_template,
                height=500,
                hovermode="x unified",
                legend=dict(
                    x=0.01,
                    y=0.99
                )
            )
            st.plotly_chart(fig_compare, use_container_width=True, config=config)

    # 하단: 거래내역 표
    with st.container():
        st.markdown("<h3>📋 Trade History</h3>", unsafe_allow_html=True)
        
        df['timestamp_display'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M')
        displayed_df = df.copy()
        displayed_df['timestamp'] = displayed_df['timestamp_display']

        displayed_df = displayed_df.drop(columns=['id', 'timestamp_display'], errors='ignore')
        displayed_df = displayed_df.rename(columns={
            'reason': '이유', 'reflection':'관점'
        })

        # 숫자 포맷
        for col in ['total_assets', 'krw_balance', 'btc_avg_buy_price', 'btc_krw_price']:
            if col in displayed_df.columns:
                displayed_df[col] = displayed_df[col].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else x)

        # 컬럼 순서 조정
        krw_btc_columns = ['krw_balance', 'btc_balance', 'btc_avg_buy_price', 'btc_krw_price']
        non_krw_btc_columns = [col for col in displayed_df.columns if col not in krw_btc_columns]
        final_columns = non_krw_btc_columns + krw_btc_columns
        displayed_df = displayed_df[final_columns]

        styled_df = displayed_df.style.applymap(
            lambda x: 'background-color: red; color: white;' if x == 'buy' else
                      'background-color: blue; color: white;' if x == 'sell' else '',
            subset=['decision']
        ).set_properties(**{
            'text-align': 'center'
        }).set_table_styles([
            {
                'selector': 'th',
                'props': [
                    ('text-align', 'center')
                ]
            },
            {
                'selector': 'td:not(.col-reason):not(.col-reflection)',
                'props': [
                    ('text-align', 'center')
                ]
            }
        ])

        st.dataframe(styled_df, use_container_width=True, height=300)

if __name__ == "__main__":
    main()
