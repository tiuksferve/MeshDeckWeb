"""
core/automations.py — Respostas automáticas a comandos recebidos por mensagem.

Alguém manda "ping" ao nó e o servidor responde sozinho, com os saltos e o
sinal com que a mensagem chegou. Corre no servidor, por isso funciona com o
browser fechado.

Um comando é a mensagem inteira: "ping" responde, "fiz ping ao router" não.
A comparação ignora maiúsculas, acentos e pontuação no fim ("Ping!", "sinal?").

Uma rede LoRa partilha pouco tempo de antena, e um bot mal configurado pode
enchê-la. Por isso vem desligado e, ligado, tem travões: tempo mínimo entre
respostas ao mesmo nó, limite por minuto, pausa quando o nó local já gastou
muito tempo de antena (o duty cycle da EU_868), e nunca responde a uma
resposta (assim dois bots não ficam a falar um com o outro).

Este módulo não fala com o rádio: decide se responde e o quê. Quem envia é
a sessão (server/session.py).
"""

import math
import re
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from i18n import get_language, tr, use_language

# ── limites ──────────────────────────────────────────────────────────────────
MAX_REPLY_BYTES = 200          # cabe numa mensagem Meshtastic com folga
MAX_TEMPLATE_CHARS = 300       # o modelo pode ter variáveis que encolhem
MAX_CUSTOM = 20
LOG_MAX = 50
REPLY_DELAY_MS = 1500          # deixa sair o ACK da mensagem antes da resposta
AIRTIME_LIMIT_PCT = 8.0        # a EU_868 permite 10 % por hora; fica margem
SIGNAL_WINDOW_S = 3600
SIGNAL_MAX_SAMPLES = 30
COOLDOWN_RANGE = (5, 3600)
PER_MINUTE_RANGE = (1, 30)
CHANNEL_RANGE = range(0, 8)

WORD_RE = re.compile(r"[a-z0-9_-]{1,24}")

ALL, FAVORITES = "all", "favorites"

# Resultados no registo.
SENT = "sent"
COOLDOWN = "cooldown"
LIMIT = "limit"
AIRTIME = "airtime"
DENIED = "denied"
MQTT = "mqtt"


@dataclass(frozen=True)
class Builtin:
    key: str
    words: tuple                  # todas as formas aceites, já normalizadas
    label: dict                   # nome mostrado no "ajuda", por idioma
    who: str = ALL
    requires: tuple = ()          # variáveis sem as quais se usa o texto de falta


BUILTINS: Dict[str, Builtin] = {b.key: b for b in (
    Builtin("ping", ("ping",), {"pt": "ping", "en": "ping"}),
    Builtin("help", ("ajuda", "help", "comandos", "commands", "?"), {"pt": "ajuda", "en": "help"}),
    Builtin("info", ("info", "status", "estado"), {"pt": "info", "en": "info"}),
    Builtin("signal", ("sinal", "signal", "snr"), {"pt": "sinal", "en": "signal"}),
    Builtin("nodes", ("nos", "nodes"), {"pt": "nos", "en": "nodes"}),
    Builtin("dist", ("dist", "distancia", "distance"), {"pt": "dist", "en": "dist"},
            who=FAVORITES, requires=("dist",)),
    Builtin("pos", ("pos", "posicao", "position", "onde", "where"), {"pt": "pos", "en": "pos"},
            who=FAVORITES, requires=("pos",)),
    Builtin("time", ("hora", "time"), {"pt": "hora", "en": "time"}),
)}

# Variáveis dos modelos, pela ordem em que a interface as lista.
VARIABLES = (
    "name", "short", "id", "hops", "signal", "snr", "rssi", "via", "heard",
    "my_name", "my_short", "battery", "uptime", "chutil", "airtx",
    "nodes_1h", "nodes_24h", "direct", "pos", "alt", "dist", "bearing",
    "time", "date", "commands",
)

VAR_RE = re.compile(r"\{(\w+)\}")
SEPARATOR = " · "


