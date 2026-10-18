"""
server/app.py — Aplicação Flask + Socket.IO do MeshDeck.

Responsabilidades: servir o frontend estático, autenticar, expor por HTTP o
que é pedido pontualmente (esquema de configuração, HTML das métricas, PSK dos
canais, detalhe de um pacote) e por Socket.IO o que é contínuo (tudo o que
acontece na rede).

A divisão não é arbitrária: o que o browser pede quando o utilizador abre um
painel vai por HTTP, onde cabe uma resposta grande e uma só vez; o que chega
sem ser pedido vai por Socket.IO, difundido a todos os clientes.
"""

import contextlib
import logging
import secrets
import os
from datetime import timedelta

from flask import Flask, abort, g, jsonify, request, send_file, send_from_directory, session
from flask_socketio import SocketIO, disconnect, join_room, leave_room

from constants import APP_NAME
from core.config_defs import MESHTASTIC_CONFIG_DEFS, get_section_label
from core.metrics import SECTION_KEYS
from core.metrics_data import use_window
from core.serialize import safe_utf8_text
from i18n import (LANGUAGES, get_language, has_lazy, normalize_language, parse_date_style, resolve, tr,
                  use_date_style, use_language)
from server import events as ev
from server import tls
from server.tile_cache import TileCache
from server.auth import (LoginThrottle, ServerConfig, is_authenticated, login, login_required,
                         logout)
from server.bridge_manager import list_serial_ports
from server.session import ConfigBusy, ConfigUnavailable, MeshSession

logger = logging.getLogger("MeshDeck.app")

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
SESSION_LIFETIME = timedelta(days=7)


def config_defs_json() -> list:
    """MESHTASTIC_CONFIG_DEFS em JSON, para o renderizador de formulários.

    As tuplas do Python viram objectos com nomes, e os limites dos campos
    numéricos passam a min/max explícitos. O browser gera as 21 secções a
    partir disto; não há uma segunda descrição dos campos no frontend.
    """
    sections = []
    for sec_key, fields in MESHTASTIC_CONFIG_DEFS.items():
        out_fields = []
        for label, name, kind, options in fields:
            field = {"label": tr(label), "name": name, "type": kind}
            if kind == "combo":
                field["choices"] = list(options or [])
            elif kind in ("spin_int", "spin_float") and options:
                field["min"], field["max"] = options[0], options[1]
            elif kind == "readonly_with_note" and options:
                field["note"] = tr(str(options))
            out_fields.append(field)
        sections.append({"key": sec_key,
                         "label": get_section_label(sec_key),
                         "fields": out_fields})
    return sections


# Idioma de cada dispositivo: o browser guarda-o neste cookie e manda-o em
# todos os pedidos (HTML das métricas, configuração, erros) e no Socket.IO.
LANG_COOKIE = "meshdeck_lang"
DATE_COOKIE = "meshdeck_dates"   # formato de data e hora, ex. "en-US.mdy.12"


def request_language() -> str:
    """Idioma do dispositivo que fez o pedido; sem escolha, o do servidor."""
    lang = request.cookies.get(LANG_COOKIE)
    return lang if lang in LANGUAGES else get_language()


def i18n_catalog(language: str) -> dict:
    """Catálogo de traduções do idioma pedido, para o browser traduzir sozinho."""
    from i18n import _STRINGS
    lang = language if language in ("pt", "en") else "en"
    return {key: variants.get(lang, key) for key, variants in _STRINGS.items()}


