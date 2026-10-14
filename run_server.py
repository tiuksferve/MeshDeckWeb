#!/usr/bin/env python3
"""
run_server.py — Arranca o MeshDeckWeb, o servidor web baseado no MeshDeck.

A ligação ao nó Meshtastic vive neste processo; o browser é interface. Por
omissão liga-se a 127.0.0.1 e não é acessível de fora da máquina; use
--host 0.0.0.0 para abrir à rede local, sabendo que quem entrar pode
reconfigurar o nó.

Exemplos:
    python3 run_server.py
    python3 run_server.py --host 0.0.0.0 --port 8080
    python3 run_server.py --node-host 192.168.1.50 --connect
    python3 run_server.py --serial /dev/ttyACM0 --connect
    python3 run_server.py --set-password
    python3 run_server.py --make-cert            # HTTPS (porta 8443) com CA local
    python3 run_server.py --https-port 9443      # mudar a porta HTTPS
    python3 run_server.py --no-tls               # só HTTP
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
from i18n import set_language
from server.app import create_app
from server import tls
from server.auth import ServerConfig, announce_first_run


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", help="endereço de bind (default: o guardado, ou 127.0.0.1)")
    p.add_argument("--port", type=int, help="porta HTTP (default: a guardada, ou 8080)")
    p.add_argument("--node-host", default=None,
                   help="host do meshtasticd a que ligar (default: localhost)")
    p.add_argument("--node-port", type=int, default=None,
                   help="porta do meshtasticd (default: 4403)")
    p.add_argument("--serial", metavar="DEVICE", default=None,
                   help="ligar por USB serial através da ponte, neste dispositivo")
    p.add_argument("--connect", action="store_true",
                   help="ligar ao nó no arranque, sem esperar pelo browser")
    p.add_argument("--set-password", action="store_true",
                   help="definir a password de acesso e sair")
    p.add_argument("--make-cert", nargs="*", metavar="NOME_OU_IP",
                   help="gerar certificado HTTPS (CA local + servidor) para estes IPs/nomes, "
                        "além dos IPs desta máquina, activar TLS e sair")
    p.add_argument("--tls-cert", metavar="FICHEIRO", help="usar este certificado (PEM) e guardar")
    p.add_argument("--tls-key", metavar="FICHEIRO", help="chave privada do --tls-cert")
    p.add_argument("--https-port", type=int, metavar="PORTA",
                   help="porta HTTPS, servida ao lado do HTTP quando há certificado "
                        "(default: a guardada, ou 8443)")
    p.add_argument("--no-tls", action="store_true", help="desactivar HTTPS (ficar só HTTP) e sair")
    p.add_argument("--debug", action="store_true", help="logging em DEBUG")
    return p.parse_args()


def set_password_interactively(config: ServerConfig) -> int:
    first = getpass.getpass("Nova password: ")
    second = getpass.getpass("Repita: ")
    if first != second:
        print("As passwords não coincidem.", file=sys.stderr)
        return 1
    try:
        config.set_password(first)
    except ValueError as e:
        print(f"Password recusada: {e}", file=sys.stderr)
        return 1
    print("Password alterada.")
    return 0


def make_certificate(config: ServerConfig, names: list) -> int:
    result = tls.make_certificates(os.path.dirname(config.path), tls.default_names(names))
    config.set_tls(result["cert"], result["key"])
    address = "%s:%d" % (result["names"][0], config.https_port)
    if config.generated_password:
        # Instalação nova: a password gerada só existe agora, em memória.
        print(f"\nPassword gerada (guarde-a, não volta a aparecer):  {config.generated_password}")
    print("\n".join([
        "",
        "HTTPS activado. Certificado do servidor válido até " + result["expires"] + " para:",
        "  " + ", ".join(result["names"]),
        "",
        ("CA local criada:  " if result["ca_created"] else "CA local reutilizada:  ") + result["ca"],
        "",
        "Reinicie o servidor. O HTTP continua como antes, sem nada a instalar;",
        "o HTTPS fica em  https://%s/  para os dispositivos que querem" % address,
        "a bússola. Para não ver o aviso de segurança, instale a CA nesses",
        "dispositivos (só uma vez; mantém-se válida se voltar a gerar o certificado):",
        "",
        "  iPhone/iPad:  abra no Safari  https://%s/ca.crt  (aceite o aviso)," % address,
        "                Definições → Perfil descarregado → Instalar; depois",
        "                Definições → Geral → Informações → Definições de confiança",
        "                de certificados → activar \"MeshDeckWeb CA\".",
        "  Android:      descarregue /ca.crt e instale em Definições → Segurança →",
        "                Encriptação e credenciais → Instalar certificado → CA.",
        "  PC:           importe meshdeck-ca.crt como Autoridade de certificação",
        "                de raiz fidedigna (ou aceite o aviso do browser).",
        "",
        "Para ficar só em HTTP:  python3 run_server.py --no-tls",
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


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # O Werkzeug e o engineio registam cada pedido; em polling isso enche o log.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)

    config = ServerConfig()

    if args.https_port:
        config.set_https_port(args.https_port)
    if args.make_cert is not None:
        return make_certificate(config, args.make_cert)
    if args.no_tls:
        config.clear_tls()
        print("HTTPS desactivado: no próximo arranque o servidor fica só em HTTP.")
        return 0
    if args.tls_cert or args.tls_key:
        if not (args.tls_cert and args.tls_key):
            print("--tls-cert e --tls-key têm de ser indicados juntos.", file=sys.stderr)
            return 1
        for path in (args.tls_cert, args.tls_key):
            if not os.path.isfile(path):
                print(f"Ficheiro não encontrado: {path}", file=sys.stderr)
                return 1
        config.set_tls(args.tls_cert, args.tls_key)
    if args.set_password:
        return set_password_interactively(config)

    set_language(settings().value("language", "en"))

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
            print(f"Ligação ao nó falhou: {result['error']}", file=sys.stderr)

    def shutdown(_signum, _frame):
        print("\na encerrar…", flush=True)
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
