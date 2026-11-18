"""
API local para outros programas da própria máquina (ex.: o DashOS, a tela ESP32 do uConsole).

A API TCP do meshtasticd só aceita um cliente; o MeshDeck já é esse cliente, por isso quem
corre ao lado pede-lhe os dados aqui em vez de abrir outra ligação ao nó.

Desligada por omissão (run_server.py --local-api on, guardado nas definições): sem ela, as rotas
respondem 404 como qualquer rota inexistente e o MeshDeck funciona exactamente como antes.
Ligada, só responde a pedidos de 127.0.0.1/::1 (não há proxy à frente, o remote_addr é o real) e
não pede login: quem já corre na máquina tem acesso ao nó de qualquer forma.

    GET  /api/local/dash   resumo: nó local, posição, contagens, não lidas, nós, últimas mensagens
    POST /api/local/send   {"text": "..", "channel": 0} ou {"text": "..", "dest": "!abcd1234"}
    POST /api/local/read   {"key": "ch:0"} ou {"all": true}
"""

from __future__ import annotations

from flask import jsonify, request

from core.messages import channel_key, dm_key

MAX_TEXT_BYTES = 200          # o firmware aceita ~228 bytes de texto por pacote
NODES_LIMIT = 100
MESSAGES_LIMIT = 30
NODE_FIELDS = ("id_string", "short_name", "long_name", "last_heard", "snr", "hops_away", "via_mqtt",
               "latitude", "longitude", "altitude", "battery_level", "voltage", "hw_model",
               "channel_utilization", "air_util_tx", "is_local")


def dash_payload(mesh) -> dict:
    nodes = [mesh.nodes.payload(rec) for rec in mesh.nodes.all()]
    nodes.sort(key=lambda n: n.get("last_heard") or 0, reverse=True)
    local = next((n for n in nodes if n.get("is_local")), {})
    names = {n["id_string"]: n.get("short_name") or n.get("long_name") or n["id_string"]
             for n in nodes if n.get("id_string")}
    channels = {c["index"]: c.get("name") or f"Canal {c['index']}" for c in mesh.channels}

    msgs = []
    for key in mesh.messages.keys():
        for m in mesh.messages.conversation(key)[-MESSAGES_LIMIT:]:
            msgs.append({
                "key": key, "time": m["time"], "text": m["text"], "outgoing": m["outgoing"],
                "direct": m["direct"], "status": m.get("status", ""),
                "from_id": m["from_id"], "from": names.get(m["from_id"], m["from_id"]),
                "to_id": m.get("to_id", ""), "to": names.get(m.get("to_id", ""), m.get("to_id", "")),
                "channel": channels.get(int(key.split(":", 1)[1]), key) if key.startswith("ch:") else "",
            })
    msgs.sort(key=lambda m: m["time"], reverse=True)

    unread = mesh.messages.unread()
    return {
        "connected": bool(mesh.connected),
        "node_ready": bool(mesh.node_ready),
        "local_node": dict(mesh.local_node),
        "local_position": dict(mesh.local_position),
        "local": {k: local.get(k) for k in NODE_FIELDS} if local else {},
        "counts": {"total": mesh.nodes.count(), "online": mesh.nodes.online_count(),
                   "contacted": mesh.nodes.contacted_count()},
        "unread": unread,
        "unread_total": sum(unread.values()),
        "channels": [{"index": i, "name": n} for i, n in sorted(channels.items())],
        "nodes": [{k: n.get(k) for k in NODE_FIELDS} for n in nodes if not n.get("is_local")][:NODES_LIMIT],
        "messages": msgs[:MESSAGES_LIMIT],
    }


def register_local_api(app, mesh, is_local_request, enabled=lambda: False) -> None:
    def local_only():
        if not enabled():
            return jsonify({"error": "not found"}), 404
        if not is_local_request():
            return jsonify({"error": "só disponível na própria máquina"}), 403
        return None

    @app.get("/api/local/dash")
    def api_local_dash():
        return local_only() or jsonify(dash_payload(mesh))

    @app.post("/api/local/send")
    def api_local_send():
        denied = local_only()
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "texto vazio"}), 400
        if len(text.encode()) > MAX_TEXT_BYTES:
            return jsonify({"error": f"texto acima de {MAX_TEXT_BYTES} bytes"}), 400
        if not mesh.connected:
            return jsonify({"error": "sem ligação ao nó"}), 503
        dest = data.get("dest")
        if dest:
            if not isinstance(dest, str) or not dest.startswith("!"):
                return jsonify({"error": "destino inválido"}), 400
            mesh.send_dm(dest, text)
            return jsonify({"ok": True, "key": dm_key(dest)})
        try:
            channel = int(data.get("channel", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "canal inválido"}), 400
        if not 0 <= channel <= 7:
            return jsonify({"error": "canal inválido"}), 400
        mesh.send_channel_message(channel, text)
        return jsonify({"ok": True, "key": channel_key(channel)})

    @app.post("/api/local/read")
    def api_local_read():
        denied = local_only()
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        keys = list(mesh.messages.unread()) if data.get("all") else [data.get("key")]
        for key in keys:
            if isinstance(key, str) and key:
                mesh.mark_read(key)
        return jsonify({"ok": True, "unread": mesh.messages.unread()})
