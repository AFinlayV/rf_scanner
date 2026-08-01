#!/bin/bash
# RF Scanner – double-click launcher for macOS
# This file lives next to rf_scanner.py

# Change to the script's own directory
cd "$(dirname "$0")"

# Check for Python 3
if ! command -v python3 &>/dev/null; then
    osascript -e 'display alert "Python 3 not found" message "Install Python 3 from python.org then try again."'
    exit 1
fi

# Auto-install dependencies if missing
pip3 install --quiet pyserial RFExplorer numpy 2>/dev/null

# Launch
python3 rf_scanner.py
