#!/bin/bash
# ============================================================
# Restaura credenciais a partir dos GitHub Secrets
# Uso: ./restaurar-credenciais.sh
# Pré-requisito: gh auth login
# ============================================================

set -e

REPO="Wilson-Salomao733/MercadoLivre"
ENV_FILE=".env"

echo "=========================================="
echo "🔐 RESTAURANDO CREDENCIAIS DO GITHUB"
echo "=========================================="

# Verifica se gh está autenticado
if ! gh auth status &>/dev/null; then
    echo "❌ Você não está autenticado no GitHub CLI."
    echo "   Execute: gh auth login"
    exit 1
fi

echo ""
echo "📄 Restaurando variáveis para $ENV_FILE..."

ML_CLIENT_ID=$(gh secret view ML_CLIENT_ID       --repo "$REPO" --json value -q .value 2>/dev/null || echo "")
ML_CLIENT_SECRET=$(gh secret view ML_CLIENT_SECRET --repo "$REPO" --json value -q .value 2>/dev/null || echo "")
TELEGRAM_TOKEN=$(gh secret view TELEGRAM_TOKEN   --repo "$REPO" --json value -q .value 2>/dev/null || echo "")
TELEGRAM_CHAT_ID=$(gh secret view TELEGRAM_CHAT_ID --repo "$REPO" --json value -q .value 2>/dev/null || echo "")
MATT_TOOL=$(gh secret view MATT_TOOL             --repo "$REPO" --json value -q .value 2>/dev/null || echo "")
MATT_WORD=$(gh secret view MATT_WORD             --repo "$REPO" --json value -q .value 2>/dev/null || echo "")

# Valida se conseguiu buscar os valores
FALHOU=0
for VAR in ML_CLIENT_ID ML_CLIENT_SECRET TELEGRAM_TOKEN TELEGRAM_CHAT_ID MATT_TOOL MATT_WORD; do
    VAL="${!VAR}"
    if [ -z "$VAL" ]; then
        echo "  ⚠️  $VAR não encontrado nos secrets"
        FALHOU=1
    else
        echo "  ✅ $VAR restaurado"
    fi
done

if [ "$FALHOU" -eq 1 ]; then
    echo ""
    echo "❌ Um ou mais secrets não foram encontrados."
    echo "   Verifique: gh secret list --repo $REPO"
    exit 1
fi

# Grava o .env
cat > "$ENV_FILE" <<EOF
ML_CLIENT_ID=$ML_CLIENT_ID
ML_CLIENT_SECRET=$ML_CLIENT_SECRET
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID
MATT_TOOL=$MATT_TOOL
MATT_WORD=$MATT_WORD
EOF

echo ""
echo "✅ Credenciais restauradas em $ENV_FILE"
echo ""
echo "Próximos passos:"
echo "  1. Verifique o tokens.json (precisa autenticar no ML se não existir)"
echo "  2. Rode o bot: docker run -d --name afiliado-bot --restart unless-stopped \\"
echo "       -e PYTHONUNBUFFERED=1 \\"
echo "       -v \"\$(pwd)/tokens.json:/app/tokens.json\" \\"
echo "       -v \"\$(pwd)/enviados.json:/app/enviados.json\" \\"
echo "       --env-file .env \\"
echo "       afiliado-bot"
