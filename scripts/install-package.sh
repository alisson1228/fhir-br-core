#!/bin/sh

set -eu

echo "========================================"
echo " FHIR BR CORE - INITIAL SETUP"
echo "========================================"

# Verifica se a instalação já foi realizada
if [ -f /setup-state/installed ]; then
    echo "[INFO] O pacote já foi instalado anteriormente."
    exit 0
fi

# 1. Gerar o pacote
echo "[1/3] Gerando pacote FHIR BR Core..."

python /workspace/scripts/build-package.py

# 2. Aguardar o HAPI FHIR ficar disponível
echo "[2/3] Aguardando o HAPI FHIR..."

attempts=0

until curl -fsS \
    http://fhir-server:8080/fhir/metadata \
    >/dev/null; do

    attempts=$((attempts + 1))

    if [ "$attempts" -ge 60 ]; then
        echo "[ERRO] HAPI FHIR não ficou disponível."
        exit 1
    fi

    echo "Aguardando inicialização... ($attempts/60)"
    sleep 5
done

# 3. Instalar o Implementation Guide
echo "[3/3] Instalando pacote FHIR BR Core..."

curl -fS -X POST \
    -H "Content-Type: application/fhir+json" \
    -H "Accept: application/fhir+json" \
    --data-binary @/workspace/fhir-packages/package.r4.json \
    'http://fhir-server:8080/fhir/ImplementationGuide/$install'

# Registrar que a instalação foi concluída
touch /setup-state/installed

echo ""
echo "[OK] FHIR BR Core instalado com sucesso!"
