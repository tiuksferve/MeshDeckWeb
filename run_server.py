#!/usr/bin/env python3
"""
run_server.py — Arranca o MeshDeckWeb, o servidor web baseado no MeshDeck.

A ligação ao nó Meshtastic vive neste processo; o browser é interface. O
texto do --help (descrição e exemplos) está no i18n, na língua escolhida no
instalador.
"""

import argparse
import getpass
import logging
import os
import signal
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.settings import settings
from i18n import set_language, tr
from server.app import create_app
from server import tls
from server.auth import ServerConfig, announce_first_run


def parse_args():
    p = argparse.ArgumentParser(
        description=tr("help_description"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", help=tr("help_host"))
    p.add_argument("--port", type=int, help=tr("help_port"))
    p.add_argument("--node-host", default=None, help=tr("help_node_host"))
    p.add_argument("--node-port", type=int, default=None, help=tr("help_node_port"))
    p.add_argument("--serial", metavar="DEVICE", default=None, help=tr("help_serial"))
    p.add_argument("--connect", action="store_true", help=tr("help_connect"))
    p.add_argument("--set-password", action="store_true", help=tr("help_set_password"))
    p.add_argument("--make-cert", nargs="*", metavar=tr("NOME_OU_IP"),
                   help=tr("help_make_cert"))
    p.add_argument("--tls-cert", metavar=tr("FICHEIRO"), help=tr("help_tls_cert"))
    p.add_argument("--tls-key", metavar=tr("FICHEIRO"), help=tr("help_tls_key"))
    p.add_argument("--https-port", type=int, metavar=tr("PORTA"), help=tr("help_https_port"))
    p.add_argument("--tile-cache-mb", type=int, metavar="MB", help=tr("help_tile_cache_mb"))
    p.add_argument("--no-tls", action="store_true", help=tr("help_no_tls"))
    p.add_argument("--debug", action="store_true", help=tr("help_debug"))
    return p.parse_args()


def set_password_interactively(config: ServerConfig) -> int:
    first = getpass.getpass(tr("Nova password: "))
    second = getpass.getpass(tr("Repita: "))
    if first != second:
        print(tr("As passwords não coincidem."), file=sys.stderr)
        return 1
    try:
        config.set_password(first)
    except ValueError as e:
        print(tr("Password recusada: {err}", err=e), file=sys.stderr)
        return 1
    print(tr("Password alterada."))
    return 0


def make_certificate(config: ServerConfig, names: list) -> int:
    result = tls.make_certificates(os.path.dirname(config.path), tls.default_names(names))
    config.set_tls(result["cert"], result["key"])
    address = "%s:%d" % (result["names"][0], config.https_port)
    if config.generated_password:
        # Instalação nova: a password gerada só existe agora, em memória.
        print("\n" + tr("Password gerada (guarde-a, não volta a aparecer):")
              + f"  {config.generated_password}")
    ca_line = tr("CA local criada:") if result["ca_created"] else tr("CA local reutilizada:")
    print("\n".join([
        "",
        tr("HTTPS activado. Certificado do servidor válido até {date} para:", date=result["expires"]),
        "  " + ", ".join(result["names"]),
        "",
        f"{ca_line}  {result['ca']}",
        "",
        tr("cert_done_steps", address=address),
        "",
        tr("Para ficar só em HTTP:") + "  python3 run_server.py --no-tls",
        "",
    ]))
    return 0


def start_https_server(app, host: str, config: ServerConfig):
    """HTTPS numa segunda porta, ao lado do HTTP: os browsers só dão a bússola
    a páginas seguras, mas quem não a usa não tem de instalar a CA. É a mesma
    app (e a mesma ligação ao nó); o middleware do Socket.IO já está no
    app.wsgi_app, por isso o tempo real funciona nas duas portas."""
    from werkzeug.serving import make_server
    try:
        server = make_server(host, config.https_port, app, threaded=True,
                             ssl_context=(config.tls_cert, config.tls_key))
    except (OSError, ValueError) as e:
        # Porta ocupada ou certificado ilegível: o HTTP continua a funcionar.
        logging.getLogger("MeshDeck").error(
            "HTTPS na porta %s não arrancou (%s); só HTTP disponível", config.https_port, e)
        return None
    threading.Thread(target=server.serve_forever, name="https", daemon=True).start()
    return server


class HideProductionWarning(logging.Filter):
    """O Flask-SocketIO avisa "Werkzeug appears to be used in a production
    deployment" sempre que o stdin não é um terminal (serviço, nohup). O
    servidor do Werkzeug com threads é a escolha deliberada: rede local,
    poucos browsers. O aviso só assustava quem lia o log."""

    def filter(self, record):
        return "production deployment" not in record.getMessage()


def quiet_library_logs():
    """O Werkzeug e o engineio registam cada pedido (em polling isso enche o
    log); a biblioteca meshtastic, o pubsub e o urllib3 detalham cada pacote."""
    for name in ("werkzeug", "engineio", "socketio", "meshtastic", "pubsub", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("werkzeug").addFilter(HideProductionWarning())


def main():
    # Língua do terminal (e do --help): a escolhida no instalador.
    set_language(settings().value("language", "en"))
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    quiet_library_logs()

    config = ServerConfig()

    if args.https_port:
        config.set_https_port(args.https_port)
    if args.tile_cache_mb is not None:
        config.set_tile_cache_mb(args.tile_cache_mb)
    if args.make_cert is not None:
        return make_certificate(config, args.make_cert)
    if args.no_tls:
        config.clear_tls()
        print(tr("HTTPS desactivado: no próximo arranque o servidor fica só em HTTP."))
        return 0
    if args.tls_cert or args.tls_key:
        if not (args.tls_cert and args.tls_key):
            print(tr("--tls-cert e --tls-key têm de ser indicados juntos."), file=sys.stderr)
            return 1
        for path in (args.tls_cert, args.tls_key):
            if not os.path.isfile(path):
                print(tr("Ficheiro não encontrado: {path}", path=path), file=sys.stderr)
                return 1
        config.set_tls(args.tls_cert, args.tls_key)
    if args.set_password:
        return set_password_interactively(config)

    host = args.host or config.bind_host
    port = args.port or config.bind_port
    if args.host or args.port:
        config.set_bind(host, port)

    app, socketio, mesh = create_app(config)
    announce_first_run(config, host, port)

    if args.connect or args.serial:
        remembered = config.last_target
        if args.serial:
            mode, node_host, node_port, serial_port = "serial", "localhost", 4403, args.serial
        else:
            mode = "tcp"
            node_host = args.node_host or remembered["host"]
            node_port = args.node_port or remembered["port"]
            serial_port = ""
        result = mesh.connect(mode=mode, host=node_host, port=node_port,
                              serial_port=serial_port)
        if not result["ok"]:
            print(tr("Ligação ao nó falhou: {err}", err=result["error"]), file=sys.stderr)

    def shutdown(_signum, _frame):
        print("\n" + tr("a encerrar…"), flush=True)
        mesh.shutdown()
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if config.tls_enabled:
        start_https_server(app, host, config)

    try:
        socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
    finally:
        mesh.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
