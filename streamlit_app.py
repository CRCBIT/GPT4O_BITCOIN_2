import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import pyupbit
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

# 항상 wide 모드 활성화, 제목 및 사이드바 설정
st.set_page_config(
    layout="wide",
    page_title="Bitcoin Dashboard",
    page_icon="📈",
    initial_sidebar_state="collapsed"
)

# 사용자 정의 CSS를 주입하여 여백 줄이기 및 제목 스타일 변경
st.markdown(
    """
    <style>
    /* 메인 컨테이너의 상단 패딩 줄이기 */
    .block-container {
        padding-top: 1rem;  /* 기본값보다 작은 패딩으로 조정 */
    }

    /* 제목 위의 여백 제거 및 제목 스타일링 */
    h1 {
        margin-top: 0;
        font-size: 48px; /* 원하는 글자 크기로 조정 */
        text-decoration: underline; /* 밑줄 추가 */
        color: #2E86C1; /* 원하는 색상으로 변경 가능 */
    }

    /* 추가적인 여백 제거 (필요 시) */
    .css-18e3th9 {  /* Streamlit의 내부 클래스 이름; 버전에 따라 다를 수 있음 */
        padding-top: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True
)

def get_connection():
    return sqlite3.connect('bitcoin_trades.db')

def load_data():
    conn = get_connection()
    query = "SELECT * FROM trades"
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def calculate_initial_investment(df):
    initial_krw_balance = df.iloc[0]['krw_balance']
    initial_btc_balance = df.iloc[0]['btc_balance']
    initial_btc_price = df.iloc[0]['btc_krw_price']
    return initial_krw_balance + (initial_btc_balance * initial_btc_price)

def calculate_current_investment(df):
    current_krw_balance = df.iloc[-1]['krw_balance']
    current_btc_balance = df.iloc[-1]['btc_balance']
    current_btc_price = pyupbit.get_current_price("KRW-BTC")
    return current_krw_balance + (current_btc_balance * current_btc_price)

def add_buy_sell_markers(fig, df, x_col, y_col):
    """Add buy and sell markers to a Plotly figure."""
    buy_points = df[df['decision'] == 'buy']
    sell_points = df[df['decision'] == 'sell']

    if not buy_points.empty:
        fig.add_trace(go.Scatter(
            x=buy_points[x_col],
            y=buy_points[y_col],
            mode='markers',
            marker=dict(size=12, color='green', symbol='triangle-up'),
            name='Buy',
            hovertemplate="<b>Buy</b><br>Time: %{x}<br>Price: %{y:,} KRW"
        ))

    if not sell_points.empty:
        fig.add_trace(go.Scatter(
            x=sell_points[x_col],
            y=sell_points[y_col],
            mode='markers',
            marker=dict(size=12, color='red', symbol='triangle-down'),
            name='Sell',
            hovertemplate="<b>Sell</b><br>Time: %{x}<br>Price: %{y:,} KRW"
        ))

    return fig

def main():
    # 페이지 자동 리프레시 (60초마다 재실행)
    st_autorefresh(interval=60000, limit=None, key="auto_refresh")

    # 데이터 로드
    df = load_data()

    if df.empty:
        st.warning('No trade data available.')
        return

    # 계산
    initial_investment = calculate_initial_investment(df)
    current_investment = calculate_current_investment(df)
    profit_rate = ((current_investment - initial_investment) / initial_investment) * 100
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # 레이아웃 구성
    st.title("Bitcoin Trading Dashboard")

    # 상단: 수익률, 총 자산 및 차트 정보
    # 변경된 레이아웃: 두 개의 컬럼 (col1과 col3)
    col1, col3 = st.columns([1, 3])

    with col1:
        st.header("⚡Performance Metrics")
        st.metric("Current Profit Rate", f"{profit_rate:.2f}%")
        st.metric("Total Assets (KRW)", f"{current_investment:,.0f} KRW")
        st.metric("Current BTC Price (KRW)", f"{current_btc_price:,.0f} KRW")

        st.header("💲Total Assets")
        df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
        
        # 모던한 스타일 적용을 위한 그래프 수정
        total_assets_fig = px.line(
            df, 
            x='timestamp', 
            y='total_assets',
            title='Total Assets',
            markers=True,
            template='plotly_dark',  # 모던한 테마 적용
            line_shape='spline',     # 부드러운 라인
            hover_data={'total_assets': ':.0f'}  # 호버 데이터 포맷 지정
        )

        # 라인 색상과 마커 스타일 커스터마이징
        total_assets_fig.update_traces(
            line=dict(color='teal', width=3),
            marker=dict(size=6, symbol='circle', color='teal')
        )

        # 레이아웃 조정
        total_assets_fig.update_layout(
            margin=dict(l=20, r=20, t=50, b=20),
            height=200,  # 사용자 요청에 따라 높이 조정
            xaxis_title=None,
            yaxis_title="Total Assets (KRW)",
            xaxis=dict(
                showgrid=False,
                showticklabels=False
            ),
            yaxis=dict(
                showgrid=True,
                gridcolor='gray',
                tickprefix="₩",
                showline=False,
                zeroline=False
            ),
            showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)',  # 투명 배경
            paper_bgcolor='rgba(0,0,0,0)'  # 투명 배경
        )

        # 호버 모드 설정
        total_assets_fig.update_layout(
            hovermode="x unified"
        )

        st.plotly_chart(total_assets_fig, use_container_width=True)

    with col3:
        st.header("📈Trade-Related Charts")
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["BTC Price Chart", "1-Year BTC Price (Daily)", "BTC Balance", "KRW Balance", "Avg Buy Price"])

        with tab1:
            st.subheader("BTC Price with Buy/Sell Points (5-Min Candles for 1 Week)")
            ohlc = pyupbit.get_ohlcv("KRW-BTC", interval="minute5", count=2016)  # 2016 = 5 min intervals in 1 week
            if ohlc is not None and not ohlc.empty:
                ohlc = ohlc.reset_index()
                fig = go.Figure(data=[go.Candlestick(
                    x=ohlc['index'],
                    open=ohlc['open'],
                    high=ohlc['high'],
                    low=ohlc['low'],
                    close=ohlc['close'],
                    name='BTC'
                )])
                fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_krw_price')
                fig.update_layout(
                    xaxis=dict(
                        title="Time",
                        rangeslider=dict(visible=True),
                        range=[ohlc['index'].iloc[-288], ohlc['index'].iloc[-1]]  # Show last day only
                    ),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=30, b=20),
                    dragmode="pan",
                    height=400,
                    template='plotly_dark'  # 동일한 테마 적용
                )
                st.plotly_chart(fig, use_container_width=True)

        with tab2:
            st.subheader("1-Year BTC Price (Daily)")
            ohlc_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=365)
            if ohlc_daily is not None and not ohlc_daily.empty:
                ohlc_daily = ohlc_daily.reset_index()
                fig = go.Figure(data=[go.Candlestick(
                    x=ohlc_daily['index'],
                    open=ohlc_daily['open'],
                    high=ohlc_daily['high'],
                    low=ohlc_daily['low'],
                    close=ohlc_daily['close'],
                    name='BTC Daily'
                )])
                fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_krw_price')
                fig.update_layout(
                    xaxis=dict(title="Date", rangeslider=dict(visible=True)),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=30, b=20),
                    height=400,
                    template='plotly_dark'
                )
                st.plotly_chart(fig, use_container_width=True)

        with tab3:
            st.subheader("BTC Balance Over Time")
            fig = px.line(df, x='timestamp', y='btc_balance', title="BTC Balance Over Time", markers=True, template='plotly_dark', line_shape='spline')
            fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_balance')
            fig.update_traces(line=dict(color='orange', width=3), marker=dict(size=6, symbol='circle', color='orange'))
            fig.update_layout(
                margin=dict(l=40, r=20, t=50, b=20),
                height=400,
                yaxis_title="BTC Balance",
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor='gray'),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                hovermode="x unified",
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)

        with tab4:
            st.subheader("KRW Balance Over Time")
            fig = px.line(df, x='timestamp', y='krw_balance', title="KRW Balance Over Time", markers=True, template='plotly_dark', line_shape='spline')
            fig = add_buy_sell_markers(fig, df, 'timestamp', 'krw_balance')
            fig.update_traces(line=dict(color='purple', width=3), marker=dict(size=6, symbol='circle', color='purple'))
            fig.update_layout(
                margin=dict(l=40, r=20, t=50, b=20),
                height=400,
                yaxis_title="KRW Balance",
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor='gray'),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                hovermode="x unified",
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)

        with tab5:
            st.subheader("BTC Average Buy Price Over Time")
            fig = px.line(df, x='timestamp', y='btc_avg_buy_price', title="BTC Average Buy Price Over Time", markers=True, template='plotly_dark', line_shape='spline')
            fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_avg_buy_price')
            fig.update_traces(line=dict(color='cyan', width=3), marker=dict(size=6, symbol='circle', color='cyan'))
            fig.update_layout(
                margin=dict(l=40, r=20, t=50, b=20),
                height=400,
                yaxis_title="Average Buy Price (KRW)",
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor='gray'),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                hovermode="x unified",
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)

    # 하단: 거래내역 표
    st.header("📋Trade History")
    # Timestamp 포맷 변경
    df['timestamp_display'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M')
    displayed_df = df.copy()
    displayed_df['timestamp'] = displayed_df['timestamp_display']

    # 필요한 수정 적용
    displayed_df = displayed_df.drop(columns=['id', 'timestamp_display'], errors='ignore')
    displayed_df = displayed_df.rename(columns={
        'reason': '이유', 'reflection':'관점'
    })

    # KRW 및 BTC 관련 열 정리
    for col in ['total_assets','krw_balance', 'btc_avg_buy_price', 'btc_krw_price']:
        if col in displayed_df.columns:
            displayed_df[col] = displayed_df[col].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else x)

    # 열 순서 변경
    krw_btc_columns = ['krw_balance', 'btc_balance', 'btc_avg_buy_price', 'btc_krw_price']
    non_krw_btc_columns = [col for col in displayed_df.columns if col not in krw_btc_columns]
    final_columns = non_krw_btc_columns + krw_btc_columns
    displayed_df = displayed_df[final_columns]

    # 스타일 적용
    styled_df = displayed_df.style.applymap(
        lambda x: 'background-color: green; color: white;' if x == 'buy' else
                  'background-color: red; color: white;' if x == 'sell' else '',
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

    # 테이블 높이 설정
    st.dataframe(styled_df, use_container_width=True, height=300)

if __name__ == "__main__":
    main()
