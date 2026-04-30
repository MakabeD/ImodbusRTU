import click
import serial
import serial.tools.list_ports

from compute.modbus_compute import (
    MasterModbusCompute,
    RegisterValue,
    VariableCandidate,
    find_variable_candidates,
)


@click.group()
def cli():
    """App CLI para lectura y analisis de sensores via Modbus RTU."""


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
    return serial.tools.list_ports.comports()


def render_registers(registers: list[RegisterValue]):
    if not registers:
        click.echo("  No se encontraron registros legibles en el rango solicitado.")
        return

    for item in registers:
        click.echo(
            f"  Registro {item.address:>4} -> valor {item.value:>6} "
            f"(esclavo {item.slave_id})"
        )


def render_variable_candidates(candidates: list[VariableCandidate]):
    if not candidates:
        click.echo("No se detectaron cambios entre las dos mediciones.")
        return

    click.echo("Posibles variables detectadas:")
    for item in candidates:
        click.echo(
            f"- Esclavo {item.slave_id}, registro {item.address}: "
            f"{item.first_value} -> {item.second_value}"
        )


def analyze_registers(
    client: MasterModbusCompute,
    slave_ids: list[int],
    register_start: int,
    register_end: int,
) -> list[RegisterValue]:
    snapshot: list[RegisterValue] = []
    for slave_id in slave_ids:
        click.echo(f"Analizando registros del esclavo {slave_id}...")
        registers = client.scan_registers(
            slave_id=slave_id,
            register_start=register_start,
            register_end=register_end,
        )
        render_registers(registers)
        snapshot.extend(registers)

    return snapshot


@cli.command()
@click.option("--port", required=True, help="Nombre del puerto, por ejemplo COM3.")
@click.option("--baud", default=9600, show_default=True, help="Baudios.")
@click.option("--timeout", default=0.2, show_default=True, help="Timeout en segundos.")
@click.option(
    "--slave-start",
    default=1,
    show_default=True,
    help="Direccion inicial de esclavo a probar.",
)
@click.option(
    "--slave-end",
    default=1,
    show_default=True,
    help="Direccion final de esclavo a probar.",
)
@click.option(
    "--probe-address",
    default=30,
    show_default=True,
    help="Registro usado para probar si el esclavo responde.",
)
@click.option(
    "--register-start",
    default=0,
    show_default=True,
    help="Primer registro a analizar por cada esclavo detectado.",
)
@click.option(
    "--register-end",
    default=67,
    show_default=True,
    help="Ultimo registro a analizar por cada esclavo detectado.",
)
@click.option(
    "--compare-ground-state/--single-pass",
    default=False,
    show_default=True,
    help=(
        "Si se activa, toma una segunda medicion despues de que el usuario cambie "
        "el estado del sensor respecto a tierra para detectar posibles variables."
    ),
)
def analyze(
    port,
    baud,
    timeout,
    slave_start,
    slave_end,
    probe_address,
    register_start,
    register_end,
    compare_ground_state,
):
    """Escanea esclavos y detecta registros que cambian entre dos mediciones."""
    if slave_start > slave_end:
        raise click.BadParameter("slave-start no puede ser mayor que slave-end.")

    if register_start > register_end:
        raise click.BadParameter("register-start no puede ser mayor que register-end.")

    with MasterModbusCompute(port=port, baudrate=baud, timeout=timeout) as client:
        if not client.serial:
            click.echo("No se pudo conectar al puerto serial.")
            return

        click.echo("Buscando direcciones Modbus activas...")
        found_slaves = client.scan_slave_addresses(
            slave_start=slave_start,
            slave_end=slave_end,
            probe_address=probe_address,
        )

        if not found_slaves:
            click.echo("No se encontraron esclavos Modbus que respondan en ese rango.")
            return

        click.echo(
            f"Direcciones activas detectadas: {', '.join(map(str, found_slaves))}"
        )
        click.echo("")
        first_snapshot = analyze_registers(
            client=client,
            slave_ids=found_slaves,
            register_start=register_start,
            register_end=register_end,
        )

        if not compare_ground_state:
            return

        click.echo("")
        click.echo(
            "Cambia el estado del sensor respecto a tierra y presiona Enter para "
            "tomar la segunda medicion."
        )
        click.prompt("", prompt_suffix="", default="", show_default=False)

        click.echo("")
        second_snapshot = analyze_registers(
            client=client,
            slave_ids=found_slaves,
            register_start=register_start,
            register_end=register_end,
        )

        click.echo("")
        render_variable_candidates(
            find_variable_candidates(
                first_snapshot=first_snapshot,
                second_snapshot=second_snapshot,
            )
        )


if __name__ == "__main__":
    cli()
