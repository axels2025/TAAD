#!/bin/bash

echo "🔍 Project Status Check"
echo "======================"
echo ""

# Check Phase 0
echo "Phase 0: Foundation"
if [ -f "src/config/base.py" ] && [ -f "src/data/database.py" ]; then
    echo "  ✅ Configuration and database modules exist"
else
    echo "  ❌ Missing Phase 0 components"
fi

# Check database
if [ -f "data/databases/trades.db" ]; then
    TABLES=$(sqlite3 data/databases/trades.db ".tables" 2>/dev/null)
    if [[ "$TABLES" == *"trades"* ]]; then
        echo "  ✅ Database with tables exists"
    else
        echo "  ❌ Database exists but no tables"
    fi
else
    echo "  ❌ Database file doesn't exist"
fi

# Check Phase 1
echo ""
echo "Phase 1: Baseline Strategy"
if [ -f "src/strategies/naked_put.py" ]; then
    echo "  ✅ Strategy implemented"
else
    echo "  ❌ Strategy not found"
fi

# Check Phase 2
echo ""
echo "Phase 2: Autonomous Execution"
if [ -d "src/execution" ]; then
    EXEC_FILES=$(ls src/execution/*.py 2>/dev/null | wc -l)
    echo "  ✅ Execution module exists ($EXEC_FILES files)"
else
    echo "  ❌ Execution module missing"
fi

# Check Phase 3
echo ""
echo "Phase 3: Learning Engine"
if [ -d "src/learning" ]; then
    LEARN_FILES=$(ls src/learning/*.py 2>/dev/null | wc -l)
    echo "  ✅ Learning module exists ($LEARN_FILES files)"
else
    echo "  ⏳ Not started yet"
fi

# Check trade data
echo ""
echo "Trade Data:"
if [ -f "data/databases/trades.db" ]; then
    COUNT=$(sqlite3 data/databases/trades.db "SELECT COUNT(*) FROM trades" 2>/dev/null)
    if [ $? -eq 0 ]; then
        echo "  ✅ Total trades: $COUNT"
        CLOSED=$(sqlite3 data/databases/trades.db "SELECT COUNT(*) FROM trades WHERE exit_date IS NOT NULL" 2>/dev/null)
        echo "  ✅ Closed trades: $CLOSED"
    else
        echo "  ❌ Cannot query trades table"
    fi
fi

echo ""
echo "======================"
