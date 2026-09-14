#!/usr/bin/env python3
"""
VenXR Adjudicator - Unified Project CLI
Starts, stops, restarts, and monitors the entire project stack:
  1. Agent Service (FastAPI / LangGraph) on port 8001
  2. Backend (Django REST Framework) on port 8000
  3. Frontend (React / Vite) on port 5173
"""

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


# ---------------------------------------------------------------------------
# Project Root Detection
# ---------------------------------------------------------------------------
def get_project_root() -> Path:
    # 1. Check environment variable
    env_root = os.environ.get("VENXR_PROJECT_ROOT")
    if env_root and (Path(env_root) / "agent-service").is_dir():
        return Path(env_root).resolve()

    # 2. Check script directory and parents
    script_dir = Path(__file__).resolve().parent
    for p in [script_dir, *script_dir.parents]:
        if (p / "agent-service").is_dir() and (p / "backend").is_dir() and (p / "frontend").is_dir():
            return p.resolve()

    # 3. Check current working directory and parents
    cwd = Path.cwd()
    for p in [cwd, *cwd.parents]:
        if (p / "agent-service").is_dir() and (p / "backend").is_dir() and (p / "frontend").is_dir():
            return p.resolve()

    # 4. Default configured path
    default_path = Path(r"D:\Venxr-Adjudicator")
    if (default_path / "agent-service").is_dir():
        return default_path.resolve()

    print("[ERROR] Could not locate VenXR project root directory.", file=sys.stderr)
    print("Set the VENXR_PROJECT_ROOT environment variable or run from within the repository.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Port & Process Helpers
# ---------------------------------------------------------------------------
def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def get_pids_on_port(port: int) -> list[int]:
    pids: set[int] = set()
    try:
        out = subprocess.check_output(
            "netstat -ano", shell=True, text=True, errors="replace", stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            line = line.strip()
            if "LISTENING" in line and (f":{port} " in line or f":{port}\t" in line):
                parts = line.split()
                if len(parts) >= 5 and parts[-1].isdigit():
                    val = int(parts[-1])
                    if val > 4:
                        pids.add(val)
    except Exception:
        pass
    return sorted(pids)


def stop_pids(pids: list[int], service_name: str, port: int) -> bool:
    if not pids:
        return False
    killed = False
    for pid in pids:
        try:
            subprocess.run(
                f"taskkill /F /T /PID {pid}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"  [STOPPED] {service_name} (PID: {pid} on port {port})")
            killed = True
        except Exception:
            pass
    return killed


def probe_http(url: str, timeout: float = 1.5) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VenXR-CLI"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def wait_for_ready(url: str, timeout: float, service_name: str) -> bool:
    sys.stdout.write(f"  Waiting for {service_name} to be ready...")
    sys.stdout.flush()
    start = time.time()
    while time.time() - start < timeout:
        if probe_http(url):
            elapsed = round(time.time() - start, 1)
            sys.stdout.write(f" [READY] ({elapsed}s)\n")
            sys.stdout.flush()
            return True
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(0.5)
    sys.stdout.write(" [TIMEOUT]\n")
    sys.stdout.flush()
    return False


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------
def cmd_start(root: Path, background: bool, no_browser: bool, force: bool, wait: bool = False):
    print("")
    print("=" * 62)
    print(" VenXR Adjudicator - Starting Project Services")
    print(f" Project Root: {root}")
    print(f" Mode: {'Background (Headless)' if background else 'Interactive Windows'}")
    print("=" * 62)
    print("")

    agent_dir = root / "agent-service"
    backend_dir = root / "backend"
    frontend_dir = root / "frontend"
    logs_dir = root / "logs"

    agent_python = agent_dir / ".venv" / "Scripts" / "python.exe"
    backend_python = backend_dir / ".venv" / "Scripts" / "python.exe"

    # Pre-flight Checks
    if not agent_python.exists():
        print(f"[ERROR] Agent service virtual environment missing at: {agent_python}", file=sys.stderr)
        print("Run: cd agent-service && python -m venv .venv && .\\.venv\\Scripts\\pip install -r requirements.txt")
        sys.exit(1)
    if not backend_python.exists():
        print(f"[ERROR] Backend virtual environment missing at: {backend_python}", file=sys.stderr)
        print("Run: cd backend && python -m venv .venv && .\\.venv\\Scripts\\pip install -r requirements.txt")
        sys.exit(1)
    if not (frontend_dir / "node_modules").exists():
        print(f"[WARN] Frontend node_modules missing at {frontend_dir}. Running npm install...")
        subprocess.run("npm install", shell=True, cwd=str(frontend_dir))

    # Port Checks
    agent_pids = get_pids_on_port(8001)
    backend_pids = get_pids_on_port(8000)
    frontend_pids = get_pids_on_port(5173)

    if force:
        print("Force flag active. Terminating any running services...")
        stop_pids(agent_pids, "Agent Service", 8001)
        stop_pids(backend_pids, "Backend Django", 8000)
        stop_pids(frontend_pids, "Frontend Vite", 5173)
        time.sleep(1)
        agent_pids, backend_pids, frontend_pids = [], [], []

    if agent_pids and backend_pids and frontend_pids:
        print("[INFO] All services are already running!")
        cmd_status(root)
        return

    logs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Start Agent Service (Port 8001)
    if agent_pids:
        print(f"  [SKIP] Agent Service is already running on port 8001 (PID: {agent_pids})")
    else:
        print("  [STARTING] Agent Service (FastAPI / Port 8001)...")
        if background:
            log_f = open(logs_dir / "agent-service.log", "a", encoding="utf-8")
            subprocess.Popen(
                [str(agent_python), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001", "--reload"],
                cwd=str(agent_dir),
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        else:
            cmd = f'title VenXR - Agent Service (8001) && cd /d "{agent_dir}" && "{agent_python}" -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload'
            subprocess.Popen(f'cmd.exe /k "{cmd}"', creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
        wait_for_ready("http://127.0.0.1:8001/health", 20, "Agent Service")

    # 2. Start Backend Django (Port 8000)
    if backend_pids:
        print(f"  [SKIP] Backend Django is already running on port 8000 (PID: {backend_pids})")
    else:
        print("  [STARTING] Backend Django (Port 8000)...")
        if background:
            log_f = open(logs_dir / "backend.log", "a", encoding="utf-8")
            subprocess.Popen(
                [str(backend_python), "manage.py", "runserver", "127.0.0.1:8000"],
                cwd=str(backend_dir),
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        else:
            cmd = f'title VenXR - Backend Django (8000) && cd /d "{backend_dir}" && "{backend_python}" manage.py runserver 127.0.0.1:8000'
            subprocess.Popen(f'cmd.exe /k "{cmd}"', creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
        wait_for_ready("http://127.0.0.1:8000/api/claims/", 15, "Backend Django")

    # 3. Start Frontend Vite (Port 5173)
    if frontend_pids:
        print(f"  [SKIP] Frontend Vite is already running on port 5173 (PID: {frontend_pids})")
    else:
        print("  [STARTING] Frontend Vite (Port 5173)...")
        if background:
            log_f = open(logs_dir / "frontend.log", "a", encoding="utf-8")
            subprocess.Popen(
                "npm run dev",
                cwd=str(frontend_dir),
                shell=True,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        else:
            cmd = f'title VenXR - Frontend Vite (5173) && cd /d "{frontend_dir}" && npm run dev'
            subprocess.Popen(f'cmd.exe /k "{cmd}"', creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
        wait_for_ready("http://localhost:5173", 15, "Frontend Vite")

    print("")
    print("=" * 62)
    print(" All Services Started Successfully!")
    print("=" * 62)
    print("  Frontend (UI):       http://localhost:5173")
    print("  Backend (Django):    http://127.0.0.1:8000/api/claims/")
    print("  Agent Service:       http://127.0.0.1:8001/health")
    print("=" * 62)
    print("  Commands available from any terminal:")
    print("    project status    - Check service health and PIDs")
    print("    project stop      - Stop all 3 services cleanly")
    print("    project restart   - Restart all services")
    print("")

    if not no_browser:
        try:
            webbrowser.open("http://localhost:5173")
        except Exception:
            pass

    if wait:
        print("Foreground mode active (--wait). Press Ctrl+C to stop all services.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down all services...")
            cmd_stop(root)


def cmd_stop(root: Path):
    print("")
    print("=" * 62)
    print(" VenXR Adjudicator - Stopping Services")
    print("=" * 62)

    stopped_any = False
    for port, name in [(8001, "Agent Service"), (8000, "Backend Django"), (5173, "Frontend Vite")]:
        pids = get_pids_on_port(port)
        if stop_pids(pids, name, port):
            stopped_any = True

    if not stopped_any:
        print("  [INFO] No active services were found running on ports 8001, 8000, 5173.")
    else:
        print("")
        print("  [OK] All VenXR services have been stopped.")
    print("")


def cmd_status(root: Path):
    print("")
    print("=" * 62)
    print(" VenXR Adjudicator - Service Status")
    print("=" * 62)

    services = [
        ("Agent Service", 8001, "http://127.0.0.1:8001/health"),
        ("Backend Django", 8000, "http://127.0.0.1:8000/api/claims/"),
        ("Frontend Vite", 5173, "http://localhost:5173"),
    ]

    for name, port, url in services:
        pids = get_pids_on_port(port)
        is_listening = len(pids) > 0
        is_healthy = probe_http(url, timeout=1.5) if is_listening else False

        if is_healthy:
            status_text = "[ONLINE]"
        elif is_listening:
            status_text = "[STARTING]"
        else:
            status_text = "[OFFLINE]"

        pid_str = f"PID: {', '.join(map(str, pids))}" if pids else "No process"
        print(f"  {name:<16} {status_text:<10} Port {port:<5} {pid_str:<16} {url}")

    print("=" * 62)
    print("")


def cmd_restart(root: Path, background: bool, no_browser: bool, force: bool):
    print("Restarting all VenXR services...")
    cmd_stop(root)
    time.sleep(1.5)
    cmd_start(root, background=background, no_browser=no_browser, force=force)


def cmd_logs(root: Path, target: str):
    logs_dir = root / "logs"
    target_map = {
        "agent": "agent-service.log",
        "backend": "backend.log",
        "frontend": "frontend.log",
    }
    fname = target_map.get(target.lower())
    if not fname:
        print("Specify which service log: project logs [agent|backend|frontend]")
        return
    log_path = logs_dir / fname
    if not log_path.exists():
        print(f"Log file does not exist yet: {log_path}")
        return
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
        for line in lines[-50:]:
            sys.stdout.write(line)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        prog="project",
        description="VenXR Adjudicator - Unified Project CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  project start             Start all services in interactive windows
  project start -b          Start all services in the background (headless)
  project status            Check health and PIDs of all services
  project stop              Stop all running project services
  project restart           Restart all project services
""",
    )

    parser.add_argument(
        "action",
        nargs="?",
        default="start",
        choices=["start", "stop", "restart", "status", "ps", "up", "down", "logs"],
        help="Command to run (default: start)",
    )
    parser.add_argument("target", nargs="?", default="", help="Sub-target (e.g. for logs: agent|backend|frontend)")
    parser.add_argument("-b", "--background", action="store_true", help="Run services in background without opening extra windows")
    parser.add_argument("-n", "--no-browser", action="store_true", help="Do not automatically open browser on start")
    parser.add_argument("-f", "--force", action="store_true", help="Force stop existing services on ports before starting")
    parser.add_argument("-w", "--wait", action="store_true", help="Keep running in foreground; stop all services on Ctrl+C")

    args = parser.parse_args()
    action = args.action.lower()
    if action in ("up", ""):
        action = "start"
    elif action == "down":
        action = "stop"
    elif action == "ps":
        action = "status"

    root = get_project_root()

    if action == "start":
        cmd_start(root, background=args.background, no_browser=args.no_browser, force=args.force, wait=args.wait)
    elif action == "stop":
        cmd_stop(root)
    elif action == "restart":
        cmd_restart(root, background=args.background, no_browser=args.no_browser, force=args.force)
    elif action == "status":
        cmd_status(root)
    elif action == "logs":
        cmd_logs(root, args.target)


if __name__ == "__main__":
    main()
