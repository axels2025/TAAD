"""Barchart Options Scanner Client.

Uses the Barchart getOptionsScreener API to efficiently find naked put candidates
across the entire US market in a single API call.

This is the "top of funnel" for our screening workflow:
1. Barchart scans entire market (fast, single API call)
2. Returns ~50-100 pre-filtered candidates
3. IBKR validates candidates (accurate, real-time data)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.config.naked_put_options_config import (
    NakedPutScreenerConfig,
    get_naked_put_config,
)


class BarchartScanResult(BaseModel):
    """Individual option result from Barchart scanner.

    Represents a single option contract returned by the Barchart API.
    """

    underlying_symbol: str = Field(description="Stock/ETF symbol (e.g., AAPL)")
    instrument_type: str = Field(description="Type: stock or etf")
    option_type: str = Field(description="Option type: put or call")
    strike: float = Field(description="Strike price")
    expiration_date: str = Field(description="Expiration date (YYYY-MM-DD)")
    last_price: float = Field(description="Last price of underlying")
    option_price: float = Field(description="Last option price")

    # Optional fields from Barchart
    bid: Optional[float] = Field(default=None, description="Option bid price")
    ask: Optional[float] = Field(default=None, description="Option ask price")
    delta: Optional[float] = Field(default=None, description="Option delta")
    gamma: Optional[float] = Field(default=None, description="Option gamma")
    theta: Optional[float] = Field(default=None, description="Option theta")
    vega: Optional[float] = Field(default=None, description="Option vega")
    volume: Optional[int] = Field(default=None, description="Option volume")
    open_interest: Optional[float] = Field(default=None, description="Open interest")
    volatility: Optional[float] = Field(default=None, description="Implied volatility")
    trade_time: Optional[str] = Field(default=None, description="Last trade time")


class BarchartScanOutput(BaseModel):
    """Complete output from a Barchart scan.

    Contains all results, metadata, and configuration used for the scan.
    """

    scan_timestamp: datetime = Field(description="When the scan was executed")
    config_used: dict = Field(description="API parameters used for this scan")
    total_results: int = Field(description="Number of results returned")
    results: list[BarchartScanResult] = Field(description="List of option results")
    api_status_code: int = Field(description="Barchart API status code")
    api_message: str = Field(description="Barchart API status message")


class BarchartScanner:
    """Client for Barchart Options Screener API.

    This scanner performs server-side filtering across the entire US options
    market, returning only contracts that match the naked put criteria.

    This is dramatically faster than the old IBKR-only approach because:
    - Single API call vs hundreds of IBKR calls
    - Server-side filtering vs client-side iteration
    - No need to fetch full option chains
    - Pre-filtered by volume, OI, delta, price

    Example:
        >>> scanner = BarchartScanner()
        >>> results = scanner.scan()
        >>> print(f"Found {results.total_results} candidates")
        >>> for r in results.results[:5]:
        ...     print(f"{r.underlying_symbol} ${r.strike} @ ${r.bid}")
    """

    def __init__(self, config: Optional[NakedPutScreenerConfig] = None):
        """Initialize Barchart scanner.

        Args:
            config: Scanner configuration. Uses default from env if None.

        Raises:
            ValueError: If BARCHART_API_KEY is not set in environment
        """
        self.config = config or get_naked_put_config()
        self.api_key = self.config.screener.api_key
        self.api_url = self.config.screener.api_url

        if not self.api_key:
            raise ValueError(
                "BARCHART_API_KEY not set. "
                "Get your API key from https://www.barchart.com/ondemand "
                "and add it to your .env file:\n"
                "BARCHART_API_KEY=your_key_here"
            )

        logger.info("Initialized BarchartScanner")

    def scan(self) -> BarchartScanOutput:
        """Execute options scan using Barchart API.

        Makes a single API call to scan the entire market based on
        configured parameters.

        Returns:
            BarchartScanOutput: Scan results with metadata

        Raises:
            httpx.HTTPStatusError: If API request fails (invalid key, rate limit, etc.)
            httpx.RequestError: If network request fails
            ValueError: If API returns unexpected response format

        Example:
            >>> scanner = BarchartScanner()
            >>> try:
            ...     results = scanner.scan()
            ...     print(f"Success: {results.total_results} results")
            ... except httpx.HTTPStatusError as e:
            ...     print(f"API error: {e}")
        """
        params = self.config.to_barchart_params()

        logger.info(
            f"Executing Barchart scan: "
            f"DTE {params['minDTE']}-{params['maxDTE']}, "
            f"Delta {params['minDelta']}-{params['maxDelta']}, "
            f"Min Bid ${params['minPrice']}"
        )

        scan_time = datetime.now()

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(self.api_url, params=params)
                response.raise_for_status()
                data = response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("Barchart API authentication failed - check your API key")
                raise ValueError(
                    "Invalid Barchart API key. "
                    "Please check your .env file and ensure BARCHART_API_KEY is correct."
                ) from e
            elif e.response.status_code == 429:
                logger.error("Barchart API rate limit exceeded")
                raise ValueError(
                    "Barchart API rate limit exceeded. "
                    "You may need to wait or upgrade your plan."
                ) from e
            else:
                logger.error(f"Barchart API error: {e}")
                raise ValueError(
                    f"Barchart API request failed with status {e.response.status_code}"
                ) from e

        except httpx.RequestError as e:
            logger.error(f"Network error contacting Barchart API: {e}")
            raise ValueError(
                "Could not reach Barchart API. Check your internet connection."
            ) from e

        # Parse API response
        status = data.get("status", {})
        status_code = status.get("code", 0)
        status_message = status.get("message", "")

        if status_code != 200:
            logger.error(
                f"Barchart API returned error: {status_code} - {status_message}"
            )
            raise ValueError(
                f"Barchart API error: {status_message} (code: {status_code})"
            )

        # Extract and parse results
        raw_results = data.get("results", [])
        results = []

        for r in raw_results:
            try:
                # Apply client-side stock price filter
                # (Barchart API doesn't support this filter directly)
                last_price = r.get("lastPrice", 0)
                if not (
                    self.config.screener.stock_price_min
                    <= last_price
                    <= self.config.screener.stock_price_max
                ):
                    continue

                # Apply client-side IV filter if needed
                volatility = r.get("volatility")
                if volatility is not None:
                    if not (
                        self.config.screener.raw_iv_min
                        <= volatility
                        <= self.config.screener.raw_iv_max
                    ):
                        continue

                # Apply client-side OTM% filter
                strike = float(r.get("strike", 0))
                if last_price > 0:
                    otm_pct = (last_price - strike) / last_price
                    if not (
                        self.config.screener.otm_pct_min
                        <= otm_pct
                        <= self.config.screener.otm_pct_max
                    ):
                        continue

                # Check earnings within DTE window
                expiration_date_str = r.get("expirationDate", "")
                underlying_symbol = r.get("underlyingSymbol", "")
                if expiration_date_str and underlying_symbol:
                    try:
                        from datetime import date as date_type

                        from src.services.earnings_service import get_cached_earnings

                        exp_date = date_type.fromisoformat(expiration_date_str)
                        earnings_info = get_cached_earnings(underlying_symbol, exp_date)
                        if earnings_info.earnings_in_dte:
                            logger.info(
                                f"BLOCKED: {underlying_symbol} has earnings on "
                                f"{earnings_info.earnings_date}, within DTE window "
                                f"(exp {expiration_date_str})"
                            )
                            continue
                    except Exception as e:
                        logger.warning(
                            f"Earnings data unavailable for {underlying_symbol}: {e}"
                        )

                # Parse into result object
                result = BarchartScanResult(
                    underlying_symbol=r.get("underlyingSymbol", ""),
                    instrument_type=r.get("instrumentType", ""),
                    option_type=r.get("type", ""),
                    strike=float(r.get("strike", 0)),
                    expiration_date=r.get("expirationDate", ""),
                    last_price=last_price,
                    option_price=float(r.get("optionPrice", 0)),
                    bid=r.get("bid"),
                    ask=r.get("ask"),
                    delta=r.get("delta"),
                    gamma=r.get("gamma"),
                    theta=r.get("theta"),
                    vega=r.get("vega"),
                    volume=r.get("volume"),
                    open_interest=r.get("openInterest"),
                    volatility=volatility,
                    trade_time=r.get("tradeTime"),
                )
                results.append(result)

            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"Failed to parse Barchart result: {r}, error: {e}")
                continue

        output = BarchartScanOutput(
            scan_timestamp=scan_time,
            config_used=params,
            total_results=len(results),
            results=results,
            api_status_code=status_code,
            api_message=status_message,
        )

        logger.info(
            f"Barchart scan complete: {len(results)} results "
            f"(API status: {status_code} - {status_message})"
        )

        return output

    def scan_and_save(self, output_path: Optional[str] = None) -> Path:
        """Execute scan and save results to JSON file.

        Useful for:
        - Debugging scan results
        - Analyzing historical scans
        - Sharing scan results

        Args:
            output_path: Custom output path. Uses config default if None.

        Returns:
            Path: Path to saved JSON file

        Example:
            >>> scanner = BarchartScanner()
            >>> file_path = scanner.scan_and_save()
            >>> print(f"Saved to {file_path}")
            Saved to data/scans/barchart_scan_20250126_143022.json
        """
        results = self.scan()

        # Determine output path
        if output_path is None:
            output_dir = Path(self.config.screener.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = results.scan_timestamp.strftime("%Y%m%d_%H%M%S")
            output_path = output_dir / f"barchart_scan_{timestamp}.json"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save to file
        with open(output_path, "w") as f:
            json.dump(
                results.model_dump(mode="json"),
                f,
                indent=2,
                default=str  # Handle datetime serialization
            )

        logger.info(f"Saved scan results to {output_path}")

        return output_path

    @staticmethod
    def load_scan(file_path: str | Path) -> BarchartScanOutput:
        """Load scan results from JSON file.

        Useful for:
        - Re-analyzing previous scans
        - Testing IBKR validation with cached data
        - Debugging scan issues

        Args:
            file_path: Path to JSON file created by scan_and_save()

        Returns:
            BarchartScanOutput: Loaded scan results

        Example:
            >>> results = BarchartScanner.load_scan("data/scans/scan_20250126.json")
            >>> print(f"Loaded {results.total_results} results")
        """
        with open(file_path) as f:
            data = json.load(f)

        return BarchartScanOutput(**data)
