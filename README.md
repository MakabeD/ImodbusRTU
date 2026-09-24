# ImodbusRTU

[![CI](https://github.com/MakabeD/ImodbusRTU/actions/workflows/ci.yml/badge.svg)](https://github.com/MakabeD/ImodbusRTU/actions/workflows/ci.yml)

CLI para escanear esclavos Modbus RTU, leer registros, monitorear en SQLite y
analizar corridas (dashboard HTML, reporte de variabilidad) para detectar
posibles variables comparando dos estados de un sensor.

## Caracteristicas

- Escaneo de esclavos (1-247) y de rangos de registros con **lecturas por
  lote** (hasta 125 registros por trama, ~10-50x mas rapido que leer de a uno)
- Fallback automatico a lectura individual cuando el dispositivo rechaza
  tramas grandes o tiene huecos de direcciones
- Timeout serial calculado por tamaño de trama, baudios y configuracion de
  la linea (9600 baudios: una respuesta de 255 bytes tarda ~270 ms)
- Monitoreo periodico a SQLite con persistencia parcial si se interrumpe
  con `Ctrl+C`
- Dashboard HTML comparativo y reporte de variabilidad (Cohen's d)
- Modo interactivo `explore` para pruebas en el banco
- Ajustes serial reutilizables via `imodbus.toml`

## Instalacion

Requiere Python 3.10+.

```powershell
# Windows (PowerShell)
git clone https://github.com/MakabeD/ImodbusRTU
cd ImodbusRTU
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

```bash
# Linux/macOS
git clone https://github.com/MakabeD/ImodbusRTU
cd ImodbusRTU
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Verifica la instalacion:

```bash
imodbus --help
imodbus list-ports
```

## Configuracion (opcional)

`imodbus.toml` en el directorio de trabajo define ajustes serial reutilizables.
Precedencia: flag del CLI > perfil > seccion `[serial]` > valores por defecto
(baud `9600`, timeout `0.2`).

```toml
[serial]
port = "COM3"
baud = 19200

[profiles.lab]
port = "COM4"
timeout = 0.5
```

```bash
imodbus analyze                 # usa COM3 @ 19200
imodbus analyze --profile lab   # usa COM4, timeout 0.5 s
imodbus analyze --port COM9     # el flag siempre gana
```

Con `--config ruta.toml` se usa otro archivo de configuracion.

## Comandos

Listar puertos seriales disponibles:

```bash
imodbus list-ports
```

Escanear esclavos y leer registros (por defecto esclavos 1-1, registros 0-67):

```bash
imodbus analyze --port COM3 --slave-start 1 --slave-end 10 --register-start 0 --register-end 67
```

Escanear y comparar dos estados del sensor (aire vs tierra) para detectar
posibles variables:

```bash
imodbus analyze --port COM3 --compare-ground-state --progress
```

Monitorear registros y guardar la corrida en SQLite (muestra cada 60 s por
defecto):

```bash
imodbus monitor-run --port COM3 --run-name grounded --slave 1 --registers 0,1,2,10 --minutes 10
```

Listar corridas guardadas:

```bash
imodbus list-runs
```

Comparar dos corridas y generar dashboard HTML con graficas de tendencia:

```bash
imodbus compare-runs --left-table run_grounded --right-table run_not_grounded --output-path data/dashboard.html
```

Calcular estadisticas por registro entre una corrida en aire y una en tierra:

```bash
imodbus variability-report --air-run run_air --soil-run run_soil --output data/variability.csv
```

El reporte muestra por registro la media y desviacion de cada estado, el delta
(mean soil - mean air) y un change score (LOW/MEDIUM/HIGH) basado en el efecto
estandarizado (Cohen's d). Agrega `--show-all` para incluir registros sin
cambio y `--top-n` para limitar la salida.

## Modo interactivo

```bash
imodbus explore --port COM3 --slave 1
```

Comandos disponibles dentro del modo: `read 0-10`, `scan 0 67`, `slaves`,
`dump`, `save nombre`, `compare`, `quit`. Usa `help` para la lista completa.

## Verbosidad

- Por defecto: mensajes informativos y warnings
- `-v / --verbose`: log de debug con nivel y modulo
- `-q / --quiet`: silencia todo excepto la salida de datos

## Desarrollo

Estructura del paquete:

```
src/imodbus_rtu/
  cli.py             # comandos click
  config.py          # carga de imodbus.toml
  compute/
    modbus_compute.py  # tramas Modbus RTU, CRC, cliente serial
    monitoring.py      # corridas de monitoreo en SQLite
    comparison.py      # comparacion de corridas + dashboard HTML
    variability.py     # reporte de variabilidad (Cohen's d)
tests/                 # pytest (105 tests)
```

Correr pruebas, lint y hooks:

```bash
pytest            # suite completa
ruff check .      # lint
ruff format .     # formato
pre-commit install  # hooks en cada commit (primera vez)
```

`requirements.txt` mantiene las versiones fijadas para reproducibilidad; la
fuente de verdad de dependencias es `pyproject.toml`.

El CI de GitHub Actions corre `ruff check` y `pytest` en Python 3.10 y 3.12
en cada push y PR.

## Notas tecnicas

- Las lecturas agrupan hasta 125 registros contiguos por trama (limite del
  spec Modbus); ante un fallo de lote se reintenta registro por registro,
  por lo que los rangos con registros inexistentes siguen funcionando.
- Las respuestas se validan por CRC16 y longitud exacta; una respuesta corta
  se descarta en vez de interpretarse como ceros.
- Las corridas de monitoreo se guardan de forma incremental si el proceso se
  interrumpe, conservando las muestras ya tomadas.
