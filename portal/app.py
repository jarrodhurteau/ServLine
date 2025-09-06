from flask import Flask, jsonify, render_template


app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    # For local dev only; VS Code task / script will run this via `flask run`
    app.run(host="0.0.0.0", port=5000, debug=True)