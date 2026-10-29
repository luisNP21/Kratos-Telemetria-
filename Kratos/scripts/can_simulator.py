#!/usr/bin/env python3
"""
KRATOS FSAE TELEMETRÍA — EMULADOR OFICIAL MULTIPROTOCOLO CAN BUS
===================================================================================
Inyecta tramas periódicas hacia 'vcan0' (o 'can0') y hacia el bridge de desarrollo
WSL2/Docker ('kratos/can_raw' en localhost:1883), respetando el ordenamiento de bytes
y tipos de datos exactos de los tres manuales oficiales de hardware:

1. Energy Meter Oficial FSAE (Big Endian / Motorola @ 500 kbps):
   - 0x10d: Bytes 0..3 = Current [A] (IEEE 754 float BE), Bytes 4..7 = Voltage [V] (IEEE 754 float BE)
   - 0x40d: Byte 0 (Bit 0 = Violation, Bit 1 = Logging), Bytes 1..3 = Reserved, Bytes 4..7 = Energy [Whr] (IEEE 754 float BE)
   - 0x60d: Byte 0 (bits 0..2 = mux_id=0, bits 3..7 = num_sensors), Byte 1 = Min Temp (0.5 °C/bit), Byte 2 = Max Temp (0.5 °C/bit)

2. Orion BMS 2 (Big Endian / Motorola @ 500 kbps):
   - 0x6B0: Bytes 0..1 = Pack Current (int16 BE, 0.1 A), Bytes 2..3 = Pack Voltage (uint16 BE, 0.1 V),
            Byte 4 = Pack SOC (uint8, 0.5 %), Byte 5 = High Temp (int8, °C), Byte 6 = Low Temp (int8, °C), Byte 7 = Relay/Fault
   - 0x6B1: Bytes 0..1 = Pack DCL (uint16 BE, A), Bytes 2..3 = Pack CCL (uint16 BE, A),
            Bytes 4..5 = High Cell Voltage (uint16 BE, 0.0001 V), Bytes 6..7 = Low Cell Voltage (uint16 BE, 0.0001 V)

3. Inversor BorgWarner / Cascadia Motion CM Family (Little Endian / Intel):
   - 0x0A2: Bytes 0..1 = Inverter Module Temp (int16 LE, 0.1 °C), Bytes 4..5 = Motor Temp (int16 LE, 0.1 °C)
   - 0x0A5: Bytes 2..3 = Motor Speed (int16 LE, RPM)
   - 0x0A6: Bytes 6..7 = DC Bus Current (int16 LE, 0.1 A)
   - 0x0A7: Bytes 0..1 = DC Bus Voltage (int16 LE, 0.1 V)
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
    ejecuta sobre WSL2 + Docker Desktop.
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


# ── 1. Constructores Energy Meter FSAE (Big Endian / Motorola) ───────────────

def build_em_0x10d_measurement(current_a: float, voltage_v: float) -> can.Message:
    """
    ID 0x10d (Measurement Message, Big Endian):
      - Bytes 0..3: Current [A] en IEEE 754 float BE (>f)
      - Bytes 4..7: Voltage [V] en IEEE 754 float BE (>f)
    """
    data = struct.pack(">ff", float(current_a), float(voltage_v))
    return can.Message(arbitration_id=0x10D, data=data, is_extended_id=False)


def build_em_0x40d_status(violation: int, logging: int, energy_whr: float) -> can.Message:
    """
    ID 0x40d (Status Message, Big Endian):
      - Byte 0: Bit 0 = Violation, Bit 1 = Logging
      - Bytes 1..3: Reservados (0x00)
      - Bytes 4..7: Energy [Whr] en IEEE 754 float BE (>f)
    """
    byte0 = (violation & 0x01) | ((logging & 0x01) << 1)
    data = struct.pack(">BBBBf", byte0, 0, 0, 0, float(energy_whr))
    return can.Message(arbitration_id=0x40D, data=data, is_extended_id=False)


def build_em_0x60d_temperature(min_temp_c: float, max_temp_c: float, num_sensors: int = 4) -> can.Message:
    """
    ID 0x60d (Temperature Message):
      - Byte 0: bits 0..2 = mux_id (0), bits 3..7 = num_sensors
      - Byte 1: Min Temp (uint8, factor 0.5 °C)
      - Byte 2: Max Temp (uint8, factor 0.5 °C)
    """
    mux_id = 0
    byte0 = (mux_id & 0x07) | ((num_sensors & 0x1F) << 3)
    raw_min = max(0, min(255, int(round(min_temp_c * 2.0))))
    raw_max = max(0, min(255, int(round(max_temp_c * 2.0))))
    data = struct.pack(">BBBBBBBB", byte0, raw_min, raw_max, 0, 0, 0, 0, 0)
    return can.Message(arbitration_id=0x60D, data=data, is_extended_id=False)


# ── 2. Constructores Orion BMS (Big Endian / Motorola) ───────────────────────

def build_bms_0x6b0_state(
    pack_current_a: float,
    pack_voltage_v: float,
    soc_pct: float,
    high_temp_c: int,
    low_temp_c: int,
    relay_status: int = 0x01,
) -> can.Message:
    """
    ID 0x6B0 (Estado del Pack BMS, Big Endian):
      - Bytes 0..1: Pack Current (int16 BE, 0.1 A)
      - Bytes 2..3: Pack Instantaneous Voltage (uint16 BE, 0.1 V)
      - Byte  4:    Pack SOC (uint8, 0.5 %)
      - Byte  5:    High Temperature (int8, °C)
      - Byte  6:    Low Temperature (int8, °C)
      - Byte  7:    Relay / Fault Status (uint8)
    """
    raw_curr = max(-32768, min(32767, int(round(pack_current_a * 10.0))))
    raw_volt = max(0, min(65535, int(round(pack_voltage_v * 10.0))))
    raw_soc = max(0, min(200, int(round(soc_pct * 2.0))))
    raw_tmax = max(-40, min(120, int(high_temp_c)))
    raw_tmin = max(-40, min(120, int(low_temp_c)))
    data = struct.pack(">hHBbbB", raw_curr, raw_volt, raw_soc, raw_tmax, raw_tmin, relay_status & 0xFF)
    return can.Message(arbitration_id=0x6B0, data=data, is_extended_id=False)


def build_bms_0x6b1_limits(
    dcl_a: int, ccl_a: int, high_cell_v: float, low_cell_v: float
) -> can.Message:
    """
    ID 0x6B1 (Límites de Corriente y Celdas BMS, Big Endian):
      - Bytes 0..1: Pack DCL (uint16 BE, A)
      - Bytes 2..3: Pack CCL (uint16 BE, A)
      - Bytes 4..5: High Cell Voltage (uint16 BE, 0.0001 V)
      - Bytes 6..7: Low Cell Voltage (uint16 BE, 0.0001 V)
    """
    raw_dcl = max(0, min(65535, int(dcl_a)))
    raw_ccl = max(0, min(65535, int(ccl_a)))
    raw_vhigh = max(0, min(65535, int(round(high_cell_v / 0.0001))))
    raw_vlow = max(0, min(65535, int(round(low_cell_v / 0.0001))))
    data = struct.pack(">HHHH", raw_dcl, raw_ccl, raw_vhigh, raw_vlow)
    return can.Message(arbitration_id=0x6B1, data=data, is_extended_id=False)


# ── 3. Constructores Inversor BorgWarner / Cascadia Motion (Little Endian!) ──

def build_inv_0x0a2_temps(inverter_temp_c: float, motor_temp_c: float) -> can.Message:
    """
    ID 0x0A2 (Temperaturas Inversor y Motor EMRAX, Little Endian):
      - Bytes 0..1: Inverter Module Temperature (int16 LE, 0.1 °C)
      - Bytes 4..5: Motor Temperature (int16 LE, 0.1 °C)
    """
    raw_inv = max(-32768, min(32767, int(round(inverter_temp_c * 10.0))))
    raw_mot = max(-32768, min(32767, int(round(motor_temp_c * 10.0))))
    data = struct.pack("<hhhh", raw_inv, raw_inv, raw_mot, 0)
    return can.Message(arbitration_id=0x0A2, data=data, is_extended_id=False)


def build_inv_0x0a5_speed(motor_speed_rpm: int) -> can.Message:
    """
    ID 0x0A5 (Posición y Velocidad del Motor, Little Endian):
      - Bytes 2..3: Motor Speed (int16 LE, RPM)
    """
    raw_rpm = max(-32768, min(32767, int(motor_speed_rpm)))
    data = struct.pack("<hhhh", 0, raw_rpm, 0, 0)
    return can.Message(arbitration_id=0x0A5, data=data, is_extended_id=False)


def build_inv_0x0a6_current(dc_bus_current_a: float) -> can.Message:
    """
    ID 0x0A6 (Corrientes del Inversor, Little Endian):
      - Bytes 6..7: DC Bus Current (int16 LE, 0.1 A)
    """
    raw_idc = max(-32768, min(32767, int(round(dc_bus_current_a * 10.0))))
    data = struct.pack("<hhhh", 0, 0, 0, raw_idc)
    return can.Message(arbitration_id=0x0A6, data=data, is_extended_id=False)


def build_inv_0x0a7_voltage(dc_bus_voltage_v: float) -> can.Message:
    """
    ID 0x0A7 (Voltaje DC del Inversor, Little Endian):
      - Bytes 0..1: DC Bus Voltage (int16 LE, 0.1 V)
    """
    raw_vdc = max(-32768, min(32767, int(round(dc_bus_voltage_v * 10.0))))
    data = struct.pack("<hhhh", raw_vdc, 0, 0, 0)
    return can.Message(arbitration_id=0x0A7, data=data, is_extended_id=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Emulador CAN Bus Kratos FSAE (Energy Meter BE + Orion BMS BE + BorgWarner LE)"
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
        help="Frecuencia de ciclo completo en Hz (por defecto: 5.0 Hz)",
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
    energy_whr = 125.0
    soc_pct = 90.0
    cycle = 0

    print(
        f"[Kratos CAN Simulator] Transmitiendo tramas oficiales:\n"
        f"  - Energy Meter (BE): 0x10d, 0x40d, 0x60d\n"
        f"  - Orion BMS    (BE): 0x6B0, 0x6B1\n"
        f"  - BorgWarner   (LE): 0x0A2, 0x0A5, 0x0A6, 0x0A7\n"
        f"Presiona Ctrl+C para detener."
    )
    try:
        while True:
            elapsed = time.monotonic() - t0
            cycle += 1

            # Simulación física sincronizada entre los 3 subsistemas
            voltage_v = 365.0 + 30.0 * math.sin(elapsed * 0.25) + random.uniform(-0.8, 0.8)
            voltage_v = max(300.0, min(400.0, voltage_v))

            current_a = 72.0 + 68.0 * math.sin(elapsed * 0.55) + random.uniform(-2.5, 2.5)
            power_kw = (voltage_v * current_a) / 1000.0

            energy_whr = max(0.0, energy_whr + (power_kw * 1000.0 * period_s) / 3600.0)
            soc_pct = max(15.0, 90.0 - (energy_whr - 125.0) * 0.015)

            violation = 1 if power_kw > 80.0 else 0
            logging = 1 if voltage_v > 60.0 else 0

            em_min_temp_c = 28.5 + 3.0 * math.sin(elapsed * 0.05)
            em_max_temp_c = em_min_temp_c + 4.5

            bms_low_temp = int(round(32.0 + 4.0 * math.sin(elapsed * 0.08)))
            bms_high_temp = bms_low_temp + random.choice([4, 5, 6])
            high_cell_v = (voltage_v / 96.0) + 0.018
            low_cell_v = (voltage_v / 96.0) - 0.015

            inv_temp_c = 41.5 + 7.5 * math.sin(elapsed * 0.12)
            motor_temp_c = 58.0 + 11.0 * math.sin(elapsed * 0.10)
            motor_rpm = max(0, int(round(3400 + 2100 * math.sin(elapsed * 0.55))))

            frames = [
                # Energy Meter (Big Endian)
                build_em_0x10d_measurement(current_a, voltage_v),
                build_em_0x40d_status(violation, logging, energy_whr),
                build_em_0x60d_temperature(em_min_temp_c, em_max_temp_c, num_sensors=4),
                # Orion BMS (Big Endian)
                build_bms_0x6b0_state(
                    pack_current_a=current_a + random.uniform(-0.3, 0.3),
                    pack_voltage_v=voltage_v + random.uniform(-0.4, 0.4),
                    soc_pct=soc_pct,
                    high_temp_c=bms_high_temp,
                    low_temp_c=bms_low_temp,
                    relay_status=0x01,
                ),
                build_bms_0x6b1_limits(
                    dcl_a=220,
                    ccl_a=45,
                    high_cell_v=high_cell_v,
                    low_cell_v=low_cell_v,
                ),
                # BorgWarner / Cascadia Motion Inverter (Little Endian)
                build_inv_0x0a2_temps(inv_temp_c, motor_temp_c),
                build_inv_0x0a5_speed(motor_rpm),
                build_inv_0x0a6_current(current_a + random.uniform(-0.5, 0.5)),
                build_inv_0x0a7_voltage(voltage_v + random.uniform(-0.5, 0.5)),
            ]

            for m in frames:
                if bus is not None:
                    try:
                        bus.send(m)
                    except Exception:
                        pass
                bridge.send_raw_can_frame(m)

            print(
                f"[TX #{cycle:04d}] "
                f"EM(BE) 0x10d: I={current_a:6.2f}A V={voltage_v:6.2f}V P={power_kw:5.2f}kW | "
                f"BMS(BE) 0x6B0: SOC={soc_pct:4.1f}% T={bms_low_temp}/{bms_high_temp}°C | "
                f"BW(LE) 0x0A5: {motor_rpm:4d}RPM Tmot={motor_temp_c:4.1f}°C"
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
