"""Black-Scholes implied volatility solver and Greeks calculator.

Reverse-engineers IV from known trade data (premium, strike, stock price, DTE,
risk-free rate) using scipy's Brent root-finding method. Also calculates
approximate Greeks from the solved IV.

Coverage: All years (2019-2026). Accuracy: within ~2-5 IV points for liquid options.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm
from loguru import logger


@dataclass
class BSResult:
    """Result from Black-Scholes IV solving and Greeks calculation."""

    iv: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None
    source: str = "bs_approximation"


def bs_put_price(
    S: float, K: float, T: float, r: float, sigma: float
) -> float:
    """Calculate Black-Scholes put option price.

    Args:
        S: Current stock price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free interest rate (annualized, e.g. 0.05 for 5%)
        sigma: Implied volatility (annualized, e.g. 0.30 for 30%)

    Returns:
        Theoretical put option price
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0

    d1 = (np.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_call_price(
    S: float, K: float, T: float, r: float, sigma: float
) -> float:
    """Calculate Black-Scholes call option price.

    Args:
        S: Current stock price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free interest rate (annualized)
        sigma: Implied volatility (annualized)

    Returns:
        Theoretical call option price
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0

    d1 = (np.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)


def solve_iv(
    option_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "P",
) -> Optional[float]:
    """Solve for implied volatility given an observed option price.

    Uses Brent's method to find the volatility that produces the observed
    option price under Black-Scholes assumptions.

    Args:
        option_price: Observed option price (premium)
        S: Current stock price
        K: Strike price
        T: Time to expiry in years (e.g. 30/365 for 30 DTE)
        r: Risk-free interest rate (annualized)
        option_type: "P" for put, "C" for call

    Returns:
        Implied volatility (annualized) or None if no solution found
    """
    if option_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None

    price_fn = bs_put_price if option_type.upper().startswith("P") else bs_call_price

    # Define the objective function: find sigma where price(sigma) = observed price
    def objective(sigma: float) -> float:
        return price_fn(S, K, T, r, sigma) - option_price

    try:
        iv = brentq(objective, 0.001, 10.0, xtol=1e-6, maxiter=100)
        return round(iv, 6)
    except ValueError:
        # No solution in range — price is outside B-S bounds
        # This can happen with deep ITM options, or if premium includes
        # significant time value beyond B-S assumptions
        logger.debug(
            f"B-S IV solver: no solution for price={option_price}, "
            f"S={S}, K={K}, T={T:.4f}, r={r}"
        )
        return None
    except Exception as e:
        logger.debug(f"B-S IV solver error: {e}")
        return None


def calculate_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "P",
) -> BSResult:
    """Calculate option Greeks from Black-Scholes model.

    Args:
        S: Current stock price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free interest rate (annualized)
        sigma: Implied volatility (annualized)
        option_type: "P" for put, "C" for call

    Returns:
        BSResult with all Greeks populated
    """
    result = BSResult(iv=sigma)

    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return result

    try:
        sqrt_T = np.sqrt(T)
        d1 = (np.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        # Gamma (same for puts and calls)
        result.gamma = round(norm.pdf(d1) / (S * sigma * sqrt_T), 6)

        # Vega (same for puts and calls) — per 1% move in IV
        result.vega = round(S * norm.pdf(d1) * sqrt_T / 100, 4)

        if option_type.upper().startswith("P"):
            result.delta = round(norm.cdf(d1) - 1, 4)
            result.theta = round(
                (
                    -S * norm.pdf(d1) * sigma / (2 * sqrt_T)
                    + r * K * np.exp(-r * T) * norm.cdf(-d2)
                ) / 365,
                4,
            )
            result.rho = round(
                -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100, 4
            )
        else:
            result.delta = round(norm.cdf(d1), 4)
            result.theta = round(
                (
                    -S * norm.pdf(d1) * sigma / (2 * sqrt_T)
                    - r * K * np.exp(-r * T) * norm.cdf(d2)
                ) / 365,
                4,
            )
            result.rho = round(
                K * T * np.exp(-r * T) * norm.cdf(d2) / 100, 4
            )

    except Exception as e:
        logger.debug(f"Greeks calculation error: {e}")

    return result


def solve_iv_and_greeks(
    option_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "P",
) -> BSResult:
    """Solve for IV and calculate all Greeks in one call.

    Convenience function combining solve_iv() and calculate_greeks().

    Args:
        option_price: Observed option price (premium)
        S: Current stock price
        K: Strike price
        T: Time to expiry in years (e.g. 30/365 for 30 DTE)
        r: Risk-free interest rate (annualized)
        option_type: "P" for put, "C" for call

    Returns:
        BSResult with IV and all Greeks, or empty result if IV can't be solved
        or DTE is too low for reliable B-S approximation.
    """
    # B-S IV is unreliable at very low DTE — gamma dominance and time-value
    # collapse produce inflated IV (e.g. 215% at 2 DTE). Return None so the
    # learning engine sees clean data. Barchart scraper fills real market IV.
    dte = T * 365
    if dte <= 5:
        logger.debug(
            f"B-S IV skipped: DTE={dte:.0f} <= 5 — too low for reliable approximation "
            f"(S={S}, K={K}, price={option_price})"
        )
        return BSResult()

    iv = solve_iv(option_price, S, K, T, r, option_type)
    if iv is None:
        return BSResult()

    return calculate_greeks(S, K, T, r, iv, option_type)


# Default risk-free rates by year (approximate US Treasury 3-month rates)
# Used as fallback when FRED API is unavailable
DEFAULT_RISK_FREE_RATES = {
    2019: 0.022,
    2020: 0.005,
    2021: 0.001,
    2022: 0.030,
    2023: 0.050,
    2024: 0.053,
    2025: 0.045,
    2026: 0.040,
}


def get_risk_free_rate(year: int) -> float:
    """Get approximate risk-free rate for a given year.

    Uses hardcoded historical 3-month Treasury rates as default.
    A future enhancement could fetch from FRED API for exact daily rates.

    Args:
        year: Calendar year

    Returns:
        Annualized risk-free rate (e.g. 0.05 for 5%)
    """
    return DEFAULT_RISK_FREE_RATES.get(year, 0.04)
