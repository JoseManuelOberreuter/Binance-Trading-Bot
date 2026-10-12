"""
Run both live bots from ONE console (start_all.ps1's "back" window).

Each bot is still its own process (own state, own lock, own log file); this only starts
them and merges their output with a colored [TESTNET] / [BINANCE] tag per line.
Ctrl+C stops both.

  python -m xrp_futures.scripts.run_bots                  # testnet + real (real starts paused)
  python -m xrp_futures.scripts.run_bots --no-production  # testnet only
  python -m xrp_futures.scripts.run_bots --no-testnet     # real only
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading

from xrp_futures.scripts.banner import BOLD, RESET, YELLOW, GREEN, RED, render

BOTS = {  # name -> (FUTURES_ENVIRONMENT, extra args, tag color)
    "TESTNET": ("testnet", [], GREEN),
    "BINANCE": ("production", ["--allow-production"], YELLOW),
}


def pipe(name: str, proc: subprocess.Popen, color: str) -> None:
    tag = f"{color}{BOLD}[{name}]{RESET} "
    for line in proc.stdout:
        print(tag + line.rstrip(), flush=True)
    code = proc.wait()
    print(f"{RED}{BOLD}[{name}] terminó (código {code}).{RESET}" if code else f"{tag}terminó.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-testnet", action="store_true")
    parser.add_argument("--no-production", action="store_true")
    args = parser.parse_args()
    chosen = [n for n in BOTS if not (n == "TESTNET" and args.no_testnet) and not (n == "BINANCE" and args.no_production)]
    if not chosen:
        raise SystemExit("nothing to run")
    if os.name == "nt":
        os.system("")              # ANSI colors
    print(render("back"), flush=True)

    procs: list[tuple[str, subprocess.Popen, str]] = []
    for name in chosen:
        env_name, extra, color = BOTS[name]
        env = {**os.environ, "FUTURES_ENVIRONMENT": env_name, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        print(f"{color}{BOLD}▶ Iniciando bot {name}…{RESET}", flush=True)
        p = subprocess.Popen([sys.executable, "-m", "xrp_futures.scripts.run_live_bot", *extra], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", bufsize=1)
        procs.append((name, p, color))
    threads = [threading.Thread(target=pipe, args=t, daemon=True) for t in procs]
    for t in threads:
        t.start()
    try:
        for t in threads:
            while t.is_alive():
                t.join(0.5)
    except KeyboardInterrupt:
        print(f"\n{BOLD}Deteniendo los bots… (los stops siguen activos en Binance){RESET}", flush=True)
        for _, p, _ in procs:
            p.terminate()
        for _, p, _ in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
