"""
Stock Index Management
Handles fetching, caching, and selection of stock indices
"""
import logging
from pathlib import Path
from typing import List, Dict, Optional
from config.ibkr_connection import create_ibkr_connection
from ib_insync import Stock, Index

logger = logging.getLogger(__name__)

# Directory for cached index files
INDICES_DIR = Path("data/indices")


# Index definitions with their identifiers
AVAILABLE_INDICES = {
    "1": {
        "name": "S&P 100 (OEX)",
        "symbol": "OEX",
        "exchange": "CBOE",
        "file": "sp100.txt",
        "description": "100 largest US companies"
    },
    "2": {
        "name": "S&P 500 (SPX)",
        "symbol": "SPX",
        "exchange": "CBOE",
        "file": "sp500.txt",
        "description": "500 largest US companies"
    },
    "3": {
        "name": "Nasdaq 100 (NDX)",
        "symbol": "NDX",
        "exchange": "NASDAQ",
        "file": "nasdaq100.txt",
        "description": "100 largest non-financial Nasdaq stocks"
    },
    "4": {
        "name": "Dow Jones Industrial Average (INDU)",
        "symbol": "INDU",
        "exchange": "NYSE",
        "file": "djia.txt",
        "description": "30 prominent US companies"
    },
    "5": {
        "name": "Russell 2000 (RUT)",
        "symbol": "RUT",
        "exchange": "RUSSELL",
        "file": "russell2000.txt",
        "description": "2000 small-cap US stocks"
    },
    "6": {
        "name": "S&P MidCap 400 (MID)",
        "symbol": "MID",
        "exchange": "CBOE",
        "file": "sp400.txt",
        "description": "400 mid-cap US companies"
    },
    "7": {
        "name": "S&P SmallCap 600 (SML)",
        "symbol": "SML",
        "exchange": "CBOE",
        "file": "sp600.txt",
        "description": "600 small-cap US companies"
    },
    "8": {
        "name": "Nasdaq Composite (COMP)",
        "symbol": "COMP",
        "exchange": "NASDAQ",
        "file": "nasdaq_composite.txt",
        "description": "All Nasdaq-listed stocks"
    },
    "9": {
        "name": "Russell 1000 (RUI)",
        "symbol": "RUI",
        "exchange": "RUSSELL",
        "file": "russell1000.txt",
        "description": "1000 largest US companies"
    },
    "10": {
        "name": "FTSE 100 (UKX)",
        "symbol": "UKX",
        "exchange": "LSE",
        "file": "ftse100.txt",
        "description": "100 largest UK companies"
    },
    "11": {
        "name": "Custom/Manual List",
        "symbol": None,
        "exchange": None,
        "file": "custom.txt",
        "description": "User-defined stock list"
    }
}


def ensure_indices_directory():
    """Create indices directory if it doesn't exist"""
    INDICES_DIR.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Ensured directory exists: {INDICES_DIR}")


def get_cached_symbols(index_file: str) -> Optional[List[str]]:
    """
    Read stock symbols from cached file

    Args:
        index_file: Filename (e.g., "sp100.txt")

    Returns:
        List of symbols or None if file doesn't exist
    """
    file_path = INDICES_DIR / index_file

    if not file_path.exists():
        logger.info(f"Cache file not found: {file_path}")
        return None

    try:
        with open(file_path, 'r') as f:
            symbols = [line.strip().upper() for line in f if line.strip()]

        logger.info(f"Loaded {len(symbols)} symbols from cache: {file_path}")
        return symbols

    except Exception as e:
        logger.error(f"Error reading cache file {file_path}: {e}")
        return None


def save_symbols_to_cache(index_file: str, symbols: List[str]) -> bool:
    """
    Save stock symbols to cache file

    Args:
        index_file: Filename (e.g., "sp100.txt")
        symbols: List of stock symbols

    Returns:
        True if successful, False otherwise
    """
    ensure_indices_directory()
    file_path = INDICES_DIR / index_file

    try:
        with open(file_path, 'w') as f:
            for symbol in symbols:
                f.write(f"{symbol.strip().upper()}\n")

        logger.info(f"Saved {len(symbols)} symbols to cache: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Error saving cache file {file_path}: {e}")
        return False


