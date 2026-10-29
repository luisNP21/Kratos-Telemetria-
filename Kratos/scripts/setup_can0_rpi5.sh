#!/usr/bin/env bash
# ==============================================================================
# KRATOS FSAE TELEMETRÍA — CONFIGURACIÓN DE INTERFAZ FÍSICA CAN0 (RASPBERRY PI 5)
# Hardware: Raspberry Pi 5 (4GB) + MCP2515 (SPI) + TJA1050 + Conversor TXB0108
# Velocidad del bus automotriz del monoplaza: 500 kbps
# ==============================================================================
#
# REQUISITO PREVIO EN LA RASPBERRY PI 5:
# Edita el archivo /boot/firmware/config.txt y asegúrate de habilitar SPI y el
# overlay del controlador MCP2515 agregando las siguientes líneas:
#
#   dtparam=spi=on
#   dtoverlay=mcp2515-can0,oscillator=8000000,interrupt=25
#
# Nota: Si el cristal del módulo MCP2515 es de 16 MHz en lugar de 8 MHz, ajusta
# oscillator=16000000. Después de editar /boot/firmware/config.txt, reinicia
# la Raspberry Pi 5 antes de ejecutar este script.
# ==============================================================================

set -euo pipefail

INTERFACE="${1:-can0}"
BITRATE="${2:-500000}"

echo "[Kratos CAN RPi5] Configurando interfaz física '$INTERFACE' a ${BITRATE} bps (500 kbps)..."

if ! ip link show "$INTERFACE" > /dev/null 2>&1; then
    echo "[ERROR] No se encontró la interfaz '$INTERFACE'." >&2
    echo "Verifica el cableado SPI/TXB0108 y que '/boot/firmware/config.txt' contenga:" >&2
    echo "  dtparam=spi=on" >&2
    echo "  dtoverlay=mcp2515-can0,oscillator=8000000,interrupt=25" >&2
    exit 1
fi

# Bajar la interfaz si ya estaba activa para reconfigurar bitrate de forma limpia
sudo ip link set "$INTERFACE" down || true

# Configurar bitrate a 500 kbps y auto-recuperación ante Bus-Off (restart-ms 100)
sudo ip link set "$INTERFACE" type can bitrate "$BITRATE" restart-ms 100
sudo ip link set "$INTERFACE" txqueuelen 1000
sudo ip link set up "$INTERFACE"

echo "[Kratos CAN RPi5] Interfaz '$INTERFACE' activa a ${BITRATE} bps:"
ip -details -statistics link show "$INTERFACE"

