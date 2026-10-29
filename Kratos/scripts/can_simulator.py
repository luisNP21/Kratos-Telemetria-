#!/usr/bin/env python3
"""
KRATOS FSAE TELEMETRÍA — EMULADOR DE TRAMAS CAN BUS (FORMATO BIG ENDIAN / MOTOROLA)
===================================================================================
Inyecta tramas periódicas realistas en 'vcan0' (o 'can0') y, simultáneamente, en el
bridge de desarrollo para WSL2/Docker Desktop ('kratos/can_raw' en localhost:1883),
simulando los bytes crudos en formato Big Endian:
  - 0x10D: Energy Meter (Voltaje [300V - 400V], Corriente [A] y Potencia [W])
  - 0x40D: Energy Meter (Energía acumulada [Wh], Estado y Contador de tramas)
  - 0x6B0: Orion BMS (Voltaje del Pack, Corriente, SOC [%] y Temperaturas de celdas [°C])

Requisitos:
  pip install python-can
"""

import argparse
import json
import math
import random
import socket
import struct
import sys
import time
from typing import Optional

try:
    import can
except ImportError:
    print(
        "[ERROR] La librería 'python-can' no está instalada.\n"
        "Instálala ejecutando: pip install python-can",
        file=sys.stderr,
    )
    sys.exit(1)