def fetch_index_components_from_ibkr(index_symbol: str, exchange: str) -> Optional[List[str]]:
    """
    Fetch index constituent stocks from IBKR

    Note: IBKR's index component data is limited. This is a best-effort approach.
    For production use, consider using a dedicated data provider.

    Args:
        index_symbol: Index symbol (e.g., "SPX", "NDX")
        exchange: Exchange where index is listed

    Returns:
        List of stock symbols or None if fetch fails
    """
    logger.info(f"Attempting to fetch components for {index_symbol} from IBKR...")

    try:
        with create_ibkr_connection() as ib:
            # Try to get index contract
            index = Index(index_symbol, exchange)
            ib.qualifyContracts(index)

            # IBKR doesn't provide a direct API to get index components
            # This is a limitation of the IB API
            logger.warning(
                f"IBKR API does not provide index components for {index_symbol}. "
                f"Please manually populate the cache file or use a data provider."
            )
            return None

    except Exception as e:
        logger.error(f"Error fetching index components: {e}")
        return None


def get_default_symbols_for_index(index_key: str) -> List[str]:
    """
    Get default/hardcoded symbols for an index
    Used when IBKR fetch fails and no cache exists

    Args:
        index_key: Index key from AVAILABLE_INDICES

    Returns:
        List of stock symbols (may be a sample/subset)
    """
    index_name = AVAILABLE_INDICES[index_key]["name"]
    logger.info(f"Using default symbol list for {index_name}")

    # These are sample/popular stocks from each index
    # In production, you'd want to fetch these from a data provider
    defaults = {
        "1": [  # S&P 100
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK B', 'V', 'JNJ',
            'WMT', 'JPM', 'PG', 'MA', 'UNH', 'HD', 'ABBV', 'CVX', 'MRK', 'LLY',
            'KO', 'PEP', 'COST', 'AVGO', 'PFE', 'TMO', 'CSCO', 'MCD', 'ABT', 'ADBE',
            'AMD', 'INTC', 'QCOM', 'TXN', 'ORCL', 'CRM', 'NOW', 'IBM', 'AMAT', 'MU',
            'BA', 'CAT', 'GE', 'HON', 'UPS', 'RTX', 'LMT', 'DE', 'MMM', 'DHR',
            'XOM', 'COP', 'SLB', 'EOG', 'NEE', 'DUK', 'SO', 'AEP', 'EXC', 'SRE',
            'BAC', 'WFC', 'C', 'GS', 'MS', 'BLK', 'SCHW', 'AXP', 'USB', 'PNC',
            'NKE', 'SBUX', 'TGT', 'LOW', 'DIS', 'NFLX', 'CMCSA', 'T', 'VZ', 'F',
            'GM', 'BMY', 'AMGN', 'GILD', 'CVS', 'CI', 'HUM', 'ELV', 'MDT', 'ISRG',
            'UNP', 'UPS', 'FDX', 'NSC', 'LUV', 'DAL', 'AAL', 'BA', 'GD', 'NOC'
        ],
        "2": [  # S&P 500 (sample - first 100)
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK B', 'V', 'JNJ',
            'WMT', 'JPM', 'PG', 'MA', 'UNH', 'HD', 'ABBV', 'CVX', 'MRK', 'LLY',
            'KO', 'PEP', 'COST', 'AVGO', 'PFE', 'TMO', 'CSCO', 'MCD', 'ABT', 'ADBE',
            'AMD', 'INTC', 'QCOM', 'TXN', 'ORCL', 'CRM', 'NOW', 'IBM', 'AMAT', 'MU',
            'BA', 'CAT', 'GE', 'HON', 'UPS', 'RTX', 'LMT', 'DE', 'MMM', 'DHR',
            'XOM', 'COP', 'SLB', 'EOG', 'NEE', 'DUK', 'SO', 'AEP', 'EXC', 'SRE',
            'BAC', 'WFC', 'C', 'GS', 'MS', 'BLK', 'SCHW', 'AXP', 'USB', 'PNC',
            'NKE', 'SBUX', 'TGT', 'LOW', 'DIS', 'NFLX', 'CMCSA', 'T', 'VZ', 'F',
            'GM', 'BMY', 'AMGN', 'GILD', 'CVS', 'CI', 'HUM', 'ELV', 'MDT', 'ISRG',
            'UNP', 'UPS', 'FDX', 'NSC', 'LUV', 'DAL', 'AAL', 'BA', 'GD', 'NOC'
        ],
        "3": [  # Nasdaq 100
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'COST', 'NFLX',
            'ADBE', 'AMD', 'INTC', 'QCOM', 'TXN', 'ORCL', 'CRM', 'NOW', 'IBM', 'AMAT',
            'MU', 'CSCO', 'INTU', 'PYPL', 'ADP', 'ISRG', 'GILD', 'MDLZ', 'REGN', 'VRTX',
            'BKNG', 'LRCX', 'SBUX', 'PANW', 'MELI', 'KLAC', 'SNPS', 'CDNS', 'MRNA', 'ASML',
            'AZN', 'CHTR', 'MNST', 'FTNT', 'MRVL', 'DXCM', 'WDAY', 'ABNB', 'TEAM', 'ZS',
            'CRWD', 'DDOG', 'COIN', 'RIVN', 'LCID', 'PLUG', 'ENPH', 'SEDG', 'TSEM', 'WOLF'
        ],
        "4": [  # Dow Jones 30
            'AAPL', 'MSFT', 'UNH', 'GS', 'HD', 'MCD', 'V', 'AMGN', 'CAT', 'BA',
            'HON', 'IBM', 'CRM', 'JPM', 'AXP', 'CVX', 'JNJ', 'WMT', 'PG', 'TRV',
            'MMM', 'NKE', 'DIS', 'MRK', 'KO', 'CSCO', 'DOW', 'VZ', 'INTC', 'WBA'
        ],
        "5": [  # Russell 2000 (sample of small caps in target range)
            'AEIS', 'AMED', 'AMSF', 'APAM', 'ATKR', 'AVA', 'AVNT', 'BKE', 'BMRC', 'BOOT',
            'CBU', 'CEIX', 'CENTA', 'COLL', 'CPRX', 'CSWI', 'CTS', 'CVCO', 'CWST', 'DY',
            'EGAN', 'EIG', 'ENS', 'EPAC', 'ESE', 'ESP', 'FCFS', 'GFF', 'GMS', 'GVA',
            'HAYW', 'HBB', 'HEES', 'HELE', 'HNI', 'HURN', 'IIIN', 'ITGR', 'KAI', 'KSCP'
        ],
        "6": [  # S&P MidCap 400 (sample)
            'ABCB', 'ABG', 'ACIW', 'ACM', 'ADNT', 'AEO', 'AIT', 'ALG', 'ALSN', 'AM',
            'AMED', 'AMG', 'AMP', 'AN', 'ANF', 'APAM', 'APLE', 'ARCH', 'ARLP', 'ASH'
        ],
        "7": [  # S&P SmallCap 600 (sample)
            'AAWW', 'AAON', 'ABCB', 'AEIS', 'AIN', 'ALKS', 'AMSF', 'ANF', 'APOG', 'ARI'
        ],
        "8": [  # Nasdaq Composite (sample - similar to NDX)
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'COST', 'NFLX'
        ],
        "9": [  # Russell 1000 (sample - similar to S&P 500)
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JNJ'
        ],
        "10": [  # FTSE 100 (UK stocks)
            'BP', 'HSBA', 'SHEL', 'AZN', 'ULVR', 'GSK', 'DGE', 'RIO', 'BAT', 'RELX'
        ],
        "11": [  # Custom - empty by default
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA'
        ]
    }

    return defaults.get(index_key, [])


