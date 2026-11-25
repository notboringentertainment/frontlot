import argparse
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser

import uvicorn

PORT = 4751
URL = f"http://127.0.0.1:{PORT}"


def ready():
    try:
        with urllib.request.urlopen(f"{URL}/api/ready", timeout=0.5) as response:
            return json.load(response).get("front_lot_lab") is True
    except (OSError, ValueError, AttributeError):
        return False


def main():
    parser = argparse.ArgumentParser(prog="python -m lab")
    parser.add_argument("command", choices=("open", "serve"))
    args = parser.parse_args()
    if args.command == "serve":
        uvicorn.run("lab.app:app", host="127.0.0.1", port=PORT, access_log=False)
        return

    if not ready():
        server = subprocess.Popen([sys.executable, "-m", "lab", "serve"], start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if ready():
                break
            if server.poll() is not None:
                sys.exit("Front Lot Lab couldn’t start. Check whether port 4751 is in use.")
            time.sleep(0.1)
        else:
            sys.exit("Front Lot Lab took too long to start.")
    if not webbrowser.open(URL):
        print(f"Open {URL} in your browser.")


if __name__ == "__main__":
    main()
