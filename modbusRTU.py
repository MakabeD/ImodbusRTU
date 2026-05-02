import logging
import sys

import click
import serial
import serial.tools.list_ports

from compute.comparison import compare_sqlite_tables, list_monitoring_tables
from compute.modbus_compute import (
    MasterModbusCompute,
    RegisterValue,
    VariableCandidate,
    find_variable_candidates,
)
from compute.monitoring import monitor_with_client


def setup_logging(verbose: bool, quiet: bool):
    if quiet:
        logging.disable(logging.CRITICAL)
    elif verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress non-essential output.")
@click.pass_context
def cli(ctx, verbose, quiet):
    """App CLI para lectura, monitoreo y analisis de sensores via Modbus RTU."""
    ctx.ensure_object(dict)
    ctx.obj["VERBOSE"] = verbose
    ctx.obj["QUIET"] = quiet
    setup_logging(verbose, quiet)


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


def parse_registers(registers: str) -> list[int]:
    parts = [item.strip() for item in registers.split(",") if item.strip()]
    if not parts:
        raise click.BadParameter("Debes enviar al menos un registro en --registers.")

    try:
        return [int(item) for item in parts]
    except ValueError as error:
        raise click.BadParameter(
            "Los registros deben ser enteros separados por comas."
        ) from error


def render_registers(registers: list[RegisterValue], verbose: bool = False):
    if not registers:
        msg = "  No se encontraron registros legibles en el rango solicitado."
        if verbose:
            logging.warning(msg)
        else:
            click.echo(msg)
        return

    for item in registers:
        click.echo(
            f"  Registro {item.address:>4} -> valor {item.value:>6} "
            f"(esclavo {item.slave_id})"
        )


def render_progress_bar(current: int, total: int, prefix: str = "", width: int = 30):
    percent = current / total if total > 0 else 0
    filled = int(width * percent)
    bar = "█" * filled + "░" * (width - filled)
    click.echo(f"\r{prefix}[{bar}] {current}/{total} ({percent * 100:.0f}%)", nl=False)
    if current >= total:
        click.echo()


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


def render_run_preview(dataframe):
    click.echo("Primeras filas del monitoreo:")
    click.echo(dataframe.head().to_string(index=False))


def analyze_registers(
    client: MasterModbusCompute,
    slave_ids: list[int],
    register_start: int,
    register_end: int,
    show_progress: bool = False,
) -> list[RegisterValue]:
    snapshot: list[RegisterValue] = []
    total_registers = (register_end - register_start + 1) * len(slave_ids)
    current = 0

    for slave_id in slave_ids:
        if show_progress:
            click.echo(
                f"\n[Esclavo {slave_id}] Escaneando registros {register_start}-{register_end}..."
            )

        for address in range(register_start, register_end + 1):
            values = client.read_holding_registers(
                slave_id=slave_id, address=address, count=1, quiet=show_progress
            )
            if values:
                snapshot.append(
                    RegisterValue(
                        slave_id=slave_id,
                        address=address,
                        value=values[0],
                    )
                )
            current += 1
            if show_progress:
                render_progress_bar(current, total_registers, prefix="Progreso: ")

        # Always render registers for each slave
        render_registers([r for r in snapshot if r.slave_id == slave_id])

    if show_progress:
        click.echo(f"\nTotal de registros leidos: {len(snapshot)}")

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
@click.option(
    "--progress/--no-progress",
    default=False,
    show_default=True,
    help="Mostrar barra de progreso durante el escaneo.",
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
    progress,
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
        if progress:
            total_to_scan = slave_end - slave_start + 1
            click.echo(f"Rango: {slave_start} - {slave_end}")

        found_slaves = []
        for i, slave_id in enumerate(range(slave_start, slave_end + 1)):
            if client.probe_slave(slave_id, probe_address=probe_address):
                found_slaves.append(slave_id)
            if progress:
                render_progress_bar(i + 1, total_to_scan, prefix="Escaneo: ")

        if progress:
            click.echo("")

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
            show_progress=progress,
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
            show_progress=progress,
        )

        click.echo("")
        render_variable_candidates(
            find_variable_candidates(
                first_snapshot=first_snapshot,
                second_snapshot=second_snapshot,
            )
        )


