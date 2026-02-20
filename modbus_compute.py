import time

import serial


def compute_crc(data):
    crc = 0xFFFF
    for pos in data:
        crc ^= pos
        for i in range(8):
            if (crc & 1) != 0:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1

    return crc.to_bytes(2, "little")


def registers_compute(registers: bytes, count: int):
    decimal_registers = [
        int.from_bytes(registers[1 + 2 * i : 3 + 2 * i], byteorder="big")
        for i in range(1, count + 1)
        if count != 0
    ]
    return decimal_registers


READ_HOLDING_RGISTER = 3


class MasterModbusCompute:
    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        parity: str = serial.PARITY_NONE,
        stopbits: float = serial.STOPBITS_ONE,
        bytesize: int = serial.EIGHTBITS,
        timeout: int = 1,
    ):
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.bytesize = bytesize
        self.timeout = timeout

    def connect(self):
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            parity=self.parity,
            stopbits=self.stopbits,
            bytesize=self.bytesize,
            timeout=self.timeout,
        )

        if self.ser.is_open:
            print("Puerto conectado.")
            self.serial = self.ser
            return True
        else:
            print(f"No hubo respuesta del sensor. Puerto: {self.port}")
            self.serial = self.ser
            return False

    def read_holding_registers(self, slave_id: int, address: int = 0, count: int = 1):
        self.slave_id = slave_id
        self.function_code = READ_HOLDING_RGISTER
        self.address = address
        self.count = count
        self.plot = self.plot_base(
            self.slave_id, self.function_code, self.address, self.count
        )
        self.final_plot = self.plot + compute_crc(self.plot)
        self.ser.write(self.final_plot)
        time.sleep(self.timeout)
        holding_registers = self.serial.read(5 + 2 * self.count)
        if holding_registers:
            return registers_compute(holding_registers, self.count)
        else:
            print("No hubo respuesta del sensor")
            return []

    def plot_base(self, slave: int, function_code: int, address: int, count: int):
        plot_base = bytearray([slave, function_code])
        plot_base.extend(address.to_bytes(2, "big"))
        plot_base.extend(count.to_bytes(2, "big"))

        return plot_base
