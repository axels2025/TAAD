#!/bin/bash

# Setup script for AI Trading Agent

echo "================================"
echo "AI Trading Agent Setup"
echo "================================"
echo ""

# Check Python version
PYTHON_PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"

if [ ! -f "$PYTHON_PATH" ]; then
    echo "Error: Python not found at $PYTHON_PATH"
    echo "Please update PYTHON_PATH in this script"
    exit 1
fi

echo "Using Python: $PYTHON_PATH"
$PYTHON_PATH --version
echo ""

# Create virtual environment (optional but recommended)
read -p "Create virtual environment? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Creating virtual environment..."
    $PYTHON_PATH -m venv venv
    source venv/bin/activate
    PYTHON_PATH="python3"
fi

# Install dependencies
echo ""
echo "Installing dependencies..."
$PYTHON_PATH -m pip install --upgrade pip
$PYTHON_PATH -m pip install -r requirements.txt

# Create .env file if not exists
if [ ! -f .env ]; then
    echo ""
    echo "Creating .env file..."
    cp .env.example .env
    echo "Please edit .env and add your ANTHROPIC_API_KEY"
fi

# Create logs directory
mkdir -p logs

echo ""
echo "================================"
echo "Setup Complete!"
echo "================================"
echo ""
echo "Next steps:"
echo "1. Edit .env and add your ANTHROPIC_API_KEY"
echo "2. Start IBKR TWS or Gateway (Paper Trading, Port 7497)"
echo "3. Run: $PYTHON_PATH main.py"
echo ""
