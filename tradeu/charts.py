from __future__ import annotations

import plotly.graph_objects as go

from tradeu.models import TradeAnalysis


def market_chart(data, analysis: TradeAnalysis, symbol: str) -> go.Figure:
    view = data.tail(180)
    figure = go.Figure(
        go.Candlestick(
            x=view["timestamp"],
            open=view["open"],
            high=view["high"],
            low=view["low"],
            close=view["close"],
            name=symbol,
            increasing_line_color="#16b981",
            decreasing_line_color="#f43f5e",
        )
    )
    figure.add_trace(
        go.Scatter(x=view["timestamp"], y=view["ema_fast"], name="EMA 20", line={"color": "#38bdf8", "width": 1.3})
    )
    figure.add_trace(
        go.Scatter(x=view["timestamp"], y=view["ema_slow"], name="EMA 50", line={"color": "#fbbf24", "width": 1.3})
    )
    levels = [
        (analysis.support.level if analysis.support else None, "Support", "#10b981", "dot"),
        (analysis.resistance.level if analysis.resistance else None, "Resistance", "#f97316", "dot"),
        (analysis.entry, "Entry", "#a78bfa", "solid"),
        (analysis.stop, "Stop", "#fb7185", "dash"),
        (analysis.target, "Target", "#34d399", "dash"),
    ]
    for level, label, color, dash in levels:
        if level is not None:
            figure.add_hline(y=level, line_color=color, line_dash=dash, annotation_text=f"{label} {level:,.2f}")
    figure.update_layout(
        height=590,
        margin={"l": 10, "r": 10, "t": 45, "b": 10},
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08},
        title=f"{symbol} · structure, levels and proposed risk",
    )
    return figure
