#!/usr/bin/env bash
set -euo pipefail

# PANW Product Helper — 数据备份/迁移打包脚本
# 用法：
#   ./backup.sh           # 仅备份 data/ 目录（RAG 知识库 + 用户数据）
#   ./backup.sh --full    # 完整打包（代码 + 配置 + 数据，可直接迁移到新 VM）

cd "$(dirname "$0")"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }

if [ "${1:-}" = "--full" ]; then
    # 完整迁移包：代码 + 配置 + 数据
    ARCHIVE="panw-helper-full-${TIMESTAMP}.tar.gz"
    info "Creating full migration package..."

    tar -czf "../${ARCHIVE}" \
        --exclude='.venv' \
        --exclude='__pycache__' \
        --exclude='.DS_Store' \
        --exclude='*.pyc' \
        -C .. "$(basename "$(pwd)")"

    SIZE=$(du -h "../${ARCHIVE}" | cut -f1)
    info "Full package created: ../${ARCHIVE} (${SIZE})"
    echo ""
    echo "  迁移步骤："
    echo "  1. 将 ${ARCHIVE} 传输到新 VM"
    echo "  2. tar -xzf ${ARCHIVE}"
    echo "  3. cd $(basename "$(pwd)")"
    echo "  4. 编辑 .env（修改 SMTP/IP 等环境相关配置）"
    echo "  5. ./deploy.sh"
else
    # 仅数据备份
    ARCHIVE="panw-helper-data-${TIMESTAMP}.tar.gz"
    info "Backing up data directory..."

    tar -czf "../${ARCHIVE}" data/

    SIZE=$(du -h "../${ARCHIVE}" | cut -f1)
    info "Data backup created: ../${ARCHIVE} (${SIZE})"
    echo ""
    echo "  包含内容："
    echo "    data/auth.db           — 用户账号、会话、域名白名单"
    echo "    data/datasheets/       — 已下载的产品规格书"
    echo "    data/internal_demos/   — 内部演示链接"
    echo "    data/external_demos/   — 外部演示文件"
    echo "    data/sku/              — SKU 计算规则"
    echo "    data/techdocs/         — 内部技术文档"
    echo ""
    echo "  恢复方法：在项目目录下执行"
    echo "    tar -xzf ${ARCHIVE}"
fi
