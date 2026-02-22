"""Flask web application for manual trade entry.

Provides a user-friendly web interface to manually enter trading opportunities
and save them directly to the database.
"""

import os
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash
from loguru import logger

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from src.data.database import get_db_session, init_database
from src.utils.timezone import us_trading_date
from src.data.models import ScanOpportunity, ScanResult
from src.data.repositories import ScanRepository
from src.tools.manual_trade_entry import ManualTradeEntry


# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")

# Initialize database
init_database()

logger.info("Started Flask web application for manual trade entry")


@app.route("/")
def index():
    """Home page - redirects to add trade form."""
    return redirect(url_for("add_trade"))


@app.route("/add", methods=["GET", "POST"])
def add_trade():
    """Display form to add a manual trade and handle submission."""
    if request.method == "POST":
        try:
            # Extract form data
            form_data = {
                "symbol": request.form.get("symbol", "").strip().upper(),
                "strike": float(request.form.get("strike", 0)),
                "expiration": request.form.get("expiration", "").strip(),
                "option_type": request.form.get("option_type", "PUT").upper(),
                "premium": _parse_optional_float(request.form.get("premium")),
                "bid": _parse_optional_float(request.form.get("bid")),
                "ask": _parse_optional_float(request.form.get("ask")),
                "delta": _parse_optional_float(request.form.get("delta")),
                "otm_pct": _parse_optional_float(request.form.get("otm_pct")),
                "stock_price": _parse_optional_float(request.form.get("stock_price")),
                "trend": request.form.get("trend", "").strip() or None,
                "volume": _parse_optional_int(request.form.get("volume")),
                "open_interest": _parse_optional_int(request.form.get("open_interest")),
                "iv": _parse_optional_float(request.form.get("iv")),
                "notes": request.form.get("notes", "").strip() or None,
            }

            # Validate using Pydantic model
            trade_entry = ManualTradeEntry(**form_data)

            # Save to database using context manager
            with get_db_session() as session:
                scan_repo = ScanRepository(session)

                # Create or get today's manual scan result
                scan_result = _get_or_create_manual_scan(session, scan_repo)

                # Calculate DTE
                exp_date = datetime.strptime(trade_entry.expiration, "%Y-%m-%d")
                dte = (exp_date.date() - us_trading_date()).days

                # Create opportunity record
                opportunity = ScanOpportunity(
                    scan_id=scan_result.id,
                    symbol=trade_entry.symbol,
                    strike=trade_entry.strike,
                    expiration=exp_date.date(),
                    option_type=trade_entry.option_type,
                    premium=trade_entry.premium,
                    bid=trade_entry.bid,
                    ask=trade_entry.ask,
                    delta=trade_entry.delta,
                    otm_pct=trade_entry.otm_pct,
                    dte=dte,
                    stock_price=trade_entry.stock_price,
                    trend=trade_entry.trend,
                    volume=trade_entry.volume,
                    open_interest=trade_entry.open_interest,
                    iv=trade_entry.iv,
                    validation_status="pending",
                    source="manual_web",
                    entry_notes=trade_entry.notes,
                    executed=False,
                )

                scan_repo.add_opportunity(opportunity)
                # session.commit() is handled by context manager

            logger.info(
                f"Saved manual trade via web: {trade_entry.symbol} "
                f"${trade_entry.strike} {trade_entry.expiration}"
            )

            flash(
                f"✓ Successfully added {trade_entry.symbol} ${trade_entry.strike} "
                f"expiring {trade_entry.expiration}",
                "success"
            )

            # Redirect to add another or view list
            if request.form.get("action") == "add_another":
                return redirect(url_for("add_trade"))
            else:
                return redirect(url_for("list_trades"))

        except ValueError as e:
            flash(f"✗ Validation error: {e}", "error")
            logger.warning(f"Validation error in web form: {e}")

        except Exception as e:
            flash(f"✗ Error saving trade: {e}", "error")
            logger.error(f"Error saving manual trade via web: {e}", exc_info=True)

    # GET request - show form
    return render_template("add_trade.html")


@app.route("/list")
def list_trades():
    """Display list of recently entered manual trades."""
    try:
        with get_db_session() as session:
            # Get manual trades from last 30 days
            opportunities = (
                session.query(ScanOpportunity)
                .join(ScanResult)
                .filter(ScanOpportunity.source.in_(["manual_web", "manual"]))
                .filter(ScanOpportunity.executed == False)
                .order_by(ScanOpportunity.created_at.desc())
                .limit(100)
                .all()
            )

            return render_template("list_trades.html", trades=opportunities)

    except Exception as e:
        flash(f"✗ Error loading trades: {e}", "error")
        logger.error(f"Error loading manual trades list: {e}", exc_info=True)
        return render_template("list_trades.html", trades=[])


def _parse_optional_float(value):
    """Parse optional float field from form."""
    if not value or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_optional_int(value):
    """Parse optional int field from form."""
    if not value or value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _get_or_create_manual_scan(session, scan_repo) -> ScanResult:
    """Get or create today's manual scan result record.

    Groups all manual web entries for a day under a single scan result.
    """
    today = us_trading_date()

    # Look for existing manual_web scan from today
    existing = (
        session.query(ScanResult)
        .filter(ScanResult.source == "manual_web")
        .filter(ScanResult.scan_timestamp >= datetime.combine(today, datetime.min.time()))
        .first()
    )

    if existing:
        # Update total_candidates count
        existing.total_candidates = (
            session.query(ScanOpportunity)
            .filter(ScanOpportunity.scan_id == existing.id)
            .count()
        ) + 1  # +1 for the one we're about to add
        return existing

    # Create new scan result for today
    scan_result = ScanResult(
        scan_timestamp=datetime.now(),
        source="manual_web",
        config_used={"method": "web_form"},
        total_candidates=0,
        validated_count=0,
        execution_time_seconds=0.0,
        notes="Manual trades entered via web interface",
    )

    return scan_repo.create_scan(scan_result)


def run_app(host="127.0.0.1", port=5000, debug=True):
    """Run the Flask application.

    Args:
        host: Host to bind to (default: localhost)
        port: Port to listen on (default: 5000)
        debug: Enable debug mode (default: True)
    """
    logger.info(f"Starting web interface at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_app()
