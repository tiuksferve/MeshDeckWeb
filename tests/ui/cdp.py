"""
tests/ui/cdp.py — Cliente mínimo do Chrome DevTools Protocol para os testes
de interface.

Arranca um Chromium/Chrome headless e fala com ele por WebSocket (o
simple-websocket já é dependência do servidor), sem Selenium nem Playwright:
o uConsole não tem builds do Playwright para ARM e o Chromium do sistema
chega. A internet fica bloqueada no browser (--host-resolver-rules), para os
testes não dependerem da rede nem baterem nos servidores do OpenStreetMap.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request

import simple_websocket

BROWSER_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")


def find_browser():
    """Caminho do Chromium/Chrome, ou None. MESHDECK_BROWSER tem prioridade."""
    explicit = os.environ.get("MESHDECK_BROWSER")
    if explicit:
        return explicit if os.path.exists(explicit) else shutil.which(explicit)
    for name in BROWSER_NAMES:
        path = shutil.which(name)
        if path:
            return path
    return None


class JSError(AssertionError):
    pass


class Browser:
    """Um separador do browser controlado por CDP."""

    def __init__(self, executable: str):
        self.profile = tempfile.mkdtemp(prefix="meshdeck-ui-")
        self.process = subprocess.Popen(
            [executable, "--headless=new", "--remote-debugging-port=0",
             f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
             "--disable-gpu", "--disable-extensions", "--mute-audio", "--window-size=1280,820",
             # Só o servidor de teste é alcançável: OSM, Esri, CDNs… dão erro de DNS.
             "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = self._devtools_port()
        page = next(t for t in self._json(port, "/json") if t["type"] == "page")
        self.ws = simple_websocket.Client.connect(page["webSocketDebuggerUrl"])
        self._seq = 0
        self.errors = []           # excepções JS e console.error, de todos os frames
        self.call("Runtime.enable")
        self.call("Page.enable")

    # -- arranque e fecho -------------------------------------------------
    def _devtools_port(self, timeout=30) -> int:
        """Com a porta 0, o browser escolhe uma livre e escreve-a no perfil."""
        marker = os.path.join(self.profile, "DevToolsActivePort")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("o browser terminou ao arrancar")
            try:
                with open(marker) as fh:
                    return int(fh.readline())
            except (OSError, ValueError):
                time.sleep(0.1)
        raise RuntimeError("o browser não abriu o DevTools a tempo")

    @staticmethod
    def _json(port, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
            return json.load(r)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    # -- protocolo --------------------------------------------------------
    def call(self, method: str, params: dict = None, timeout: float = 30):
        self._seq += 1
        seq = self._seq
        self.ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            raw = self.ws.receive(timeout=max(deadline - time.time(), 0.1))
            if raw is None:
                if time.time() >= deadline:
                    raise TimeoutError(f"{method}: sem resposta do browser")
                continue
            message = json.loads(raw)
            if message.get("id") == seq:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._on_event(message)

    def _on_event(self, message: dict):
        method = message.get("method")
        params = message.get("params", {})
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            text = details.get("exception", {}).get("description") or details.get("text")
            self.errors.append(f"{text} ({details.get('url', '')}:{details.get('lineNumber')})")
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            args = params.get("args", [])
            self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in args))

    # -- ajudas -----------------------------------------------------------
    def js(self, expression: str, timeout: float = 30):
        """Avalia no separador (com await) e devolve o valor em JSON."""
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        }, timeout=timeout)
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise JSError(details.get("exception", {}).get("description") or details.get("text"))
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 10, message: str = None):
        """Repete a expressão até dar um valor verdadeiro; devolve esse valor.
        Um elemento do DOM conta como verdadeiro (por valor viria como {})."""
        expression = f"(() => {{ const v = ({expression}); return v instanceof Node ? true : v; }})()"
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            try:
                value = self.js(expression)
                if value:
                    return value
            except JSError as e:
                last_error = e
            time.sleep(0.1)
        raise AssertionError(message or f"esperei {timeout} s por: {expression}"
                             + (f" (último erro: {last_error})" if last_error else ""))

    def navigate(self, url: str):
        self.call("Page.navigate", {"url": url})
        self.wait_for("document.readyState === 'complete'", timeout=20)

    def viewport(self, width: int, height: int, mobile: bool = False, touch: bool = False):
        self.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
        self.call("Emulation.setTouchEmulationEnabled", {"enabled": touch, "maxTouchPoints": 5 if touch else 1})

    def click(self, selector: str):
        """Clica no primeiro elemento visível que corresponde ao selector."""
        found = self.js(f"""(() => {{
            const el = [...document.querySelectorAll({json.dumps(selector)})].find((e) => e.offsetParent !== null);
            if (!el) return false;
            el.scrollIntoView({{ block: 'center' }});
            el.click();
            return true;
        }})()""")
        assert found, f"nenhum elemento visível para {selector}"

    def text(self, selector: str) -> str:
        return self.js(f"document.querySelector({json.dumps(selector)})?.innerText ?? null")

    def screenshot(self, path: str):
        data = self.call("Page.captureScreenshot", {"format": "png"})["data"]
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))

    def drain_errors(self):
        """Lê eventos pendentes e devolve (e limpa) os erros JS acumulados."""
        self.js("0")
        errors, self.errors = self.errors, []
        return errors