@cli.command("monitor-run")
@click.option("--port", required=True, help="Nombre del puerto, por ejemplo COM3.")
@click.option("--run-name", required=True, help="Nombre del experimento a guardar.")
@click.option("--slave", required=True, type=int, help="Direccion del esclavo.")
@click.option(
    "--registers",
    required=True,
    help="Lista de registros separados por coma. Ejemplo: 0,1,2,10",
)
@click.option(
    "--minutes",
    required=True,
    type=int,
    help="Duracion del monitoreo en minutos.",
)
@click.option("--baud", default=9600, show_default=True, help="Baudios.")
@click.option("--timeout", default=0.2, show_default=True, help="Timeout en segundos.")
@click.option(
    "--sample-every-seconds",
    default=60,
    show_default=True,
    type=int,
    help="Cada cuantos segundos se toma una muestra.",
)
@click.option(
    "--database-path",
    default="data/monitoring.sqlite",
    show_default=True,
    help="Archivo SQLite donde se guardan las corridas.",
)
def monitor_run(
    port,
    run_name,
    slave,
    registers,
    minutes,
    baud,
    timeout,
    sample_every_seconds,
    database_path,
):
    """Monitorea registros y guarda la corrida en SQLite."""
    register_list = parse_registers(registers)

    with MasterModbusCompute(port=port, baudrate=baud, timeout=timeout) as client:
        if not client.serial:
            click.echo("No se pudo conectar al puerto serial.")
            return

        click.echo(
            f"Iniciando monitoreo '{run_name}' del esclavo {slave} en registros "
            f"{', '.join(map(str, register_list))}..."
        )
        run = monitor_with_client(
            run_name=run_name,
            client=client,
            slave_id=slave,
            registers=register_list,
            duration_minutes=minutes,
            database_path=database_path,
            sample_every_seconds=sample_every_seconds,
        )

    click.echo(f"Corrida guardada en {run.database_path}")
    click.echo(f"Tabla SQLite: {run.table_name}")
    render_run_preview(run.dataframe)


@cli.command("list-runs")
@click.option(
    "--database-path",
    default="data/monitoring.sqlite",
    show_default=True,
    help="Archivo SQLite donde se guardan las corridas.",
)
def list_runs(database_path):
    """Lista las corridas de monitoreo almacenadas."""
    runs_df = list_monitoring_tables(database_path)
    if runs_df.empty:
        click.echo("No hay corridas registradas en la base de datos.")
        return

    click.echo(runs_df.to_string(index=False))


@cli.command("compare-runs")
@click.option(
    "--database-path",
    default="data/monitoring.sqlite",
    show_default=True,
    help="Archivo SQLite donde se guardan las corridas.",
)
@click.option("--left-table", required=True, help="Nombre de la primera tabla.")
@click.option("--right-table", required=True, help="Nombre de la segunda tabla.")
@click.option(
    "--output-path",
    default="data/dashboard.html",
    show_default=True,
    help="Ruta del dashboard HTML de salida.",
)
def compare_runs(database_path, left_table, right_table, output_path):
    """Compara dos tablas SQLite y genera un dashboard HTML."""
    dashboard = compare_sqlite_tables(
        database_path=database_path,
        left_table=left_table,
        right_table=right_table,
        output_path=output_path,
    )

    click.echo(f"Dashboard generado en {dashboard.output_path}")
    click.echo("")
    click.echo("Resumen de cambios por registro:")
    click.echo(dashboard.summary_df.to_string(index=False))


EXPLORE_COMMANDS = {
    "help": "Mostrar comandos disponibles",
    "read": "Leer uno o mas registros (ej: read 0, read 0-10)",
    "scan": "Escanear rango de registros (ej: scan 0 67)",
    " slaves": "Detectar esclavos activos",
    "dump": "Mostrar ultimo snapshot leido",
    "save": "Guardar snapshot actual (ej: save nombre)",
    "compare": "Comparar con snapshot anterior",
    "quit": "Salir del modo interactivo",
}


