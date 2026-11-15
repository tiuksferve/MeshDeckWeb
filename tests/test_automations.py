"""
Testes das automações (core/automations.py): o que é um comando, os
travões, os modelos e as variáveis.
"""

from datetime import datetime, timedelta

import pytest

from core import automations as auto
from core.node_store import NodeStore
from core.settings import Settings
from i18n import set_language

LOCAL = "!0000aaaa"
PEER = "!11110001"


@pytest.fixture(autouse=True)
def portuguese():
    set_language("pt")
    yield
    set_language("en")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def engine(tmp_path):
    clock = Clock()
    eng = auto.AutomationEngine(Settings(filename="automations.json", directory=str(tmp_path)),
                                clock=clock)
    eng.clock = clock
    cfg = eng.config
    cfg["enabled"] = True
    eng.save(cfg)
    return eng


@pytest.fixture
def view():
    nodes = NodeStore()
    nodes.set_local_node(LOCAL)
    now = datetime.now()
    nodes.update(LOCAL, {"long_name": "Base CT7", "short_name": "BASE",
                         "battery_level": 101, "uptime_seconds": 3 * 86400 + 4 * 3600,
                         "channel_utilization": 12.34, "air_util_tx": 1.4})
    nodes.update(PEER, {"long_name": "Peer One", "short_name": "P1",
                        "last_heard": now, "hops_away": 0,
                        "latitude": 38.80, "longitude": -9.10})
    nodes.update("!22220002", {"short_name": "P2", "last_heard": now - timedelta(hours=5),
                               "hops_away": 2})
    favorites = set()
    v = auto.MeshView(nodes=nodes,
                      local_position={"lat": 38.7223, "lon": -9.1393, "alt": 85, "found": True},
                      is_favorite=lambda nid: nid in favorites,
                      signal_log=auto.SignalLog(),
                      now=lambda: now)
    v.favorites = favorites
    return v


def dm(text, **packet):
    return auto.Request(from_id=PEER, text=text, channel=None,
                        packet={"id": 0x77, "hopStart": 3, "hopLimit": 3,
                                "rxSnr": 6.5, "rxRssi": -98, **packet})


# ── o que é um comando ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("ping", "ping"), ("Ping", "ping"), ("  PING!  ", "ping"), ("ping?", "ping"),
    ("Sinal?", "sinal"), ("posição", "posicao"), ("/ping", "ping"), ("?", "?"),
    ("fiz ping ao router", ""), ("ping ping", ""), ("", ""), ("🏓", ""),
])
def test_normalize_accepts_only_a_single_word(text, expected):
    assert auto.normalize(text) == expected


def test_a_sentence_is_not_a_command(engine, view):
    assert engine.handle(dm("fiz ping ao router"), view) is None
    assert engine.log() == []


def test_ping_replies_with_hops_and_signal(engine, view):
    decision = engine.handle(dm("Ping"), view)
    assert decision.outcome == auto.SENT
    assert decision.reply == "🏓 pong P1 · directo · SNR 6.5 dB · RSSI -98 dBm · RF"
    assert decision.channel is None and decision.to_id == PEER
    assert decision.reply_id == 0x77


def test_relayed_ping_counts_hops_from_the_packet(engine, view):
    decision = engine.handle(dm("ping", hopStart=5, hopLimit=3), view)
    assert decision.reply == "🏓 pong P1 · 2 saltos · último salto SNR 6.5 dB · RSSI -98 dBm · RF"


def test_disabled_engine_ignores_everything(engine, view):
    cfg = engine.config
    cfg["enabled"] = False
    engine.save(cfg)
    assert engine.handle(dm("ping"), view) is None


def test_disabled_command_is_ignored(engine, view):
    cfg = engine.config
    cfg["commands"]["ping"]["enabled"] = False
    engine.save(cfg)
    assert engine.handle(dm("ping"), view) is None


def test_replies_to_a_message_are_never_answered(engine, view):
    """Anti-loop: dois bots não ficam a responder um ao outro."""
    assert engine.handle(dm("ping", decoded={"replyId": 0x55}), view) is None


