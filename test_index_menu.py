"""
Test the index menu system
"""
import logging
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from utils_indices import get_user_index_selection

def main():
    """Test index selection"""
    print("\n" + "="*70)
    print("TESTING INDEX MENU SYSTEM")
    print("="*70)

    # Test with max 10 stocks
    result = get_user_index_selection(max_stocks=10)

    if result:
        print("\n" + "="*70)
        print("SELECTION RESULT")
        print("="*70)
        print(f"Index:       {result['index_name']}")
        print(f"Available:   {result['total_available']} stocks")
        print(f"Scanning:    {result['scanning']} stocks")
        print(f"\nSymbols:     {', '.join(result['symbols'][:20])}")
        if len(result['symbols']) > 20:
            print(f"             ... and {len(result['symbols']) - 20} more")
        print("="*70)
        return 0
    else:
        print("\nNo selection made.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
