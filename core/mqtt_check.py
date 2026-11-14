"""
core/mqtt_check.py — O servidor MQTT do nó responde?

Com o MQTT ligado e o servidor inacessível, o meshtasticd fica parado em
cada tentativa de ligação ao servidor (o connect é síncrono e leva uns 2
minutos a desistir): nesse tempo não atende as aplicações ligadas a ele e as
mensagens não saem, e volta a tentar logo a seguir. Do lado de cá não se
evita, mas pode-se avisar: o servidor testa o mesmo endereço e a página
mostra porque é que o nó parece parado.

Com proxy_to_client_enabled o nó não se liga ao servidor (passa as mensagens
à aplicação), por isso não há nada a testar.
"""

import socket
from typing import Optional

DEFAULT_HOST = "mqtt.meshtastic.org"      # o do firmware quando o endereço fica vazio
PLAIN_PORT = 1883
TLS_PORT = 8883
CHECK_INTERVAL_MS = 5 * 60_000
PROBE_TIMEOUT_S = 5


def broker_of(mqtt) -> Optional[tuple]:
    """(host, porta) a que o nó se liga, ou None se não se liga a nenhum.

    mqtt é o moduleConfig.mqtt do nó. O endereço pode trazer a porta
    ("host:1884"); sem ela, 1883, ou 8883 com TLS.
    """
    if mqtt is None or not getattr(mqtt, "enabled", False):
        return None
    if getattr(mqtt, "proxy_to_client_enabled", False):
        return None
    address = (getattr(mqtt, "address", "") or "").strip() or DEFAULT_HOST
    port = TLS_PORT if getattr(mqtt, "tls_enabled", False) else PLAIN_PORT
    host = address
    if address.count(":") == 1:             # host:porta (um IPv6 tem vários ":")
        host, _, port_text = address.partition(":")
        if port_text.isdigit():
            port = int(port_text)
    return host, port


def reachable(host: str, port: int, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """Abre e fecha uma ligação TCP. Bloqueia até timeout: chamar fora do loop."""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False
