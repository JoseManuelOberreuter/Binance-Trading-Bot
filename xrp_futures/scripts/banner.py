"""
Big console banners so each window says what it's running (start_all.ps1).

  python -m xrp_futures.scripts.banner front
  python -m xrp_futures.scripts.banner back
"""

from __future__ import annotations

import os
import sys

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
CYAN, GREEN, YELLOW, RED = "\033[96m", "\033[92m", "\033[93m", "\033[91m"

ART = {
    "front": [
        "███████ ██████   ██████  ███    ██ ████████",
        "██      ██   ██ ██    ██ ████   ██    ██   ",
        "█████   ██████  ██    ██ ██ ██  ██    ██   ",
        "██      ██   ██ ██    ██ ██  ██ ██    ██   ",
        "██      ██   ██  ██████  ██   ████    ██   ",
    ],
    "back": [
        "██████   █████   ██████ ██   ██",
        "██   ██ ██   ██ ██      ██  ██ ",
        "██████  ███████ ██      █████  ",
        "██   ██ ██   ██ ██      ██  ██ ",
        "██████  ██   ██  ██████ ██   ██",
    ],
}

INFO = {
    "front": (CYAN, "DASHBOARD · la pantalla que ves en el navegador", [
        "Qué es:   Streamlit, en http://localhost:8501",
        "Qué hace: muestra capital, operaciones y controles de los dos bots",
        "No opera: solo lee los archivos de los bots y les deja comandos",
        "Detener:  Ctrl+C en esta ventana (los bots siguen corriendo)",
    ]),
    "back": (GREEN, "BOTS DE TRADING · el motor que opera en Binance Futures", [
        "TESTNET: dinero de prueba",
        "BINANCE: dinero real · arranca EN PAUSA hasta que lo actives en el dashboard",
        "Ciclo:   revisa Binance cada 1 minuto; decide al cierre de cada vela de 12 h",
        "Detener: Ctrl+C en esta ventana (los stops siguen activos en Binance)",
    ]),
}


def render(kind: str) -> str:
    color, title, lines = INFO[kind]
    width = max(len(title), *(len(line) for line in lines), len(ART[kind][0])) + 4
    rule = "═" * width
    out = ["", f"{color}{BOLD}" + "\n".join("  " + row for row in ART[kind]) + RESET, "",
           f"{color}  ╔{rule}╗", f"  ║  {BOLD}{title.ljust(width - 2)}{RESET}{color}║",
           f"  ╚{rule}╝{RESET}"]
    out += [f"  {DIM}{line}{RESET}" for line in lines] + [""]
    return "\n".join(out)


def main() -> None:
    kind = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if kind not in ART:
        raise SystemExit("usage: python -m xrp_futures.scripts.banner front|back")
    if os.name == "nt":
        os.system("")              # enable ANSI colors in the Windows console
    print(render(kind), flush=True)


if __name__ == "__main__":
    main()
