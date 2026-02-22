"""TAAD import orchestrator.

Ties together: Flex Query client → XML parser → DB persistence → trade matching.
"""

from datetime import date, datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.config.base import get_config
from src.taad.flex_parser import ParsedExecution, parse_flex_xml
from src.taad.flex_query_client import FlexQueryClient, FlexQueryError
from src.taad.models import IBKRRawImport, ImportSession
from src.taad.trade_matcher import match_trades, persist_matches


class ImportResult:
    """Result of an import operation."""

    def __init__(self) -> None:
        self.session_id: int | None = None
        self.total_xml_records: int = 0
        self.imported: int = 0
        self.skipped_duplicates: int = 0
        self.errors: int = 0
        self.matched_trades: int = 0
        self.error_messages: list[str] = []

    def __repr__(self) -> str:
        return (
            f"ImportResult(imported={self.imported}, "
            f"skipped={self.skipped_duplicates}, "
            f"errors={self.errors}, "
            f"matched={self.matched_trades})"
        )


def run_import(
    session: Session,
    account_id: str | None = None,
    xml_text: str | None = None,
    run_matching: bool = True,
    query: str = "daily",
) -> ImportResult:
    """Run a full TAAD import: fetch → parse → store → match.

    Either provide xml_text directly (for testing/file imports) or
    leave it None to fetch from the Flex Query API using credentials
    from .env.

    Args:
        session: SQLAlchemy database session.
        account_id: IBKR account ID. If None, uses first configured account.
        xml_text: Pre-fetched XML (skips API call if provided).
        run_matching: Whether to run trade matching after import.
        query: Query type - 'daily' (trade confirmation), 'last_month',
               'last_quarter', 'last_year' (activity queries).

    Returns:
        ImportResult with statistics.
    """
    result = ImportResult()
    config = get_config()

    # Resolve credentials based on query type
    creds = config.get_flex_credentials_for_query(query, account_id)
    if not creds and not xml_text:
        raise FlexQueryError(
            f"No Flex Query credentials found for query={query!r}, "
            f"account={account_id!r}. "
            f"Configured accounts: {config.list_flex_accounts()}"
        )

    resolved_account = creds["account_id"] if creds else (account_id or "unknown")

    # Create import session record
    source_type = "flex_query" if query == "daily" else "activity_flex_query"
    import_session = ImportSession(
        status="running",
        source_type=source_type,
        source_file=creds["query_id"] if creds else "manual_xml",
        account_id=resolved_account,
    )
    session.add(import_session)
    session.flush()
    result.session_id = import_session.id

    try:
        # Step 1: Fetch XML
        if xml_text is None:
            logger.info(f"Fetching Flex Query for account {resolved_account}...")
            client = FlexQueryClient(
                token=creds["token"],
                query_id=creds["query_id"],
            )
            xml_text = client.fetch_report()
        else:
            logger.info("Using provided XML text (skipping API call)")

        # Step 2: Parse XML
        executions = parse_flex_xml(xml_text)
        result.total_xml_records = len(executions)
        logger.info(f"Parsed {len(executions)} execution records from XML")

        if executions:
            # Track date range
            dates = [e.trade_date for e in executions if e.trade_date]
            if dates:
                import_session.date_range_start = min(dates)
                import_session.date_range_end = max(dates)

        # Step 3: Persist to database (with dedup)
        for execution in executions:
            try:
                _persist_execution(session, execution, import_session.id)
                result.imported += 1
            except DuplicateExecIDError:
                result.skipped_duplicates += 1
            except Exception as e:
                result.errors += 1
                msg = f"Error persisting {execution.exec_id}: {e}"
                result.error_messages.append(msg)
                logger.error(msg)

        # Update import session stats
        import_session.total_records = result.total_xml_records
        import_session.imported_records = result.imported
        import_session.skipped_duplicates = result.skipped_duplicates
        import_session.error_count = result.errors

        session.flush()

        # Step 4: Match trades
        if run_matching:
            matches = match_trades(session, account_id=resolved_account)
            result.matched_trades = persist_matches(session, matches)

        # Mark session complete
        import_session.status = "completed"
        import_session.completed_at = datetime.utcnow()

        logger.info(
            f"Import complete: {result.imported} imported, "
            f"{result.skipped_duplicates} skipped (dup), "
            f"{result.errors} errors, "
            f"{result.matched_trades} matched"
        )

    except Exception as e:
        import_session.status = "failed"
        import_session.error_details = str(e)
        import_session.completed_at = datetime.utcnow()
        logger.error(f"Import failed: {e}")
        raise

    return result


class DuplicateExecIDError(Exception):
    """Raised when an execID already exists in the database."""
    pass


def _persist_execution(
    session: Session,
    execution: ParsedExecution,
    import_session_id: int,
) -> IBKRRawImport:
    """Persist a single parsed execution to the database.

    Args:
        session: SQLAlchemy session.
        execution: Parsed execution record.
        import_session_id: ID of the parent ImportSession.

    Returns:
        The created IBKRRawImport record.

    Raises:
        DuplicateExecIDError: If this execID already exists.
    """
    # Check for duplicate
    if execution.exec_id:
        existing = session.query(IBKRRawImport).filter(
            IBKRRawImport.ibkr_exec_id == execution.exec_id
        ).first()
        if existing:
            raise DuplicateExecIDError(f"execID {execution.exec_id} already imported")

    record = IBKRRawImport(
        import_session_id=import_session_id,
        source_type="flex_query",
        account_id=execution.account_id,
        account_alias=execution.account_alias,
        raw_data=execution.raw_data,
        trade_date=execution.trade_date,
        settle_date=execution.settle_date,
        symbol=execution.symbol,
        underlying_symbol=execution.underlying_symbol,
        strike=execution.strike,
        expiry=execution.expiry,
        put_call=execution.put_call,
        asset_category=execution.asset_category,
        buy_sell=execution.buy_sell,
        open_close=execution.open_close,
        quantity=execution.quantity,
        price=execution.price,
        amount=execution.amount,
        proceeds=execution.proceeds,
        net_cash=execution.net_cash,
        commission=execution.commission,
        multiplier=execution.multiplier,
        ibkr_exec_id=execution.exec_id,
        ibkr_trade_id=execution.trade_id,
        ibkr_order_id=execution.order_id,
        ibkr_conid=execution.conid,
        order_type=execution.order_type,
        exchange=execution.exchange,
        order_time=execution.order_time,
        execution_time=execution.execution_time,
        level_of_detail=execution.level_of_detail,
    )
    session.add(record)
    session.flush()
    return record
