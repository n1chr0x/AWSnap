#!/bin/bash

# AWSnap Setup Script by @n1chr0x
# The "Quiet & Clean" Version

# 1. Colors for the "Pro" look
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}⚡ Starting AWSnap Setup...${NC}"

# 2. Check if running as root
if [ "$EUID" -ne 0 ]; then 
  echo "❌ Error: Please run as root (sudo ./setup.sh)"
  exit
fi

# 3. System Tools
echo -n "📦 Installing System Tools (gdisk, e2fsprogs)... "
apt-get update -y > /dev/null 2>&1
apt-get install -y gdisk util-linux e2fsprogs python3-pip > /dev/null 2>&1
echo -e "${GREEN}Done!${NC}"

# 4. Python Libraries
echo -n "🐍 Installing Python Libraries (boto3, tqdm)... "
pip3 install boto3 tqdm --break-system-packages > /dev/null 2>&1
echo -e "${GREEN}Done!${NC}"

echo -e "\n${GREEN}✅ AWSnap is ready for action, @n1chr0x!${NC}"
echo "Run it with: sudo python3 AWSnap.py"
