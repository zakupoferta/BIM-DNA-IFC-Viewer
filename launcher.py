import socket, subprocess, sys, time, urllib.request, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def free_port(start=8765, end=8795):
    for p in range(start, end + 1):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    raise RuntimeError("Nie znaleziono wolnego portu 8765-8795.")

port = free_port()
url = f"http://127.0.0.1:{port}/"
(ROOT / "viewer_url.txt").write_text(url, encoding="utf-8")

env = os.environ.copy()
env["BIM_DNA_PORT"] = str(port)

proc = subprocess.Popen(
    [sys.executable, str(ROOT / "server.py")],
    cwd=str(ROOT),
    env=env,
)

for _ in range(60):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
            if r.status == 200:
                break
    except Exception:
        time.sleep(0.5)
else:
    print("\nNie udało się uruchomić serwera BIM DNA.")
    print("Sprawdź komunikat powyżej.")
    proc.wait()
    raise SystemExit(1)

print(f"\nBIM DNA IFC Viewer 4.10.3 działa: {url}")
try:
    os.startfile(url)
except Exception:
    print("Otwórz ręcznie:", url)

proc.wait()
