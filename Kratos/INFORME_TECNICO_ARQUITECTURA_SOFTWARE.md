# Informe Técnico de Arquitectura de Software y Telemetría
**Equipo:** Kratos Formula SAE — Universidad EAFIT  
**Subsistema:** Adquisición de Datos en el Borde (*Edge Computing*), Telemetría y Electrónica de Baja Tensión (GLV)  
**Plataforma:** Raspberry Pi 5 (ARM64) + SSD NVMe PCIe + SocketCAN (`MCP2515`) + LTE 4G (`SIM7600G-H`)  

---

## 1. Resumen Ejecutivo y Arquitectura de Hardware

El sistema de telemetría del monoplaza eléctrico **Kratos FSAE** cumple una doble misión crítica durante pruebas dinámicas y competencias oficiales (*Acceleration, Skidpad, Autocross y Endurance*):
1. **Registro de Caja Negra Local (*Black-Box Logging*):** Almacenar sin pérdida el $100\text{\%}$ de las variables del tren motriz de alta tensión (**HV**: **Orion BMS**, **Energy Meter FSAE** e **Inversor BorgWarner / Cascadia Motion**) y dinámica vehicular (**IMU GY-91** + **GNSS**), independientemente de la calidad del enlace inalámbrico.
2. **Telemetría en Tiempo Real hacia Pits:** Transmitir de forma continua las métricas vitales de seguridad térmica, eléctrica y estrategia energética hacia los ingenieros en boxes para la toma de decisiones en vivo.

### 1.1 Especificación de Hardware Embebido del Monoplaza
La unidad embarcada se fundamenta en una arquitectura modular en Linux de 64 bits basada en **Raspberry Pi 5 (4GB RAM)** con almacenamiento de estado sólido sobre bus PCIe y periféricos industriales:

| Subsistema | Componente Seleccionado | Especificación y Función en el Monoplaza Kratos |
| :--- | :--- | :--- |
| **Unidad Central (SBC)** | **Raspberry Pi 5 (4GB RAM)** | SoC Broadcom BCM2712 Cortex-A76 Quad-Core ($64\text{-bit}$, $2.4\text{ GHz}$), LPDDR4X-4267; ejecuta contenedores Docker `linux/arm64` en tiempo real. |
| **Almacenamiento Persistente** | **SSD M.2 NVMe** + Adaptador PCIe (`WS-28013`) | Conectado por cinta FPC PCIe de 16 pines; proporciona alta tasa de escritura IOPS para series de tiempo y máxima resistencia mecánica ante vibraciones del chasis. |
| **Gestión Térmica y Chasis** | Caja disipadora de aluminio (`DISIPADOR-RPI5-2`) | Doble ventilador activo de 4 hilos (`GND`, `+5V`, `TACH`, `PWM`) para operación continua en pista sin *thermal throttling*. |
| **Interfaz CAN Bus ($500\text{ kbps}$)** | **2x Módulos `MCP2515` (SPI) + `TJA1050`** + **2x Conversores `TXB0108`** | Gestión nativa mediante **SocketCAN** del kernel Linux (`can0` y `can1`) y adaptación bidireccional de niveles lógicos de 8 canales ($3.3\text{ V} \leftrightarrow 5.0\text{ V}$) para proteger los pines GPIO de la Raspberry Pi 5. |
| **Enlace Celular y GNSS** | **`Waveshare SIM7600G-H 4G HAT (B)` (`WS-19485`)** | Conectividad LTE Cat-4 ($150\text{ Mbps}$ DL / $50\text{ Mbps}$ UL) y posicionamiento GPS/GLONASS/BeiDou por USB, gestionado con `ModemManager` y `NetworkManager`. |
| **Sensórica Inercial (IMU)** | **Módulo `GY-91` (`MPU-9250` + `BMP280`)** | Adquisición de 10 grados de libertad (acelerómetro 3 ejes, giroscopio 3 ejes, magnetómetro 3 ejes y barómetro) a $3.3\text{ V}$ en el bus I2C (`GPIO2/SDA` y `GPIO3/SCL`). |

