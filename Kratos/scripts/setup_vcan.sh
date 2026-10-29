#!/usr/bin/env bash
# ==============================================================================
# KRATOS FSAE TELEMETRÍA — CONFIGURACIÓN DE INTERFAZ CAN VIRTUAL (vcan0)
# Uso en Ubuntu para pruebas 100% software sin hardware físico conectado
# ==============================================================================

set -euo pipefail

INTERFACE="${1:-vcan0}"

echo "[Kratos CAN] Cargando módulo del kernel 'vcan'..."
sudo modprobe vcan

if ip link show "$INTERFACE" > /dev/null 2>&1; then
    echo "[Kratos CAN] La interfaz '$INTERFACE' ya existe. Asegurando que esté activa..."
else
    echo "[Kratos CAN] Creando interfaz virtual '$INTERFACE'..."
    sudo ip link add dev "$INTERFACE" type vcan
fi

sudo ip link set up "$INTERFACE"

echo "[Kratos CAN] Interfaz '$INTERFACE' activa y lista para simulación:"
ip -details link show "$INTERFACE"

