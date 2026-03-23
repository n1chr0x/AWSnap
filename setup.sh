#!/bin/bash

# AWSnap Setup Script by @n1chr0x
echo "⚡ Starting AWSnap Dependency Setup..."

# 1. Check if running as root
if [ "$EUID" -ne 0 ]; then 
  echo "❌ Please run as root (use sudo ./setup.sh)"
  exit
fi

# 2. Update and Install System Tools (The 'Hardware' tools)
echo "📦 Installing System Tools (gdisk, util-linux, e2fsprogs)..."
apt-get update -y && apt-get install -y gdisk util-linux e2fsprogs python3-pip

# 3. Install Python Libraries (The 'Software' brains)
echo "🐍 Installing Python Libraries (boto3, tqdm)..."
pip3 install boto3 tqdm --break-system-packages

echo "✅ Setup Complete! You can now run: sudo python3 awsnap.py"