def normalize(text: str) -> str:
    """A mensagem reduzida à forma em que se compara com os comandos.

    Devolve "" quando não é uma palavra só: uma frase nunca é um comando.
    """
    t = unicodedata.normalize("NFKD", (text or "").strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    if t == "?":
        return t
    t = t.lstrip("/!").rstrip("?!.")
    return t if WORD_RE.fullmatch(t) else ""


def default_template(key: str) -> str:
    return tr(f"auto_tpl_{key}")


def default_config() -> dict:
    return {
        "enabled": False,
        "dm": True,
        "channels": [],
        "cooldown_s": 30,
        "max_per_min": 5,
        "ignore_mqtt": True,
        "commands": {key: {"enabled": True, "who": b.who, "template": ""}
                     for key, b in BUILTINS.items()},
        "custom": [],
    }


def _int_in(value, lo, hi, name) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(tr("auto_err_number", field=name))
    if not lo <= number <= hi:
        raise ValueError(tr("auto_err_range", field=name, lo=lo, hi=hi))
    return number


def validate(payload: dict) -> dict:
    """Configuração vinda do browser, conferida e completada. ValueError
    com uma mensagem para o utilizador se alguma coisa não serve."""
    if not isinstance(payload, dict):
        raise ValueError(tr("pedido inválido"))
    cfg = default_config()
    cfg["enabled"] = bool(payload.get("enabled", cfg["enabled"]))
    cfg["dm"] = bool(payload.get("dm", cfg["dm"]))
    cfg["ignore_mqtt"] = bool(payload.get("ignore_mqtt", cfg["ignore_mqtt"]))
    cfg["cooldown_s"] = _int_in(payload.get("cooldown_s", cfg["cooldown_s"]),
                                *COOLDOWN_RANGE, tr("auto_f_cooldown"))
    cfg["max_per_min"] = _int_in(payload.get("max_per_min", cfg["max_per_min"]),
                                 *PER_MINUTE_RANGE, tr("auto_f_per_min"))

    channels = payload.get("channels") or []
    if not isinstance(channels, list):
        raise ValueError(tr("pedido inválido"))
    cfg["channels"] = sorted({_int_in(c, 0, 7, tr("Canal")) for c in channels})

    commands = payload.get("commands") or {}
    if not isinstance(commands, dict):
        raise ValueError(tr("pedido inválido"))
    for key, entry in commands.items():
        if key not in BUILTINS or not isinstance(entry, dict):
            continue
        target = cfg["commands"][key]
        target["enabled"] = bool(entry.get("enabled", target["enabled"]))
        target["who"] = FAVORITES if entry.get("who") == FAVORITES else ALL
        template = str(entry.get("template") or "").strip()
        if len(template) > MAX_TEMPLATE_CHARS:
            raise ValueError(tr("auto_err_template_long", word=key, max=MAX_TEMPLATE_CHARS))
        # O texto por omissão não se grava: assim segue o idioma do servidor.
        target["template"] = "" if template == default_template(key) else template

    custom = payload.get("custom") or []
    if not isinstance(custom, list):
        raise ValueError(tr("pedido inválido"))
    if len(custom) > MAX_CUSTOM:
        raise ValueError(tr("auto_err_custom_many", max=MAX_CUSTOM))
    taken = {w for b in BUILTINS.values() for w in b.words}
    for entry in custom:
        if not isinstance(entry, dict):
            raise ValueError(tr("pedido inválido"))
        raw_word = str(entry.get("word") or "").strip()
        word = normalize(raw_word)
        if not word or word == "?":
            raise ValueError(tr("auto_err_word", word=raw_word))
        if word in taken:
            raise ValueError(tr("auto_err_word_taken", word=word))
        taken.add(word)
        reply = str(entry.get("reply") or "").strip()
        if not reply:
            raise ValueError(tr("auto_err_reply_empty", word=word))
        if len(reply) > MAX_TEMPLATE_CHARS:
            raise ValueError(tr("auto_err_template_long", word=word, max=MAX_TEMPLATE_CHARS))
        cfg["custom"].append({"word": word, "reply": reply,
                              "enabled": bool(entry.get("enabled", True)),
                              "who": FAVORITES if entry.get("who") == FAVORITES else ALL})
    return cfg


def merge_saved(saved) -> dict:
    """Configuração guardada em disco, completada com o que lhe faltar
    (comandos novos de uma versão mais recente). Um ficheiro estragado
    à mão não impede o arranque: fica a configuração por omissão."""
    try:
        return validate(saved) if isinstance(saved, dict) else default_config()
    except ValueError:
        return default_config()


def truncate_utf8(text: str, max_bytes: int = MAX_REPLY_BYTES) -> str:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[:max_bytes - 3].decode("utf-8", errors="ignore").rstrip() + "…"


def render(template: str, values: dict) -> str:
    """Preenche o modelo. Variáveis sem valor desaparecem, e com elas o
    separador " · " que ficaria sozinho; uma variável desconhecida fica como
    está, para o utilizador ver o erro na pré-visualização."""
    def sub(match):
        name = match.group(1)
        if name not in values:
            return match.group(0)
        value = values[name]
        return "" if value is None else str(value)
    text = VAR_RE.sub(sub, template)
    lines = []
    for line in text.split("\n"):
        parts = [p for p in line.split(SEPARATOR) if p.strip()]
        lines.append(SEPARATOR.join(p.strip() for p in parts) if len(parts) > 1
                     else (parts[0].strip() if parts else ""))
    return "\n".join(lines).strip()


# ── formatação dos valores ───────────────────────────────────────────────────

def _fmt_uptime(seconds) -> Optional[str]:
    if seconds is None:
        return None
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"up {days}d{hours}h"
    if hours:
        return f"up {hours}h{minutes:02d}m"
    return f"up {minutes}m"


def _fmt_battery(level, voltage=None) -> Optional[str]:
    if level is None:
        return f"{voltage:.2f} V" if voltage else None
    if level > 100:
        return "🔌"
    return f"🔋{int(level)}%"


def _fmt_pct(label: str, value) -> Optional[str]:
    return None if value is None else f"{label} {float(value):.1f}%"


def _fmt_distance(km: float) -> str:
    if km < 1:
        return f"{km * 1000:.0f} m"
    return f"{km:.1f} km" if km < 100 else f"{km:.0f} km"


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass_point(deg: float) -> str:
    points = tr("auto_compass_points").split()
    return points[int((deg + 22.5) // 45) % 8] if len(points) == 8 else ""


def packet_hops(packet: dict) -> Optional[int]:
    """Saltos desta mensagem: hopStart − hopLimit, do próprio pacote. O
    hopsAway da NodeDB é do último pacote do nó, que pode ter vindo por
    outro caminho."""
    start, limit = packet.get("hopStart"), packet.get("hopLimit")
    if start is not None and limit is not None:
        try:
            return max(0, int(start) - int(limit))
        except (TypeError, ValueError):
            pass
    hops = packet.get("hopsAway")
    return int(hops) if isinstance(hops, (int, float)) else None


class SignalLog:
    """SNR e RSSI dos pacotes recebidos directamente de cada nó, na última
    hora. Só os directos contam: num pacote retransmitido o sinal é o do
    último retransmissor, não o de quem o enviou."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._samples: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def record(self, packet: dict):
        if not packet or packet.get("viaMqtt"):
            return
        node_id = (packet.get("fromId") or "").lower()
        snr = packet.get("rxSnr")
        if not node_id or snr is None or packet_hops(packet) != 0:
            return
        if packet.get("hopStart") is None:
            return                    # firmware antigo: não se sabe se foi directo
        with self._lock:
            samples = self._samples.setdefault(node_id, deque(maxlen=SIGNAL_MAX_SAMPLES))
            samples.append((self._clock(), float(snr), packet.get("rxRssi")))

    def summary(self, node_id: str) -> Optional[dict]:
        cutoff = self._clock() - SIGNAL_WINDOW_S
        with self._lock:
            samples = [s for s in self._samples.get((node_id or "").lower(), ()) if s[0] >= cutoff]
        if not samples:
            return None
        rssis = [s[2] for s in samples if s[2] is not None]
        return {"count": len(samples),
                "snr": sum(s[1] for s in samples) / len(samples),
                "rssi": sum(rssis) / len(rssis) if rssis else None}

    def clear(self):
        with self._lock:
            self._samples.clear()


@dataclass
class Request:
    """Uma mensagem recebida. channel=None numa DM."""
    from_id: str
    text: str
    channel: Optional[int]
    packet: dict = field(default_factory=dict)


@dataclass
class MeshView:
    """O que as respostas precisam de saber da rede, emprestado pela sessão."""
    nodes: object                                   # NodeStore
    local_position: dict
    is_favorite: Callable[[str], bool]
    signal_log: SignalLog
    now: Callable[[], datetime] = datetime.now


@dataclass
class Decision:
    command: str                  # chave do comando, ou a palavra do personalizado
    outcome: str
    reply: str = ""
    channel: Optional[int] = None
    to_id: str = ""
    reply_id: int = 0
    log_entry: dict = field(default_factory=dict)


class AutomationEngine:
    """Configuração, travões e registo das automações."""

    def __init__(self, settings, clock: Callable[[], float] = time.monotonic):
        """settings: core.settings.Settings onde a configuração persiste."""
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._config = merge_saved(settings.value("automations"))
        self._last_reply: Dict[str, float] = {}
        self._recent: deque = deque()
        self._log: deque = deque(maxlen=LOG_MAX)
        self.signal_log = SignalLog()

    # -- configuração -----------------------------------------------------
    @property
    def config(self) -> dict:
        with self._lock:
            return _deep_copy(self._config)

    def save(self, payload: dict) -> dict:
        cfg = validate(payload)
        with self._lock:
            self._config = cfg
        self._settings.set_value("automations", cfg)
        return _deep_copy(cfg)

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._config["enabled"]

    def log(self) -> List[dict]:
        with self._lock:
            return list(self._log)

    # -- decisão ----------------------------------------------------------
    def match(self, text: str, cfg: dict = None) -> Optional[tuple]:
        """(chave, Builtin ou entrada personalizada) do comando activo que a
        mensagem é, ou None."""
        cfg = cfg or self.config
        word = normalize(text)
        if not word:
            return None
        for key, builtin in BUILTINS.items():
            if word in builtin.words:
                return (key, builtin) if cfg["commands"][key]["enabled"] else None
        for entry in cfg["custom"]:
            if entry["word"] == word:
                return (word, entry) if entry["enabled"] else None
        return None

    def handle(self, request: Request, view: MeshView) -> Optional[Decision]:
        """Decide a resposta a uma mensagem recebida. None quando a mensagem
        não é com o bot (não é um comando, ou chegou por onde ele não está a
        ouvir); uma Decision, registada, quando é — enviada ou travada."""
        cfg = self.config
        if not cfg["enabled"]:
            return None
        packet = request.packet or {}
        if request.channel is None:
            if not cfg["dm"]:
                return None
        elif request.channel not in cfg["channels"]:
            return None
        if (packet.get("decoded") or {}).get("replyId"):
            return None               # resposta a uma mensagem: é conversa, não comando
        found = self.match(request.text, cfg)
        if found is None:
            return None
        key, spec = found
        from_id = (request.from_id or "").lower()
        decision = Decision(command=key, outcome=SENT, channel=request.channel,
                            to_id=from_id, reply_id=int(packet.get("id") or 0))
        who = spec.get("who") if isinstance(spec, dict) else cfg["commands"][key]["who"]

        now = self._clock()
        if packet.get("viaMqtt") and cfg["ignore_mqtt"]:
            decision.outcome = MQTT
        elif who == FAVORITES and not view.is_favorite(from_id):
            decision.outcome = DENIED
        else:
            with self._lock:
                last = self._last_reply.get(from_id)
                while self._recent and self._recent[0] < now - 60:
                    self._recent.popleft()
                if last is not None and now - last < cfg["cooldown_s"]:
                    decision.outcome = COOLDOWN
                elif len(self._recent) >= cfg["max_per_min"]:
                    decision.outcome = LIMIT
            if decision.outcome == SENT and self._airtime_exceeded(view):
                decision.outcome = AIRTIME

        if decision.outcome == SENT:
            decision.reply = self.reply_text(key, spec, cfg, request, view)
            with self._lock:
                self._last_reply[from_id] = now
                self._recent.append(now)
        self._record(decision, view)
        return decision

    def reply_text(self, key: str, spec, cfg: dict, request: Request, view: MeshView) -> str:
        """O texto da resposta, no idioma do servidor (vai para a rede, não
        para um browser)."""
        with use_language(get_language()):
            values = build_values(request, view, cfg)
            if isinstance(spec, dict):
                text = render(spec["reply"], values)
            else:
                template = cfg["commands"][key]["template"] or default_template(key)
                if any(values.get(name) is None for name in spec.requires):
                    text = tr(f"auto_missing_{key}")
                else:
                    text = render(template, values)
        return truncate_utf8(text or "…")

    def preview(self, command: str, template: str, view: MeshView) -> dict:
        """O modelo preenchido com dados reais, como se o nó ouvido há menos
        tempo tivesse mandado o comando."""
        cfg = self.config
        sender = _latest_peer(view)
        packet = {}
        if sender:
            hops = sender.get("hops_away")
            packet = {"fromId": sender["id_string"], "rxSnr": sender.get("snr"),
                      "viaMqtt": bool(sender.get("via_mqtt")),
                      "hopStart": 3 if hops is not None else None,
                      "hopLimit": 3 - int(hops) if hops is not None else None}
        request = Request(from_id=sender["id_string"] if sender else "!00000000",
                          text=command, channel=None, packet=packet)
        spec = BUILTINS.get(command)
        if spec is None:
            spec = {"reply": template, "who": ALL}
        else:
            cfg["commands"][command]["template"] = template
        text = self.reply_text(command, spec, cfg, request, view)
        return {"text": text, "bytes": len(text.encode("utf-8")),
                "sender": sender["id_string"] if sender else None}

    def _airtime_exceeded(self, view: MeshView) -> bool:
        local_id = getattr(view.nodes, "local_id", None)
        record = view.nodes.get(local_id) if local_id else None
        air = (record or {}).get("air_util_tx")
        return air is not None and float(air) >= AIRTIME_LIMIT_PCT

    def _record(self, decision: Decision, view: MeshView):
        record = view.nodes.get(decision.to_id) or {}
        entry = {"time": time.time(), "from_id": decision.to_id,
                 "name": record.get("short_name") or record.get("long_name") or decision.to_id,
                 "command": decision.command, "channel": decision.channel,
                 "outcome": decision.outcome, "reply": decision.reply}
        with self._lock:
            self._log.appendleft(entry)
        decision.log_entry = entry


def _deep_copy(cfg: dict) -> dict:
    return {**cfg, "channels": list(cfg["channels"]),
            "commands": {k: dict(v) for k, v in cfg["commands"].items()},
            "custom": [dict(c) for c in cfg["custom"]]}


def _latest_peer(view: MeshView) -> Optional[dict]:
    local_id = getattr(view.nodes, "local_id", None)
    peers = [n for n in view.nodes.all()
             if n.get("id_string") != local_id and isinstance(n.get("last_heard"), datetime)]
    return max(peers, key=lambda n: n["last_heard"]) if peers else None


def build_values(request: Request, view: MeshView, cfg: dict) -> dict:
    """Todas as variáveis dos modelos. None quando o valor não se sabe."""
    packet = request.packet or {}
    nodes = view.nodes
    from_id = (request.from_id or "").lower()
    sender = nodes.get(from_id) or {}
    local_id = getattr(nodes, "local_id", None)
    local = (nodes.get(local_id) if local_id else None) or {}
    values: dict = {}

    values["id"] = from_id
    values["short"] = sender.get("short_name") or from_id[-4:]
    values["name"] = sender.get("long_name") or values["short"]

    hops = packet_hops(packet)
    via_mqtt = bool(packet.get("viaMqtt"))
    values["via"] = "MQTT" if via_mqtt else "RF"
    if hops is None:
        values["hops"] = None
    elif hops == 0:
        values["hops"] = tr("auto_direct")
    else:
        values["hops"] = tr("auto_hops_one") if hops == 1 else tr("auto_hops_many", n=hops)
    snr, rssi = packet.get("rxSnr"), packet.get("rxRssi")
    values["snr"] = None if snr is None or via_mqtt else f"{float(snr):.1f} dB"
    values["rssi"] = None if not rssi or via_mqtt else f"{int(rssi)} dBm"
    signal = " · ".join(v for v in (values["snr"] and f"SNR {values['snr']}",
                                    values["rssi"] and f"RSSI {values['rssi']}") if v)
    if signal and hops:
        signal = f"{tr('auto_last_hop')} {signal}"
    values["signal"] = signal or None

    summary = view.signal_log.summary(from_id)
    if summary:
        parts = [f"SNR {summary['snr']:.1f} dB"]
        if summary["rssi"] is not None:
            parts.append(f"RSSI {summary['rssi']:.0f} dBm")
        values["heard"] = tr("auto_heard", signal=" · ".join(parts), n=summary["count"])
    else:
        values["heard"] = tr("auto_not_heard")

    values["my_short"] = local.get("short_name") or (local_id or "")[-4:] or None
    values["my_name"] = local.get("long_name") or values["my_short"]
    values["battery"] = _fmt_battery(local.get("battery_level"), local.get("voltage"))
    values["uptime"] = _fmt_uptime(local.get("uptime_seconds"))
    values["chutil"] = _fmt_pct("ChU", local.get("channel_utilization"))
    values["airtx"] = _fmt_pct("TX", local.get("air_util_tx"))

    now = view.now()
    hour_ago, day_ago = now - timedelta(hours=1), now - timedelta(hours=24)
    peers = [n for n in nodes.all()
             if n.get("id_string") != local_id and isinstance(n.get("last_heard"), datetime)]
    values["nodes_1h"] = sum(1 for n in peers if n["last_heard"] >= hour_ago)
    values["nodes_24h"] = sum(1 for n in peers if n["last_heard"] >= day_ago)
    values["direct"] = sum(1 for n in peers if n["last_heard"] >= day_ago
                           and n.get("hops_away") == 0 and not n.get("via_mqtt"))

    pos = view.local_position or {}
    lat, lon = (pos.get("lat"), pos.get("lon")) if pos.get("found") else (None, None)
    if lat is None and local.get("latitude") is not None:
        lat, lon = local.get("latitude"), local.get("longitude")
    if lat is not None and lon is not None and (lat or lon):
        values["pos"] = f"{lat:.5f},{lon:.5f}"
        alt = pos.get("alt") if pos.get("found") else local.get("altitude")
        values["alt"] = f"{int(alt)} m" if alt else None
    else:
        lat = lon = None
        values["pos"] = values["alt"] = None

    slat, slon = sender.get("latitude"), sender.get("longitude")
    if lat is not None and slat is not None and slon is not None and (slat or slon):
        values["dist"] = _fmt_distance(haversine_km(lat, lon, slat, slon))
        deg = bearing_deg(lat, lon, slat, slon)
        values["bearing"] = f"{deg:.0f}° {compass_point(deg)}".strip()
    else:
        values["dist"] = values["bearing"] = None

    values["time"] = now.strftime("%H:%M")
    values["date"] = now.strftime("%d/%m/%Y")
    values["commands"] = ", ".join(command_names(cfg))
    return values


def command_names(cfg: dict) -> List[str]:
    """Os comandos activos, pelos nomes do idioma do servidor."""
    lang = get_language()
    names = [b.label.get(lang, b.label["en"]) for key, b in BUILTINS.items()
             if cfg["commands"][key]["enabled"]]
    names += [c["word"] for c in cfg["custom"] if c["enabled"]]
    return names
