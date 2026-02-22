#!/bin/bash
# Documentation Consolidation Cleanup Script
# Run from trading_agent directory

set -e

# Create archive directory
mkdir -p docs/archive

# Files to archive (old phase docs, bugfixes, test summaries)
ARCHIVE_FILES=(
    # Phase checkpoints (now in DEVELOPMENT_HISTORY.md)
    "docs/PHASE_0_CHECKPOINT.md"
    "docs/PHASE_1_CHECKPOINT.md"
    "docs/PHASE_2_CHECKPOINT.md"
    "docs/PHASE2_VALIDATION_REPORT.md"
    
    # Bugfix reports
    "docs/BUGFIX_REPORT.md"
    "docs/ENRICHMENT_FIX_COMPLETE.md"
    "docs/MANUAL_ENRICHMENT_FIX.md"
    "docs/MANUAL_ONLY_BARCHART_FIX.md"
    "docs/MANUAL_ONLY_DEFAULT_FIX.md"
    "docs/TYPER_EXIT_FIX.md"
    "docs/CONSOLE_OUTPUT_CLEANUP.md"
    "docs/ERROR_HANDLING_SUMMARY.md"
    "docs/IBKR_CONNECTION_ERROR_IMPROVEMENTS.md"
    "docs/SCAN_FIXES_2026-01-22.md"
    
    # Test summaries
    "docs/phase2_test_summary.md"
    "docs/phase2_testing_complete.md"
    "docs/test_results_final.md"
    "docs/test_run_summary.md"
    "docs/LIVE_TESTS_IMPLEMENTATION.md"
    "docs/TESTING_STRATEGY.md"
    
    # Migration/audit docs
    "docs/BARCHART_MIGRATION.md"
    "docs/SYSTEM_AUDIT_2026-01-22.md"
    "docs/FINAL_VERIFICATION_2026-01-22.md"
    
    # Now consolidated into OPERATIONS_GUIDE.md
    "docs/24_7_OPERATION_GUIDE.md"
    "docs/CLI_REFERENCE.md"
    "docs/CLI_COMMAND_RENAME_CHANGELOG.md"
    "docs/PARAMETER_SYSTEM_EXPLAINED.md"
    "docs/TROUBLESHOOTING_VALIDATION.md"
    "docs/ENRICHMENT_DEBUG_GUIDE.md"
    "docs/MARKET_CLOSED_FALLBACK.md"
    "docs/BARCHART_ERROR_HANDLING.md"
    
    # Now consolidated into TECHNICAL_REFERENCE.md
    "docs/BARCHART_API_GUIDE.md"
    "docs/IBKR_FIELD_REFERENCE.md"
    "docs/STOCK_SELECTION_SYSTEM.md"
    "docs/TRADE_COMMAND_IMPROVEMENT_PLAN.md"
    
    # Root level phase docs (now in DEVELOPMENT_HISTORY.md)
    "PHASE_2_6_COMPLETE.md"
    "PHASE_2_6_INTEGRATION_COMPLETE.md"
    "PHASE_3_1_COMPLETE.md"
    "PHASE_3_2_COMPLETE.md"
    "PHASE_3_3_COMPLETE.md"
    "PHASE_3_4_COMPLETE.md"
    "PHASE_3_5_COMPLETE.md"
    "PHASE_3_COMPLETE.md"
    "PHASE_3_INTEGRATION_STATUS.md"
    "PHASE_3_PATTERN_DETECTOR_FIX.md"
    "EMERGENCY_EXIT_BUG_FIX.md"
    "CHECKPOINT_PHASE_2_5_5.md"
    "CHECKPOINT_PHASE_2_5A.md"
    "CHECKPOINT_PHASE_2_5B.md"
    "CHECKPOINT_PHASE_2_5C.md"
    "CHECKPOINT_PHASE_2_5D.md"
    "IMPLEMENTATION_SUMMARY.md"
    
    # Now consolidated into TECHNICAL_REFERENCE.md
    "TRADE_DATA_COLLECTION_SPEC.md"
    "TRADE_DATA_COLLECTION_IMPLEMENTATION_PLAN.md"
    "naked_put_ranking_rules_v2.md"
    "scoring_implementation_approach.md"
    "Implementation_Plan_Modifications_for_Self_Learning_Goal.md"
    
    # Misc fixes
    "CACHE_QUICK_START.md"
    "CHANGELOG_CONNECTION_POOL.md"
    "CONFIGURATION.md"
    "CONNECTION_POOL.md"
    "EVENT_LOOP_FIX.md"
    "FILTERS_GUIDE.md"
    "FIX_CONNECTION_ERROR.md"
    "FIX_SUMMARY.md"
    "HISTORICAL_DATA_CACHE.md"
    "INDEX_SELECTION_GUIDE.md"
    "OPTIONS_FINDER_FIX.md"
    "PRE_INSTALLATION_CHECKLIST.md"
    "PROGRESS_INDICATORS_GUIDE.md"
    "QUICK_START_OLD.md"
    "SCANNER_REDESIGN_COMPLETE.md"
    "SUMMARY_EVENT_LOOP_FIX.md"
    "SUMMARY_OPTIONS_FIX.md"
    "THREAD_LOCAL_FIX.md"
)

echo "Moving files to archive..."
for file in "${ARCHIVE_FILES[@]}"; do
    if [ -f "$file" ]; then
        filename=$(basename "$file")
        mv "$file" "docs/archive/$filename"
        echo "  Archived: $filename"
    fi
done

echo ""
echo "=== Documentation Consolidation Complete ==="
echo ""
echo "New structure:"
echo "  trading_agent/"
echo "  ├── CLAUDE.md                    # AI development config"
echo "  ├── README.md                    # User setup guide"
echo "  ├── SPEC_TRADING_SYSTEM.md       # Master specification"
echo "  └── docs/"
echo "      ├── OPERATIONS_GUIDE.md      # How to use the system"
echo "      ├── TECHNICAL_REFERENCE.md   # Architecture & specs"
echo "      ├── DEVELOPMENT_HISTORY.md   # Phase completion summary"
echo "      └── archive/                 # Historical docs"
echo ""
echo "Done!"
