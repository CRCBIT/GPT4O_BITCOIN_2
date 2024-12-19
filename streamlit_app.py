import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import pyupbit
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

# Set Streamlit page configuration
st.set_page_config(
    layout="wide",
    page_title="Bitcoin Dashboard",
    page_icon="📈",
    initial_sidebar_state="collapsed"
)

# Inject custom CSS for styling
st.markdown(
    """
    <style>
    /* Reduce top padding of the main container */
    .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
    }

    /* Style the main title with underline */
    h1 {
        margin-top: 0;
        text-decoration: underline;
        text-decoration-color: currentColor;
        text-decoration-thickness: 3px;
    }

    /* Center align headers within columns */
    .metric-header {
        text-align: center;
        font-size: 1.2rem;
        font-weight: bold;
    }

    /* Style the trade history table headers */
    .streamlit-expanderHeader {
        font-size: 1.1rem;
        font-weight: bold;
    }

    /* Customize the decision column in the table */
    .decision-buy {
        background-color: green;
        color: white;
        text-align: center;
        font-weight: bold;
    }

    .decision-sell {
        background-color: red;
        color: white;
        text-align: center;
        font-weight: bold;
    }

    /* Adjust spacing for tabs */
    .stTabs > .stTabs__tabList > button {
        padding: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

def get_connection():
    """Establish a connection to the SQLite database."""
    return sqlite3.connect('bitcoin_trades.db')

def load_data():
    """Load trade data from the SQLite database."""
    conn = get_connection()
    query = "SELECT * FROM trades"
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def calculate_initial_investment(df):
    """Calculate the initial investment based on the first record."""
    initial_krw_balance = df.iloc[0]['krw_balance']
    initial_btc_balance = df.iloc[0]['btc_balance']
    initial_btc_price = df.iloc[0]['btc_krw_price']
    return initial_krw_balance + (initial_btc_balance * initial_btc_price)

def calculate_current_investment(df):
    """Calculate the current investment based on the latest record and current BTC price."""
    current_krw_balance = df.iloc[-1]['krw_balance']
    current_btc_balance = df.iloc[-1]['btc_balance']
    current_btc_price = pyupbit.get_current_price("KRW-BTC")
    return current_krw_balance + (current_btc_balance * current_btc_price)

def add_buy_sell_markers(fig, df, x_col, y_col):
    """
    Add buy and sell markers to a Plotly figure.
    
    Args:
        fig (plotly.graph_objects.Figure): The Plotly figure to modify.
        df (pandas.DataFrame): The dataframe containing trade data.
        x_col (str): The column name for the x-axis.
        y_col (str): The column name for the y-axis.
    
    Returns:
        plotly.graph_objects.Figure: The modified Plotly figure with markers.
    """
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
    """Main function to render the Streamlit app."""
    # Auto-refresh the page every 60 seconds
    st_autorefresh(interval=60000, limit=None, key="auto_refresh")

    # Load trade data
    df = load_data()

    if df.empty:
        st.warning('No trade data available.')
        return

    # Calculate investment metrics
    initial_investment = calculate_initial_investment(df)
    current_investment = calculate_current_investment(df)
    profit_rate = ((current_investment - initial_investment) / initial_investment) * 100
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # Render the main title
    st.title("Bitcoin Trading Dashboard")

    # Performance Metrics Section
    with st.container():
        st.markdown("### ⚡ Performance Metrics")
        # Create three equal-width columns for metrics
        col1, col2, col3 = st.columns(3)

        # Current Profit Rate with Conditional Formatting
        with col1:
            if profit_rate > 0:
                formatted_profit = f"<span style='color:red; font-weight:bold;'>+{profit_rate:.2f}%</span>"
            elif profit_rate < 0:
                formatted_profit = f"<span style='color:blue; font-weight:bold;'>{profit_rate:.2f}%</span>"
            else:
                formatted_profit = f"{profit_rate:.2f}%"

            st.markdown("**Current Profit Rate**")
            st.markdown(formatted_profit, unsafe_allow_html=True)

        # Total Assets
        with col2:
            st.markdown("**Total Assets (KRW)**")
            st.markdown(f"{current_investment:,.0f} KRW", unsafe_allow_html=True)

        # Current BTC Price
        with col3:
            st.markdown("**Current BTC Price (KRW)**")
            st.markdown(f"{current_btc_price:,.0f} KRW", unsafe_allow_html=True)

    # Total Assets Line Chart
    with st.container():
        df['total_assets'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
        total_assets_fig = px.line(
            df, 
            x='timestamp', 
            y='total_assets',
            title='Total Assets Over Time',
            markers=True,
            template='plotly_dark',
            line_shape='spline',
            hover_data={'total_assets': ':.0f'}
        )

        total_assets_fig.update_traces(
            line=dict(color='teal', width=3),
            marker=dict(size=6, symbol='circle', color='teal')
        )

        total_assets_fig.update_layout(
            margin=dict(l=20, r=20, t=50, b=20),
            height=300,
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
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified"
        )

        st.plotly_chart(total_assets_fig, use_container_width=True)

    # Trade-Related Charts Section with Tabs
    with st.container():
        st.markdown("### 📈 Trade-Related Charts")
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "BTC Price Chart", 
            "1-Year BTC Price (Daily)", 
            "BTC Balance", 
            "KRW Balance", 
            "Avg Buy Price"
        ])

        # Tab 1: BTC Price with Buy/Sell Points
        with tab1:
            st.subheader("BTC Price with Buy/Sell Points (5-Min Candles for 1 Week)")
            ohlc = pyupbit.get_ohlcv("KRW-BTC", interval="minute5", count=2016)  # 1 week
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
                        range=[ohlc['index'].iloc[-288], ohlc['index'].iloc[-1]]  # Last day
                    ),
                    yaxis=dict(title="Price (KRW)"),
                    margin=dict(l=40, r=20, t=30, b=20),
                    dragmode="pan",
                    height=400,
                    template='plotly_dark'
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("No BTC price data available.")

        # Tab 2: 1-Year BTC Price (Daily)
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
            else:
                st.warning("No daily BTC price data available.")

        # Tab 3: BTC Balance Over Time
        with tab3:
            st.subheader("BTC Balance Over Time")
            fig = px.line(
                df, 
                x='timestamp', 
                y='btc_balance', 
                title="BTC Balance Over Time", 
                markers=True, 
                template='plotly_dark', 
                line_shape='spline'
            )
            fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_balance')
            fig.update_traces(
                line=dict(color='orange', width=3),
                marker=dict(size=6, symbol='circle', color='orange')
            )
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

        # Tab 4: KRW Balance Over Time
        with tab4:
            st.subheader("KRW Balance Over Time")
            fig = px.line(
                df, 
                x='timestamp', 
                y='krw_balance', 
                title="KRW Balance Over Time", 
                markers=True, 
                template='plotly_dark', 
                line_shape='spline'
            )
            fig = add_buy_sell_markers(fig, df, 'timestamp', 'krw_balance')
            fig.update_traces(
                line=dict(color='purple', width=3),
                marker=dict(size=6, symbol='circle', color='purple')
            )
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

        # Tab 5: BTC Average Buy Price Over Time
        with tab5:
            st.subheader("BTC Average Buy Price Over Time")
            fig = px.line(
                df, 
                x='timestamp', 
                y='btc_avg_buy_price', 
                title="BTC Average Buy Price Over Time", 
                markers=True, 
                template='plotly_dark', 
                line_shape='spline'
            )
            fig = add_buy_sell_markers(fig, df, 'timestamp', 'btc_avg_buy_price')
            fig.update_traces(
                line=dict(color='cyan', width=3),
                marker=dict(size=6, symbol='circle', color='cyan')
            )
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

    # Trade History Section
    with st.container():
        st.markdown("### 📋 Trade History")
        # Format timestamp for display
        df['timestamp_display'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M')
        displayed_df = df.copy()
        displayed_df['timestamp'] = displayed_df['timestamp_display']

        # Drop unnecessary columns and rename for clarity
        displayed_df = displayed_df.drop(columns=['id', 'timestamp_display'], errors='ignore')
        displayed_df = displayed_df.rename(columns={
            'reason': 'Reason', 
            'reflection': 'Reflection',
            'decision': 'Decision'
        })

        # Format numerical columns with commas
        for col in ['total_assets','krw_balance', 'btc_avg_buy_price', 'btc_krw_price']:
            if col in displayed_df.columns:
                displayed_df[col] = displayed_df[col].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else x)

        # Reorder columns for better readability
        krw_btc_columns = ['krw_balance', 'btc_balance', 'btc_avg_buy_price', 'btc_krw_price']
        non_krw_btc_columns = [col for col in displayed_df.columns if col not in krw_btc_columns]
        final_columns = ['timestamp'] + non_krw_btc_columns + krw_btc_columns
        displayed_df = displayed_df[final_columns]

        # Apply styling to the dataframe
        def highlight_decision(val):
            """Highlight the Decision column based on buy/sell."""
            if val == 'buy':
                return 'background-color: green; color: white; text-align: center; font-weight: bold;'
            elif val == 'sell':
                return 'background-color: red; color: white; text-align: center; font-weight: bold;'
            return ''

        styled_df = displayed_df.style.applymap(
            highlight_decision, subset=['Decision']
        ).set_properties(**{
            'text-align': 'center'
        }).set_table_styles([
            {
                'selector': 'th',
                'props': [
                    ('text-align', 'center'),
                    ('background-color', '#f0f2f6'),
                    ('font-weight', 'bold')
                ]
            },
            {
                'selector': 'td',
                'props': [
                    ('text-align', 'center')
                ]
            }
        ])

        # Display the styled dataframe
        st.dataframe(styled_df, use_container_width=True, height=300)

if __name__ == "__main__":
    main()
