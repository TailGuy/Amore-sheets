"""
Webhook Server for Tournament Tracker
Triggers opgg_tracker.py when receiving POST requests from Google Apps Script.
"""

import os
import subprocess
import secrets
from flask import Flask, request, jsonify

app = Flask(__name__)

# Secret token for authentication (set via environment variable)
SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET", "change-me-in-production")

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})

@app.route("/trigger", methods=["POST"])
def trigger():
    """Trigger the tracker script."""
    # Validate authorization
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {SECRET_TOKEN}":
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        # Run the tracker script
        result = subprocess.run(
            ["python3", "opgg_tracker.py"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        return jsonify({
            "status": "success" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:] if result.stdout else "",  # Last 2000 chars
            "stderr": result.stderr[-500:] if result.stderr else ""
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Script timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Only for development - use gunicorn in production
    app.run(host="127.0.0.1", port=5000, debug=False)
