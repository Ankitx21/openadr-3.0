#!/usr/bin/env bash
set -e

echo "============================================================"
echo "  Grid-Interactive Competition: OpenADR 3.0 Setup"
echo "============================================================"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is not installed or not in PATH."
    exit 1
fi

echo "[1/2] Checking Python..."
python3 -c "import sys; print('Using Python ' + sys.version.split()[0])"

echo ""
echo "[2/2] Installing requirements..."
pip3 install -r requirements.txt || pip install -r requirements.txt

echo ""
echo "============================================================"
echo "  Setup Complete!"
echo "  To launch the team client, run:"
echo "     python3 openadr3_client.py"
echo "============================================================"
