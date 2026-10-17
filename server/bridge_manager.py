"""
server/bridge_manager.py — Ciclo de vida da ponte USB→TCP.

A ponte é arrancada pelo processo que serve a interface e morre com ele;
senão ficaria a segurar a porta serial depois de o servidor sair.

meshtastic_bridge.py corre como subprocesso, tal como pela linha de comandos.
"""

import logging
import os
import subprocess
import sys
import threading
import time

from i18n import tr

logger = logging.getLogger("MeshDeck.bridge")

BRIDGE_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "meshtastic_bridge.py",
)
STARTUP_GRACE_S = 2.0      # tempo para a ponte abrir o porto TCP
STOP_TIMEOUT_S = 5.0


def list_serial_ports() -> list:
    """Portas serial candidatas a ter um nó Meshtastic.

    Reutiliza a tabela de VID conhecidos de meshtastic_bridge.py em vez de
    repetir a lista aqui.
    """
    try:
        import serial.tools.list_ports
        from meshtastic_bridge import KNOWN_DEVICES
    except ImportError as e:
        logger.warning("não consigo listar portas serial: %s", e)
        return []

    out = []
    for info in serial.tools.list_ports.comports():
        vid = f"{info.vid:04x}" if info.vid else ""
        desc = (info.description or "").lower()
        mfr = (info.manufacturer or "").lower()
        known = vid in KNOWN_DEVICES
        by_name = any(kw in desc or kw in mfr for kw in
                      ("meshtastic", "rak", "heltec", "lilygo", "t-beam", "tbeam"))
        out.append({
            "device":      info.device,
            "description": info.description or "",
            "vendor":      KNOWN_DEVICES.get(vid, ""),
            "likely":      bool(known or by_name),
        })
    # Os candidatos prováveis primeiro, para a lista da interface ser útil.
    out.sort(key=lambda p: (not p["likely"], p["device"]))
    return out


class BridgeManager:
    """Arranca e pára a ponte serial como subprocesso filho."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4403):
        self.host = host
        self.port = port
        self._proc: subprocess.Popen = None
        self._device: str = None
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def device(self):
        return self._device

    def status(self) -> dict:
        return {"running": self.is_running, "device": self._device,
                "host": self.host, "port": self.port}

    def start(self, device: str, baud: int = 115200) -> dict:
        """Arranca a ponte. Devolve {"ok": bool, "error": str}."""
        with self._lock:
            if self.is_running:
                if self._device == device:
                    return {"ok": True, "error": ""}
                self.stop()

            if not os.path.exists(BRIDGE_SCRIPT):
                return {"ok": False, "error": tr("ponte não encontrada em {path}", path=BRIDGE_SCRIPT)}

            cmd = [sys.executable, BRIDGE_SCRIPT, "--port", device,
                   "--baud", str(baud), "--host", self.host,
                   "--tcp-port", str(self.port)]
            logger.info("a arrancar a ponte serial: %s", " ".join(cmd))
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
            except OSError as e:
                self._proc = None
                return {"ok": False, "error": str(e)}

            self._device = device
            threading.Thread(target=self._drain_output, args=(self._proc,),
                             daemon=True, name="bridge-log").start()

        # A ponte precisa de um instante para abrir a porta serial e o socket.
        # Se morrer nesse intervalo, o erro é dela e vale a pena reportá-lo.
        time.sleep(STARTUP_GRACE_S)
        if not self.is_running:
            code = self._proc.returncode if self._proc else "?"
            self._device = None
            return {"ok": False,
                    "error": tr("a ponte terminou imediatamente (código {code}); "
                                "verifique se a porta existe e tem permissões", code=code)}
        return {"ok": True, "error": ""}

    def stop(self):
        with self._lock:
            proc = self._proc
            self._proc = None
            self._device = None
        if proc is None or proc.poll() is not None:
            return
        logger.info("a parar a ponte serial")
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            logger.warning("a ponte não respondeu ao terminate; a matar")
            proc.kill()
            proc.wait(timeout=STOP_TIMEOUT_S)

    @staticmethod
    def _drain_output(proc: subprocess.Popen):
        """Reencaminha o log da ponte para o nosso, para aparecer na consola web.

        Sem isto, o pipe enche e a ponte bloqueia a escrever.
        """
        try:
            for line in proc.stdout:
                logger.info("ponte: %s", line.rstrip())
        except Exception as e:
            logger.debug("leitura do log da ponte terminou: %s", e)
