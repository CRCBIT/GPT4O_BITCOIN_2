with tab3:
    st.markdown("### 💹 Asset Percentage Over Time")

    # BTC Percentage 막대그래프
    fig_btc_pct = px.bar(
        df,
        x='timestamp',
        y='btc_percentage',
        title="BTC Balance Percentage Over Time",
        labels={'btc_percentage': 'BTC Balance (%)'},
        template=plotly_template
    )
    fig_btc_pct.update_layout(
        xaxis_title='Time',
        yaxis_title='Percentage (%)',
        margin=dict(l=40, r=20, t=50, b=50),
        height=300,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        hovermode='x unified',
        showlegend=False
    )
    st.plotly_chart(fig_btc_pct, use_container_width=True, config=config)

    # KRW Percentage 막대그래프
    fig_krw_pct = px.bar(
        df,
        x='timestamp',
        y='krw_percentage',
        title="KRW Balance Percentage Over Time",
        labels={'krw_percentage': 'KRW Balance (%)'},
        template=plotly_template
    )
    fig_krw_pct.update_layout(
        xaxis_title='Time',
        yaxis_title='Percentage (%)',
        margin=dict(l=40, r=20, t=50, b=50),
        height=300,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        hovermode='x unified',
        showlegend=False
    )
    st.plotly_chart(fig_krw_pct, use_container_width=True, config=config)
