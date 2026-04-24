# ============================================================
# Render-Proof Flask App (AI Trading Terminal)
# Safe startup + template diagnostics + fallback homepage
# ============================================================

import os
import traceback
from flask import Flask, render_template, jsonify
from flask_cors import CORS

# ============================================================
# PATHS
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

# Create Flask app with explicit template folder
app = Flask(__name__, template_folder=TEMPLATE_DIR)
CORS(app)

# ============================================================
# STARTUP DIAGNOSTICS
# ============================================================

print("=" * 60)
print("FLASK STARTUP")
print("BASE_DIR:", BASE_DIR)
print("TEMPLATE_DIR:", TEMPLATE_DIR)
print("templates exists:", os.path.isdir(TEMPLATE_DIR))

if os.path.isdir(TEMPLATE_DIR):
    print("Template files:", os.listdir(TEMPLATE_DIR))
else:
    print("No templates folder found.")

print("=" * 60)

# ============================================================
# SAFE HELPERS
# ============================================================

def template_exists(name):
    path = os.path.join(TEMPLATE_DIR, name)
    return os.path.isfile(path)

# ============================================================
# ROUTES
# ============================================================

@app.route("/test")
def test():
    return "Flask is working on Render."

@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "AI Trading Terminal",
        "templates_folder": TEMPLATE_DIR,
        "templates_exists": os.path.isdir(TEMPLATE_DIR),
        "preview_exists": template_exists("preview.html")
    })

@app.route("/")
def home():
    try:
        if template_exists("preview.html"):
            return render_template("preview.html")

        return f"""
        <html>
        <head>
            <title>Template Missing</title>
            <style>
                body {{
                    background:#0b1220;
                    color:white;
                    font-family:Arial;
                    padding:40px;
                }}
                .box {{
                    background:#111827;
                    padding:25px;
                    border-radius:12px;
                    max-width:700px;
                }}
                code {{
                    color:#22c55e;
                }}
            </style>
        </head>
        <body>
            <div class="box">
                <h1>preview.html not found</h1>
                <p>Create this folder structure:</p>
                <code>
                app.py<br>
                requirements.txt<br>
                templates/preview.html
                </code>
            </div>
        </body>
        </html>
        """

    except Exception as e:
        return f"""
        <h1>Homepage Error</h1>
        <pre>{str(e)}</pre>
        <pre>{traceback.format_exc()}</pre>
        """, 500

# ============================================================
# OPTIONAL PAGES
# ============================================================

@app.route("/charts")
@app.route("/analytics")
@app.route("/realtime")
@app.route("/backtester")
def pages():
    try:
        if template_exists("preview.html"):
            return render_template("preview.html")
        return "preview.html missing", 500
    except Exception as e:
        return str(e), 500

# ============================================================
# SAMPLE API ROUTES
# ============================================================

@app.route("/signal")
def signal():
    return jsonify({
        "status": "ok",
        "message": "Signal endpoint working"
    })

@app.route("/live_trades")
def live_trades():
    return jsonify([])

@app.route("/alerts")
def alerts():
    return jsonify([])

@app.route("/stats")
def stats():
    return jsonify({
        "wins": 0,
        "losses": 0,
        "balance": 1000
    })

# ============================================================
# RUN LOCAL
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
