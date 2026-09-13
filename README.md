# ImodbusRTU

CLI para escanear esclavos Modbus RTU, leer registros y detectar posibles variables
comparando dos mediciones.

## Uso

Listar puertos:

```powershell
.venv\Scripts\python.exe modbusRTU.py list-ports
```

Escanear esclavos y leer registros:

```powershell
.venv\Scripts\python.exe modbusRTU.py analyze --port COM3 --slave-start 1 --slave-end 10 --register-start 0 --register-end 67
```

Escanear y comparar dos estados del sensor para detectar posibles variables:

```powershell
.venv\Scripts\python.exe modbusRTU.py analyze --port COM3 --compare-ground-state
```

Registrar una corrida de monitoreo en SQLite:

```powershell
.venv\Scripts\python.exe modbusRTU.py monitor-run --port COM3 --run-name grounded --slave 1 --registers 0,1,2,10 --minutes 10
```

Listar corridas guardadas:

```powershell
.venv\Scripts\python.exe modbusRTU.py list-runs
```

Comparar dos corridas guardadas y generar dashboard:

```powershell
.venv\Scripts\python.exe modbusRTU.py compare-runs --left-table run_grounded --right-table run_not_grounded --output-path data/dashboard.html
```

Calcular estadisticas por registro entre una corrida en aire y una en tierra:

```powershell
.venv\Scripts\python.exe modbusRTU.py variability-report --air-run run_air --soil-run run_soil --output data/variability.csv
```

El reporte muestra por registro la media y desviacion de cada estado, el delta
(mean soil - mean air) y un change score (LOW/MEDIUM/HIGH) basado en el efecto
estandarizado (Cohen's d). Agrega `--show-all` para incluir registros sin cambio.