class RawMqttBridge:
    """
    Cliente MQTT 3.1.1 minimalista (usando solo 'socket' estándar de Python)
    para inyectar los bytes crudos de las tramas CAN hacia Node-RED cuando se
    ejecuta sobre WSL2 + Docker Desktop (donde la VM de Docker está aislada de vcan0).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 1883, topic: str = "kratos/can_raw") -> None:
        self.host = host
        self.port = port
        self.topic = topic.encode("utf-8")
        self.sock: Optional[socket.socket] = None
        self._connect()

    def _connect(self) -> None:
        try:
            s = socket.create_connection((self.host, self.port), timeout=2.0)
            client_id = b"kratos-can-sim"
            var_header = b"\x00\x04MQTT\x04\x02\x00\x3c"
            payload = struct.pack(">H", len(client_id)) + client_id
            rem_len = len(var_header) + len(payload)
            packet = bytes([0x10, rem_len]) + var_header + payload
            s.sendall(packet)
            s.recv(4)  # CONNACK
            self.sock = s
        except Exception:
            self.sock = None

    def send_raw_can_frame(self, msg: can.Message) -> bool:
        if self.sock is None:
            self._connect()
            if self.sock is None:
                return False
        try:
            body = json.dumps(
                {
                    "canid": msg.arbitration_id,
                    "ext": msg.is_extended_id,
                    "rtr": msg.is_remote_frame,
                    "data": list(msg.data),
                }
            ).encode("utf-8")
            vh = struct.pack(">H", len(self.topic)) + self.topic
            rem_len = len(vh) + len(body)
            # Codificar Remaining Length de MQTT
            rl_bytes = bytearray()
            x = rem_len
            while True:
                encoded = x % 128
                x //= 128
                if x > 0:
                    encoded |= 128
                rl_bytes.append(encoded)
                if x <= 0:
                    break
            packet = bytes([0x30]) + bytes(rl_bytes) + vh + body
            self.sock.sendall(packet)
            return True
        except Exception:
            self.sock = None
            return False

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


def build_energy_meter_vi_frame(voltage_v: float, current_a: float) -> can.Message:
    """
    Construye trama 0x10D (8 bytes en Big Endian / Motorola):
      - Bytes 0..1: uint16 BE -> Voltaje * 10 (0.1 V/bit)
      - Bytes 2..3: int16 BE  -> Corriente * 10 (0.1 A/bit)
      - Bytes 4..7: int32 BE  -> Potencia instantánea en W
    """
    raw_voltage = max(0, min(65535, int(round(voltage_v * 10.0))))
    raw_current = max(-32768, min(32767, int(round(current_a * 10.0))))
    power_w = int(round(voltage_v * current_a))

    data = struct.pack(">Hhi", raw_voltage, raw_current, power_w)
    return can.Message(arbitration_id=0x10D, data=data, is_extended_id=False)


def build_energy_meter_status_frame(energy_wh: float, status: int, counter: int) -> can.Message:
    """
    Construye trama 0x40D (8 bytes en Big Endian / Motorola):
      - Bytes 0..3: uint32 BE -> Energía acumulada * 10 (0.1 Wh/bit)
      - Byte  4:    uint8     -> Código de estado (0=OK, 1=WARN, 2=FAULT)
      - Bytes 5..6: uint16 BE -> Contador de tramas (0..65535)
      - Byte  7:    uint8     -> Reservado (0)
    """
    raw_energy = max(0, int(round(energy_wh * 10.0)))
    raw_counter = counter & 0xFFFF
    data = struct.pack(">IBHB", raw_energy, status & 0xFF, raw_counter, 0)
    return can.Message(arbitration_id=0x40D, data=data, is_extended_id=False)


def build_orion_bms_frame(
    pack_voltage_v: float,
    pack_current_a: float,
    soc_pct: float,
    temp_max_c: int,
    temp_avg_c: int,
    bms_status: int = 0,
) -> can.Message:
    """
    Construye trama 0x6B0 del Orion BMS (8 bytes en Big Endian / Motorola):
      - Bytes 0..1: uint16 BE -> Voltaje Pack * 10 (0.1 V/bit)
      - Bytes 2..3: int16 BE  -> Corriente Pack * 10 (0.1 A/bit)
      - Byte  4:    uint8     -> SOC * 2 (0.5 %/bit)
      - Byte  5:    int8      -> Temperatura máxima de celdas (°C)
      - Byte  6:    int8      -> Temperatura promedio de celdas (°C)
      - Byte  7:    uint8     -> Flags de estado BMS
    """
    raw_v = max(0, min(65535, int(round(pack_voltage_v * 10.0))))
    raw_i = max(-32768, min(32767, int(round(pack_current_a * 10.0))))
    raw_soc = max(0, min(200, int(round(soc_pct * 2.0))))
    raw_tmax = max(-40, min(120, int(temp_max_c)))
    raw_tavg = max(-40, min(120, int(temp_avg_c)))

    data = struct.pack(">HhBbbB", raw_v, raw_i, raw_soc, raw_tmax, raw_tavg, bms_status & 0xFF)
    return can.Message(arbitration_id=0x6B0, data=data, is_extended_id=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Emulador CAN Bus Kratos FSAE (Big Endian / Motorola) para vcan0/can0 y WSL2"
    )
    parser.add_argument(
        "-i",
        "--interface",
        default="vcan0",
        help="Interfaz SocketCAN destino (por defecto: vcan0)",
    )
    parser.add_argument(
        "-f",
        "--freq",
        type=float,
        default=5.0,
        help="Frecuencia de envío en Hz (por defecto: 5.0 Hz)",
    )
    args = parser.parse_args()

    period_s = 1.0 / max(0.1, args.freq)

    bus: Optional[can.BusABC] = None
    try:
        bus = can.interface.Bus(channel=args.interface, interface="socketcan")
        print(f"[Kratos CAN Simulator] Conectado a SocketCAN nativo en '{args.interface}'.")
    except Exception as exc:
        print(
            f"[AVISO] Interfaz SocketCAN '{args.interface}' no disponible ({exc}).\n"
            f"        Usando inyección directa de tramas binarias hacia Node-RED (modo WSL2/Docker)."
        )

    bridge = RawMqttBridge(host="127.0.0.1", port=1883, topic="kratos/can_raw")

    t0 = time.monotonic()
    energy_wh = 120.0
    soc_pct = 92.0
    frame_counter = 0

    print(
        f"[Kratos CAN Simulator] Transmitiendo tramas 0x10D, 0x40D y 0x6B0 en Big Endian ({args.freq} Hz). "
        "Presiona Ctrl+C para detener."
    )
    try:
        while True:
            elapsed = time.monotonic() - t0
            frame_counter += 1

            # Simulación realista de vuelta en pista:
            # - Voltaje oscila entre 300 V y 400 V (centro 350 V)
            voltage_v = 350.0 + 48.0 * math.sin(elapsed * 0.25) + random.uniform(-1.5, 1.5)
            voltage_v = max(300.0, min(400.0, voltage_v))

            # - Corriente de tracción/regeneración entre -20 A y 180 A
            current_a = 75.0 + 65.0 * math.sin(elapsed * 0.6) + random.uniform(-4.0, 4.0)

            # - Integración de energía acumulada (Wh) y descarga progresiva de SOC
            power_w = voltage_v * current_a
            energy_wh = max(0.0, energy_wh + (power_w * period_s) / 3600.0)
            soc_pct = max(15.0, 92.0 - (energy_wh - 120.0) * 0.015)

            # - Temperaturas de celdas Orion BMS (32 °C a 52 °C)
            temp_avg_c = int(round(34.0 + 6.0 * math.sin(elapsed * 0.08)))
            temp_max_c = temp_avg_c + random.choice([3, 4, 5])

            status_code = 0 if voltage_v >= 310.0 and temp_max_c < 55 else 1

            msg_10d = build_energy_meter_vi_frame(voltage_v, current_a)
            msg_40d = build_energy_meter_status_frame(energy_wh, status_code, frame_counter)
            msg_6b0 = build_orion_bms_frame(
                pack_voltage_v=voltage_v + random.uniform(-0.8, 0.8),
                pack_current_a=current_a + random.uniform(-0.5, 0.5),
                soc_pct=soc_pct,
                temp_max_c=temp_max_c,
                temp_avg_c=temp_avg_c,
                bms_status=status_code,
            )

            for m in (msg_10d, msg_40d, msg_6b0):
                if bus is not None:
                    try:
                        bus.send(m)
                    except Exception:
                        pass
                bridge.send_raw_can_frame(m)

            print(
                f"[TX #{frame_counter:04d}] "
                f"0x10D: V={voltage_v:6.1f}V I={current_a:6.1f}A P={int(power_w):6d}W | "
                f"0x40D: E={energy_wh:7.1f}Wh ST={status_code} | "
                f"0x6B0 (BMS): SOC={soc_pct:4.1f}% Tmax={temp_max_c}°C Tavg={temp_avg_c}°C"
            )

            time.sleep(period_s)
    except KeyboardInterrupt:
        print("\n[Kratos CAN Simulator] Simulación detenida por el usuario.")
    finally:
        if bus is not None:
            bus.shutdown()
        bridge.close()


if __name__ == "__main__":
    main()
