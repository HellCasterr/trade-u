from __future__ import annotations

import math
from datetime import date

import pandas as pd


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def black_scholes(spot: float, strike: float, years: float, rate: float, volatility: float, option_type: str) -> float:
    """European BSM estimate used only for transparent option scenarios."""
    years = max(years, 1 / 3650)
    volatility = max(volatility, 0.0001)
    d1 = (math.log(spot / strike) + (rate + volatility**2 / 2) * years) / (volatility * math.sqrt(years))
    d2 = d1 - volatility * math.sqrt(years)
    discount = math.exp(-rate * years)
    if option_type.upper() in {"CE", "CALL"}:
        return spot * _normal_cdf(d1) - strike * discount * _normal_cdf(d2)
    return strike * discount * _normal_cdf(-d2) - spot * _normal_cdf(-d1)


def option_scenarios(
    current_premium: float,
    spot: float,
    strike: float,
    expiry: date,
    option_type: str,
    target_spot: float,
    stop_spot: float,
    implied_volatility_pct: float,
    holding_days: float = 1,
    risk_free_rate_pct: float = 6.5,
) -> pd.DataFrame:
    days = max((expiry - date.today()).days - holding_days, 0.1)
    rows = []
    for label, multiplier in [("IV -20%", 0.8), ("IV unchanged", 1.0), ("IV +20%", 1.2)]:
        iv = implied_volatility_pct / 100 * multiplier
        target_premium = black_scholes(target_spot, strike, days / 365, risk_free_rate_pct / 100, iv, option_type)
        stop_premium = black_scholes(stop_spot, strike, days / 365, risk_free_rate_pct / 100, iv, option_type)
        rows.append(
            {
                "Scenario": label,
                "Est. premium at target": target_premium,
                "Est. premium at stop": stop_premium,
                "Premium P/L at target %": (target_premium / current_premium - 1) * 100,
                "Premium P/L at stop %": (stop_premium / current_premium - 1) * 100,
            }
        )
    return pd.DataFrame(rows)
