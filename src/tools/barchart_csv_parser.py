"""Barchart CSV Parser.

Parses naked put screener exports from Barchart.com into candidate objects
for validation and analysis.

Usage:
    from src.tools.barchart_csv_parser import parse_barchart_csv

    candidates = parse_barchart_csv("/path/to/export.csv")
    for candidate in candidates:
        print(f"{candidate.symbol} {candidate.strike}P @ {candidate.bid}")
"""

import csv
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.data.candidates import BarchartCandidate


def parse_percentage(value: str) -> float:
    """Convert percentage string to decimal float.

    Examples:
        "44.81%" -> 0.4481
        "-11.53%" -> -0.1153
        "1.0%" -> 0.01

    Args:
        value: Percentage string with "%" suffix

    Returns:
        Decimal float representation

    Raises:
        ValueError: If value cannot be parsed as percentage
    """
    try:
        # Validate that '%' is present
        cleaned = value.strip()
        if not cleaned.endswith("%"):
            raise ValueError(f"Percentage string must end with '%': {value}")

        # Remove '%' and convert to float, then divide by 100
        cleaned = cleaned.rstrip("%")
        return float(cleaned) / 100.0
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Cannot parse percentage: {value}") from e


def parse_expiration(date_str: str) -> datetime.date:
    """Parse expiration date string to date object.

    Expected format: "2026-02-27" (ISO format)

    Args:
        date_str: Date string in ISO format

    Returns:
        Date object

    Raises:
        ValueError: If date string is invalid
    """
    try:
        return datetime.fromisoformat(date_str).date()
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Cannot parse expiration date: {date_str}") from e


def is_metadata_row(row: dict) -> bool:
    """Check if row is the trailing metadata row.

    Barchart appends a row like:
    "Downloaded from Barchart.com as of 01-28-2026 11:20pm CST"

    Args:
        row: CSV row as dictionary

    Returns:
        True if row is metadata, False otherwise
    """
    # Check if the Symbol field contains "Downloaded" or "Barchart"
    symbol = row.get("Symbol", "").strip()
    return "Downloaded" in symbol or "Barchart" in symbol


