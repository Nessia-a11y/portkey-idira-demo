#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# --- Check prerequisites & auto-install Docker ---
if ! command -v docker >/dev/null 2>&1; then
    info "Docker not found. Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo systemctl start docker
    sudo systemctl enable docker
    # Add current user to docker group
    if ! groups | grep -q docker; then
        sudo usermod -aG docker "$USER"
        warn "Added $USER to docker group. If permission errors occur, re-login or run: newgrp docker"
    fi
    info "Docker installed successfully."
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    info "Docker Compose not found. Installing Docker Compose plugin..."
    sudo apt-get update -qq && sudo apt-get install -y -qq docker-compose-plugin
    COMPOSE="docker compose"
fi

# --- Ensure .env exists ---
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        warn ".env not found — created from .env.example"
        warn "Please edit .env and set your PORTKEY_API_KEY, then re-run this script."
        exit 1
    else
        error ".env file not found and no .env.example to copy from."
    fi
fi

# --- Validate PORTKEY_API_KEY is set ---
source .env 2>/dev/null || true
if [ -z "${PORTKEY_API_KEY:-}" ] || [ "$PORTKEY_API_KEY" = "your-portkey-api-key-here" ]; then
    error "PORTKEY_API_KEY is not configured. Edit .env and set a valid API key."
fi

# --- Generate SSL certificate if not exists ---
if [ ! -f nginx/certs/server.crt ] || [ ! -f nginx/certs/server.key ]; then
    info "SSL certificate not found. Generating self-signed certificate..."
    # Try to detect public IP for cert subject
    PUBLIC_IP=$(curl -sf --max-time 5 http://ifconfig.me 2>/dev/null || echo "localhost")
    ./nginx/generate-cert.sh "$PUBLIC_IP"
    info "Certificate generated for: $PUBLIC_IP"
fi

# --- Build and run ---
info "Building Docker images..."
$COMPOSE build

info "Starting services..."
$COMPOSE up -d

info "Waiting for service to be ready..."
for i in $(seq 1 15); do
    if curl -skf https://localhost/health >/dev/null 2>&1; then
        echo ""
        info "Service is up and running!"
        echo ""
        echo "  Web UI:   https://<your-ip>"
        echo "  Health:   https://<your-ip>/health"
        echo "  Chat API: POST https://<your-ip>/chat"
        echo ""
        echo "  (HTTP :80 auto-redirects to HTTPS :443)"
        echo ""
        info "Logs: $COMPOSE logs -f"
        info "Stop: $COMPOSE down"
        exit 0
    fi
    printf "."
    sleep 2
done

warn "Service started but health check not responding yet."
warn "Check logs with: $COMPOSE logs"