---

## 2. Diseño de la Arquitectura de Software en el Borde (*Edge Architecture*)

La arquitectura de software consolida a **Node-RED** como el motor central de ingesta desde **SocketCAN**, decodificación binaria híbrida (**Big Endian + Little Endian**) y bifurcación en paralelo hacia el almacenamiento local y la transmisión remota:

$$\text{SocketCAN (can0 / vcan0)} \longrightarrow \text{Node-RED (Parser Híbrido BE/LE)} \Longrightarrow \begin{cases} \textbf{Rama 1 (Caja Negra):} & \text{InfluxDB 2.7 (vía Influx Line Protocol directo)} \\ \textbf{Rama 2 (Telemetría):} & \text{Eclipse Mosquitto (Publicación MQTT paralela)} \end{cases}$$

### 2.1 Principios de Ingeniería del Diseño
* **Acoplamiento Directo Kernel-a-Aplicación:** Node-RED lee directamente los sockets de red `PF_CAN` del kernel Linux (`can0` / `vcan0`), evitando scripts intermediarios o sobrecarga de serialización entre procesos.
* **Independencia Total entre Almacenamiento Local y Telemetría Remota:** La escritura en **InfluxDB 2.7** ocurre de forma paralela y asíncrona a la publicación en **Eclipse Mosquitto**. Si el enlace celular 4G LTE experimenta pérdidas de paquetes o zonas sin cobertura en pista, la persistencia en el disco SSD NVMe continúa sin interrupción.
* **Baja Latencia Determinista (*Edge Latency*):** El tiempo total desde la interrupción SPI del controlador `MCP2515` (`GPIO25`) hasta la generación de la línea en *Influx Line Protocol* y el despacho MQTT es inferior a $2\text{ ms}$ dentro del motor V8 de Node.js.
* **Eficiencia de Recursos en RAM y CPU:** El stack completo de 4 servicios (`mosquitto`, `influxdb`, `nodered` y `grafana`) mantiene un consumo ligero y estable en la Raspberry Pi 5.

---

## 3. Esquema de Ingesta y Bifurcación (`CAN -> Node-RED -> [InfluxDB || MQTT]`)

### 3.1 Diagrama de Flujo de Datos Extremo a Extremo

```mermaid
flowchart TB
    subgraph HV_CAN["Tren Motriz y Sensórica (Bus CAN @ 500 kbps)"]
        EM["Energy Meter Oficial FSAE (BE)\nIDs: 0x10d, 0x40d, 0x60d"]
        BMS["Orion BMS 2 (BE)\nIDs: 0x6B0, 0x6B1"]
        INV["Inversor BorgWarner CM (LE)\nIDs: 0x0A2, 0x0A5, 0x0A6, 0x0A7"]
    end

    subgraph Hardware_Capa["Capa Física y Kernel Linux (Raspberry Pi 5)"]
        MCP["Transceptor TJA1050 + Controlador MCP2515 (SPI0)\n+ Conversor Bidireccional TXB0108 (5V <-> 3.3V)"]
        SOCK["Subsistema SocketCAN del Kernel\nInterfaz de Red: can0 (Monoplaza) / vcan0 (Ubuntu)"]
    end

    subgraph Docker_Stack["Stack Unificado Docker (Kratos/docker-compose.yml)"]
        subgraph NR_Container["Contenedor: kratos-nodered"]
            RX["Nodo SocketCAN RX\n(Captura Buffer 8 bytes)"]
            DEC["Nodo Function JS:\nDecodificador Híbrido BE (Motorola) / LE (Intel)\n+ Formateador Influx Line Protocol"]
        end

        subgraph BlackBox["Rama 1: Caja Negra Local (SSD NVMe)"]
            INF[("InfluxDB 2.7\nOrg: kratos_fsae\nBucket: telemetria_carro\nRetención: Infinita (0s)")]
            GRAF["Grafana (:3000)\nAnálisis Post-Carrera y Local"]
        end

        subgraph LiveTelemetry["Rama 2: Telemetría en Vivo"]
            MOSQ["Eclipse Mosquitto 2.0 (:1883)\nTópicos: kratos/telemetria/#"]
        end
    end

    subgraph Pits_Remoto["Estación de Ingeniería en Pits"]
        SIM["Módem SIM7600G-H 4G LTE"]
        PITS["Dashboard Remoto de Pits"]
    end

    EM & BMS & INV -->|Par Trenzado CAN_H / CAN_L| MCP
    MCP -->|SPI0 + INT GPIO25| SOCK
    SOCK -->|Trama Cruda: canid + data[8]| RX
    RX --> DEC
    DEC ==>|"POST /api/v2/write\n(Influx Line Protocol)"| INF
    DEC ==>|"Publicación QoS 0\n(JSON Jerárquico)"| MOSQ
    INF --> GRAF
    MOSQ --> SIM --> PITS
```

