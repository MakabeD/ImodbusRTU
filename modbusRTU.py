import click
import pandas as pd
import serial
import serial.tools.list_ports

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
        tabla = pd.DataFrame({"Nombre": ["Ana", "Luis", "María"]})
        registro1 = []
        registro2 = []
        if client.serial:
            n = 68
            for i in range(n):
                try:
                    res = client.read_holding_registers(1, address=i)
                    print(f"El valor del sensor es: {res}")
                    if res[0] != 49393:
                        registro1.append(structure(res[0], i))
                finally:
                    client.disconnect()
            input("==================ESPERA==================")  ####espera

            for i in range(n):
                try:
                    res = client.read_holding_registers(1, address=i)
                    print(f"El valor del sensor es: {res}")
                    if res[0] != 49393:
                        registro2.append(structure(res[0], i))
                finally:
                    client.disconnect()

            x = compare_structured_list(regi1=registro1, regi2=registro2)
            print_everytwo(x)
        else:
            print("No se pudo leer correctamente, error al conectar")


def print_everytwo(x):
    for i in range(len(x)):
        if i % 2 == 0:
            print("\n")
        print(x[i])


def compare_structured_list(regi1: structure, regi2: structure):
    true = []
    if len(regi1) != len(regi2):
        print("los registros tienen diferente longitud")
        return
    else:
        for i in range(len(regi1)):
            if regi1[i].val != regi2[i].val and regi1[i].index == regi2[i].index:
                true.append(regi1[i])
                true.append(regi2[i])
    return true


class structure:
    def __str__(self):
        return "valor: {:>8}. Direccion: {:>8}".format(self.val, self.index)

    def __init__(self, val: float, index: int):
        self.val = val
        self.index = index


if __name__ == "__main__":
    cli()
