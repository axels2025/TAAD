-- Migration: Add Order Reconciliation Columns
-- Date: February 3, 2026
-- Purpose: Add columns needed for Phase C order reconciliation

-- Add reconciliation tracking columns
ALTER TABLE trades ADD COLUMN reconciled_at DATETIME;
ALTER TABLE trades ADD COLUMN tws_status VARCHAR(50);
ALTER TABLE trades ADD COLUMN commission FLOAT;
ALTER TABLE trades ADD COLUMN fill_time DATETIME;
ALTER TABLE trades ADD COLUMN fill_price_discrepancy FLOAT;

-- Verify the migration
.schema trades