def parse_barchart_csv(
    filepath: Path | str,
    otm_pct_min: float | None = None,
    otm_pct_max: float | None = None,
    check_earnings: bool = True,
) -> list[BarchartCandidate]:
    """Parse Barchart CSV export file.

    Reads a CSV file exported from Barchart's naked put options screener
    and converts each row into a BarchartCandidate object.

    Special handling:
    - Skips the last metadata row
    - Converts percentage strings to decimals
    - Converts delta sign (Barchart shows positive, converts to negative for puts)
    - Handles "Price~" column name with tilde
    - Filters by OTM% range if provided (loaded from config by default)
    - Filters candidates with earnings within DTE window (if check_earnings=True)

    Args:
        filepath: Path to the CSV file
        otm_pct_min: Minimum OTM% filter (e.g., 0.10 for 10%). If None, loads from config.
        otm_pct_max: Maximum OTM% filter (e.g., 0.25 for 25%). If None, loads from config.
        check_earnings: If True, exclude candidates with earnings within DTE window.

    Returns:
        List of BarchartCandidate objects (filtered by OTM% range and earnings)

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If CSV format is invalid or missing required columns
    """
    filepath = Path(filepath)

    if not filepath.exists():
        raise FileNotFoundError(f"CSV file not found: {filepath}")

    # Load OTM% filter from config if not explicitly provided
    if otm_pct_min is None or otm_pct_max is None:
        import os

        otm_pct_min = otm_pct_min if otm_pct_min is not None else float(
            os.getenv("BARCHART_OTM_PCT_MIN", "0.10")
        )
        otm_pct_max = otm_pct_max if otm_pct_max is not None else float(
            os.getenv("BARCHART_OTM_PCT_MAX", "0.25")
        )

    candidates = []
    skipped_count = 0
    otm_filtered_count = 0
    earnings_filtered_count = 0

    logger.info(f"Parsing Barchart CSV: {filepath}")
    logger.info(f"OTM% filter: {otm_pct_min:.0%} - {otm_pct_max:.0%}")
    logger.info(f"Earnings check: {'enabled' if check_earnings else 'disabled'}")

    try:
        with open(filepath, encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Validate required columns exist
            required_columns = {
                "Symbol",
                "Price~",
                "Exp Date",
                "DTE",
                "Strike",
                "Type",
                "Moneyness",
                "Bid",
                "BE (Bid)",
                "%BE (Bid)",
                "Volume",
                "Open Int",
                "IV Rank",
                "Delta",
                "Return",
                "Ann Rtn",
                "Profit Prob",
            }

            if reader.fieldnames is None:
                raise ValueError("CSV file is empty or has no header row")

            missing_columns = required_columns - set(reader.fieldnames)
            if missing_columns:
                raise ValueError(
                    f"CSV missing required columns: {', '.join(missing_columns)}"
                )

            for row_num, row in enumerate(reader, start=2):  # Start at 2 (header = 1)
                # Skip metadata row
                if is_metadata_row(row):
                    logger.debug(f"Skipping metadata row at line {row_num}")
                    skipped_count += 1
                    continue

                # Skip empty rows
                if not row.get("Symbol", "").strip():
                    logger.debug(f"Skipping empty row at line {row_num}")
                    skipped_count += 1
                    continue

                try:
                    # Parse all fields
                    symbol = row["Symbol"].strip()
                    underlying_price = float(row["Price~"])
                    expiration = parse_expiration(row["Exp Date"])
                    dte = int(row["DTE"])
                    strike = float(row["Strike"])
                    option_type = row["Type"].strip().upper()
                    moneyness_pct = parse_percentage(row["Moneyness"])
                    bid = float(row["Bid"])
                    breakeven = float(row["BE (Bid)"])
                    breakeven_pct = parse_percentage(row["%BE (Bid)"])
                    volume = int(row["Volume"])
                    open_interest = int(row["Open Int"])
                    iv_rank = parse_percentage(row["IV Rank"])
                    delta = float(row["Delta"])
                    premium_return_pct = parse_percentage(row["Return"])
                    annualized_return_pct = parse_percentage(row["Ann Rtn"])
                    profit_probability = parse_percentage(row["Profit Prob"])

                    # Convert delta to negative for puts
                    # Barchart shows positive delta for puts, but standard convention is negative
                    if option_type == "PUT" and delta > 0:
                        delta = -delta

                    # Calculate OTM% and apply filter
                    # Use (stock_price - strike) / stock_price for puts
                    otm_pct = (underlying_price - strike) / underlying_price if underlying_price > 0 else 0.0

                    if otm_pct < otm_pct_min or otm_pct > otm_pct_max:
                        logger.debug(
                            f"OTM% {otm_pct:.1%} outside range {otm_pct_min:.0%}-{otm_pct_max:.0%}: "
                            f"{symbol} ${strike} (stock ${underlying_price:.2f})"
                        )
                        otm_filtered_count += 1
                        continue

                    # Check earnings within DTE window
                    if check_earnings:
                        try:
                            from src.services.earnings_service import get_cached_earnings

                            earnings_info = get_cached_earnings(symbol, expiration)
                            if earnings_info.earnings_in_dte:
                                logger.info(
                                    f"BLOCKED: {symbol} has earnings on {earnings_info.earnings_date}, "
                                    f"within DTE window (exp {expiration})"
                                )
                                earnings_filtered_count += 1
                                continue
                        except Exception as e:
                            # Don't block on earnings data gaps
                            logger.warning(
                                f"WARNING: Earnings data unavailable for {symbol}: {e}"
                            )

                    # Create candidate
                    candidate = BarchartCandidate(
                        symbol=symbol,
                        expiration=expiration,
                        strike=strike,
                        option_type=option_type,
                        underlying_price=underlying_price,
                        bid=bid,
                        dte=dte,
                        moneyness_pct=moneyness_pct,
                        breakeven=breakeven,
                        breakeven_pct=breakeven_pct,
                        volume=volume,
                        open_interest=open_interest,
                        iv_rank=iv_rank,
                        delta=delta,
                        premium_return_pct=premium_return_pct,
                        annualized_return_pct=annualized_return_pct,
                        profit_probability=profit_probability,
                        source="barchart_csv",
                        raw_row=dict(row),  # Preserve original for debugging
                    )

                    candidates.append(candidate)

                except (ValueError, KeyError) as e:
                    logger.warning(
                        f"Failed to parse row {row_num}: {e}",
                        extra={"row": row, "error": str(e)},
                    )
                    skipped_count += 1
                    continue

    except Exception as e:
        logger.error(f"Error reading CSV file {filepath}: {e}")
        raise ValueError(f"Failed to parse CSV file: {e}") from e

    logger.info(
        f"Parsed {len(candidates)} candidates from {filepath} "
        f"(skipped {skipped_count} rows, filtered {otm_filtered_count} by OTM%, "
        f"{earnings_filtered_count} by earnings)"
    )

    return candidates
