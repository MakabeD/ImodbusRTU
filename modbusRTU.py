import click
import serial
import serial.tools.list_ports
from pymodbus.client import ModbusSerialClient


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
    default=9800,
    required=False,
    help="Velocidad de transmisión (default 9600)",
)
@click.option(
    "--timeout", default=1, required=False, help="TimeOut(tiempo de espera en segundos)"
)
def read(port, baud, timeout):
    client = ModbusSerialClient(port=port, baudrate=baud, timeout=timeout)
    if client.connect():
        print("disque si")


if __name__ == "__main__":
    cli()
