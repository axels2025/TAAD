# 24/7 Operation Guide

**Running the Trading Agent Continuously with Automated Data Collection**

This guide explains how to set up the trading agent for continuous 24/7 operation with automated data collection, position monitoring, and exit management.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Automated Data Collection](#automated-data-collection)
4. [Cron Job Setup](#cron-job-setup)
5. [Systemd Service Setup (Alternative)](#systemd-service-alternative)
6. [Monitoring & Logs](#monitoring--logs)
7. [Troubleshooting](#troubleshooting)

---

## Overview

The trading agent collects data at three key points in the trade lifecycle:

1. **Entry Snapshot** (98 fields) - Captured automatically when trade is executed
2. **Position Snapshots** (daily) - Captured at market close (4 PM ET) for all open positions
3. **Exit Snapshot** (24 fields) - Captured automatically when trade is closed

The only component requiring scheduled automation is **daily position snapshots**.

---

## Prerequisites

### 1. System Requirements

- Linux, macOS, or Windows with WSL
- Python 3.11+
- IBKR Gateway or TWS running
- Trading agent installed and configured

### 2. Verify Installation

```bash
cd /path/to/trading_agent
source venv/bin/activate

# Test the CLI commands
python -m src.cli.main snapshot-positions --help
python -m src.cli.main export-learning-data --help
python -m src.cli.main learning-stats --help
```

### 3. Environment Configuration

Ensure your `.env` file is properly configured:

```bash
# IBKR Connection
IBKR_HOST=127.0.0.1
IBKR_PORT=7497  # 7497 for paper, 7496 for live
IBKR_CLIENT_ID=1
IBKR_ACCOUNT=DU123456

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/app.log

# Paper trading (CRITICAL - keep true during development)
PAPER_TRADING=true
```

---

## Automated Data Collection

### 🔹 Entry Snapshots (Automatic)

**When:** Immediately after trade execution
**Trigger:** Automatic (integrated in `OrderExecutor`)
**Setup Required:** None - already integrated

Entry snapshots capture 98 comprehensive fields:
- Option pricing and Greeks
- Technical indicators (RSI, MACD, ADX, ATR, Bollinger, S/R)
- Market context (indices, sector, regimes, calendar)
- Earnings data
- All 8 critical fields for learning

**Verification:**
```bash
# Check if entry snapshots are being captured
sqlite3 data/databases/trades.db "SELECT COUNT(*) FROM trade_entry_snapshots;"
```

---

### 🔹 Position Snapshots (Daily - Requires Scheduling)

**When:** Daily at 4:00 PM ET (market close)
**Trigger:** Cron job or systemd timer
**Setup Required:** Yes - see below

Position snapshots track trade evolution:
- Current P&L and premium
- Greeks changes over time
- Distance to strike
- Path data for learning

**Manual Test:**
```bash
python -m src.cli.main snapshot-positions
```

**Expected Output:**
```
Capturing Daily Position Snapshots

✓ Connected to IBKR

Position Snapshots Captured (3)
┌────────┬──────────┬─────────┬─────┬──────────┐
│ Symbol │ P&L      │ P&L %   │ DTE │ Distance │
├────────┼──────────┼─────────┼─────┼──────────┤
│ AAPL   │ $125.00  │ 50.0%   │ 25  │ 8.5%     │
│ MSFT   │ -$50.00  │ -20.0%  │ 30  │ 12.1%    │
│ GOOGL  │ $200.00  │ 80.0%   │ 15  │ 6.2%     │
└────────┴──────────┴─────────┴─────┴──────────┘

✓ Captured 3 position snapshots
```

---

### 🔹 Exit Snapshots (Automatic)

**When:** Immediately after trade closes
**Trigger:** Automatic (integrated in `ExitManager`)
**Setup Required:** None - already integrated

Exit snapshots capture complete outcomes:
- P&L and ROI metrics
- Context changes during trade (IV crush, price movement)
- Path analysis (max profit, max drawdown, profit capture efficiency)
- Trade quality score

**Verification:**
```bash
# Check if exit snapshots are being captured
sqlite3 data/databases/trades.db "SELECT COUNT(*) FROM trade_exit_snapshots;"
```

---

## Cron Job Setup

### Option 1: Linux/macOS Cron

Cron is the traditional Unix job scheduler, perfect for running daily position snapshots.

#### 1. Get Absolute Paths

```bash
# Get project path
PROJECT_PATH=$(pwd)
echo $PROJECT_PATH

# Get python path
PYTHON_PATH=$(which python)
echo $PYTHON_PATH
```

#### 2. Create Cron Wrapper Script

Create a wrapper script to ensure environment is loaded:

```bash
# File: /path/to/trading_agent/scripts/daily_snapshot.sh
#!/bin/bash

# Change to project directory
cd /path/to/trading_agent

# Activate virtual environment
source venv/bin/activate

# Load environment variables
export $(cat .env | xargs)

# Run snapshot command
python -m src.cli.main snapshot-positions >> logs/snapshot_cron.log 2>&1

# Log completion
echo "[$(date)] Daily snapshot completed" >> logs/snapshot_cron.log
```

Make it executable:
```bash
chmod +x scripts/daily_snapshot.sh
```

#### 3. Add to Crontab

```bash
# Edit crontab
crontab -e

# Add this line (runs Mon-Fri at 4:00 PM ET)
# Adjust timezone as needed (use TZ=America/New_York for ET)
0 16 * * 1-5 TZ=America/New_York /path/to/trading_agent/scripts/daily_snapshot.sh
```

**Cron Schedule Examples:**
```bash
# 4:00 PM ET, Monday-Friday
0 16 * * 1-5 TZ=America/New_York /path/to/script.sh

# 4:05 PM ET, Monday-Friday (5 minutes after close)
5 16 * * 1-5 TZ=America/New_York /path/to/script.sh

# 4:15 PM ET, Monday-Friday (wait for settlement)
15 16 * * 1-5 TZ=America/New_York /path/to/script.sh
```

#### 4. Verify Cron Job

```bash
# List active cron jobs
crontab -l

# Check cron logs (location varies by system)
tail -f /var/log/syslog | grep CRON        # Ubuntu/Debian
tail -f /var/log/cron                      # CentOS/RHEL
tail -f logs/snapshot_cron.log             # Your app logs
```

---

### Option 2: macOS LaunchAgent

macOS users can use LaunchAgent for more reliable scheduling:

#### 1. Create LaunchAgent plist

```bash
# File: ~/Library/LaunchAgents/com.trading.snapshot.plist
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.trading.snapshot</string>

    <key>ProgramArguments</key>
    <array>
        <string>/path/to/trading_agent/scripts/daily_snapshot.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Weekday</key>
            <integer>1</integer>
            <key>Hour</key>
            <integer>16</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key>
            <integer>2</integer>
            <key>Hour</key>
            <integer>16</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key>
            <integer>3</integer>
            <key>Hour</key>
            <integer>16</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key>
            <integer>4</integer>
            <key>Hour</key>
            <integer>16</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key>
            <integer>5</integer>
            <key>Hour</key>
            <integer>16</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>

    <key>StandardOutPath</key>
    <string>/path/to/trading_agent/logs/snapshot_launchd.log</string>

    <key>StandardErrorPath</key>
    <string>/path/to/trading_agent/logs/snapshot_launchd_error.log</string>
</dict>
</plist>
```

#### 2. Load LaunchAgent

```bash
# Load the agent
launchctl load ~/Library/LaunchAgents/com.trading.snapshot.plist

# Verify it's loaded
launchctl list | grep trading

# Test run manually
launchctl start com.trading.snapshot
```

---

## Systemd Service Setup (Alternative)

For Linux servers, systemd provides robust service management.

### 1. Create Systemd Service

```bash
# File: /etc/systemd/system/trading-snapshot.service
[Unit]
Description=Trading Agent Daily Position Snapshot
After=network.target

[Service]
Type=oneshot
User=your_username
WorkingDirectory=/path/to/trading_agent
Environment="PATH=/path/to/trading_agent/venv/bin"
EnvironmentFile=/path/to/trading_agent/.env
ExecStart=/path/to/trading_agent/venv/bin/python -m src.cli.main snapshot-positions

[Install]
WantedBy=multi-user.target
```

### 2. Create Systemd Timer

```bash
# File: /etc/systemd/system/trading-snapshot.timer
[Unit]
Description=Run trading snapshot daily at market close
Requires=trading-snapshot.service

[Timer]
OnCalendar=Mon-Fri 16:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

### 3. Enable and Start

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable timer (start on boot)
sudo systemctl enable trading-snapshot.timer

# Start timer
sudo systemctl start trading-snapshot.timer

# Check status
sudo systemctl status trading-snapshot.timer

# View logs
sudo journalctl -u trading-snapshot.service -f
```

---

## Monitoring & Logs

### Log Locations

```bash
# Application logs
tail -f logs/app.log

# Cron-specific logs
tail -f logs/snapshot_cron.log

# Trading system logs
tail -f logs/trades.log
tail -f logs/learning.log
```

### Database Verification

```bash
# Check snapshot counts
sqlite3 data/databases/trades.db << EOF
SELECT
    'Entry Snapshots' as Type, COUNT(*) as Count
FROM trade_entry_snapshots
UNION ALL
SELECT
    'Position Snapshots', COUNT(*)
FROM position_snapshots
UNION ALL
SELECT
    'Exit Snapshots', COUNT(*)
FROM trade_exit_snapshots;
EOF
```

### Health Check Script

```bash
# File: scripts/health_check.sh
#!/bin/bash

echo "=== Trading Agent Health Check ==="
echo ""

# Check IBKR connection
echo "1. IBKR Connection:"
if netstat -an | grep -q "127.0.0.1:7497"; then
    echo "   ✓ IBKR Gateway running"
else
    echo "   ✗ IBKR Gateway NOT running"
fi

# Check recent snapshots
echo ""
echo "2. Recent Snapshots (last 24 hours):"
sqlite3 data/databases/trades.db << EOF
SELECT COUNT(*) || ' position snapshots'
FROM position_snapshots
WHERE captured_at > datetime('now', '-1 day');
EOF

# Check cron job
echo ""
echo "3. Cron Job Status:"
crontab -l | grep snapshot || echo "   ⚠ No cron job found"

# Check disk space
echo ""
echo "4. Disk Usage:"
du -sh data/databases/*.db

echo ""
echo "=== Health Check Complete ==="
```

---

## Troubleshooting

### Cron Job Not Running

**Symptom:** No snapshots captured automatically

**Checks:**
```bash
# 1. Verify cron service is running
sudo service cron status          # Linux
sudo launchctl list | grep cron   # macOS

# 2. Check cron job is registered
crontab -l

# 3. Verify script is executable
ls -l scripts/daily_snapshot.sh

# 4. Test script manually
bash -x scripts/daily_snapshot.sh

# 5. Check cron logs
grep CRON /var/log/syslog
```

**Common Issues:**
- Incorrect PATH in cron environment
- .env file not found
- Virtual environment not activated
- Permissions issues

**Fix:**
Use absolute paths everywhere in your cron script.

---

### IBKR Connection Fails in Cron

**Symptom:** "Failed to connect to IBKR" in logs

**Cause:** IBKR Gateway/TWS not running or connection settings incorrect

**Fix:**
```bash
# 1. Ensure IBKR Gateway is running 24/7
# Use auto-restart scripts or systemd

# 2. Verify connection settings in .env
echo $IBKR_HOST  # Should be 127.0.0.1
echo $IBKR_PORT  # Should be 7497 (paper) or 7496 (live)

# 3. Test connection manually
python -m src.cli.main snapshot-positions
```

---

### Missing Data in Snapshots

**Symptom:** Snapshots captured but many fields are NULL

**Cause:** Market closed or data retrieval errors

**Check:**
```bash
# View data quality
python -m src.cli.main learning-stats

# Check logs for specific errors
grep "Failed to capture" logs/app.log
```

**Note:** Some fields (Greeks, IV) require market hours and cannot be captured after close.

---

### Timezone Issues

**Symptom:** Snapshots run at wrong time

**Fix:**
```bash
# Set timezone in cron
0 16 * * 1-5 TZ=America/New_York /path/to/script.sh

# Or set system timezone
sudo timedatectl set-timezone America/New_York
```

---

## Best Practices

### 1. Run After Market Close

Schedule snapshots for **4:05 PM ET** (5 minutes after close) to ensure:
- All orders settled
- Final prices available
- Reduced API load

### 2. Monitor Data Quality

```bash
# Weekly data quality check
python -m src.cli.main learning-stats

# Alert if critical fields coverage drops below 80%
```

### 3. Backup Database

```bash
# Daily database backup (add to cron)
0 17 * * 1-5 cp data/databases/trades.db "data/backups/trades_$(date +\%Y\%m\%d).db"
```

### 4. Log Rotation

```bash
# Add to /etc/logrotate.d/trading-agent
/path/to/trading_agent/logs/*.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
}
```

### 5. Export Learning Data Weekly

```bash
# Export data every Sunday for analysis
0 10 * * 0 cd /path/to/trading_agent && python -m src.cli.main export-learning-data
```

---

## Summary Checklist

- [ ] Virtual environment created and activated
- [ ] .env file configured correctly
- [ ] IBKR Gateway/TWS running on correct port
- [ ] Test commands manually: `snapshot-positions`, `export-learning-data`, `learning-stats`
- [ ] Wrapper script created and made executable
- [ ] Cron job added to crontab (or LaunchAgent/systemd configured)
- [ ] Verified cron job is scheduled: `crontab -l`
- [ ] Test cron job runs: check logs after scheduled time
- [ ] Verify snapshots in database: `SELECT COUNT(*) FROM position_snapshots`
- [ ] Set up log rotation
- [ ] Set up database backup
- [ ] Configure monitoring/alerts

---

## Quick Reference

```bash
# Manual snapshot
python -m src.cli.main snapshot-positions

# Export learning data
python -m src.cli.main export-learning-data --output data/learning.csv

# View statistics
python -m src.cli.main learning-stats

# Check database
sqlite3 data/databases/trades.db "SELECT COUNT(*) FROM position_snapshots;"

# View logs
tail -f logs/app.log

# Test cron script
bash -x scripts/daily_snapshot.sh

# Edit crontab
crontab -e

# List cron jobs
crontab -l
```

---

**The trading agent is now configured for 24/7 operation with automated data collection!** 🎉

All trade data (entry, daily monitoring, exit) will be captured automatically and made available to the learning engine for pattern detection and strategy optimization.