@cli.command("explore")
@click.option("--port", required=True, help="Nombre del puerto, por ejemplo COM3.")
@click.option("--baud", default=9600, show_default=True, help="Baudios.")
@click.option("--timeout", default=0.2, show_default=True, help="Timeout en segundos.")
@click.option(
    "--slave", default=1, show_default=True, type=int, help="Direccion del esclavo."
)
def explore(port, baud, timeout, slave):
    """Modo interactivo para explorar registros Modbus."""
    click.echo("=== Modo Explorador Modbus RTU ===")
    click.echo("Escribe 'help' para ver comandos disponibles, 'quit' para salir.\n")

    with MasterModbusCompute(port=port, baudrate=baud, timeout=timeout) as client:
        if not client.serial:
            click.echo("No se pudo conectar al puerto serial.")
            return

        click.echo(f"Conectado a {port} @ {baud} baud (esclavo {slave})")
        current_snapshot: list[RegisterValue] = []
        previous_snapshot: list[RegisterValue] = []

        while True:
            try:
                command = click.prompt(
                    "\n(modbus)> ",
                    prompt_suffix="",
                    default="",
                    show_default=False,
                ).strip()

                if not command:
                    continue

                if command.lower() in ("quit", "exit", "q"):
                    click.echo("Saliendo...")
                    break

                if command.lower() == "help":
                    for cmd, desc in EXPLORE_COMMANDS.items():
                        click.echo(f"  {cmd:<12} {desc}")
                    continue

                parts = command.split()
                action = parts[0].lower()

                if action == "read":
                    if len(parts) < 2:
                        click.echo("Uso: read <registro> o read <inicio>-<fin>")
                        continue

                    addresses = []
                    for arg in parts[1:]:
                        if "-" in arg:
                            start, end = arg.split("-")
                            addresses.extend(range(int(start), int(end) + 1))
                        else:
                            addresses.append(int(arg))

                    current_snapshot = []
                    for addr in addresses:
                        values = client.read_holding_registers(slave, addr, 1)
                        if values:
                            reg = RegisterValue(
                                slave_id=slave, address=addr, value=values[0]
                            )
                            current_snapshot.append(reg)
                            click.echo(f"  [{addr}] = {values[0]}")
                        else:
                            click.echo(f"  [{addr}] = (sin respuesta)")

                elif action == "scan":
                    if len(parts) < 3:
                        click.echo("Uso: scan <inicio> <fin>")
                        continue

                    start, end = int(parts[1]), int(parts[2])
                    current_snapshot = client.scan_registers(slave, start, end)

                    click.echo(f"\nRegistros leidos: {len(current_snapshot)}")
                    for reg in current_snapshot:
                        click.echo(f"  [{reg.address:>4}] = {reg.value:>6}")

                elif action == "slaves":
                    click.echo("Buscando esclavos...")
                    found = client.scan_slave_addresses(
                        slave_start=1, slave_end=247, probe_address=0
                    )
                    if found:
                        click.echo(f"Esclavos activos: {', '.join(map(str, found))}")
                    else:
                        click.echo("No se encontraron esclavos.")

                elif action == "dump":
                    if not current_snapshot:
                        click.echo(
                            "No hay datos guardados. Usa 'read' o 'scan' primero."
                        )
                    else:
                        click.echo(
                            f"\nSnapshot actual ({len(current_snapshot)} registros):"
                        )
                        for reg in current_snapshot:
                            click.echo(f"  [{reg.address:>4}] = {reg.value:>6}")

                elif action == "save":
                    if len(parts) < 2:
                        click.echo("Uso: save <nombre>")
                        continue

                    previous_snapshot = current_snapshot
                    click.echo(f"Snapshot guardado como '{parts[1]}'")

                elif action == "compare":
                    if not previous_snapshot or not current_snapshot:
                        click.echo(
                            "Necesitas dos snapshots para comparar. Usa 'save' primero."
                        )
                    else:
                        candidates = find_variable_candidates(
                            previous_snapshot, current_snapshot
                        )
                        if not candidates:
                            click.echo("No se detectaron cambios.")
                        else:
                            click.echo(f"\nCambios detectados ({len(candidates)}):")
                            for c in candidates:
                                click.echo(
                                    f"  [{c.address:>4}] {c.first_value} -> {c.second_value}"
                                )

                else:
                    click.echo(
                        f"Comando desconocido: {action}. Usa 'help' para ver comandos."
                    )

            except click.Abort:
                click.echo("\nSaliendo...")
                break
            except Exception as e:
                click.echo(f"Error: {e}")


if __name__ == "__main__":
    cli()