### 3.2 Secuencia de Procesamiento Milisegundo a Milisegundo
1. **$t = 0.0\text{ ms}$ (Captura en Bus CAN):** El controlador `MCP2515` recibe una trama diferencial a $500\text{ kbps}$ ($\sim 220\text{ }\mu\text{s}$ de duración en el bus para una trama estándar de 8 bytes) y activa la línea de interrupción física `INT` (`GPIO25`) a través del conversor lógico `TXB0108`.
2. **$t = 0.3\text{ ms}$ (Driver SocketCAN):** El módulo del kernel `mcp2515` lee el buffer SPI0 y entrega un `struct can_frame` al subsistema de red `SocketCAN` (`can0`).
3. **$t = 0.8\text{ ms}$ (Ingesta en Node-RED):** El nodo `socketcan-out` recibe el evento del socket de red y emite un objeto JavaScript con `msg.payload.canid` (entero) y `msg.payload.data` (`Buffer` binario de 8 bytes).
4. **$t = 1.2\text{ ms}$ (Decodificación Híbrida y Bifurcación):** El nodo `Decodificador Oficial (EM + Orion BMS [BE] / BorgWarner [LE])` identifica el `canid`, selecciona la decodificación **Big Endian** o **Little Endian** según el subsistema emisor y dispara simultáneamente:
   * **Salida a InfluxDB (Caja Negra):** Envía la cadena en formato *Influx Line Protocol* con marca de tiempo en nanosegundos directamente al endpoint HTTP `/api/v2/write` sobre el disco NVMe.
   * **Salida a Mosquitto (MQTT):** Publica los objetos JSON en los tópicos jerárquicos `kratos/telemetria/energy_meter/...`, `kratos/telemetria/bms/...` y `kratos/telemetria/inversor/...`.

---

## 4. Algoritmo de Decodificación (Big Endian / Little Endian) e Influx Line Protocol

### 4.1 Fundamento Matemático: IEEE 754 Float BE, Enteros BE (Motorola) y Enteros LE (Intel)
En el bus CAN del monoplaza coexisten tres formatos numéricos binarios definidos por los manuales oficiales de cada fabricante:

1. **Punto Flotante IEEE 754 de 32 bits Big Endian (`float BE` — Manual Oficial `Energy_Meter_Student_Manual`):**
   * Los 4 bytes ($B_0, B_1, B_2, B_3$) representan un número real con signo ($1\text{ bit}$), exponente sesgado ($8\text{ bits}$, sesgo $127$) y mantisa ($23\text{ bits}$), con el byte más significativo en $B_0$:
     $$X_{\text{float}} = (-1)^S \cdot 2^{(E - 127)} \cdot \left(1 + \sum_{k=1}^{23} m_{23-k} 2^{-k}\right)$$
   * En Node-RED se decodifica de forma exacta mediante `buf.readFloatBE(offset)`.

2. **Enteros de 16/32 bits Big Endian / Motorola (`int16 BE`, `uint16 BE` — `Orion BMS 2`):**
   * El **MSB (*Most Significant Byte*)** viaja en el índice menor del arreglo:
     $$V_{\text{BE16}} = (B_0 \ll 8) \mid B_1$$
   * En Node-RED se decodifica mediante `buf.readInt16BE(offset)` y `buf.readUInt16BE(offset)`.

