#!/usr/bin/env python3
"""
install.py — Instalação guiada do MeshDeckWeb / MeshDeckWeb guided setup.

    python3 install.py          (Linux, macOS)
    py install.py               (Windows)

Funciona em duas etapas. A primeira corre com o Python do sistema e só usa a
biblioteca padrão: verifica a versão, cria o ambiente virtual (.venv) e
instala as dependências. A segunda corre já dentro do .venv e configura a
primeira execução com o próprio código do servidor (password, acesso,
ligação ao nó, HTTPS, mapa offline): o resultado é o mesmo que as opções do
run_server.py.

No fim fica um atalho de arranque (start.sh ou start.bat). Pode correr o
instalador outra vez para mudar a configuração; o ambiente já instalado é
reaproveitado.
"""

import getpass
import os
import socket
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
WINDOWS = os.name == "nt"
VENV_PY = os.path.join(VENV, "Scripts", "python.exe") if WINDOWS else os.path.join(VENV, "bin", "python")
LAUNCHER = os.path.join(ROOT, "start.bat" if WINDOWS else "start.sh")
MIN_PYTHON = (3, 9)

TEXTS = {
    "title": ("📡 MeshDeckWeb — instalação guiada", "📡 MeshDeckWeb — guided setup"),
    "old_python": ("É precisa a versão {need} do Python ou mais recente (tem a {have}).",
                   "Python {need} or newer is required (you have {have})."),
    "step_venv": ("A criar o ambiente virtual em .venv …", "Creating the virtual environment in .venv …"),
    "venv_failed": ("Não foi possível criar o ambiente virtual.", "Could not create the virtual environment."),
    "venv_hint": ("No Debian, Ubuntu e Raspberry Pi OS instale primeiro:  sudo apt install python3-venv",
                  "On Debian, Ubuntu and Raspberry Pi OS install it first:  sudo apt install python3-venv"),
    "step_deps": ("A instalar as dependências (pode demorar alguns minutos) …",
                  "Installing the dependencies (this may take a few minutes) …"),
    "deps_failed": ("A instalação das dependências falhou. Veja as mensagens acima e confirme a ligação à internet.",
                    "Installing the dependencies failed. Check the messages above and your internet connection."),
    "deps_ok": ("✅ Dependências instaladas.", "✅ Dependencies installed."),
    "yes_no": (" [s/n]", " [y/n]"),
    "yes_letters": (("s", "sim", "y", "yes"), ("y", "yes", "s", "sim")),
    "invalid": ("Resposta inválida, tente de novo.", "Invalid answer, try again."),

    "sec_access": ("1/5 · Acesso", "1/5 · Access"),
    "ask_network": ("Abrir o servidor à rede local, para entrar a partir do telemóvel?",
                    "Open the server to your local network, to use it from your phone?"),
    "network_warn": ("⚠ Quem entrar pode reconfigurar o nó, incluindo canais e PSK. Use só em redes de confiança.",
                     "⚠ Anyone who logs in can reconfigure the node, channels and PSKs included. Only on trusted networks."),
    "ask_port": ("Porta HTTP", "HTTP port"),
    "port_busy": ("⚠ A porta {port} está ocupada neste momento (talvez o MeshDeckWeb já esteja a correr).\n"
                  "  Pare o outro programa antes de arrancar, ou corra o instalador outra vez e escolha outra porta.",
                  "⚠ Port {port} is in use right now (maybe MeshDeckWeb is already running).\n"
                  "  Stop the other program before starting, or run the installer again and pick another port."),

    "sec_password": ("2/5 · Password de acesso", "2/5 · Access password"),
    "ask_own_password": ("Quer escolher a password? (se não, fica uma gerada ao acaso)",
                         "Do you want to choose the password? (otherwise a random one is kept)"),
    "ask_keep_password": ("Já existe uma password. Mantê-la?", "A password already exists. Keep it?"),
    "pw_enter": ("Nova password (mínimo 8 caracteres): ", "New password (at least 8 characters): "),
    "pw_repeat": ("Repita: ", "Repeat: "),
    "pw_mismatch": ("As passwords não coincidem.", "The passwords do not match."),
    "pw_generated": ("Password gerada — GUARDE-A, não volta a ser mostrada:",
                     "Generated password — SAVE IT, it will not be shown again:"),
    "pw_set": ("✅ Password definida.", "✅ Password set."),

    "sec_node": ("3/5 · Ligação ao nó Meshtastic", "3/5 · Meshtastic node connection"),
    "node_menu": ("Como liga ao nó?\n"
                  "  1) meshtasticd nesta máquina (localhost:4403)\n"
                  "  2) Nó noutro endereço da rede (TCP)\n"
                  "  3) Cabo USB (série)\n"
                  "  4) Escolho depois, no browser",
                  "How do you connect to the node?\n"
                  "  1) meshtasticd on this machine (localhost:4403)\n"
                  "  2) Node at another network address (TCP)\n"
                  "  3) USB cable (serial)\n"
                  "  4) I'll choose later, in the browser"),
    "ask_choice": ("Opção", "Option"),
    "ask_node_host": ("Endereço do nó", "Node address"),
    "ask_node_port": ("Porta do nó", "Node port"),
    "no_ports": ("Não foi encontrada nenhuma porta USB. Confirme que o cabo transmite dados; pode escolher a porta depois no browser.",
                 "No USB port found. Check that the cable carries data; you can pick the port later in the browser."),
    "port_list": ("Portas encontradas:", "Ports found:"),
    "ask_port_number": ("Número da porta", "Port number"),
    "serial_perm": ("⚠ O seu utilizador não tem acesso a {dev}. Corra:  sudo usermod -aG dialout $USER  e volte a entrar na sessão.",
                    "⚠ Your user cannot access {dev}. Run:  sudo usermod -aG dialout $USER  and log in again."),
    "ask_autoconnect": ("Ligar ao nó automaticamente ao arrancar o servidor?",
                        "Connect to the node automatically when the server starts?"),

    "sec_https": ("4/5 · HTTPS (bússola do telemóvel)", "4/5 · HTTPS (phone compass)"),
    "https_info": ("Os browsers só dão acesso à bússola do telemóvel em HTTPS. O HTTPS fica numa segunda porta;\n"
                   "o HTTP continua igual para os outros dispositivos.",
                   "Browsers only give access to the phone compass over HTTPS. HTTPS runs on a second port;\n"
                   "plain HTTP stays the same for other devices."),
    "ask_https": ("Activar HTTPS?", "Enable HTTPS?"),
    "ask_https_port": ("Porta HTTPS", "HTTPS port"),
    "https_ok": ("✅ Certificado criado para: {names}", "✅ Certificate created for: {names}"),
    "https_off": ("HTTPS desactivado.", "HTTPS disabled."),

    "sec_map": ("5/5 · Mapa offline", "5/5 · Offline map"),
    "map_info": ("O servidor guarda os mosaicos do mapa que forem vistos, para o mapa funcionar sem internet\n"
                 "nessas zonas. Uso típico: 20–60 MB. Use 0 para não guardar nada.",
                 "The server keeps the map tiles you view, so the map works without internet in those areas.\n"
                 "Typical use: 20–60 MB. Use 0 to keep nothing."),
    "ask_map_mb": ("Espaço máximo em MB", "Maximum space in MB"),
    "launcher": ("✅ Atalho de arranque criado: {path}", "✅ Start shortcut created: {path}"),
    "done": ("Pronto! Para arrancar o servidor:", "Done! To start the server:"),
    "open_at": ("Depois abra no browser:", "Then open in the browser:"),
    "open_https": ("Com bússola (HTTPS):", "With compass (HTTPS):"),
    "ca_hint": ("Para não ver o aviso de segurança, instale a CA em cada dispositivo a partir de {url}\n"
                "(passos no README, secção \"HTTPS e a Bússola do Dispositivo\").",
                "To avoid the security warning, install the CA on each device from {url}\n"
                "(steps in the README, section \"HTTPS and the Device Compass\")."),
    "firewall": ("Se o telemóvel não abrir a página, confirme que a firewall deixa passar a porta {port}.",
                 "If the phone cannot open the page, check that the firewall allows port {port}."),
    "ip_hint": ("O IP pode mudar. Para o fixar, reserve-o no router (DHCP estático).",
                "The IP may change. To keep it fixed, reserve it in your router (static DHCP)."),
    "mac_ip_hint": ("No macOS, ponha também \"Endereço Wi-Fi privado\" em Fixo (Definições → Wi-Fi → Detalhes).\n"
                    "Se o IP mudar, o servidor mostra o novo ao arrancar; o HTTPS precisa de  run_server.py --make-cert.",
                    "On macOS, also set \"Private Wi-Fi Address\" to Fixed (Settings → Wi-Fi → Details).\n"
                    "If the IP changes, the server shows the new one at start; HTTPS then needs  run_server.py --make-cert."),
    "mac_fw_on": ("A firewall do macOS está ligada e ainda não deixa o Python aceitar ligações:\n"
                  "  {app}\nSem isto, o telemóvel não chega ao servidor (no próprio Mac, localhost funciona).",
                  "The macOS firewall is on and does not yet let Python accept connections:\n"
                  "  {app}\nWithout this, the phone cannot reach the server (localhost on the Mac still works)."),
    "mac_fw_blockall": ("⚠ A firewall está a \"Bloquear todas as ligações de entrada\": desligue essa opção\n"
                        "  em Definições → Rede → Firewall → Opções, senão nenhuma autorização tem efeito.",
                        "⚠ The firewall is set to \"Block all incoming connections\": turn that option off\n"
                        "  in Settings → Network → Firewall → Options, or no permission will take effect."),
    "mac_fw_ask": ("Autorizar agora? (pede a password do Mac)", "Allow it now? (asks for your Mac password)"),
    "mac_fw_ok": ("✅ Python autorizado na firewall.", "✅ Python allowed through the firewall."),
    "mac_fw_manual": ("Para autorizar mais tarde, corra:", "To allow it later, run:"),
    "ask_start": ("Arrancar o servidor agora?", "Start the server now?"),
}