def test_channels_only_when_chosen(engine, view):
    request = auto.Request(from_id=PEER, text="ping", channel=0, packet={"id": 1})
    assert engine.handle(request, view) is None
    cfg = engine.config
    cfg["channels"] = [0]
    engine.save(cfg)
    decision = engine.handle(request, view)
    assert decision.outcome == auto.SENT and decision.channel == 0


def test_dm_can_be_turned_off(engine, view):
    cfg = engine.config
    cfg["dm"] = False
    engine.save(cfg)
    assert engine.handle(dm("ping"), view) is None


# ── travões ──────────────────────────────────────────────────────────────────

def test_cooldown_per_node(engine, view):
    assert engine.handle(dm("ping"), view).outcome == auto.SENT
    engine.clock.t += 10
    assert engine.handle(dm("ping"), view).outcome == auto.COOLDOWN
    engine.clock.t += 25
    assert engine.handle(dm("ping"), view).outcome == auto.SENT


def test_global_limit_per_minute(engine, view):
    cfg = engine.config
    cfg["max_per_min"] = 2
    engine.save(cfg)
    for i in range(2):
        req = auto.Request(from_id=f"!0000000{i}", text="ping", channel=None, packet={})
        assert engine.handle(req, view).outcome == auto.SENT
    req = auto.Request(from_id="!00000009", text="ping", channel=None, packet={})
    assert engine.handle(req, view).outcome == auto.LIMIT
    engine.clock.t += 61
    assert engine.handle(req, view).outcome == auto.SENT


def test_stops_when_airtime_is_high(engine, view):
    view.nodes.update(LOCAL, {"air_util_tx": 9.0})
    assert engine.handle(dm("ping"), view).outcome == auto.AIRTIME


def test_mqtt_requests_are_ignored_by_default(engine, view):
    assert engine.handle(dm("ping", viaMqtt=True), view).outcome == auto.MQTT


def test_favorites_only_commands(engine, view):
    """pos e dist dizem onde o nó está: por omissão, só para favoritos."""
    assert engine.handle(dm("pos"), view).outcome == auto.DENIED
    view.favorites.add(PEER)
    assert engine.handle(dm("pos"), view).reply == "📍 BASE: 38.72230,-9.13930 · 85 m"


def test_blocked_requests_go_to_the_log(engine, view):
    engine.handle(dm("ping"), view)
    engine.handle(dm("ping"), view)
    outcomes = [e["outcome"] for e in engine.log()]
    assert outcomes == [auto.COOLDOWN, auto.SENT]
    assert engine.log()[0]["name"] == "P1"


# ── respostas ────────────────────────────────────────────────────────────────

def test_help_lists_active_commands(engine, view):
    cfg = engine.config
    cfg["commands"]["time"]["enabled"] = False
    cfg["custom"] = [{"word": "regras", "reply": "Sem spam.", "enabled": True}]
    engine.save(cfg)
    reply = engine.handle(dm("ajuda"), view).reply
    assert reply.startswith("🤖 Comandos: ping, ajuda, info")
    assert "hora" not in reply and reply.endswith("regras")


def test_english_words_work_with_a_portuguese_server(engine, view):
    assert engine.handle(dm("help"), view).outcome == auto.SENT


def test_info_reports_the_local_node(engine, view):
    reply = engine.handle(dm("info"), view).reply
    assert reply == "ℹ️ Base CT7 · up 3d4h · 🔌 · ChU 12.3% · TX 1.4%"


def test_nodes_counts(engine, view):
    reply = engine.handle(dm("nos"), view).reply
    assert reply == "📡 Nós ouvidos: 1 na última hora · 2 em 24 h · 1 directos"


def test_dist_and_bearing(engine, view):
    view.favorites.add(PEER)
    reply = engine.handle(dm("dist"), view).reply
    assert reply == "📍 Estás a 9.3 km · rumo 22° N"


def test_dist_without_sender_position_says_so(engine, view):
    view.favorites.add(PEER)
    view.nodes.get(PEER)["latitude"] = None
    assert engine.handle(dm("dist"), view).reply.startswith("📍 Não sei a distância")


