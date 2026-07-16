#!/usr/bin/env bash
set -euo pipefail

# 生成自签 SSL 证书（有效期 10 年）
# 用法: ./nginx/generate-cert.sh [IP或域名]

CERT_DIR="$(dirname "$0")/certs"
mkdir -p "$CERT_DIR"

SUBJECT="${1:-localhost}"

echo "Generating self-signed certificate for: $SUBJECT"

openssl req -x509 -nodes -days 3650 \
    -newkey rsa:2048 \
    -keyout "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.crt" \
    -subj "/C=CN/ST=Shanghai/L=Shanghai/O=PANW-Helper/CN=$SUBJECT" \
    -addext "subjectAltName=DNS:$SUBJECT,IP:$SUBJECT" \
    2>/dev/null

echo "Certificate generated:"
echo "  $CERT_DIR/server.crt"
echo "  $CERT_DIR/server.key"
echo ""
echo "Valid for 10 years. Subject: $SUBJECT"
