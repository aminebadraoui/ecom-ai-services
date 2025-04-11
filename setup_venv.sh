#!/bin/bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

echo "Virtual environment setup complete. Activate it with: source venv/bin/activate" 