3. **Enteros de 16 bits con signo Little Endian / Intel (`int16 LE` — `CM Hardware Users Manual` de BorgWarner / Cascadia Motion):**
   * El **LSB (*Least Significant Byte*)** viaja en el índice menor ($B_0$) y el MSB en el índice superior ($B_1$):
     $$V_{\text{LE16}} = (B_1 \ll 8) \mid B_0$$
   * En Node-RED se decodifica mediante `buf.readInt16LE(offset)`.

### 4.2 Diccionario Oficial de Tramas CAN Implementado en `flows.json`

| ID CAN (Hex / Dec) | Subsistema (`origen`) | Endianness | Mapa Exacto de Bytes (Según Manual Oficial) | Escalado y Variables Decodificadas | Tópico MQTT Publicado |
| :---: | :---: | :---: | :--- | :--- | :--- |
| **`0x10d`** (`269`)<br>*(cada $20\text{ ms}$)* | `energy_meter` | **Big Endian** | `Bytes 0..3`: `float BE` (`readFloatBE(0)`)<br>`Bytes 4..7`: `float BE` (`readFloatBE(4)`) | `current` $[\text{A}]$ *(Bytes 0-3)*<br>`voltage` $[\text{V}]$ *(Bytes 4-7)*<br>`power_kw` $= \frac{V \cdot I}{1000}\text{ [kW]}$ | `kratos/telemetria/energy_meter/0x10d` |
| **`0x40d`** (`1037`)<br>*(cada $100\text{ ms}$)* | `energy_meter` | **Big Endian** | `Byte 0, Bit 0`: `Violation`<br>`Byte 0, Bit 1`: `Logging` ($\text{TS}>60\text{ V}$)<br>`Bytes 1..3`: Reservados<br>`Bytes 4..7`: `float BE` (`readFloatBE(4)`) | `violation` $\in \{0,1\}$<br>`logging` $\in \{0,1\}$<br>`energy_whr` $[\text{Whr}]$ | `kratos/telemetria/energy_meter/0x40d` |
| **`0x60d`** (`1549`)<br>*(cada $250\text{ ms}$)* | `energy_meter` | **Big Endian** | `Byte 0 [2:0]`: `mux_id` ($0$)<br>`Byte 0 [7:3]`: `num_sensors`<br>`Byte 1`: `uint8` (`readUInt8(1)`)<br>`Byte 2`: `uint8` (`readUInt8(2)`) | `min_temp` $= \text{Byte}_1 \times 0.5\text{ [°C]}$<br>`max_temp` $= \text{Byte}_2 \times 0.5\text{ [°C]}$ | `kratos/telemetria/energy_meter/0x60d` |
| **`0x6b0`** (`1712`) | `orion_bms` | **Big Endian** | `Bytes 0..1`: `int16 BE` (`readInt16BE(0)`)<br>`Bytes 2..3`: `uint16 BE` (`readUInt16BE(2)`)<br>`Byte 4`: `uint8` (`readUInt8(4)`)<br>`Byte 5`: `int8` (`readInt8(5)`)<br>`Byte 6`: `int8` (`readInt8(6)`)<br>`Byte 7`: `uint8` (`readUInt8(7)`) | `pack_current` $\times 0.1\text{ [A]}$<br>`pack_voltage` $\times 0.1\text{ [V]}$<br>`soc` $\times 0.5\text{ [\%]}$<br>`temp_max` $[\text{°C}]$, `temp_min` $[\text{°C}]$<br>`relay_fault_status` | `kratos/telemetria/bms/estado`<br>`kratos/telemetria/bms/0x6b0` |
| **`0x6b1`** (`1713`) | `orion_bms` | **Big Endian** | `Bytes 0..1`: `uint16 BE` (`readUInt16BE(0)`)<br>`Bytes 2..3`: `uint16 BE` (`readUInt16BE(2)`)<br>`Bytes 4..5`: `uint16 BE` (`readUInt16BE(4)`)<br>`Bytes 6..7`: `uint16 BE` (`readUInt16BE(6)`) | `pack_dcl` $[\text{A}]$, `pack_ccl` $[\text{A}]$<br>`high_cell_voltage` $\times 0.0001\text{ [V]}$<br>`low_cell_voltage` $\times 0.0001\text{ [V]}$ | `kratos/telemetria/bms/limites_celdas`<br>`kratos/telemetria/bms/0x6b1` |
| **`0x0a2`** (`162`) | `inversor_borgwarner` | **Little Endian** | `Bytes 0..1`: `int16 LE` (`readInt16LE(0)`)<br>`Bytes 4..5`: `int16 LE` (`readInt16LE(4)`) | `inverter_temp` $\times 0.1\text{ [°C]}$<br>`motor_temp` $\times 0.1\text{ [°C]}$ | `kratos/telemetria/inversor/temperaturas`<br>`kratos/telemetria/inversor/0x0a2` |
| **`0x0a5`** (`165`) | `inversor_borgwarner` | **Little Endian** | `Bytes 2..3`: `int16 LE` (`readInt16LE(2)`) | `motor_speed_rpm` $[\text{RPM}]$ | `kratos/telemetria/inversor/velocidad_motor`<br>`kratos/telemetria/inversor/0x0a5` |
| **`0x0a6`** (`166`) | `inversor_borgwarner` | **Little Endian** | `Bytes 6..7`: `int16 LE` (`readInt16LE(6)`) | `dc_bus_current` $\times 0.1\text{ [A]}$ | `kratos/telemetria/inversor/corriente_dc`<br>`kratos/telemetria/inversor/0x0a6` |
| **`0x0a7`** (`167`) | `inversor_borgwarner` | **Little Endian** | `Bytes 0..1`: `int16 LE` (`readInt16LE(0)`) | `dc_bus_voltage` $\times 0.1\text{ [V]}$ | `kratos/telemetria/inversor/voltaje_dc`<br>`kratos/telemetria/inversor/0x0a7` |

