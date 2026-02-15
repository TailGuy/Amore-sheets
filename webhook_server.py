import os
import subprocess
import threading
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)
SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET", "change-me-in-production")
VENV_PYTHON = "/opt/opgg-tracker/venv/bin/python3"
LOG_DIR = "/opt/opgg-tracker/logs"

def run_tracker():
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = f"{LOG_DIR}/run_{timestamp}.log"
    
    with open(log_file, "w") as f:
        f.write(f"=== Tracker run started at {timestamp} ===\n\n")
        result = subprocess.run(
            [VENV_PYTHON, "opgg_tracker.py"],
            cwd="/opt/opgg-tracker",
            stdout=f,
            stderr=subprocess.STDOUT
        )
        f.write(f"\n=== Finished with exit code {result.returncode} ===\n")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/trigger", methods=["POST"])
def trigger():
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {SECRET_TOKEN}":
        return jsonify({"error": "Unauthorized"}), 401
    
    threading.Thread(target=run_tracker, daemon=True).start()
    return jsonify({"status": "started", "message": "Tracker running in background"})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
