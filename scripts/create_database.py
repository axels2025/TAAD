import sqlite3
from pathlib import Path

# Ensure directory exists
Path("data/databases").mkdir(parents=True, exist_ok=True)

# Connect to database
conn = sqlite3.connect('data/databases/trades.db')
cursor = conn.cursor()

# Create trades table
cursor.execute('''
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,
    
    -- Trade details
    symbol TEXT NOT NULL,
    strike REAL NOT NULL,
    expiration DATE NOT NULL,
    option_type TEXT DEFAULT 'PUT',
    
    -- Entry
    entry_date TIMESTAMP NOT NULL,
    entry_premium REAL NOT NULL,
    contracts INTEGER NOT NULL,
    
    -- Exit
    exit_date TIMESTAMP,
    exit_premium REAL,
    exit_reason TEXT,
    
    -- P&L
    profit_loss REAL,
    profit_pct REAL,
    roi REAL,
    days_held INTEGER,
    
    -- Strategy parameters
    otm_pct REAL NOT NULL,
    dte INTEGER NOT NULL,
    config_version INTEGER,
    
    -- Market context
    vix_at_entry REAL,
    vix_at_exit REAL,
    spy_price_at_entry REAL,
    market_regime TEXT,
    
    -- Experiment tracking
    is_experiment BOOLEAN DEFAULT 0,
    experiment_id INTEGER,
    
    -- AI context
    ai_confidence REAL,
    ai_reasoning TEXT,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Create experiments table
cursor.execute('''
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT UNIQUE NOT NULL,
    
    -- Hypothesis
    name TEXT NOT NULL,
    description TEXT,
    parameter_name TEXT NOT NULL,
    control_value TEXT NOT NULL,
    test_value TEXT NOT NULL,
    
    -- Status
    status TEXT DEFAULT 'active',
    start_date TIMESTAMP NOT NULL,
    end_date TIMESTAMP,
    
    -- Results
    control_trades INTEGER DEFAULT 0,
    test_trades INTEGER DEFAULT 0,
    p_value REAL,
    effect_size REAL,
    decision TEXT,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Create learning_history table
cursor.execute('''
CREATE TABLE IF NOT EXISTS learning_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Learning event
    event_type TEXT NOT NULL,
    event_date TIMESTAMP NOT NULL,
    
    -- Details
    pattern_name TEXT,
    confidence REAL,
    sample_size INTEGER,
    
    -- Change made
    parameter_changed TEXT,
    old_value TEXT,
    new_value TEXT,
    
    -- Justification
    reasoning TEXT,
    expected_improvement REAL,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Create patterns table
cursor.execute('''
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Pattern identification
    pattern_type TEXT NOT NULL,
    pattern_name TEXT NOT NULL,
    
    -- Statistics
    sample_size INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    avg_roi REAL NOT NULL,
    confidence REAL NOT NULL,
    p_value REAL NOT NULL,
    
    -- Context
    market_regime TEXT,
    date_detected TIMESTAMP NOT NULL,
    
    -- Status
    status TEXT DEFAULT 'active',
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

conn.commit()
print("✅ Database created successfully!")

# Verify tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f"✅ Tables created: {[t[0] for t in tables]}")

conn.close()