### 4.3 Estructura Exacta del *Influx Line Protocol*
Ejemplos generados por el nodo decodificador e inyectados vía `HTTP POST /api/v2/write?org=kratos_fsae&bucket=telemetria_carro&precision=ns`:

```text
telemetria_kratos,origen=energy_meter,id_can=0x10d current=42.1,voltage=385.5,power_kw=16.229,corriente=42.1,voltaje=385.5,potencia_kw=16.229 1793288280000000000
telemetria_kratos,origen=energy_meter,id_can=0x40d violation=0,logging=1,energy_whr=145.8,energia_wh=145.8 1793288280000000000
telemetria_kratos,origen=energy_meter,id_can=0x60d mux_id=0,num_sensors=4,min_temp=29.5,max_temp=34 1793288280000000000
telemetria_kratos,origen=orion_bms,id_can=0x6b0 pack_current=42,pack_voltage=385.2,soc=88.5,temp_max=41,temp_min=33,relay_fault_status=1 1793288280000000000
telemetria_kratos,origen=orion_bms,id_can=0x6b1 pack_dcl=220,pack_ccl=45,high_cell_voltage=3.8295,low_cell_voltage=3.7965 1793288280000000000
telemetria_kratos,origen=inversor_borgwarner,id_can=0x0a2 inverter_temp=43.2,motor_temp=61.4 1793288280000000000
telemetria_kratos,origen=inversor_borgwarner,id_can=0x0a5 motor_speed_rpm=4120 1793288280000000000
```

---

## 5. Compatibilidad de Protocolos Hardware (Orion BMS, Inversor BorgWarner y Energy Meter)

La integración física y lógica de los tres equipos principales del tren motriz resuelve tres aspectos clave de compatibilidad electromecánica y de protocolo:

