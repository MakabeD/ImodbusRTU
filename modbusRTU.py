import click
import serial
import serial.tools.list_ports


if not hasattr(serial, "tools"):
    serial_module = getattr(serial, "__file__", "desconocido")
    raise ImportError(
        "Se importo un paquete incorrecto llamado 'serial' en lugar de 'pyserial'. "
        f"Modulo cargado: {serial_module}. "
        "Solucion: ejecuta '.\\.venv\\Scripts\\python.exe -m pip uninstall -y serial' "
        "y luego '.\\.venv\\Scripts\\python.exe -m pip install --force-reinstall pyserial==3.5'."
    )

from compute.modbus_compute import MasterModbusCompute


@click.group()
def cli():
    """
    App cli para lectura de sensores via modbusRTU (amo-esclavo)
    """


@cli.command()
def list_ports():
    """Lista todos los puertos COM disponibles en el sistema."""
    ports = get_available_ports()
    if not ports:
        click.echo("No se detectaron puertos COM.")
        return
    click.echo("Puertos disponibles:")
    for port in ports:
        click.echo(f"- {port.device}: {port.description}")


def get_available_ports():
    """Retorna una lista de puertos COM disponibles."""
    return serial.tools.list_ports.comports()


@cli.command()
@click.option(
    "--port",
    required=True,
    help="Nombre del puerto (ej. COM3 o /dev/ttyUSB0)",
)
@click.option(
    "--baud",
    default=9600,
    required=False,
    help="Velocidad de transmisión (default 9600)",
)
@click.option(
    "--timeout", default=1, required=False, help="TimeOut(tiempo de espera en segundos)"
)
def read(port, baud, timeout):

    with MasterModbusCompute(port=port, baudrate=baud, timeout=timeout) as client:
        if client.serial:
            try:
                res = client.read_holding_registers(1)
                print(f"El valor del sensor es: {res}")
            finally:
                client.disconnect()
        else:
            print("No se pudo leer correctamente, error al conectar")


if __name__ == "__main__":
    cli()