def test_signal_uses_direct_packets_of_the_last_hour(engine, view):
    log = view.signal_log
    log.record({"fromId": PEER, "rxSnr": 4.0, "rxRssi": -100, "hopStart": 3, "hopLimit": 3})
    log.record({"fromId": PEER, "rxSnr": 6.0, "rxRssi": -110, "hopStart": 3, "hopLimit": 3})
    log.record({"fromId": PEER, "rxSnr": 20.0, "hopStart": 3, "hopLimit": 1})   # retransmitido
    log.record({"fromId": PEER, "rxSnr": 20.0, "viaMqtt": True})
    reply = engine.handle(dm("sinal"), view).reply
    assert reply == "📶 P1: ouço-te directo com SNR 5.0 dB · RSSI -105 dBm (2 pacotes na última hora)"


def test_signal_when_never_heard_directly(engine, view):
    assert engine.handle(dm("sinal"), view).reply == "📶 P1: não te ouvi directamente na última hora"


def test_custom_command(engine, view):
    cfg = engine.config
    cfg["custom"] = [{"word": "Regras", "reply": "Olá {short}, sem spam.", "enabled": True}]
    engine.save(cfg)
    assert engine.config["custom"][0]["word"] == "regras"
    assert engine.handle(dm("regras"), view).reply == "Olá P1, sem spam."


def test_edited_template(engine, view):
    cfg = engine.config
    cfg["commands"]["ping"]["template"] = "pong {name} ({hops})"
    engine.save(cfg)
    assert engine.handle(dm("ping"), view).reply == "pong Peer One (directo)"


def test_reply_is_cut_to_fit_a_message(engine, view):
    cfg = engine.config
    cfg["custom"] = [{"word": "longo", "reply": "é" * 290, "enabled": True}]
    engine.save(cfg)
    reply = engine.handle(dm("longo"), view).reply
    assert len(reply.encode("utf-8")) <= auto.MAX_REPLY_BYTES
    assert reply.endswith("…")


def test_render_drops_empty_values_and_their_separator():
    assert auto.render("a · {x} · b", {"x": None}) == "a · b"
    assert auto.render("a · {x}", {"x": ""}) == "a"
    assert auto.render("{nope}", {}) == "{nope}"


# ── configuração ─────────────────────────────────────────────────────────────

def test_starts_disabled_and_persists(tmp_path):
    settings = Settings(filename="automations.json", directory=str(tmp_path))
    eng = auto.AutomationEngine(settings)
    assert eng.enabled is False
    cfg = eng.config
    cfg.update(enabled=True, cooldown_s=60, channels=[1, 0])
    eng.save(cfg)
    again = auto.AutomationEngine(Settings(filename="automations.json", directory=str(tmp_path)))
    assert again.enabled and again.config["cooldown_s"] == 60
    assert again.config["channels"] == [0, 1]


def test_default_template_is_not_stored(engine):
    cfg = engine.config
    cfg["commands"]["ping"]["template"] = auto.default_template("ping")
    assert engine.save(cfg)["commands"]["ping"]["template"] == ""


@pytest.mark.parametrize("change, message", [
    ({"cooldown_s": 1}, "Intervalo por nó"),
    ({"max_per_min": "x"}, "Respostas por minuto"),
    ({"custom": [{"word": "duas palavras", "reply": "x"}]}, "não serve"),
    ({"custom": [{"word": "ping", "reply": "x"}]}, "já existe"),
    ({"custom": [{"word": "a", "reply": "x"}, {"word": "A", "reply": "y"}]}, "já existe"),
    ({"custom": [{"word": "vazio", "reply": "  "}]}, "não tem resposta"),
])
def test_invalid_configuration_is_refused(engine, change, message):
    cfg = engine.config
    cfg.update(change)
    with pytest.raises(ValueError, match=message):
        engine.save(cfg)


def test_a_broken_file_falls_back_to_defaults(tmp_path):
    settings = Settings(filename="automations.json", directory=str(tmp_path))
    settings.set_value("automations", {"cooldown_s": "lots"})
    assert auto.AutomationEngine(settings).config == auto.default_config()


def test_preview_uses_the_latest_heard_node(engine, view):
    result = engine.preview("ping", "pong {short} {hops}", view)
    assert result["text"] == "pong P1 directo"
    assert result["sender"] == PEER
    assert result["bytes"] == len("pong P1 directo")
