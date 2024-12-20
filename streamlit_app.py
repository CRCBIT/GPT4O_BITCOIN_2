import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pyupbit
from streamlit_autorefresh import st_autorefresh

deposit_withdrawal = 400000

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
    .css-18e3th9 {  /* Streamlit의 내부 클래스 이름; 버전에 따라 다를 수 있음 */
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
                size=10,  # 마커 크기 약간 축소
                color='red',
                symbol='triangle-up',
                line=dict(width=1.5, color=border_color)  # 테두리 두께 조정
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
                size=10,  # 마커 크기 약간 축소
                color='blue',
                symbol='triangle-down',
                line=dict(width=1.5, color=border_color)  # 테두리 두께 조정
            ),
            name='Sell',
            hovertemplate="<b>Sell</b><br>Time: %{x}<br>Price: %{y:,} KRW"
        ))

    return fig

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

    # 계산
    initial_investment = calculate_initial_investment(df)
    current_investment = calculate_current_investment(df)
    profit_rate = ((current_investment - initial_investment) / initial_investment) * 100
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # 레이아웃 구성
    st.title("AI BTC Dashboard")  # CSS에서 글자 크기 조절됨

    # 상단: 수익률, 총 자산 및 차트 정보
    # 변경된 레이아웃: 두 개의 컬럼 (col1과 col3)
    col1, col3 = st.columns([1, 3])

    # Plotly Configuration 설정
    config = {
        'displayModeBar': False  # 모드바 완전히 숨기기
        # 또는 특정 버튼만 제거하려면 다음과 같이 설정
        # 'modeBarButtonsToRemove': ['toImage', 'toggleSpikelines']
    }

    with col1:
        # Performance Metrics 제목 조절
        st.markdown("<h3>⚡ Performance Metrics</h3>", unsafe_allow_html=True)
        
        # Current Profit Rate - 조건부 색상 및 포맷팅
        if profit_rate > 0:
            formatted_profit = f"<span style='color:red; font-weight:bold;'>+{profit_rate:.2f}%</span>"
        elif profit_rate < 0:
            formatted_profit = f"<span style='color:blue; font-weight:bold;'>{profit_rate:.2f}%</span>"
        else:
            formatted_profit = f"{profit_rate:.2f}%"
        
        st.markdown(f"**Current Profit Rate:** {formatted_profit}", unsafe_allow_html=True)
        
        # Total Assets (KRW) - 조건부 색상 및 포맷팅
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
        
        # Current BTC Price (KRW) - 하루 전 데이터로 조건부 색상 및 화살표 추가
        latest_time = df.iloc[-1]['timestamp']
        one_day_ago_time = latest_time - pd.Timedelta(days=1)
        
        # 하루 전 시간에 가장 가까운 데이터를 찾기
        previous_data = df[df['timestamp'] <= one_day_ago_time]
        
        if not previous_data.empty:
            previous_btc_price = previous_data.iloc[-1]['btc_krw_price']
        else:
            previous_btc_price = df.iloc[-1]['btc_krw_price']  # 하루 전 데이터가 없으면 현재 가격 사용
        
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

        # Total Assets 제목과 그래프 사이의 여백을 제거하여 그래프가 딱 붙게 함
        st.markdown("<h3>💵 Total Assets</h3>", unsafe_allow_html=True)
        
        # 총 자산 계산
        df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
        
        # y축 범위 계산 (패딩 포함)
        y_min = df['total_assets'].min()
        y_max = df['total_assets'].max()
        padding = (y_max - y_min) * 0.05  # 5% 패딩
        y_range = [y_min - padding, y_max + padding]

        # Total Assets 영역 그래프 생성
        total_assets_fig = px.area(
            df, 
            x='timestamp', 
            y='total_assets',
            template=plotly_template,  # 사용자 선택에 따른 템플릿 적용
            hover_data={'total_assets': ':.0f'}  # 호버 데이터 포맷 지정
        )
        
        # 색상과 마커 스타일 커스터마이징
        total_assets_fig.update_traces(
            line=dict(color='green', width=2),  # 선 두께 축소
            fillcolor='rgba(0, 128, 0, 0.3)',  # 반투명 녹색으로 채움
            marker=dict(size=4, symbol='circle', color='green')  # 마커 크기 축소
        )
        
        # 초기 투자 기준선 추가
        total_assets_fig.add_hline(
            y=initial_investment,
            line_dash="dash",
            line_color="gray",
            annotation_text="Initial Investment",
            annotation_position="bottom right"
        )

        # 레이아웃 조정
        total_assets_fig.update_layout(
            xaxis=dict(
                title="Time",
                rangeslider=dict(visible=True),
                type="date"
            ),
            yaxis=dict(
                title="Total Assets (KRW)", 
                tickprefix="₩",
                range=y_range  # 동적으로 계산된 y축 범위 적용
            ),
            margin=dict(l=20, r=20, t=0, b=50),
            height=350,  # 차트 높이 축소
            hovermode="x unified",
            showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)',  # 투명 배경
            paper_bgcolor='rgba(0,0,0,0)'  # 투명 배경
        )
        
        # Plotly 그래프 출력 시 모드바 숨기기
        st.plotly_chart(total_assets_fig, use_container_width=True, config=config)

    with col3:
        # Trade-Related Charts 제목 조절
        st.markdown("<h3>📈 Trade-Related Charts</h3>", unsafe_allow_html=True)
        
        # 탭 생성 (tab3, tab4를 새로 정의)
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "BTC Price Chart",
            "1-Year BTC Price (Daily)",
            "BTC/KRW Balance Ratio Pie Chart",
            "BTC/KRW Balance Ratio",
            "Avg Buy Price"
        ])

        # tab1: BTC Price Chart
        with tab1:
            ohlc = pyupbit.get_ohlcv("KRW-BTC", interval="minute5", count=2016)  # 2016 = 5 min intervals in 1 week
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
                        line=dict(color='#FF9999'),  # Light Red for increasing candles
                        fillcolor='#FF9999'
                    ),
                    decreasing=dict(
                        line=dict(color='#9999FF'),  # Light Blue for decreasing candles
                        fillcolor='#9999FF'
                    )
                )])
                # BUY/SELL 마커 추가
                fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_krw_price', border_color=marker_border_color)
                fig.update_layout(
                    xaxis=dict(
                        title="Time",
                        rangeslider=dict(visible=True),
                        range=[ohlc['index'].iloc[-288], ohlc['index'].iloc[-1]]  # Show last day only
                    ),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=0, b=0),
                    dragmode="pan",
                    height=450,  # 차트 높이 조정
                    template=plotly_template,  # 사용자 선택에 따른 템플릿 적용
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
                        line=dict(color='#FF9999'),  # Light Red for increasing candles
                        fillcolor='#FF9999'
                    ),
                    decreasing=dict(
                        line=dict(color='#9999FF'),  # Light Blue for decreasing candles
                        fillcolor='#9999FF'
                    )
                )])
                # BUY/SELL 마커 추가
                fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_krw_price', border_color=marker_border_color)
                fig.update_layout(
                    xaxis=dict(title="Date", rangeslider=dict(visible=True)),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=0, b=0),
                    height=450,  # 차트 높이 조정
                    template=plotly_template,  # 사용자 선택에 따른 템플릿 적용
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True, config=config)

        # tab3: BTC/KRW Balance Ratio Pie Chart
        with tab3:
            # 현재 BTC 잔액을 KRW 단위로 환산
            current_btc_balance = df.iloc[-1]['btc_balance']
            btc_balance_krw = current_btc_balance * current_btc_price
            current_krw_balance = df.iloc[-1]['krw_balance']
            
            labels = ['BTC Balance (KRW)', 'KRW Balance']
            values = [btc_balance_krw, current_krw_balance]
            
            # 원형 그래프 생성
            fig_pie = px.pie(
                names=labels,
                values=values,
                title="Current BTC/KRW Balance Ratio",
                template=plotly_template,
                hole=0.4  # 도넛 형태로 표시 (선택 사항)
            )
            
            # 색상 커스터마이징: Light Blue for BTC, Light Green for KRW
            fig_pie.update_traces(
                marker=dict(colors=['#ADD8E6', '#90EE90']),  # Light Blue and Light Green
                textinfo='percent+label'
            )
            
            # 레이아웃 조정
            fig_pie.update_layout(
                margin=dict(l=20, r=20, t=50, b=20),
                height=450,  # 그래프 높이 조정
                showlegend=True,
                plot_bgcolor='rgba(0,0,0,0)',  # 투명 배경
                paper_bgcolor='rgba(0,0,0,0)'  # 투명 배경
            )
            
            # 그래프 표시
            st.plotly_chart(fig_pie, use_container_width=True, config=config)

        # tab4: BTC/KRW Balance Ratio (100% Stacked Bar) with BTC Price Line
        with tab4:
            # Resample data to hourly intervals, taking the last available data point in each hour
            df_hourly = df.set_index('timestamp').resample('H').last().reset_index()
            
            # BTC 잔액을 KRW 단위로 환산
            df_hourly['btc_balance_krw'] = df_hourly['btc_balance'] * df_hourly['btc_krw_price']
            
            # 100% 누적 막대 그래프를 위한 비율 계산
            total_balance_krw = df_hourly['btc_balance_krw'] + df_hourly['krw_balance']
            df_hourly['btc_percentage'] = (df_hourly['btc_balance_krw'] / total_balance_krw) * 100
            df_hourly['krw_percentage'] = (df_hourly['krw_balance'] / total_balance_krw) * 100

            # 잠재적인 0으로 나누는 경우 처리
            df_hourly[['btc_percentage', 'krw_percentage']] = df_hourly[['btc_percentage', 'krw_percentage']].replace([float('inf'), -float('inf')], 0)
            df_hourly[['btc_percentage', 'krw_percentage']] = df_hourly[['btc_percentage', 'krw_percentage']].fillna(0)
            
            # 시간대별 BTC 가격 데이터 보완
            # 모든 시간대를 포함하도록 인덱스를 재설정하고, 결측값을 보간
            full_time_range = pd.date_range(start=df_hourly['timestamp'].min(), end=df_hourly['timestamp'].max(), freq='H')
            df_hourly = df_hourly.set_index('timestamp').reindex(full_time_range).rename_axis('timestamp').reset_index()
            df_hourly['btc_balance_krw'] = df_hourly['btc_balance_krw'].fillna(method='ffill')
            df_hourly['krw_balance'] = df_hourly['krw_balance'].fillna(method='ffill')
            df_hourly['btc_percentage'] = df_hourly['btc_percentage'].fillna(0)
            df_hourly['krw_percentage'] = df_hourly['krw_percentage'].fillna(0)
            df_hourly['btc_krw_price'] = df_hourly['btc_krw_price'].fillna(method='ffill')
            df_hourly['btc_balance'] = df_hourly['btc_balance'].fillna(method='ffill')
            df_hourly['krw_balance'] = df_hourly['krw_balance'].fillna(method='ffill')

            # Plotly Express를 위한 데이터 변환 (Melt)
            df_melted = df_hourly.melt(id_vars=['timestamp'], value_vars=['btc_percentage', 'krw_percentage'],
                                       var_name='Balance Type', value_name='Percentage')

            # 가독성을 위한 이름 변경
            df_melted['Balance Type'] = df_melted['Balance Type'].replace({
                'btc_percentage': 'BTC Balance (KRW)',
                'krw_percentage': 'KRW Balance'
            })

            # Create subplots with secondary y-axis
            fig_ratio = make_subplots(specs=[[{"secondary_y": True}]])

            # Add BTC Balance (KRW) as a bar trace
            fig_ratio.add_trace(
                go.Bar(
                    x=df_melted[df_melted['Balance Type'] == 'BTC Balance (KRW)']['timestamp'],
                    y=df_melted[df_melted['Balance Type'] == 'BTC Balance (KRW)']['Percentage'],
                    name='BTC Balance (KRW)',
                    marker_color='#ADD8E6',  # Light Blue
                ),
                secondary_y=False,
            )

            # Add KRW Balance as a bar trace
            fig_ratio.add_trace(
                go.Bar(
                    x=df_melted[df_melted['Balance Type'] == 'KRW Balance']['timestamp'],
                    y=df_melted[df_melted['Balance Type'] == 'KRW Balance']['Percentage'],
                    name='KRW Balance',
                    marker_color='#90EE90',  # Light Green
                ),
                secondary_y=False,
            )

            # Add BTC Price as a line trace on secondary y-axis
            fig_ratio.add_trace(
                go.Scatter(
                    x=df_hourly['timestamp'],
                    y=df_hourly['btc_krw_price'],
                    name='BTC Price (KRW)',
                    mode='lines',
                    line=dict(color='purple', width=2),
                    yaxis='y2'
                ),
                secondary_y=True,
            )

            # Update layout for 100% stacked bars and secondary y-axis
            fig_ratio.update_layout(
                barmode='stack',
                title_text="Hourly BTC/KRW Balance Ratio (100% Stacked) with BTC Price",
                template=plotly_template,
                height=450,  # 그래프 높이 조정
                margin=dict(l=40, r=20, t=50, b=100),
                plot_bgcolor='rgba(0,0,0,0)',  # 투명 배경
                paper_bgcolor='rgba(0,0,0,0)',  # 투명 배경
                hovermode="x unified",
                showlegend=True
            )

            # Configure x-axis
            fig_ratio.update_xaxes(
                title="Time",
                tickformat="%Y-%m-%d %H:%M",
                tickangle=45,
                type='date',
                range=[df_hourly['timestamp'].min(), df_hourly['timestamp'].max()]
            )

            # Configure primary y-axis (Percentage)
            fig_ratio.update_yaxes(
                title="Percentage (%)",
                range=[0, 100],
                secondary_y=False
            )

            # Configure secondary y-axis (BTC Price)
            fig_ratio.update_yaxes(
                title="BTC Price (KRW)",
                secondary_y=True,
                showgrid=False,
                tickprefix="₩"
            )

            # Adjust legend to avoid overlap
            fig_ratio.update_layout(
                legend=dict(
                    x=0.01,
                    y=0.99,
                    bgcolor='rgba(255,255,255,0.5)',
                    bordercolor='rgba(0,0,0,0)',
                    borderwidth=1
                )
            )

            # 그래프 표시
            st.plotly_chart(fig_ratio, use_container_width=True, config=config)

        # tab5: Avg Buy Price
        with tab5:
            fig = px.line(
                df, 
                x='timestamp', 
                y='btc_avg_buy_price', 
                title="BTC Average Buy Price Over Time", 
                markers=True, 
                template=plotly_template
                # Removed 'name' parameter
            )
            # Set the trace name
            fig.update_traces(name='BTC Avg Buy Price')

            # BUY/SELL 마커 추가
            fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_avg_buy_price', border_color=marker_border_color)
            
            fig.update_traces(
                selector=dict(name='BTC Avg Buy Price'),  # 메인 트레이스만 선택
                line=dict(color='black', width=2),  # 선 색상 변경 및 두께 축소
                marker=dict(size=4, symbol='circle', color='black')  # 마커 크기 축소
            )
            fig.update_layout(
                margin=dict(l=40, r=20, t=30, b=20),  # 상단 마진 약간 추가
                height=450,  # 차트 높이 조정
                yaxis_title="Average Buy Price (KRW)",
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor='gray'),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                hovermode="x unified",
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True, config=config)

    # 하단: 거래내역 표
    with st.container():
        # Trade History 제목 조절
        st.markdown("<h3>📋 Trade History</h3>", unsafe_allow_html=True)
        
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

        # 테이블 높이 설정
        st.dataframe(styled_df, use_container_width=True, height=300)

if __name__ == "__main__":
    main()