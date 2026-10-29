#!/usr/bin/env bash
# ==============================================================================
# KRATOS FSAE TELEMETRÍA — CONFIGURACIÓN DE INTERFAZ CAN VIRTUAL (vcan0)
# Uso en Ubuntu / WSL2 para pruebas 100% software sin hardware físico conectado
# ==============================================================================

set -euo pipefail

INTERFACE="${1:-vcan0}"

# 1. Verificar e instalar can-utils (candump, cansend) si no está presente
if ! command -v candump > /dev/null 2>&1 || ! command -v cansend > /dev/null 2>&1; then
    echo "[Kratos CAN] Instalando paquete 'can-utils' (candump, cansend)..."
    sudo apt-get update && sudo apt-get install -y can-utils
else
    echo "[Kratos CAN] Herramientas 'can-utils' verificadas ($(command -v candump))."
fi

# 2. Cargar módulos del kernel Linux para SocketCAN
echo "[Kratos CAN] Cargando módulos del kernel 'can', 'can_raw' y 'vcan'..."
sudo modprobe can
sudo modprobe can_raw
sudo modprobe vcan

# 3. Crear y levantar la interfaz virtual vcan0
if ip link show "$INTERFACE" > /dev/null 2>&1; then
    echo "[Kratos CAN] La interfaz '$INTERFACE' ya existe. Asegurando que esté activa..."
else
    echo "[Kratos CAN] Creando interfaz virtual '$INTERFACE'..."
    sudo ip link add dev "$INTERFACE" type vcan
fi

sudo ip link set up "$INTERFACE"

echo "[Kratos CAN] Interfaz '$INTERFACE' activa y lista para simulación:"
ip -details link show "$INTERFACE"