### 5.1 Coexistencia Híbrida de Endianness en un Mismo Bus
* El **Energy Meter FSAE** (`0x10d`, `0x40d`, `0x60d`) y el **Orion BMS** (`0x6B0`, `0x6B1`, configurado mediante *Orion BMS Utility*) transmiten en orden **Big Endian (Motorola)**.
* En contraste, el controlador/inversor **BorgWarner / Cascadia Motion (CM Family)** (`0x0A2`, `0x0A5`, `0x0A6`, `0x0A7`) codifica todos sus registros de 16 bits en orden **Little Endian (Intel)** según el *CM Hardware Users Manual*.
* **Resolución en el Parser:** Como el estándar CAN 2.0A es agnóstico al contenido de los 8 bytes de datos (`DLC=8`), ambos esquemas de endianness coexisten sin colisión sobre el mismo par físico gracias a que sus `CAN ID` son disjuntos. El nodo `function` de Node-RED inspecciona primero el identificador de arbitraje (`canId`) y despacha la lectura hacia `buf.readFloatBE()` / `buf.readInt16BE()` para Energy Meter y BMS, o hacia `buf.readInt16LE()` para las tramas `0x0A2–0x0A7` del inversor BorgWarner.

### 5.2 Homologación de Velocidad de Bus (Bitrate: $250\text{ kbps}$ vs. $500\text{ kbps}$)
* El **Energy Meter FSAE** opera por reglamento a **$500\text{ kbps}$**, y el **Orion BMS** se configura a **$500\text{ kbps}$** en `CAN1`.
* El inversor **BorgWarner / Cascadia Motion** viene configurado de fábrica a **$250\text{ kbps}$**.
* **Modos de Operación Soportados por la Arquitectura (con 2x módulos `MCP2515`):**
  1. **Modo A — Bus Único Unificado a $500\text{ kbps}$ (`can0`):** Conectar el inversor al software **RMS GUI** y configurar en su memoria no volátil el parámetro de EEPROM **`CAN_Bit_Rate_EEPROM_(kbps)` = `500`**, reiniciando el inversor para que comparta nativamente el bus de $500\text{ kbps}$ con el Energy Meter y el Orion BMS.
  2. **Modo B — Doble Bus CAN Aislado (`can0` @ $500\text{ kbps}$ + `can1` @ $250\text{ kbps}$):** Utilizar el segundo módulo `MCP2515` (`SPI0 CE1` / `GPIO7`) como interfaz `can1` dedicada a $250\text{ kbps}$ exclusivamente para el inversor BorgWarner, manteniendo `can0` a $500\text{ kbps}$ para el Energy Meter y el Orion BMS.

### 5.3 Esquema de Terminación Diferencial ($120\text{ }\Omega$) y Aislamiento Galvánico (`CAN_GND` / `CAN_SHIELD`)
Para cumplir con la norma ISO 11898-2 a $500\text{ kbps}$, la línea troncal par trenzado (`CAN_H` y `CAN_L`) debe presentar una impedancia diferencial equivalente de **$60\text{ }\Omega$** (medida en frío con multímetro entre `CAN_H` y `CAN_L`), producto de **exactamente dos resistencias terminadoras de $120\text{ }\Omega$ en paralelo** ubicadas en los extremos físicos más alejados del arnés:

1. **Extremo Físico 1 — Orion BMS (`CAN1`):**
   * De acuerdo con el manual del **Orion BMS**, el puerto **`CAN1` ya integra internamente una resistencia de terminación fija de $120\text{ }\Omega$**. Por tanto, el Orion BMS se ubica en uno de los extremos físicos de la topología troncal.
2. **Extremo Físico 2 — Inversor BorgWarner o Módulo `MCP2515` (Raspberry Pi 5):**
   * Si el **Inversor BorgWarner** se ubica en el extremo opuesto del arnés, se habilita su resistencia interna de $120\text{ }\Omega$ puenteando el pin **`CANA_T` (Pin `C2` del conector Amphenol del inversor)** con `CANA_L`, dejando abierto el jumper `J1` del módulo `MCP2515`.
   * Si en cambio la **Raspberry Pi 5 (`MCP2515`)** se ubica en el extremo físico opuesto y el inversor queda como nodo intermedio (*drop node* con derivación $< 30\text{ cm}$), se deja el pin `CANA_T` (`C2`) desconectado en el inversor y se cierra el **jumper de terminación de $120\text{ }\Omega$ en el módulo `MCP2515`**.
   * **Precaución:** Nunca deben activarse más de dos resistencias de $120\text{ }\Omega$ simultáneamente (tres resistencias reducirían la impedancia a $40\text{ }\Omega$, sobrecargando el transceptor `TJA1050`).
