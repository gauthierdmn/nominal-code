#!/usr/bin/env bash
set -euo pipefail

# Install the latest cloudflared binary for Linux amd64.

curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

echo "cloudflared installed: $(cloudflared --version)"
