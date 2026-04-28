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
