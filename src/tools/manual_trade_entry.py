"""Manual trade entry system.

Provides utilities for manually entering trading opportunities and
saving them to JSON files for import into the trading system.
"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from src.utils.timezone import us_trading_date


class ManualTradeEntry(BaseModel):
    """Single manual trade entry."""

    symbol: str = Field(description="Stock symbol (e.g., AAPL)")
    strike: float = Field(description="Strike price")
    expiration: str = Field(description="Expiration date (YYYY-MM-DD)")
    option_type: str = Field(default="PUT", description="Option type (PUT/CALL)")
    premium: Optional[float] = Field(default=None, description="Expected premium")
    bid: Optional[float] = Field(default=None, description="Bid price")
    ask: Optional[float] = Field(default=None, description="Ask price")
    delta: Optional[float] = Field(default=None, description="Delta")
    otm_pct: Optional[float] = Field(default=None, description="OTM percentage")
    stock_price: Optional[float] = Field(default=None, description="Current stock price")
    trend: Optional[str] = Field(default=None, description="Trend (uptrend/downtrend/sideways)")
    volume: Optional[int] = Field(default=None, description="Option volume")
    open_interest: Optional[int] = Field(default=None, description="Open interest")
    iv: Optional[float] = Field(default=None, description="Implied volatility")
    notes: Optional[str] = Field(default=None, description="Your notes/reasoning")

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Validate and uppercase symbol."""
        return v.upper().strip()

    @field_validator("option_type")
    @classmethod
    def validate_option_type(cls, v: str) -> str:
        """Validate option type."""
        v = v.upper().strip()
        if v not in ["PUT", "CALL"]:
            raise ValueError("Option type must be PUT or CALL")
        return v

    @field_validator("expiration")
    @classmethod
    def validate_expiration(cls, v: str) -> str:
        """Validate expiration date format."""
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Expiration must be in YYYY-MM-DD format")
        return v

    def calculate_dte(self) -> int:
        """Calculate days to expiration."""
        exp_date = datetime.strptime(self.expiration, "%Y-%m-%d").date()
        today = us_trading_date()
        return (exp_date - today).days


class ManualTradeFile(BaseModel):
    """Container for manual trade entries saved to JSON."""

    source: str = Field(default="manual", description="Source identifier")
    scan_timestamp: str = Field(description="When trades were entered")
    notes: Optional[str] = Field(default=None, description="General notes for this batch")
    opportunities: list[ManualTradeEntry] = Field(description="List of trade entries")

    @classmethod
    def create_new(
        cls, opportunities: list[ManualTradeEntry], notes: Optional[str] = None
    ) -> "ManualTradeFile":
        """Create a new manual trade file.

        Args:
            opportunities: List of trade entries
            notes: Optional notes for this batch

        Returns:
            ManualTradeFile ready to save
        """
        return cls(
            source="manual",
            scan_timestamp=datetime.now().isoformat(),
            notes=notes,
            opportunities=opportunities,
        )


class ManualTradeManager:
    """Manager for manual trade entry and file operations."""

    def __init__(self, pending_dir: str = "data/manual_trades/pending"):
        """Initialize manager.

        Args:
            pending_dir: Directory for pending manual trades
        """
        self.pending_dir = Path(pending_dir)
        self.pending_dir.mkdir(parents=True, exist_ok=True)

        # Also create imported and templates directories
        self.imported_dir = self.pending_dir.parent / "imported"
        self.imported_dir.mkdir(parents=True, exist_ok=True)

        self.templates_dir = self.pending_dir.parent / "templates"
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Initialized ManualTradeManager (pending: {self.pending_dir})")

    def save_trades(
        self,
        opportunities: list[ManualTradeEntry],
        notes: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> Path:
        """Save manual trades to JSON file.

        Args:
            opportunities: List of trade entries
            notes: Optional batch notes
            filename: Custom filename (None = auto-timestamp)

        Returns:
            Path to saved file
        """
        # Create trade file
        trade_file = ManualTradeFile.create_new(opportunities, notes)

        # Generate filename
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"manual_{timestamp}.json"

        # Ensure .json extension
        if not filename.endswith(".json"):
            filename = f"{filename}.json"

        # Save file
        file_path = self.pending_dir / filename
        with open(file_path, "w") as f:
            json.dump(trade_file.model_dump(mode="json"), f, indent=2)

        logger.info(f"Saved {len(opportunities)} manual trades to {file_path}")
        return file_path

    def load_pending_files(self) -> list[tuple[Path, ManualTradeFile]]:
        """Load all pending manual trade files.

        Returns:
            List of (filepath, ManualTradeFile) tuples
        """
        pending_files = []

        for json_file in self.pending_dir.glob("*.json"):
            try:
                with open(json_file) as f:
                    data = json.load(f)

                trade_file = ManualTradeFile(**data)
                pending_files.append((json_file, trade_file))
                logger.debug(f"Loaded pending file: {json_file.name}")

            except Exception as e:
                logger.warning(f"Failed to load {json_file.name}: {e}")
                continue

        logger.info(f"Found {len(pending_files)} pending manual trade files")
        return pending_files

    def move_to_imported(self, file_path: Path) -> Path:
        """Move file from pending to imported directory.

        Args:
            file_path: Path to pending file

        Returns:
            New path in imported directory
        """
        new_path = self.imported_dir / file_path.name
        file_path.rename(new_path)
        logger.info(f"Moved {file_path.name} to imported directory")
        return new_path

    def create_template(self) -> Path:
        """Create a template JSON file as example.

        Returns:
            Path to template file
        """
        template_entry = ManualTradeEntry(
            symbol="AAPL",
            strike=180.0,
            expiration="2025-02-14",
            option_type="PUT",
            premium=0.45,
            bid=0.44,
            ask=0.46,
            delta=-0.15,
            otm_pct=0.12,
            stock_price=204.50,
            trend="uptrend",
            volume=450,
            open_interest=1200,
            iv=0.35,
            notes="Example trade - strong uptrend, tested support at 200",
        )

        template_file = ManualTradeFile.create_new(
            opportunities=[template_entry],
            notes="This is an example template. Edit and save to data/manual_trades/pending/",
        )

        template_path = self.templates_dir / "manual_trade_template.json"
        with open(template_path, "w") as f:
            json.dump(template_file.model_dump(mode="json"), f, indent=2)

        logger.info(f"Created template file: {template_path}")
        return template_path

    def get_pending_count(self) -> int:
        """Get count of pending manual trade files.

        Returns:
            Number of pending JSON files
        """
        return len(list(self.pending_dir.glob("*.json")))
