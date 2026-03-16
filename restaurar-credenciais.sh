#!/bin/bash
# ============================================================
# Restaura credenciais a partir do arquivo criptografado
# Uso: ./restaurar-credenciais.sh
# Pré-requisito: gpg instalado e senha do arquivo
# ============================================================

set -e

echo "=========================================="
echo "🔐 RESTAURANDO CREDENCIAIS"
echo "=========================================="

if [ ! -f ".env.gpg" ]; then
    echo "❌ Arquivo .env.gpg não encontrado."
    echo "   Certifique-se de estar na pasta do projeto."
    exit 1
fi

echo ""
echo "🔑 Descriptografando .env.gpg..."
echo "   (será solicitada a senha)"
echo ""

gpg --decrypt --output .env .env.gpg

echo ""
echo "✅ .env restaurado com sucesso!"
echo ""
echo "Próximos passos:"
echo "  1. Build do Docker:"
echo "     DOCKER_BUILDKIT=0 docker build -t afiliado-bot ."
echo ""
echo "  2. Sobe o container:"
echo "     docker run -d --name afiliado-bot --restart unless-stopped \\"
echo "       -e PYTHONUNBUFFERED=1 \\"
echo "       -v \"\$(pwd)/tokens.json:/app/tokens.json\" \\"
echo "       -v \"\$(pwd)/enviados.json:/app/enviados.json\" \\"
echo "       --env-file .env \\"
echo "       afiliado-bot"
