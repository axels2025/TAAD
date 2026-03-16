"""Account filter for learning analysis.

Reads the `learning.learning_accounts` config from phase5.yaml to
determine which trade accounts are included in learning analysis.

If the list is empty (default), all non-paper accounts are included.
If specific accounts are checked in the dashboard, only those are used.
"""

from pathlib import Path
from typing import Optional

import sqlalchemy as sa
from loguru import logger

from src.data.models import Trade


def get_learning_account_filter():
    """Return a SQLAlchemy filter clause for learning-eligible trades.

    Reads `learning.learning_accounts` from phase5.yaml config.
    - Empty list → exclude paper trades (default behaviour)
    - Non-empty list → include only trades matching the listed account:source keys

    Returns:
        SQLAlchemy filter clause to apply to Trade queries
    """
    accounts = _load_learning_accounts()

    if not accounts:
        # Default: include everything except paper trades
        return sa.or_(Trade.trade_source.is_(None), Trade.trade_source != "paper")

    # Parse account keys (format: "account_id:trade_source")
    conditions = []
    for key in accounts:
        parts = key.split(":", 1)
        account_id = parts[0] if parts[0] else None
        trade_source = parts[1] if len(parts) > 1 and parts[1] else None

        if account_id and trade_source:
            conditions.append(
                sa.and_(Trade.account_id == account_id, Trade.trade_source == trade_source)
            )
        elif account_id:
            conditions.append(Trade.account_id == account_id)
        elif trade_source:
            conditions.append(Trade.trade_source == trade_source)

    if not conditions:
        # Fallback if parsing produced nothing
        return sa.or_(Trade.trade_source.is_(None), Trade.trade_source != "paper")

    return sa.or_(*conditions)


def _load_learning_accounts() -> list[str]:
    """Load the learning_accounts list from phase5.yaml.

    Returns:
        List of account keys, or empty list
    """
    try:
        import yaml

        config_path = Path(__file__).parent.parent.parent / "config" / "phase5.yaml"
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            return config.get("learning", {}).get("learning_accounts", [])
    except Exception as e:
        logger.debug(f"Could not load learning accounts config: {e}")
    return []