def get_index_symbols(index_key: str, max_stocks: int = None) -> List[str]:
    """
    Get stock symbols for a selected index

    1. Check cache file
    2. If not cached, try to fetch from IBKR
    3. If fetch fails, use default hardcoded list
    4. Save to cache for future use
    5. Limit to max_stocks if specified

    Args:
        index_key: Key from AVAILABLE_INDICES
        max_stocks: Maximum number of stocks to return (None = all)

    Returns:
        List of stock symbols
    """
    if index_key not in AVAILABLE_INDICES:
        logger.error(f"Invalid index key: {index_key}")
        return []

    index_info = AVAILABLE_INDICES[index_key]
    index_file = index_info["file"]

    # Try to load from cache
    symbols = get_cached_symbols(index_file)

    if symbols:
        logger.info(f"Using cached symbols for {index_info['name']}")
    else:
        logger.info(f"No cache found for {index_info['name']}, fetching...")

        # Try to fetch from IBKR (will likely fail due to API limitations)
        if index_info["symbol"] and index_info["exchange"]:
            symbols = fetch_index_components_from_ibkr(
                index_info["symbol"],
                index_info["exchange"]
            )

        # If fetch fails, use defaults
        if not symbols:
            logger.info(f"Using default symbol list for {index_info['name']}")
            symbols = get_default_symbols_for_index(index_key)

            # Save defaults to cache
            if symbols:
                save_symbols_to_cache(index_file, symbols)

    # Limit to max_stocks if specified
    if max_stocks and len(symbols) > max_stocks:
        logger.info(f"Limiting from {len(symbols)} to {max_stocks} stocks")
        symbols = symbols[:max_stocks]

    return symbols


