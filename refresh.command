#!/bin/bash
# Splits — Refresh Data
# Double-click this file in Finder to run the scraper

# Get the directory where this script lives
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Make sure Terminal stays open on error
cd "$DIR"

echo "Starting Splits scraper..."
echo ""

# Check Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo "Please install it from https://python.org"
    read -p "Press Enter to close..."
    exit 1
fi

# Run the scraper
python3 "$DIR/scrape_splits.py"
