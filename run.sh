#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Export PYTHONPATH to include the src directory
export PYTHONPATH="$PYTHONPATH:$SCRIPT_DIR/src"

# Run the MindSpace bot
echo "🚀 Starting MindSpace..."
python3 -m mindspace.main "$@"
