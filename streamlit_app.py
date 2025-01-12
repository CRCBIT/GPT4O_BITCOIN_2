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
    일간 포트폴리오 수익률 계산:
      1) timestamp 기준으로 1일 단위 resample -> 마지막 값
      2) total_assets의 pct_change()로 일간 수익률
      3) (1 + 일간수익률).cumprod() -> 누적수익률
    """
    if 'total_assets' not in df.columns:
        df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
    df_daily = df.set_index('timestamp').resample('D').last().dropna(subset=['total_assets'])
    df_daily['portfolio_return'] = df_daily['total_assets'].pct_change().fillna(0)
    df_daily['portfolio_cum_return'] = (1 + df_daily['portfolio_return']).cumprod()
    return df_daily

def compute_market_daily_returns(start_date, end_date):
    """
    시장(BTC) 일간 수익률 계산:
      - pyupbit로 [start_date, end_date] 범위의 일봉 데이터를 가져온 뒤
      - 종가 기준 pct_change() -> 누적수익률 계산
    """
    # pyupbit.get_ohlcv는 count 기반이므로 대략 날짜 범위를 추정해서 넉넉히 가져온 후 필터링
    day_count = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days + 5
    if day_count < 1:
        day_count = 10

    ohlc_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=day_count)
    if ohlc_daily is None or ohlc_daily.empty:
        return pd.DataFrame()  # 데이터 불러오기 실패 시 빈 df

    # 날짜 필터링
    ohlc_daily = ohlc_daily.reset_index()
    ohlc_daily = ohlc_daily[(ohlc_daily['index'] >= pd.to_datetime(start_date)) & 
                            (ohlc_daily['index'] <= pd.to_datetime(end_date))]

    ohlc_daily = ohlc_daily.sort_values('index').set_index('index')
    ohlc_daily['market_return'] = ohlc_daily['close'].pct_change().fillna(0)
    ohlc_daily['market_cum_return'] = (1 + ohlc_daily['market_return']).cumprod()
    return ohlc_daily

def main():
    # 페이지 자동 리프레시 (80초마다 재실행)
    st_autorefresh(interval=80000, limit=None, key="auto_refresh")

    # 사용자에게 테마 선택을 요청
    theme = st.sidebar.radio("테마 선택", ("light", "dark"))
    
    # Plotly 템플릿 설정
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

    # 초기/현재 투자금액, 내 수익률
    initial_investment = calculate_initial_investment(df)
    current_investment = calculate_current_investment(df)
    my_return_rate = ((current_investment - initial_investment) / initial_investment) * 100

    # 시장 수익률(=BTC 단순 보유) 계산을 위해, "투자 시작 시점(최초 timestamp)" ~ "지금" 구간의 일간 데이터 사용
    start_date = df['timestamp'].iloc[0].date()  # 최초 거래 날짜
    end_date = pd.Timestamp.now().date()         # 오늘 날짜
    # 포트폴리오 일간 수익률
    df_daily = compute_portfolio_daily_returns(df)
    # 시장 일간 수익률
    market_df = compute_market_daily_returns(start_date, end_date)

    # 만약 시장 데이터가 정상적으로 있다면, Start Price & Current Price 이용하여 Market Return
    if not market_df.empty:
        # 시작일 종가, 최신 종가
        market_start_price = market_df['close'].iloc[0]
        market_current_price = market_df['close'].iloc[-1]
        market_return_rate = ((market_current_price - market_start_price) / market_start_price) * 100
    else:
        market_return_rate = 0.0

    # 현재 BTC 가격 (KRW) - UI 표시용
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # 레이아웃 구성
    st.title("AI BTC Dashboard")

    # 상단: 수익률, 총 자산, 시장 비교
    col1, col3 = st.columns([1, 3])
    config = {'displayModeBar': False}

    with col1:
        st.markdown("<h3>⚡ Performance Metrics</h3>", unsafe_allow_html=True)
        
        # 내 수익률
        if my_return_rate > 0:
            formatted_my_return = f"<span style='color:red; font-weight:bold;'>+{my_return_rate:.2f}%</span>"
        elif my_return_rate < 0:
            formatted_my_return = f"<span style='color:blue; font-weight:bold;'>{my_return_rate:.2f}%</span>"
        else:
            formatted_my_return = f"{my_return_rate:.2f}%"

        # 시장 수익률
        if market_return_rate > 0:
            formatted_mkt_return = f"<span style='color:red; font-weight:bold;'>+{market_return_rate:.2f}%</span>"
        elif market_return_rate < 0:
            formatted_mkt_return = f"<span style='color:blue; font-weight:bold;'>{market_return_rate:.2f}%</span>"
        else:
            formatted_mkt_return = f"{market_return_rate:.2f}%"

        # 나란히 표시
        st.markdown(
            f"**Current Profit Rate (My):** {formatted_my_return} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"**Market Return from Start:** {formatted_mkt_return}",
            unsafe_allow_html=True
        )
        
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
        
        # Current BTC Price (KRW) (어제 대비 상승/하락 표시)
        latest_time = df.iloc[-1]['timestamp']
        one_day_ago_time = latest_time - pd.Timedelta(days=1)
        previous_data = df[df['timestamp'] <= one_day_ago_time]
        if not previous_data.empty:
            previous_btc_price = previous_data.iloc[-1]['btc_krw_price']
        else:
            previous_btc_price = df.iloc[-1]['btc_krw_price']

        if current_btc_price and previous_btc_price:
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
        else:
            formatted_btc_price = "N/A"

        st.markdown(f"**Current BTC Price (KRW):** {formatted_btc_price}", unsafe_allow_html=True)

        # Portfolio Total Assets 그래프
        st.markdown("<h3>💵 Total Assets</h3>", unsafe_allow_html=True)
        df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])

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
        
        # 탭 생성 (tab4, tab5 삭제 후, 새 tab4 추가)
        tab1, tab2, tab3, tab4 = st.tabs([
            "BTC Price Chart",
            "1-Year BTC Price (Daily)",
            "BTC/KRW Balance Ratio Pie Chart",
            "Portfolio vs. Market Return"
        ])

        # tab1: BTC Price Chart (5분봉, 최근 7일)
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
                fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_krw_price', border_color=marker_border_color)
                fig.update_layout(
                    xaxis=dict(
                        title="Time",
                        rangeslider=dict(visible=False),
                        range=[ohlc['index'].iloc[0], ohlc['index'].iloc[-1]]
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
            btc_balance_krw = current_btc_balance * (current_btc_price if current_btc_price else 0)
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

        # 새 tab4: 내 포트폴리오 vs. 시장(BTC) 누적수익률
        with tab4:
            if df_daily.empty or market_df.empty:
                st.warning("포트폴리오 또는 시장 데이터를 불러올 수 없습니다.")
            else:
                # df_daily: (index=날짜) portfolio_cum_return
                # market_df: (index=날짜) market_cum_return
                # 그래프를 위해 인덱스를 rename & merge
                port_plot = df_daily[['portfolio_cum_return']].copy()
                port_plot['date'] = port_plot.index
                mkt_plot = market_df[['market_cum_return']].copy()
                mkt_plot['date'] = mkt_plot.index

                merged = pd.merge(port_plot, mkt_plot, on='date', how='inner')
                merged = merged.sort_values('date')

                fig_compare = go.Figure()
                fig_compare.add_trace(
                    go.Scatter(
                        x=merged['date'],
                        y=merged['portfolio_cum_return'],
                        mode='lines',
                        name='Portfolio Cumulative Return',
                        line=dict(color='blue')
                    )
                )
                fig_compare.add_trace(
                    go.Scatter(
                        x=merged['date'],
                        y=merged['market_cum_return'],
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
                    legend=dict(x=0.01, y=0.99)
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
            'reason': '이유', 
            'reflection': '관점'
        })
        
        # 숫자 포맷
        if 'total_assets' not in displayed_df.columns:
            displayed_df['total_assets'] = (displayed_df['krw_balance'] + 
                                            displayed_df['btc_balance'] * displayed_df['btc_krw_price'])
        for col in ['total_assets','krw_balance','btc_avg_buy_price','btc_krw_price']:
            if col in displayed_df.columns:
                displayed_df[col] = displayed_df[col].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else x)

        # 컬럼 순서 맞춤
        krw_btc_columns = ['krw_balance', 'btc_balance', 'btc_avg_buy_price', 'btc_krw_price']
        non_krw_btc_columns = [col for col in displayed_df.columns if col not in krw_btc_columns]
        final_columns = non_krw_btc_columns + krw_btc_columns
        displayed_df = displayed_df[final_columns]

        # BUY / SELL 강조색
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
