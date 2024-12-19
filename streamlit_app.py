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
    line=dict(color='green', width=3),
    fillcolor='rgba(0, 128, 0, 0.3)',  # 반투명 녹색으로 채움
    marker=dict(size=6, symbol='circle', color='green')
)

# 초기 투자 기준선 추가
total_assets_fig.add_hline(
    y=initial_investment,
    line_dash="dash",
    line_color="gray",
    annotation_text="Initial Investment",
    annotation_position="bottom right"
)

# BUY/SELL 마커 추가
total_assets_fig = add_buy_sell_markers(total_assets_fig, df, 'timestamp', 'total_assets', border_color=marker_border_color)

# 레이아웃 조정
total_assets_fig.update_layout(
    xaxis=dict(
        title="Time",
        rangeslider=dict(visible=True),
        type="date"
    ),
    yaxis=dict(title="Total Assets (KRW)", tickprefix="₩"),
    margin=dict(l=20, r=20, t=50, b=20),
    height=350,
    hovermode="x unified",
    showlegend=False,
    plot_bgcolor='rgba(0,0,0,0)',  # 투명 배경
    paper_bgcolor='rgba(0,0,0,0)'  # 투명 배경
)

st.plotly_chart(total_assets_fig, use_container_width=True)
