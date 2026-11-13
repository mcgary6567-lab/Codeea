"""Webhook server that receives Dip2Green PRO alerts and routes them to a broker.

Run locally:   python app.py
Run for real:  waitress-serve --port=$PORT app:app   (behind HTTPS, see README)
"""
import json
import logging

from flask import Flask, jsonify, request

from broker import Broker
from config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bridge.app")

Config.validate()
app = Flask(__name__)
broker = Broker()

REQUIRED = ("action", "symbol", "entry", "sl", "tp1", "tp2")


def _parse_payload():
    """TradingView sends the alert message as the raw body. Accept JSON body
    or form/text that contains JSON."""
    data = request.get_json(silent=True)
    if data is None:
        raw = request.get_data(as_text=True).strip()
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
    return data


def _authorized(data: dict) -> bool:
    token = request.args.get("token") or (data or {}).get("secret") or request.headers.get("X-Webhook-Token")
    return bool(token) and token == Config.WEBHOOK_TOKEN


@app.get("/health")
def health():
    return jsonify(status="ok", exchange=Config.EXCHANGE, dry_run=Config.DRY_RUN, testnet=Config.TESTNET)


@app.post("/webhook")
def webhook():
    data = _parse_payload()
    if not isinstance(data, dict):
        return jsonify(error="body is not valid JSON"), 400
    if not _authorized(data):
        log.warning("Rejected unauthorized webhook from %s", request.remote_addr)
        return jsonify(error="unauthorized"), 401

    missing = [k for k in REQUIRED if k not in data]
    if missing:
        return jsonify(error=f"missing fields: {missing}"), 400
    if data["action"] not in ("BUY", "SELL"):
        return jsonify(error="action must be BUY or SELL"), 400

    sym = str(data["symbol"]).upper().replace("/", "").replace("-", "")
    if Config.SYMBOL_WHITELIST and sym not in Config.SYMBOL_WHITELIST:
        log.warning("Symbol %s not in whitelist; ignoring.", sym)
        return jsonify(status="ignored", reason="symbol not whitelisted"), 200

    try:
        result = broker.execute(data)
    except Exception as exc:  # noqa: BLE001 — surface the reason, keep serving
        log.exception("Execution failed")
        return jsonify(error=str(exc)), 500
    return jsonify(result), 200


if __name__ == "__main__":
    log.info("Starting bridge | exchange=%s dry_run=%s testnet=%s", Config.EXCHANGE, Config.DRY_RUN, Config.TESTNET)
    app.run(host="0.0.0.0", port=Config.PORT)
