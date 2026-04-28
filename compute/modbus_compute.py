import time

import serial


if not hasattr(serial, "PARITY_NONE"):
    serial_module = getattr(serial, "__file__", "desconocido")
    raise ImportError(
        "Se importo un paquete incorrecto llamado 'serial' en lugar de 'pyserial'. "
        f"Modulo cargado: {serial_module}. "
        "Solucion: ejecuta '.\\.venv\\Scripts\\python.exe -m pip uninstall -y serial' "
        "y luego '.\\.venv\\Scripts\\python.exe -m pip install --force-reinstall pyserial==3.5'."
    )


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


def error_bit_validation(registers):
    # El byte en la posicion 1 es el codigo de funcion (ej. 0x03 o 0x83)
    funcion_recibida = registers[1]

    # Verificamos si tiene el bit de error encendido (0x80 = 128 decimal)
    if funcion_recibida >= 0x80:
        codigo_error = registers[2]
        errores = {
            1: "01 Función Ilegal (Comando no soportado)",
            2: "02 Dirección Ilegal (El registro no existe o pediste demasiados)",
            3: "03 Valor de Datos Ilegal",
            4: "04 Fallo interno del dispositivo",
        }
        mensaje = errores.get(codigo_error, f"Error desconocido: {codigo_error}")
        print(f"Excepción Modbus detectada: {mensaje}")
        return []  # Retornamos vacio porque no hay datos que procesar


def validate_response_crc(response: bytes) -> bool:
    """Valida que la trama recibida no esté corrupta usando el CRC."""
    #  5 bytes (ID, Func, Count, CRC_L, CRC_H)
    if len(response) < 5:
        return False

    menssage_no_crc = response[:-2]

    crc_received = response[-2:]

    crc_computed = compute_crc(menssage_no_crc)

    return crc_received == crc_computed


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

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.serial and self.serial.is_open:
            self.serial.close()
            print(f"Puerto {self.port} cerrado y liberado de forma segura.")

    def connect(self) -> bool:
        try:
            # Intentamos abrir el puerto
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

        except serial.SerialException as e:
            # Si explota (puerto no existe, o está ocupado), caemos aquí
            print(f"Error critico: No se pudo abrir el puerto {self.port}.")
            print(f"Detalle tecnico: {e}")
            self.serial = None
            return False

    def read_holding_registers(self, slave_id: int, address: int = 0, count: int = 1):
        if self.serial is None:
            return
        function_code = READ_HOLDING_RGISTER
        address = address
        count = count
        plot = self.plot_base(slave_id, function_code, address, count)
        final_plot = plot + compute_crc(plot)
        self.serial.write(final_plot)
        time.sleep(self.timeout)
        holding_registers = self.serial.read(5 + 2 * count)
        if holding_registers:
            if validate_response_crc(holding_registers):
                error_bit_validation(holding_registers)
                return registers_compute(holding_registers, count)
            else:
                print("Error de CRC: Hay ruido en el cable o la trama llegó corrupta")
                return []
        else:
            print("No hubo respuesta del sensor")
            return []

    def disconnect(self):
        if self.serial is None:
            raise
        if hasattr(self, "Serial") and self.serial.is_open:
            self.serial.close()
            print("Puerto cerrado correctamente.")

    def plot_base(self, slave: int, function_code: int, address: int, count: int):
        plot_base = bytearray([slave, function_code])
        plot_base.extend(address.to_bytes(2, "big"))
        plot_base.extend(count.to_bytes(2, "big"))

        return plot_base