def display_index_menu() -> str:
    """
    Display interactive menu for index selection

    Returns:
        Selected index key
    """
    print("\n" + "=" * 70)
    print("  STOCK INDEX SELECTION")
    print("=" * 70)
    print("\nAvailable Indices:\n")

    for key, info in sorted(AVAILABLE_INDICES.items()):
        print(f"  [{key:>2}] {info['name']:<35} - {info['description']}")

    print("\n" + "=" * 70)

    while True:
        try:
            choice = input("\nSelect an index (1-11) or 'q' to quit: ").strip()

            if choice.lower() == 'q':
                print("Exiting...")
                return None

            if choice in AVAILABLE_INDICES:
                return choice
            else:
                print(f"Invalid choice '{choice}'. Please select 1-11.")

        except KeyboardInterrupt:
            print("\n\nExiting...")
            return None
        except Exception as e:
            logger.error(f"Error reading input: {e}")
            return None


def get_user_index_selection(max_stocks: int = 10) -> Dict:
    """
    Get index selection from user and return symbols

    Args:
        max_stocks: Maximum number of stocks to scan

    Returns:
        Dict with index info and symbols
    """
    index_key = display_index_menu()

    if not index_key:
        return None

    index_info = AVAILABLE_INDICES[index_key]

    print(f"\n→ Selected: {index_info['name']}")
    print(f"→ Description: {index_info['description']}")

    # Special handling for custom index
    if index_key == "11":
        print("\n" + "=" * 70)
        print("Custom Stock List")
        print("=" * 70)
        print(f"\nPlease edit the file: {INDICES_DIR / index_info['file']}")
        print("Add one stock symbol per line, then run the program again.")
        print("\nCreating sample file...")

        ensure_indices_directory()
        sample_symbols = get_default_symbols_for_index(index_key)
        save_symbols_to_cache(index_info["file"], sample_symbols)

        print(f"✓ Created {INDICES_DIR / index_info['file']} with sample symbols")
        return None

    # Get symbols
    print(f"\n→ Loading stock symbols...")
    symbols = get_index_symbols(index_key, max_stocks=max_stocks)

    if not symbols:
        print(f"\n✗ Error: No symbols found for {index_info['name']}")
        return None

    print(f"→ Loaded {len(symbols)} symbols")
    print(f"→ Will scan: {max_stocks if max_stocks else 'all'} stocks")

    # Display first few symbols
    print(f"\n→ Sample symbols: {', '.join(symbols[:10])}")
    if len(symbols) > 10:
        print(f"  ... and {len(symbols) - 10} more")

    return {
        "index_name": index_info["name"],
        "index_key": index_key,
        "symbols": symbols,
        "total_available": len(symbols),
        "scanning": min(len(symbols), max_stocks) if max_stocks else len(symbols)
    }


if __name__ == "__main__":
    # Test the index selection
    logging.basicConfig(level=logging.INFO)

    result = get_user_index_selection(max_stocks=10)

    if result:
        print("\n" + "=" * 70)
        print("Selection Summary:")
        print("=" * 70)
        print(f"Index: {result['index_name']}")
        print(f"Total symbols available: {result['total_available']}")
        print(f"Will scan: {result['scanning']} stocks")
        print(f"Symbols: {result['symbols']}")
