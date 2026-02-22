"""
Main Entry Point for AI Trading Agent
Orchestrates the complete workflow with logging and error handling
"""
import argparse
import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from agents.trading_agent import run_trading_workflow
from config.ibkr_connection import create_ibkr_connection, get_connection_pool
from config import trading_config as cfg
from utils_indices import get_user_index_selection

# Load environment variables
load_dotenv()


def parse_arguments():
    """Parse command-line arguments for trend filtering and other options"""
    parser = argparse.ArgumentParser(
        description='AI Trading Agent - PUT Options Scanner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Trend Filter Examples:
  python main.py                    # Default: analyze only uptrend stocks
  python main.py --no-trend         # Analyze all stocks (report trend but don't filter)
  python main.py --uptrend          # Explicit: only uptrend stocks
  python main.py --downtrend        # Only downtrend stocks
  python main.py --sideways         # Only sideways/consolidating stocks
  python main.py --uptrend --sideways  # Uptrend OR sideways stocks

Trend Definitions:
  Uptrend:    Price > SMA20 > SMA50
  Downtrend:  Price < SMA20 < SMA50
  Sideways:   SMA20 and SMA50 within 2% of each other
        """
    )

    # Trend filter arguments
    trend_group = parser.add_argument_group('Trend Filters')
    trend_group.add_argument(
        '--no-trend',
        action='store_true',
        help='Analyze all stocks regardless of trend (report trend but don\'t filter)'
    )
    trend_group.add_argument(
        '--uptrend',
        action='store_true',
        help='Only analyze stocks in uptrend (Price > SMA20 > SMA50) [DEFAULT]'
    )
    trend_group.add_argument(
        '--downtrend',
        action='store_true',
        help='Only analyze stocks in downtrend (Price < SMA20 < SMA50)'
    )
    trend_group.add_argument(
        '--sideways',
        action='store_true',
        help='Only analyze stocks in sideways/consolidation pattern (SMA20 ≈ SMA50)'
    )

    args = parser.parse_args()

    # Determine trend filter based on arguments
    if args.no_trend:
        trend_filter = ['all']
        trend_description = "All trends (no filtering)"
    else:
        trend_filter = []
        trend_descriptions = []

        if args.uptrend:
            trend_filter.append('uptrend')
            trend_descriptions.append('Uptrend')
        if args.downtrend:
            trend_filter.append('downtrend')
            trend_descriptions.append('Downtrend')
        if args.sideways:
            trend_filter.append('sideways')
            trend_descriptions.append('Sideways')

        # Default to uptrend if nothing specified
        if not trend_filter:
            trend_filter = ['uptrend']
            trend_description = "Uptrend (default)"
        else:
            trend_description = " OR ".join(trend_descriptions)

    return {
        'trend_filter': trend_filter,
        'trend_description': trend_description
    }


def setup_logging():
    """Configure logging with file and console handlers"""
    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Create log filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"trading_agent_{timestamp}.log"

    # Create formatters
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # File handler - logs everything at INFO level
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # Console handler - suppress noisy libraries to avoid interfering with progress bars
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # Filter for console to suppress noisy libraries
    class ConsoleFilter(logging.Filter):
        def filter(self, record):
            # Suppress INFO level from these noisy libraries on console
            # (they still go to the file)
            noisy_libs = ['ib_insync', 'utils_historical_data']
            if any(record.name.startswith(lib) for lib in noisy_libs):
                return record.levelno >= logging.WARNING
            return True

    console_handler.addFilter(ConsoleFilter())

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized. Log file: {log_file}")
    return logger


def check_prerequisites(logger):
    """Check all prerequisites before running"""
    logger.info("Checking prerequisites...")

    # Check API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not found in environment")
        return False

    # Check IBKR connection
    try:
        logger.info("Testing IBKR connection...")

        # Use connection pool for testing
        with create_ibkr_connection() as ib:
            # Test connection
            if ib.isConnected():
                logger.info("IBKR connection successful")
                account = ib.managedAccounts()[0] if ib.managedAccounts() else "Unknown"
                logger.info(f"Connected to account: {account}")
                return True
            else:
                logger.error("IBKR connection failed")
                return False

    except Exception as e:
        logger.error(f"IBKR connection error: {e}")
        logger.error("Make sure IBKR TWS/Gateway is running on port 7497")
        return False


def print_banner():
    """Print application banner"""
    banner = """
    ╔══════════════════════════════════════════════════════════════╗
    ║                                                              ║
    ║          AI TRADING AGENT - PUT OPTIONS SCANNER              ║
    ║                                                              ║
    ║  Features:                                                   ║
    ║  • Multi-Index Stock Screener (11 major indices)            ║
    ║  • Multi-Trend Detection (Uptrend/Downtrend/Sideways)      ║
    ║  • PUT Options Finder (12-25% OTM, $0.25-$0.50 premium)    ║
    ║  • Margin Efficiency Calculator                             ║
    ║  • LangGraph AI Orchestration                               ║
    ║                                                              ║
    ║  Run with --help for trend filter options                   ║
    ║                                                              ║
    ╚══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def main():
    """Main execution function"""
    # Parse command-line arguments
    args = parse_arguments()

    # Print banner
    print_banner()

    # Setup logging
    logger = setup_logging()
    logger.info("="*80)
    logger.info("AI Trading Agent Started")
    logger.info("="*80)

    # Log command-line options
    logger.info(f"Trend Filter: {args['trend_description']}")

    # Check prerequisites
    if not check_prerequisites(logger):
        logger.error("Prerequisites check failed. Exiting.")
        sys.exit(1)

    logger.info("All prerequisites met. Starting workflow...")

    # Display current configuration
    print(cfg.get_config_summary())

    # Get index selection from user
    logger.info("\n" + "="*80)
    logger.info("STOCK INDEX SELECTION")
    logger.info("="*80 + "\n")

    index_selection = get_user_index_selection(max_stocks=cfg.NUM_STOCKS_TO_SCREEN)

    if not index_selection:
        logger.info("No index selected. Exiting.")
        sys.exit(0)

    # Display selection summary
    print("\n" + "="*80)
    print("TRADING WORKFLOW STARTING")
    print("="*80)
    print(f"Index:           {index_selection['index_name']}")
    print(f"Total Available: {index_selection['total_available']} stocks")
    print(f"Scanning:        {index_selection['scanning']} stocks")
    print(f"Price Range:     ${cfg.MIN_STOCK_PRICE:.0f} - ${cfg.MAX_STOCK_PRICE:.0f}")
    print(f"Trend Filter:    {args['trend_description']}")
    print("="*80 + "\n")

    logger.info(f"Selected index: {index_selection['index_name']}")
    logger.info(f"Will scan {index_selection['scanning']} stocks")
    logger.info(f"Trend filter: {args['trend_filter']}")

    try:
        # Run trading workflow with selected symbols
        logger.info("\n" + "="*80)
        logger.info("EXECUTING TRADING WORKFLOW")
        logger.info("="*80 + "\n")

        result = run_trading_workflow(
            num_stocks=index_selection['scanning'],
            symbols=index_selection['symbols'],
            trend_filter=args['trend_filter']
        )

        # Process results
        if result["success"]:
            logger.info("\n" + "="*80)
            logger.info("WORKFLOW COMPLETED SUCCESSFULLY")
            logger.info("="*80 + "\n")

            # Print final recommendation
            print("\n" + "="*80)
            print("TRADING RECOMMENDATIONS")
            print("="*80)
            print(result["final_recommendation"])
            print("="*80 + "\n")

            # Log message count
            msg_count = len(result.get("messages", []))
            logger.info(f"Total messages exchanged: {msg_count}")

        else:
            logger.error("\n" + "="*80)
            logger.error("WORKFLOW FAILED")
            logger.error("="*80)
            logger.error(f"Error: {result.get('error', 'Unknown error')}")
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("\nWorkflow interrupted by user")
        sys.exit(0)

    except Exception as e:
        logger.error(f"Unexpected error in main: {e}", exc_info=True)
        sys.exit(1)

    finally:
        # Cleanup connection pool
        logger.info("Cleaning up connection pool...")
        try:
            pool = get_connection_pool()
            pool.close()
        except Exception as e:
            logger.warning(f"Error closing connection pool: {e}")

        logger.info("="*80)
        logger.info("AI Trading Agent Finished")
        logger.info("="*80)


if __name__ == "__main__":
    main()