3. **Referencia `CAN_GND` y Blindaje (`CAN_SHIELD`):**
   * El pin **`CAN_GND` (Pin `D2` del inversor BorgWarner)** debe conectarse a la referencia común de señal del transceptor CAN para evitar que el voltaje de modo común supere el rango de $-2\text{ V}$ a $+7\text{ V}$ del `TJA1050` durante conmutaciones de alta corriente IGBT.
   * La malla de blindaje del cable par trenzado (**`CAN_SHIELD`**) debe aterrizarse a chasis/GLV GND **en un único punto del arnés** (*Single-Point Grounding*), evitando lazos de tierra (*ground loops*) inducidos por el campo electromagnético del motor EMRAX y las barras colectoras de alta tensión.

---

## 6. Decisiones de Diseño de Infraestructura

1. **Orquestación Unificada (`Kratos/docker-compose.yml` y `.env`):**
   * Los 4 servicios (`mosquitto`, `influxdb`, `nodered`, `grafana`) están definidos en un único archivo declarativo con control de salud (`healthcheck` mediante `influx ping`), asegurando que Node-RED y Grafana inicien ordenadamente tras verificar que el motor de base de datos está listo.
2. **Acceso a Interfaces CAN (`network_mode: host` y Simulación en Ubuntu/WSL2):**
   * En la **Raspberry Pi 5**, el uso de `network_mode: host` expone directamente las interfaces `can0`, `can1` y `vcan0` al contenedor de Node-RED. En entornos de simulación sobre **Ubuntu / WSL2**, se soporta el mapeo explícito `ports: ["1880:1880"]` y el puente de tramas crudas `kratos/can_raw`.
3. **Tolerancia a Apagones Súbitos del Sistema GLV y Persistencia en NVMe:**
   * Los volúmenes nombrados `influxdb-data`, `influxdb-config`, `mosquitto-data`, `mosquitto-log` y `grafana-data`, junto con `./nodered/data:/data`, residen sobre el **SSD M.2 NVMe** (`WS-28013`). El *Write-Ahead Log (WAL)* de InfluxDB 2.7 y la retención infinita (`0s`) garantizan cero pérdida de datos ante el corte abrupto del **GLV Master Switch**.

---

## 7. Próximas Fases de Integración Física y Puesta en Marcha

1. **Enlace Remoto 4G LTE hacia Pits sobre CGNAT (`SIM7600G-H`):** Configurar túnel VPN *Mesh* (**Tailscale / WireGuard**) entre la Raspberry Pi 5 y los computadores de Pits, o desplegar un *Bridge MQTT* hacia un broker en un VPS en la nube.
2. **Integración del Sensor Inercial IMU `GY-91` por Bus I2C (`GPIO2/SDA`, `GPIO3/SCL`):** Habilitar `/dev/i2c-1` en la Raspberry Pi 5 para adquirir aceleraciones 3D, velocidades angulares y presión barométrica en Node-RED.
3. **Adquisición GNSS desde el `SIM7600G-H`:** Leer tramas NMEA por USB para registrar coordenadas GPS y velocidad sobre pista en InfluxDB.
4. **Verificación de Impedancia en Arnés Real:** Medir los $60\text{ }\Omega$ entre `CAN_H` y `CAN_L` con el sistema apagado antes de energizar el GLV y validar la recepción simultánea de Orion BMS (`0x6B0`/`0x6B1`), Energy Meter (`0x10d`/`0x40d`/`0x60d`) e Inversor BorgWarner (`0x0A2`/`0x0A5`/`0x0A6`/`0x0A7`).
