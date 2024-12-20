import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pyupbit
from streamlit_autorefresh import st_autorefresh

# Always set to wide mode, set title and sidebar
st.set_page_config(
    layout="wide",
    page_title="AI BTC",
    page_icon="📈",
    initial_sidebar_state="collapsed"
)

# Inject custom CSS for styling
st.markdown(
    """
    <style>
    /* Reduce top padding of main container */
    .block-container {
        padding-top: 1rem;
    }

    /* Remove top margin and add underline to h1 */
    h1 {
        margin-top: 0;
        margin-bottom: 0.5rem;
        text-decoration: underline;
        text-decoration-color: currentColor;
        text-decoration-thickness: 2px;
        font-size: 30px !important;
    }

    /* Consistent styling for all h3 elements */
    h3 {
        margin-top: 0.5rem;
        margin-bottom: 0.5rem;
        font-size: 20px;
    }

    /* Remove additional padding if necessary */
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
    """Connect to SQLite database."""
    return sqlite3.connect('bitcoin_trades.db')

def load_trades_data():
    """Load trade data from the database."""
    conn = get_connection()
    query = "SELECT * FROM trades ORDER BY timestamp ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def load_transactions_data():
    """Load transaction data from the database."""
    conn = get_connection()
    query = "SELECT * FROM transactions ORDER BY timestamp ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def calculate_deposits(df_transactions):
    """Calculate total deposits from transactions."""
    deposits = df_transactions[df_transactions['type'] == 'deposit']['amount'].sum()
    return deposits

def calculate_withdrawals(df_transactions):
    """Calculate total withdrawals from transactions."""
    withdrawals = df_transactions[df_transactions['type'] == 'withdrawal']['amount'].sum()
    return withdrawals

def calculate_net_investment(df_transactions):
    """Calculate net investment considering deposits and withdrawals."""
    total_deposits = calculate_deposits(df_transactions)
    total_withdrawals = calculate_withdrawals(df_transactions)
    net_investment = total_deposits - total_withdrawals
    return net_investment

def calculate_initial_investment(df_trades, df_transactions):
    """초기 투자 금액을 계산합니다."""
    if not df_trades.empty:
        initial_krw_balance = df_trades.iloc[0]['krw_balance']
        initial_btc_balance = df_trades.iloc[0]['btc_balance']
        initial_btc_price = df_trades.iloc[0]['btc_krw_price']
        return initial_krw_balance + (initial_btc_balance * initial_btc_price)
    elif not df_transactions.empty:
        # transactions 테이블을 기반으로 초기 투자 계산
        first_transaction = df_transactions.iloc[0]
        if first_transaction['type'] == 'deposit':
            return first_transaction['amount']
    return 0

def calculate_current_investment(df_trades):
    """
    Calculate the current investment including current balances and latest BTC price.
    """
    if not df_trades.empty:
        current_krw_balance = df_trades.iloc[-1]['krw_balance']
        current_btc_balance = df_trades.iloc[-1]['btc_balance']
        current_btc_price = pyupbit.get_current_price("KRW-BTC")
        return current_krw_balance + (current_btc_balance * current_btc_price)
    return 0

def calculate_profit_rate(df_trades, df_transactions):
    """Calculate the profit rate based on net investment."""
    initial_investment = calculate_initial_investment(df_trades, df_transactions)
    net_investment = calculate_net_investment(df_transactions)
    current_investment = calculate_current_investment(df_trades)
    if (initial_investment + net_investment) != 0:
        profit = current_investment - (initial_investment + net_investment)
        profit_rate = (profit / (initial_investment + net_investment)) * 100
    else:
        profit_rate = 0
    return profit_rate, initial_investment, net_investment, current_investment

def add_buy_sell_markers(fig, df, x_col, y_col, border_color='black'):
    """
    Add BUY and SELL markers to Plotly graph.
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

def main():
    # 페이지 자동 새로고침 (80초마다)
    st_autorefresh(interval=80000, limit=None, key="auto_refresh")

    # 테마 선택
    theme = st.sidebar.radio("테마 선택", ("light", "dark"))
    
    if theme == 'dark':
        plotly_template = 'plotly_dark'
        marker_border_color = 'white'
    else:
        plotly_template = 'plotly_white'
        marker_border_color = 'black'

    # 데이터 로드
    df_trades = load_trades_data()
    df_transactions = load_transactions_data()

    # 데이터프레임 열 확인
    # st.write("Trades 테이블의 열:", df_trades.columns.tolist())
    # st.write("Transactions 테이블의 열:", df_transactions.columns.tolist())

    if df_trades.empty and df_transactions.empty:
        st.warning('No trade or transaction data available.')
        return

    # 순투자 및 수익률 계산
    profit_rate, initial_investment, net_investment, current_investment = calculate_profit_rate(df_trades, df_transactions)
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # Layout configuration
    st.title("AI BTC Dashboard")  # Adjusted by CSS

    # Top section: Performance Metrics and Charts
    col1, col3 = st.columns([1, 3])

    # Plotly Configuration
    config = {
        'displayModeBar': False  # Hide mode bar
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
        
        # Net Investment
        formatted_net_investment = f"<span style='color:black; font-weight:bold;'>{net_investment:,.0f} KRW</span>"
        st.markdown(f"**Net Investment (KRW):** {formatted_net_investment}", unsafe_allow_html=True)
        
        # Total Assets (KRW)
        if current_investment > initial_investment + net_investment:
            assets_color = "red"
            assets_symbol = "+"
        elif current_investment < initial_investment + net_investment:
            assets_color = "blue"
            assets_symbol = "-"
        else:
            assets_color = "black"
            assets_symbol = ""
        
        formatted_assets = f"<span style='color:{assets_color}; font-weight:bold;'>{assets_symbol}{current_investment:,.0f} KRW</span>"
        st.markdown(f"**Total Assets (KRW):** {formatted_assets}", unsafe_allow_html=True)
        
        # Current BTC Price with comparison to 1 day ago
        if not df_trades.empty:
            latest_time = df_trades.iloc[-1]['timestamp']
        elif not df_transactions.empty:
            latest_time = df_transactions.iloc[-1]['timestamp']
        else:
            latest_time = pd.Timestamp.now()
        
        one_day_ago_time = latest_time - pd.Timedelta(days=1)
        
        # Find the closest data point to one day ago
        if not df_trades.empty:
            previous_data = df_trades[df_trades['timestamp'] <= one_day_ago_time]
        elif not df_transactions.empty:
            previous_data = df_transactions[df_transactions['timestamp'] <= one_day_ago_time]
        else:
            previous_data = pd.DataFrame()
        
        if not previous_data.empty:
            previous_btc_price = previous_data.iloc[-1]['btc_krw_price'] if 'btc_krw_price' in previous_data.columns else current_btc_price
        else:
            previous_btc_price = current_btc_price  # Use current price if no data from a day ago
        
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

        # Total Assets Chart
        st.markdown("<h3>💵 Total Assets</h3>", unsafe_allow_html=True)
        
        if not df_trades.empty:
            df_trades['total_assets'] = df_trades['krw_balance'] + (df_trades['btc_balance'] * df_trades['btc_krw_price'])
            
            # Determine y-axis range with padding
            y_min = df_trades['total_assets'].min()
            y_max = df_trades['total_assets'].max()
            padding = (y_max - y_min) * 0.05  # 5% padding
            y_range = [y_min - padding, y_max + padding]

            # Create Total Assets area chart
            total_assets_fig = px.area(
                df_trades, 
                x='timestamp', 
                y='total_assets',
                template=plotly_template,
                hover_data={'total_assets': ':.0f'}
            )
            
            # Customize colors and markers
            total_assets_fig.update_traces(
                line=dict(color='green', width=2),
                fillcolor='rgba(0, 128, 0, 0.3)',
                marker=dict(size=4, symbol='circle', color='green')
            )
            
            # Add initial investment baseline
            total_assets_fig.add_hline(
                y=initial_investment + net_investment,
                line_dash="dash",
                line_color="gray",
                annotation_text="Net Investment",
                annotation_position="bottom right"
            )

            # Adjust layout
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
                height=300,
                hovermode="x unified",
                showlegend=False,
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)'
            )
            
            # Display Plotly chart without mode bar
            st.plotly_chart(total_assets_fig, use_container_width=True, config=config)
        else:
            st.write("Trades 데이터가 없습니다.")

    with col3:
        st.markdown("<h3>📈 Trade-Related Charts</h3>", unsafe_allow_html=True)
        
        # Create tabs for different charts
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["BTC Price Chart", "1-Year BTC Price (Daily)", "BTC Balance", "KRW Balance", "Avg Buy Price"])

        with tab1:
            ohlc = pyupbit.get_ohlcv("KRW-BTC", interval="minute5", count=2016)  # 5-minute intervals for ~1 week
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
                # Add BUY/SELL markers from trades table
                if not df_trades.empty:
                    fig = add_buy_sell_markers(fig, df_trades, 'timestamp', 'btc_krw_price', border_color=marker_border_color)
                fig.update_layout(
                    xaxis=dict(
                        title="Time",
                        rangeslider=dict(visible=True),
                        range=[ohlc['index'].iloc[-288], ohlc['index'].iloc[-1]]  # Show last day
                    ),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=0, b=0),
                    dragmode="pan",
                    height=600,
                    template=plotly_template,
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True, config=config)

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
                # Add BUY/SELL markers from trades table
                if not df_trades.empty:
                    fig = add_buy_sell_markers(fig, df_trades, 'timestamp', 'btc_krw_price', border_color=marker_border_color)
                fig.update_layout(
                    xaxis=dict(title="Date", rangeslider=dict(visible=True)),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=0, b=0),
                    height=600,
                    template=plotly_template,
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True, config=config)

        with tab3:
            if not df_trades.empty:
                fig = px.line(
                    df_trades, 
                    x='timestamp', 
                    y='btc_balance', 
                    title="BTC Balance Over Time", 
                    markers=True, 
                    template=plotly_template
                )
                # Set the trace name
                fig.update_traces(name='BTC Balance')

                # Add BUY/SELL markers
                fig = add_buy_sell_markers(fig, df_trades, 'timestamp', 'btc_balance', border_color=marker_border_color)
                
                fig.update_traces(
                    selector=dict(name='BTC Balance'),
                    line=dict(color='black', width=2),
                    marker=dict(size=4, symbol='circle', color='black')
                )
                fig.update_layout(
                    margin=dict(l=40, r=20, t=30, b=20),
                    height=600,
                    yaxis_title="BTC Balance",
                    xaxis=dict(showgrid=False),
                    yaxis=dict(showgrid=True, gridcolor='gray'),
                    plot_bgcolor='rgba(0,0,0,0)',
                    paper_bgcolor='rgba(0,0,0,0)',
                    hovermode="x unified",
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True, config=config)
            else:
                st.write("Trades 데이터가 없습니다.")

        with tab4:
            if not df_trades.empty:
                fig = px.line(
                    df_trades, 
                    x='timestamp', 
                    y='krw_balance', 
                    title="KRW Balance Over Time", 
                    markers=True, 
                    template=plotly_template
                )
                # Set the trace name
                fig.update_traces(name='KRW Balance')

                # Add BUY/SELL markers
                fig = add_buy_sell_markers(fig, df_trades, 'timestamp', 'krw_balance', border_color=marker_border_color)
                
                fig.update_traces(
                    selector=dict(name='KRW Balance'),
                    line=dict(color='black', width=2),
                    marker=dict(size=4, symbol='circle', color='black')
                )
                fig.update_layout(
                    margin=dict(l=40, r=20, t=30, b=20),
                    height=600,
                    yaxis_title="KRW Balance",
                    xaxis=dict(showgrid=False),
                    yaxis=dict(showgrid=True, gridcolor='gray'),
                    plot_bgcolor='rgba(0,0,0,0)',
                    paper_bgcolor='rgba(0,0,0,0)',
                    hovermode="x unified",
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True, config=config)
            else:
                st.write("Trades 데이터가 없습니다.")

        with tab5:
            if not df_trades.empty:
                fig = px.line(
                    df_trades, 
                    x='timestamp', 
                    y='btc_avg_buy_price', 
                    title="BTC Average Buy Price Over Time", 
                    markers=True, 
                    template=plotly_template
                )
                # Set the trace name
                fig.update_traces(name='BTC Avg Buy Price')

                # Add BUY/SELL markers
                fig = add_buy_sell_markers(fig, df_trades, 'timestamp', 'btc_avg_buy_price', border_color=marker_border_color)
                
                fig.update_traces(
                    selector=dict(name='BTC Avg Buy Price'),
                    line=dict(color='black', width=2),
                    marker=dict(size=4, symbol='circle', color='black')
                )
                fig.update_layout(
                    margin=dict(l=40, r=20, t=30, b=20),
                    height=600,
                    yaxis_title="Average Buy Price (KRW)",
                    xaxis=dict(showgrid=False),
                    yaxis=dict(showgrid=True, gridcolor='gray'),
                    plot_bgcolor='rgba(0,0,0,0)',
                    paper_bgcolor='rgba(0,0,0,0)',
                    hovermode="x unified",
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True, config=config)
            else:
                st.write("Trades 데이터가 없습니다.")

    # Bottom section: Trade History Table
    with st.container():
        st.markdown("<h3>📋 Trade History</h3>", unsafe_allow_html=True)
        
        # Format timestamp for display
        if not df_trades.empty:
            df_trades['timestamp_display'] = df_trades['timestamp'].dt.strftime('%Y-%m-%d %H:%M')
            displayed_df = df_trades.copy()
            displayed_df['timestamp'] = displayed_df['timestamp_display']

            # Drop unnecessary columns and rename
            displayed_df = displayed_df.drop(columns=['id', 'timestamp_display'], errors='ignore')
            displayed_df = displayed_df.rename(columns={
                'reason': '이유', 'reflection':'관점'
            })

            # Format KRW and BTC related columns
            for col in ['total_assets','krw_balance', 'btc_avg_buy_price', 'btc_krw_price']:
                if col in displayed_df.columns:
                    displayed_df[col] = displayed_df[col].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else x)

            # Reorder columns
            krw_btc_columns = ['krw_balance', 'btc_balance', 'btc_avg_buy_price', 'btc_krw_price']
            non_krw_btc_columns = [col for col in displayed_df.columns if col not in krw_btc_columns]
            final_columns = non_krw_btc_columns + krw_btc_columns
            displayed_df = displayed_df[final_columns]

            # Apply styling
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

            # Set table height
            st.dataframe(styled_df, use_container_width=True, height=300)
        else:
            st.write("Trades 데이터가 없습니다.")

if __name__ == "__main__":
    main()
