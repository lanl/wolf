#!/usr/bin/env bash
# Clean up __pycache__ directories
# Usage: ./nuke_pycaches.sh

# Find all __pycache__ directories and delete them safely
find . -type d -name "__pycache__" -exec rm -rf {} +

echo "All __pycache__ directories have been removed."