def create_app(config: ServerConfig = None) -> tuple:
    """Devolve (app, socketio, session). Separado de main() para os testes."""
    config = config or ServerConfig()

    app = Flask(__name__, static_folder=None)
    app.secret_key = config.secret_key
    app.permanent_session_lifetime = SESSION_LIFETIME
    # Nome próprio (não o "session" do Flask): durante uns tempos o servidor
    # correu só em HTTPS e marcou "session" como Secure; os browsers não
    # deixam uma página HTTP substituir esse cookie, e o login por HTTP
    # "funcionava" sem nunca criar sessão. Serve as duas portas, HTTP e HTTPS.
    app.config["SESSION_COOKIE_NAME"] = "meshdeck_session"
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins=[])

    def broadcast(event: str, payload: dict):
        # Um texto por traduzir (LazyText) vai a cada sala de idioma já
        # traduzido; o resto vai igual para todos.
        if not has_lazy(payload):
            socketio.emit(event, payload)
            return
        for lang in LANGUAGES:
            with use_language(lang):
                socketio.emit(event, resolve(payload), to=f"lang:{lang}")

    mesh = MeshSession(broadcast, config)
    app.mesh = mesh
    app.server_config = config
    client_languages = {}            # sid do Socket.IO -> idioma do dispositivo

    @app.before_request
    def enter_device_language():
        g.device_language = contextlib.ExitStack()
        g.device_language.enter_context(use_language(request_language()))
        g.device_language.enter_context(use_date_style(parse_date_style(request.cookies.get(DATE_COOKIE))))

    @app.teardown_request
    def leave_device_language(_error=None):
        context = g.pop("device_language", None)
        if context is not None:
            context.close()

    # ------------------------------------------------------------------
    # Frontend estático
    # ------------------------------------------------------------------
    @app.route("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    # CA local (só o certificado público), sem autenticação: é o que os
    # dispositivos descarregam para passar a confiar no HTTPS do servidor.
    # O tipo MIME faz o iOS/Android oferecerem a instalação do perfil.
    @app.get("/ca.crt")
    def ca_certificate():
        path = tls.ca_path(os.path.dirname(config.path))
        if not path:
            abort(404)
        return send_file(path, mimetype="application/x-x509-ca-cert",
                         as_attachment=True, download_name="meshdeck-ca.crt")

    # ------------------------------------------------------------------
    # Mosaicos do mapa, com cache em disco (mapa offline)
    # ------------------------------------------------------------------
    tiles = TileCache(limit_mb=config.tile_cache_mb)
    app.tiles = tiles

    @app.get("/tiles/osm/<int:z>/<int:x>/<int:y>.png")
    @login_required
    def map_tile(z, x, y):
        data = tiles.get(z, x, y)
        if data is None:
            # Sem internet e nunca visto: o browser volta a pedir mais tarde.
            response = app.response_class(status=404)
            response.headers["Cache-Control"] = "no-store"
            return response
        response = app.response_class(data, mimetype="image/png")
        response.headers["Cache-Control"] = "private, max-age=86400"
        return response

    @app.get("/api/tiles")
    @login_required
    def api_tiles_stats():
        return jsonify(tiles.stats())

    @app.post("/api/tiles/clear")
    @login_required
    def api_tiles_clear():
        tiles.clear()
        return jsonify(tiles.stats())

    @app.route("/<path:filename>")
    def static_files(filename):
        # O frontend não tem passo de build: os ficheiros são servidos como estão.
        return send_from_directory(WEB_DIR, filename)

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------
    throttle = LoginThrottle()
    app.login_throttle = throttle

    def too_many(seconds: int):
        response = jsonify({"error": tr("Demasiadas tentativas falhadas. Tente de novo dentro de {n} s.",
                                        n=seconds), "retry_after": seconds})
        response.status_code = 429
        response.headers["Retry-After"] = str(seconds)
        return response

    @app.post("/api/login")
    def api_login():
        ip = request.remote_addr or "?"
        # Bloqueado: responde já, sem sequer verificar a password.
        wait = throttle.retry_after(ip)
        if wait:
            return too_many(wait)
        payload = request.get_json(silent=True) or {}
        if not config.check_password(payload.get("password", "")):
            logger.warning("tentativa de login falhada de %s", ip)
            locked = throttle.failure(ip)
            if locked:
                logger.warning("login bloqueado durante %d s para %s (demasiadas tentativas)", locked, ip)
                return too_many(locked)
            return jsonify({"error": tr("err_bad_login")}), 401
        throttle.success(ip)
        login(remember=bool(payload.get("remember", True)))
        logger.info("login de %s", ip)
        return jsonify({"ok": True})

    @app.post("/api/logout")
    def api_logout():
        logout()
        return jsonify({"ok": True})

    @app.get("/api/whoami")
    def api_whoami():
        return jsonify({"authenticated": is_authenticated(),
                        "app": APP_NAME,
                        "language": request_language(),
                        # Porta HTTPS, para a página em HTTP indicar onde a bússola funciona.
                        "https_port": config.https_port if config.tls_enabled else None})

    # ------------------------------------------------------------------
    # Estado e recursos pedidos pontualmente
    # ------------------------------------------------------------------
    @app.get("/api/state")
    @login_required
    def api_state():
        return jsonify(mesh.snapshot())

    # Público: só contém textos da interface, e o ecrã de login também
    # precisa de ser traduzido.
    @app.get("/api/i18n")
    def api_i18n():
        lang = normalize_language(request.args.get("lang") or request_language())
        return jsonify({"language": lang, "strings": i18n_catalog(lang)})

    @app.get("/api/config/defs")
    @login_required
    def api_config_defs():
        return jsonify({"sections": config_defs_json()})

    @app.get("/api/channels")
    @login_required
    def api_channels():
        """Inclui as PSK. Pedido só quando o editor de canais é aberto."""
        return jsonify({"channels": mesh.channels_with_psk()})

    # ------------------------------------------------------------------
    # Configuração do nó (leitura e escrita)
    # ------------------------------------------------------------------
    def config_call(action):
        try:
            return jsonify(action())
        except ConfigUnavailable:
            return jsonify({"error": tr("Ligue-se a um nó para ver e editar a configuração.")}), 409
        except ConfigBusy:
            return jsonify({"error": tr("Outro utilizador está a gravar a configuração. Tente daqui a pouco.")}), 409

    def client_id() -> str:
        # Um id por login, para o lock de configuração saber quem o tem.
        if "client_id" not in session:
            session["client_id"] = secrets.token_hex(8)
        return session["client_id"]

    @app.get("/api/config/values")
    @login_required
    def api_config_values():
        return config_call(mesh.config_values)

    @app.post("/api/config/save")
    @login_required
    def api_config_save():
        payload = request.get_json(silent=True) or {}
        sections = payload.get("sections") or {}
        owner = payload.get("owner") or {}
        if not isinstance(sections, dict) or not isinstance(owner, dict):
            return jsonify({"error": tr("pedido inválido")}), 400
        return config_call(lambda: mesh.save_config(client_id(), sections, owner))

    @app.post("/api/channels/save")
    @login_required
    def api_channels_save():
        channels = (request.get_json(silent=True) or {}).get("channels")
        if not isinstance(channels, list):
            return jsonify({"error": tr("pedido inválido")}), 400
        return config_call(lambda: mesh.save_channels(client_id(), channels))

    @app.get("/api/serial-ports")
    @login_required
    def api_serial_ports():
        return jsonify({"ports": list_serial_ports(),
                        "bridge": mesh.bridge.status()})

    # Nós TCP já usados, para o diálogo de ligação.
    @app.get("/api/targets")
    @login_required
    def api_targets():
        return jsonify({"targets": config.saved_targets})

    @app.post("/api/targets/favorite")
    @login_required
    def api_target_favorite():
        payload = request.get_json(silent=True) or {}
        try:
            port = int(payload.get("port", 0))
        except (TypeError, ValueError):
            return jsonify({"error": tr("porta inválida")}), 400
        if not config.set_target_favorite(payload.get("host", ""), port,
                                          bool(payload.get("favorite"))):
            return jsonify({"error": tr("nó desconhecido")}), 404
        return jsonify({"targets": config.saved_targets})

    @app.post("/api/targets/forget")
    @login_required
    def api_target_forget():
        payload = request.get_json(silent=True) or {}
        try:
            port = int(payload.get("port", 0))
        except (TypeError, ValueError):
            return jsonify({"error": tr("porta inválida")}), 400
        config.forget_target(payload.get("host", ""), port)
        return jsonify({"targets": config.saved_targets})

    @app.get("/api/metrics/<section>")
    @login_required
    def api_metrics_html(section):
        if section not in SECTION_KEYS:
            return jsonify({"error": tr("err_unknown_section", section=section)}), 404
        with use_window(request.args.get("window")):
            html = safe_utf8_text(mesh.metrics_html(section))
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.get("/api/metrics/<section>/data")
    @login_required
    def api_metrics_data(section):
        if section not in SECTION_KEYS:
            return jsonify({"error": tr("err_unknown_section", section=section)}), 404
        with use_window(request.args.get("window")):
            return jsonify(mesh.metrics_data(section))

    # Actualização periódica da aba: dados para o gráfico já carregado, ou a
    # indicação de que a secção tem mesmo de ser recarregada.
    @app.get("/api/metrics/<section>/refresh")
    @login_required
    def api_metrics_refresh(section):
        if section not in SECTION_KEYS:
            return jsonify({"error": tr("err_unknown_section", section=section)}), 404
        with use_window(request.args.get("window")):
            return jsonify(mesh.metrics_refresh(section))

    @app.get("/api/node/<node_id>/packet")
    @login_required
    def api_node_packet(node_id):
        packet = mesh.node_packet(node_id)
        if packet is None:
            return jsonify({"error": tr("sem pacote registado para este nó")}), 404
        return jsonify({"packet": packet})

    # ------------------------------------------------------------------
    # Socket.IO
    # ------------------------------------------------------------------
    @socketio.on("connect")
    def on_connect(auth=None):
        if not is_authenticated():
            logger.warning("socket rejeitado por falta de sessão (%s)", request.remote_addr)
            return False
        logger.info("cliente ligado: %s", request.sid)
        lang = request_language()
        client_languages[request.sid] = lang
        join_room(f"lang:{lang}")
        with use_language(lang):
            socketio.emit(ev.SNAPSHOT, mesh.snapshot(), to=request.sid)

    @socketio.on("disconnect")
    def on_disconnect():
        # Um cliente que fecha o separador não pode deixar o nó bloqueado.
        mesh.config_lock.release_if_held(request.sid)
        client_languages.pop(request.sid, None)
        socketio.emit(ev.CONFIG_LOCK, mesh.config_lock.state())
        logger.info("cliente desligado: %s", request.sid)

    def authed(handler):
        """Recusa comandos de sockets sem sessão válida."""
        from functools import wraps

        @wraps(handler)
        def wrapper(*args, **kwargs):
            if not is_authenticated():
                disconnect()
                return None
            with use_language(client_languages.get(request.sid)):
                return handler(*args, **kwargs)
        return wrapper

    @socketio.on(ev.CMD_REQUEST_SNAPSHOT)
    @authed
    def on_request_snapshot(_data=None):
        socketio.emit(ev.SNAPSHOT, mesh.snapshot(), to=request.sid)

    @socketio.on(ev.CMD_CONNECT)
    @authed
    def on_connect_node(data=None):
        data = data or {}
        result = mesh.connect(
            mode=data.get("mode", "tcp"),
            host=data.get("host", "localhost"),
            port=int(data.get("port", 4403)),
            serial_port=data.get("serial_port", ""),
        )
        if not result["ok"]:
            socketio.emit(ev.ERROR, ev.error_occurred(result["error"]), to=request.sid)

    @socketio.on(ev.CMD_DISCONNECT)
    @authed
    def on_disconnect_node(_data=None):
        mesh.disconnect()

    @socketio.on(ev.CMD_SEND_CHANNEL_MSG)
    @authed
    def on_send_channel_message(data):
        mesh.send_channel_message(data.get("channel", 0), data.get("text", ""),
                                  data.get("reply_id") or 0)

    @socketio.on(ev.CMD_SEND_DM)
    @authed
    def on_send_dm(data):
        mesh.send_dm(data.get("node_id", ""), data.get("text", ""), data.get("reply_id") or 0)

    @socketio.on(ev.CMD_TRACEROUTE)
    @authed
    def on_traceroute(data):
        mesh.send_traceroute(data.get("node_id", ""), data.get("hop_limit", 3))

    @socketio.on(ev.CMD_SEND_POSITION)
    @authed
    def on_send_position(_data=None):
        mesh.send_position()

    @socketio.on(ev.CMD_SEND_NODEINFO)
    @authed
    def on_send_nodeinfo(_data=None):
        mesh.send_node_info()

    @socketio.on(ev.CMD_REFRESH_POSITION)
    @authed
    def on_refresh_position(_data=None):
        mesh.refresh_local_position()

    @socketio.on(ev.CMD_RESET_NODEDB)
    @authed
    def on_reset_nodedb(_data=None):
        mesh.reset_nodedb()

    @socketio.on(ev.CMD_CLEAR_METRICS)
    @authed
    def on_clear_metrics(_data=None):
        mesh.clear_metrics()

    @socketio.on(ev.CMD_MARK_READ)
    @authed
    def on_mark_read(data):
        key = (data or {}).get("key", "")
        if isinstance(key, str) and key:
            mesh.mark_read(key)

    @socketio.on(ev.CMD_TOGGLE_FAVORITE)
    @authed
    def on_toggle_favorite(data):
        mesh.toggle_favorite(data.get("node_id", ""))

    @socketio.on(ev.CMD_SET_LANGUAGE)
    @authed
    def on_set_language(data):
        """Só muda o idioma deste dispositivo (o browser já gravou o cookie)."""
        lang = normalize_language((data or {}).get("language"))
        old = client_languages.get(request.sid)
        if old:
            leave_room(f"lang:{old}")
        client_languages[request.sid] = lang
        join_room(f"lang:{lang}")
        with use_language(lang):
            socketio.emit(ev.SNAPSHOT, mesh.snapshot(), to=request.sid)
        return mesh.metric_sections(lang)

    return app, socketio, mesh
