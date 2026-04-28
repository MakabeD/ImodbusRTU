from __future__ import annotations

import time
from dataclasses import dataclass

import serial


if not hasattr(serial, "PARITY_NONE"):
    serial_module = getattr(serial, "__file__", "desconocido")
    raise ImportError(
        "Se importo un paquete incorrecto llamado 'serial' en lugar de 'pyserial'. "
        f"Modulo cargado: {serial_module}. "
        "Solucion: ejecuta '.\\.venv\\Scripts\\python.exe -m pip uninstall -y serial' "
        "y luego '.\\.venv\\Scripts\\python.exe -m pip install --force-reinstall pyserial==3.5'."
    )


READ_HOLDING_REGISTER = 3


@dataclass(frozen=True)
class RegisterValue:
    slave_id: int
    address: int
    value: int


@dataclass(frozen=True)
class VariableCandidate:
    slave_id: int
    address: int
    first_value: int
    second_value: int


def compute_crc(data: bytes) -> bytes:
    crc = 0xFFFF
    for pos in data:
        crc ^= pos
        for _ in range(8):
            if (crc & 1) != 0:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1

    return crc.to_bytes(2, "little")


def registers_compute(registers: bytes, count: int) -> list[int]:
    if count <= 0:
        return []

    decimal_registers = [
        int.from_bytes(registers[3 + 2 * i : 5 + 2 * i], byteorder="big")
        for i in range(count)
    ]
    return decimal_registers


def error_bit_validation(registers: bytes) -> list[int] | None:
    funcion_recibida = registers[1]

    if funcion_recibida >= 0x80:
        codigo_error = registers[2]
        errores = {
            1: "01 Funcion ilegal (Comando no soportado)",
            2: "02 Direccion ilegal (El registro no existe o pediste demasiados)",
            3: "03 Valor de datos ilegal",
            4: "04 Fallo interno del dispositivo",
        }
        mensaje = errores.get(codigo_error, f"Error desconocido: {codigo_error}")
        print(f"Excepcion Modbus detectada: {mensaje}")
        return []

    return None


def validate_response_crc(response: bytes) -> bool:
    if len(response) < 5:
        return False

    message_no_crc = response[:-2]
    crc_received = response[-2:]
    crc_computed = compute_crc(message_no_crc)

    return crc_received == crc_computed


def find_variable_candidates(
    first_snapshot: list[RegisterValue],
    second_snapshot: list[RegisterValue],
) -> list[VariableCandidate]:
    first_map = {(item.slave_id, item.address): item.value for item in first_snapshot}
    second_map = {(item.slave_id, item.address): item.value for item in second_snapshot}

    candidates: list[VariableCandidate] = []
    for key in sorted(first_map.keys() & second_map.keys()):
        first_value = first_map[key]
        second_value = second_map[key]
        if first_value != second_value:
            candidates.append(
                VariableCandidate(
                    slave_id=key[0],
                    address=key[1],
                    first_value=first_value,
                    second_value=second_value,
                )
            )

    return candidates


class MasterModbusCompute:
    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        parity: str = serial.PARITY_NONE,
        stopbits: float = serial.STOPBITS_ONE,
        bytesize: int = serial.EIGHTBITS,
        timeout: float = 1,
    ):
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.bytesize = bytesize
        self.timeout = timeout
        self.serial: serial.Serial | None = None

    def __enter__(self) -> "MasterModbusCompute":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def connect(self) -> bool:
        if self.serial and self.serial.is_open:
            return True

        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                parity=self.parity,
                stopbits=self.stopbits,
                bytesize=self.bytesize,
                timeout=self.timeout,
            )
            print(f"Puerto {self.port} conectado exitosamente.")
            return True
        except serial.SerialException as error:
            print(f"Error critico: No se pudo abrir el puerto {self.port}.")
            print(f"Detalle tecnico: {error}")
            self.serial = None
            return False

    def disconnect(self):
        if self.serial and self.serial.is_open:
            self.serial.close()
            print(f"Puerto {self.port} cerrado y liberado de forma segura.")

    def read_holding_registers(self, slave_id: int, address: int = 0, count: int = 1):
        if self.serial is None or not self.serial.is_open:
            return []

        request = self.plot_base(slave_id, READ_HOLDING_REGISTER, address, count)
        frame = request + compute_crc(request)
        expected_length = 5 + 2 * count

        try:
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
            self.serial.write(frame)
            time.sleep(self.timeout)
            holding_registers = self.serial.read(expected_length)
        except serial.SerialException as error:
            print(f"Fallo de comunicacion con el esclavo {slave_id}: {error}")
            return []

        if not holding_registers:
            return []

        if not validate_response_crc(holding_registers):
            print(
                f"Error de CRC al leer esclavo {slave_id}, direccion {address}. "
                "La trama pudo llegar corrupta."
            )
            return []

        error_response = error_bit_validation(holding_registers)
        if error_response == []:
            return []

        return registers_compute(holding_registers, count)

    def probe_slave(self, slave_id: int, probe_address: int = 0, count: int = 1) -> bool:
        return bool(
            self.read_holding_registers(
                slave_id=slave_id,
                address=probe_address,
                count=count,
            )
        )

    def scan_slave_addresses(
        self,
        slave_start: int = 1,
        slave_end: int = 247,
        probe_address: int = 0,
        count: int = 1,
        delay: float = 0.05,
    ) -> list[int]:
        found_slaves: list[int] = []
        for slave_id in range(slave_start, slave_end + 1):
            if self.probe_slave(slave_id, probe_address=probe_address, count=count):
                found_slaves.append(slave_id)
            if delay > 0:
                time.sleep(delay)

        return found_slaves

    def scan_registers(
        self,
        slave_id: int,
        register_start: int,
        register_end: int,
        count: int = 1,
        delay: float = 0.02,
    ) -> list[RegisterValue]:
        found_registers: list[RegisterValue] = []
        for address in range(register_start, register_end + 1):
            values = self.read_holding_registers(
                slave_id=slave_id,
                address=address,
                count=count,
            )
            if values:
                found_registers.append(
                    RegisterValue(
                        slave_id=slave_id,
                        address=address,
                        value=values[0],
                    )
                )
            if delay > 0:
                time.sleep(delay)

        return found_registers

    @staticmethod
    def plot_base(slave: int, function_code: int, address: int, count: int) -> bytearray:
        plot_base = bytearray([slave, function_code])
        plot_base.extend(address.to_bytes(2, "big"))
        plot_base.extend(count.to_bytes(2, "big"))
        return plot_base