lang = "en"


def t(key, **kwargs):
    text = TEXTS[key][0 if lang == "pt" else 1]
    return text.format(**kwargs) if kwargs else text


def _read_line_unbuffered(prompt):
    """input() sem buffer: lê só até ao fim da linha, deixando o resto da
    entrada para a segunda etapa (outro processo). Com o stdin num ficheiro
    ou pipe, o input() normal leria tudo de uma vez."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    data = b""
    while True:
        ch = os.read(0, 1)
        if not ch or ch == b"\n":
            break
        data += ch
    return data.decode("utf-8", "replace").rstrip("\r")


def ask(prompt, default="", unbuffered=False):
    suffix = f" [{default}]" if default != "" else ""
    try:
        text = f"{prompt}{suffix}: "
        answer = (_read_line_unbuffered(text) if unbuffered else input(text)).strip()
    except EOFError:
        answer = ""
    return answer or str(default)


def port_in_use(port, host="0.0.0.0"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def ask_int(prompt, default, low=1, high=65535):
    while True:
        value = ask(prompt, default)
        if value.isdigit() and low <= int(value) <= high:
            return int(value)
        print(t("invalid"))


def ask_yes(prompt, default=True):
    hint = t("yes_no")
    default_txt = (("s" if lang == "pt" else "y") if default else "n")
    while True:
        try:
            answer = input(f"{prompt}{hint} [{default_txt}]: ").strip().lower()
        except EOFError:
            answer = ""
        if not answer:
            return default
        if answer in t("yes_letters"):
            return True
        if answer in ("n", "no", "nao", "não"):
            return False
        print(t("invalid"))


def section(key):
    print(f"\n── {t(key)} " + "─" * max(4, 60 - len(t(key))))


# ── etapa 1: Python do sistema ───────────────────────────────────────────────

def choose_language():
    global lang
    answer = ask("Idioma / Language — pt / en", "pt", unbuffered=True).lower()
    lang = "pt" if answer.startswith("p") else "en"


def stage_install():
    print("\n" + "=" * 64)
    choose_language()
    print(t("title"))
    print("=" * 64)
    if sys.version_info < MIN_PYTHON:
        print(t("old_python", need=".".join(map(str, MIN_PYTHON)),
                have=".".join(map(str, sys.version_info[:3]))))
        return 1

    if not os.path.exists(VENV_PY):
        print("\n" + t("step_venv"))
        if subprocess.call([sys.executable, "-m", "venv", VENV]) != 0 or not os.path.exists(VENV_PY):
            print(t("venv_failed"))
            if sys.platform.startswith("linux"):
                print(t("venv_hint"))
            return 1

    print("\n" + t("step_deps"))
    requirements = os.path.join(ROOT, "requirements.txt")
    if subprocess.call([VENV_PY, "-m", "pip", "install", "--disable-pip-version-check",
                        "-q", "-r", requirements]) != 0:
        print(t("deps_failed"))
        return 1
    print(t("deps_ok"))

    # Segunda etapa dentro do .venv, onde o código do servidor já importa.
    return subprocess.call([VENV_PY, os.path.abspath(__file__), "--configure", lang])


# ── etapa 2: dentro do .venv ─────────────────────────────────────────────────

def configure_password(config):
    section("sec_password")
    if config.generated_password:
        wants_own = ask_yes(t("ask_own_password"), default=False)
    else:
        wants_own = not ask_yes(t("ask_keep_password"), default=True)
    if wants_own:
        while True:
            first = getpass.getpass(t("pw_enter"))
            if first != getpass.getpass(t("pw_repeat")):
                print(t("pw_mismatch"))
                continue
            try:
                config.set_password(first)
            except ValueError as e:
                print(e)
                continue
            print(t("pw_set"))
            return
    elif config.generated_password:
        print(f"\n  {t('pw_generated')}\n\n      {config.generated_password}\n")


def configure_node(config):
    """Devolve as opções de arranque (lista) para o atalho."""
    from server.bridge_manager import list_serial_ports
    section("sec_node")
    print(t("node_menu"))
    choice = ask(t("ask_choice"), "1")
    while choice not in ("1", "2", "3", "4"):
        print(t("invalid"))
        choice = ask(t("ask_choice"), "1")

    if choice == "4":
        return []
    if choice in ("1", "2"):
        host, port = "localhost", 4403
        if choice == "2":
            host = ask(t("ask_node_host"), config.last_target.get("host") or "192.168.1.50")
            port = ask_int(t("ask_node_port"), 4403)
        config.remember_target("tcp", host, port, "")
        config.record_target(host, port)
        return ["--connect"] if ask_yes(t("ask_autoconnect"), default=True) else []

    ports = list_serial_ports()
    if not ports:
        print(t("no_ports"))
        return []
    print(t("port_list"))
    for i, p in enumerate(ports, 1):
        star = " ⭐" if p["likely"] else ""
        print(f"  {i}) {p['device']}  {p['description']} {p['vendor']}{star}")
    number = ask_int(t("ask_port_number"), 1, 1, len(ports))
    device = ports[number - 1]["device"]
    config.remember_target("serial", "localhost", 4403, device)
    if not WINDOWS and os.path.exists(device) and not os.access(device, os.R_OK | os.W_OK):
        print(t("serial_perm", dev=device))
    return ["--serial", device] if ask_yes(t("ask_autoconnect"), default=True) else []


def configure_https(config, network: bool):
    """Só com acesso pela rede: em localhost o browser é o do próprio
    computador, que não tem bússola."""
    from server import tls
    if not network:
        if config.tls_enabled:
            config.clear_tls()
        return
    section("sec_https")
    print(t("https_info"))
    if not ask_yes(t("ask_https"), default=False):
        if config.tls_enabled:
            config.clear_tls()
            print(t("https_off"))
        return
    https_port = ask_int(t("ask_https_port"), config.https_port)
    if port_in_use(https_port):
        print(t("port_busy", port=https_port))
    config.set_https_port(https_port)
    result = tls.make_certificates(os.path.dirname(config.path), tls.default_names([]))
    config.set_tls(result["cert"], result["key"])
    print(t("https_ok", names=", ".join(result["names"])))


MAC_FIREWALL = "/usr/libexec/ApplicationFirewall/socketfilterfw"


def macos_python_app():
    """O que a firewall do macOS vê a escutar: num Python "framework"
    (python.org, Homebrew) o processo corre dentro do Python.app, não no
    python3 do .venv, e a regra só funciona se apontar para essa app."""
    app = os.path.realpath(os.path.join(sys.base_prefix, "Resources", "Python.app"))
    return app if os.path.isdir(app) else os.path.realpath(sys.executable)


def macos_firewall(run=None):
    """Com a firewall do macOS ligada, as ligações vindas da rede são
    recusadas sem aviso até o Python ser autorizado. Oferece-se para o
    autorizar (com sudo), ou mostra os comandos."""
    if sys.platform != "darwin" or not os.path.exists(MAC_FIREWALL):
        return
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True).stdout)
    if "is enabled" not in run([MAC_FIREWALL, "--getglobalstate"]):
        return
    app = macos_python_app()
    if "permitted" in run([MAC_FIREWALL, "--getappblocked", app]):
        return
    print("\n" + t("mac_fw_on", app=app))
    if "enabled" in run([MAC_FIREWALL, "--getblockall"]).lower():
        print(t("mac_fw_blockall"))
    commands = [["sudo", MAC_FIREWALL, "--add", app], ["sudo", MAC_FIREWALL, "--unblockapp", app]]
    if ask_yes(t("mac_fw_ask"), default=True):
        if all(subprocess.call(c) == 0 for c in commands):
            print(t("mac_fw_ok"))
            return
    print(t("mac_fw_manual"))
    for c in commands:
        print("    " + " ".join(f'"{a}"' if " " in a else a for a in c))


def write_launcher(flags):
    args = "".join(" " + (f'"{a}"' if " " in a else a) for a in flags)
    if WINDOWS:
        body = f'@echo off\r\ncd /d "%~dp0"\r\n".venv\\Scripts\\python.exe" run_server.py{args} %*\r\n'
    else:
        body = f'#!/bin/sh\n# Arranca o MeshDeckWeb (criado pelo install.py)\ncd "$(dirname "$0")"\nexec .venv/bin/python run_server.py{args} "$@"\n'
    with open(LAUNCHER, "w", newline="") as fh:
        fh.write(body)
    if not WINDOWS:
        os.chmod(LAUNCHER, 0o755)
    print("\n" + t("launcher", path=os.path.basename(LAUNCHER)))


def stage_configure(chosen_lang):
    global lang
    lang = chosen_lang
    sys.path.insert(0, ROOT)
    from core.settings import settings
    from server import tls
    from server.auth import ServerConfig

    config = ServerConfig()            # numa instalação nova gera a password aqui
    settings().set_value("language", lang)

    section("sec_access")
    network = ask_yes(t("ask_network"), default=True)
    if network:
        print(t("network_warn"))
    port = ask_int(t("ask_port"), config.bind_port)
    if port_in_use(port):
        print(t("port_busy", port=port))
    config.set_bind("0.0.0.0" if network else "127.0.0.1", port)

    configure_password(config)
    flags = configure_node(config)
    configure_https(config, network)
    section("sec_map")
    print(t("map_info"))
    config.set_tile_cache_mb(ask_int(t("ask_map_mb"), config.tile_cache_mb, 0, 100000))
    write_launcher(flags)

    hosts = (tls.local_addresses() or ["<ip>"]) if network else ["127.0.0.1"]
    run = "start.bat" if WINDOWS else "./start.sh"
    print("\n" + "=" * 64)
    print(t("done") + f"\n\n    {run}\n")
    print(t("open_at"))
    for h in hosts:
        print(f"    http://{h}:{port}/")
    if config.tls_enabled:
        print(t("open_https"))
        for h in hosts:
            print(f"    https://{h}:{config.https_port}/")
        print("\n" + t("ca_hint", url=f"https://{hosts[0]}:{config.https_port}/ca.crt"))
    if network:
        print("\n" + t("ip_hint"))
        if sys.platform == "darwin":
            print(t("mac_ip_hint"))
        else:
            print(t("firewall", port=port))
    print("=" * 64)
    if network:
        macos_firewall()

    if ask_yes(t("ask_start"), default=True):
        os.chdir(ROOT)
        return subprocess.call([VENV_PY, "run_server.py", *flags])
    return 0


if __name__ == "__main__":
    # Linha a linha, também com a saída redireccionada: as duas etapas
    # escrevem no mesmo ecrã e a ordem tem de se manter.
    sys.stdout.reconfigure(line_buffering=True)
    try:
        if len(sys.argv) >= 3 and sys.argv[1] == "--configure":
            sys.exit(stage_configure(sys.argv[2]))
        sys.exit(stage_install())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
