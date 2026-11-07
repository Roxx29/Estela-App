"""Mide CPU y memoria de Estela durante 30 s (pip install -r requirements-dev.txt).

Uso: abre la app, pon música, elige un modo del borde y ejecuta: python tools\\diag_cpu.py
"""
import time

import psutil

procs = [p for p in psutil.process_iter(["name", "cmdline"])
         if p.info["name"] == "Estela.exe" or "run.py" in " ".join(p.info["cmdline"] or [])]
if not procs:
    raise SystemExit("Estela no está abierto")
# prioriza la versión de desarrollo (run.py); el lanzador del venv es un proceso aparte y pequeño
proc = max(procs, key=lambda p: ("run.py" in " ".join(p.cmdline()), p.memory_info().rss))
proc.cpu_percent()
samples = []
for _ in range(30):
    time.sleep(1)
    samples.append(proc.cpu_percent() / psutil.cpu_count())
mem = proc.memory_info().rss / 2 ** 20
print(f"PID {proc.pid} ({' '.join(proc.cmdline()[-1:])})  CPU media {sum(samples) / len(samples):.1f} %  máx {max(samples):.1f} %  memoria {mem:.0f} MB")
