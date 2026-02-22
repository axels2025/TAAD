import sqlite3
from pathlib import Path

# Create directory if it doesn't exist
Path('data/databases').mkdir(parents=True, exist_ok=True)

# Now create database
conn = sqlite3.connect('data/databases/test.db')
cursor = conn.cursor()

# Create test table
cursor.execute('''
CREATE TABLE test2 (
    id INTEGER PRIMARY KEY,
    name TEXT
)
''')

# Insert test data
cursor.execute("INSERT INTO test (name) VALUES ('works')")
conn.commit()

# Query
result = cursor.execute("SELECT * FROM test2").fetchall()
print(f"Database working: {result}")
conn.close()