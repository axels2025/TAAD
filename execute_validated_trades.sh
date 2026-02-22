#!/bin/bash
# Execute validated trades from your scan
# Edit the trades below to match your validated results

# Usage:
# 1. Edit the trades below to match your scan results
# 2. Make executable: chmod +x execute_validated_trades.sh
# 3. Run: ./execute_validated_trades.sh

# Set to --dry-run for testing, or remove for real execution
DRY_RUN=""

echo "Executing validated trades..."
echo ""

# SLV trades
python -m src.cli.main execute SLV 86.50 2026-02-27 --premium 4.62 --contracts 5 $DRY_RUN
python -m src.cli.main execute SLV 85.00 2026-02-13 --premium 2.49 --contracts 5 $DRY_RUN

# MSTR trades
python -m src.cli.main execute MSTR 125.00 2026-02-13 --premium 3.30 --contracts 5 $DRY_RUN
python -m src.cli.main execute MSTR 120.00 2026-02-13 --premium 2.37 --contracts 5 $DRY_RUN
python -m src.cli.main execute MSTR 115.00 2026-02-13 --premium 1.70 --contracts 5 $DRY_RUN

echo ""
echo "All trades executed!"
