"""
Testes de core/config_writer.py — leitura e escrita da configuração do nó.

O nó de teste herda do Node real da biblioteca meshtastic: writeConfig,
writeChannel, setOwner e a transacção correm o código da biblioteca, e só o
envio pela rádio (_sendAdmin) é interceptado. Assim verifica-se a mensagem
admin exacta que o firmware receberia.
"""

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from meshtastic.node import Node
from meshtastic.protobuf import channel_pb2, config_pb2, localonly_pb2

from core import config_writer as cw
from core.config_defs import MESHTASTIC_CONFIG_DEFS


class RecordingNode(Node):
    def __init__(self, iface):
        super().__init__(iface, 0x11223344, noProto=True)
        self.localConfig = localonly_pb2.LocalConfig()
        self.moduleConfig = localonly_pb2.LocalModuleConfig()
        self.channels = [channel_pb2.Channel(index=i) for i in range(8)]
        self.channels[0].role = channel_pb2.Channel.Role.PRIMARY
        self.channels[0].settings.name = "LongFast"
        self.channels[0].settings.psk = b"\x01"
        self.cannedPluginMessage = "Ok|Sim"
        self.sent = []

    def ensureSessionKey(self):
        pass

    def _sendAdmin(self, p, *args, **kwargs):
        self.sent.append(p)

    def set_canned_message(self, message):
        self.canned_set = message


class FakeIface:
    def __init__(self):
        self.localNode = RecordingNode(self)

    def getMyNodeInfo(self):
        return {"user": {"id": "!11223344", "longName": "Base", "shortName": "BS",
                         "hwModel": "TBEAM", "isLicensed": False},
                "firmwareVersion": "2.7.11"}


@pytest.fixture
def iface():
    return FakeIface()


def kinds(node):
    """Resumo das mensagens admin enviadas: o campo do oneof de cada uma."""
    return [m.WhichOneof("payload_variant") for m in node.sent]


# ── leitura ──────────────────────────────────────────────────────────────────

def test_read_config_covers_every_section_and_converts_values(iface):
    node = iface.localNode
    node.localConfig.device.role = config_pb2.Config.DeviceConfig.Role.ROUTER
    node.localConfig.network.ipv4_config.ip = int.from_bytes(bytes([192, 168, 1, 10]), "little")
    node.localConfig.lora.region = config_pb2.Config.LoRaConfig.RegionCode.EU_868

    data = cw.read_config(iface)
    assert set(data["sections"]) == set(MESHTASTIC_CONFIG_DEFS)
    assert data["sections"]["localConfig.device"]["role"] == "ROUTER"
    assert data["sections"]["localConfig.lora"]["region"] == "EU_868"
    assert data["sections"]["localConfig.network"]["ipv4_config.ip"] == "192.168.1.10"
    assert data["sections"]["moduleConfig.cannedMessage"]["__canned_messages__"] == "Ok|Sim"
    assert data["owner"] == {"long_name": "Base", "short_name": "BS", "is_licensed": False}
    assert data["device"]["firmware"] == "2.7.11"


def test_private_key_is_never_read(iface):
    iface.localNode.localConfig.security.private_key = b"secret-key-bytes"
    data = cw.read_config(iface)
    assert "private_key" not in data["sections"]["localConfig.security"]


# ── escrita: configuração ────────────────────────────────────────────────────

def test_save_config_sends_one_transaction_with_only_changed_sections(iface):
    node = iface.localNode
    result = cw.save_config(iface, {
        "localConfig.device": {"role": "ROUTER", "rebroadcast_mode": "ALL"},  # ALL é o actual
        "localConfig.lora": {"region": "EU_868", "hop_limit": 5},
        "moduleConfig.mqtt": {},
    })
    assert result["errors"] == []
    assert result["saved"] == ["device", "lora"]
    assert kinds(node) == ["begin_edit_settings", "set_config", "set_config", "commit_edit_settings"]
    device, lora = node.sent[1].set_config, node.sent[2].set_config
    assert device.device.role == config_pb2.Config.DeviceConfig.Role.ROUTER
    assert lora.lora.region == config_pb2.Config.LoRaConfig.RegionCode.EU_868
    assert lora.lora.hop_limit == 5


def test_unchanged_values_send_nothing(iface):
    node = iface.localNode
    result = cw.save_config(iface, {"localConfig.device": {"role": "CLIENT"}})
    assert result == {"saved": [], "owner": False, "canned": False, "errors": []}
    assert node.sent == []


def test_validation_errors_write_nothing(iface):
    node = iface.localNode
    result = cw.save_config(iface, {
        "localConfig.lora": {"hop_limit": 12, "region": "EU_868"},   # máximo é 7
    })
    assert result["saved"] == []
    assert any("hop_limit" in e for e in result["errors"])
    assert node.sent == []


def test_bool_can_be_turned_off(iface):
    node = iface.localNode
    node.moduleConfig.mqtt.enabled = True
    result = cw.save_config(iface, {"moduleConfig.mqtt": {"enabled": False}})
    assert result["saved"] == ["mqtt"]
    assert node.moduleConfig.mqtt.enabled is False


def test_ipv4_is_written_from_dotted_text(iface):
    node = iface.localNode
    cw.save_config(iface, {"localConfig.network": {"ipv4_config.gateway": "10.0.0.1"}})
    assert node.localConfig.network.ipv4_config.gateway == int.from_bytes(bytes([10, 0, 0, 1]), "little")


def test_section_unknown_to_the_library_is_written_without_exiting(iface):
    """statusmessage não está no writeConfig desta biblioteca, que chamaria
    our_exit(); o writer monta a mensagem admin ele próprio."""
    node = iface.localNode
    result = cw.save_config(iface, {"moduleConfig.statusmessage": {"node_status": "Em QRV"}})
    assert result["errors"] == []
    assert result["saved"] == ["statusmessage"]
    admin = [m for m in node.sent if m.WhichOneof("payload_variant") == "set_module_config"][0]
    assert admin.set_module_config.statusmessage.node_status == "Em QRV"


def test_owner_and_canned_messages(iface):
    node = iface.localNode
    result = cw.save_config(iface, {
        "moduleConfig.cannedMessage": {"__canned_messages__": "Ok\nA caminho\n\nSim"},
    }, owner={"long_name": "Base Norte", "short_name": "BN"})
    assert result["owner"] is True and result["canned"] is True
    assert node.canned_set == "Ok|A caminho|Sim"
    owner = [m for m in node.sent if m.WhichOneof("payload_variant") == "set_owner"][0]
    assert owner.set_owner.long_name == "Base Norte"
    assert owner.set_owner.short_name == "BN"


@pytest.mark.parametrize("lang, word", [("pt", "vazio"), ("en", "empty")])
def test_empty_owner_name_is_rejected_before_the_library(iface, lang, word):
    """O erro sai no idioma escolhido na interface."""
    import i18n
    previous = i18n.get_language()
    i18n.set_language(lang)
    try:
        result = cw.save_config(iface, {}, owner={"short_name": "  "})
    finally:
        i18n.set_language(previous)
    assert result["owner"] is False
    assert any(word in e for e in result["errors"]), result["errors"]


# ── canais ───────────────────────────────────────────────────────────────────

def test_read_channels_lists_only_used_slots(iface):
    channels = cw.read_channels(iface)
    assert channels == [{
        "index": 0, "role": "PRIMARY", "name": "LongFast", "psk": "AQ==",
        "uplink_enabled": False, "downlink_enabled": False,
        "is_muted": False, "position_precision": 0,
    }]


def test_save_channels_writes_only_changed_slots(iface):
    node = iface.localNode
    key = base64.b64encode(os.urandom(32)).decode()
    result = cw.save_channels(iface, [
        {"index": 0, "role": "PRIMARY", "name": "LongFast", "psk": "AQ=="},     # igual
        {"index": 1, "role": "SECONDARY", "name": "Equipa", "psk": key,
         "uplink_enabled": True, "position_precision": 13},
    ])
    assert result == {"saved": [1], "errors": []}
    sent = node.sent[0].set_channel
    assert sent.index == 1 and sent.settings.name == "Equipa"
    assert sent.settings.psk == base64.b64decode(key)
    assert sent.settings.uplink_enabled is True
    assert sent.settings.module_settings.position_precision == 13


def test_removed_channel_is_disabled_and_cleared(iface):
    node = iface.localNode
    node.channels[2].role = channel_pb2.Channel.Role.SECONDARY
    node.channels[2].settings.name = "Velho"
    result = cw.save_channels(iface, [{"index": 0, "role": "PRIMARY", "name": "LongFast", "psk": "AQ=="}])
    assert result["saved"] == [2]
    assert node.channels[2].role == channel_pb2.Channel.Role.DISABLED
    assert node.channels[2].settings.name == ""


@pytest.mark.parametrize("channels, expected", [
    ([{"index": 0, "role": "SECONDARY", "name": "x", "psk": "AQ=="}], "PRIMARY"),
    ([{"index": 0, "role": "PRIMARY", "name": "x", "psk": "não-é-base64!"}], "PSK"),
    ([{"index": 0, "role": "PRIMARY", "name": "x", "psk": base64.b64encode(b"12345").decode()}], "PSK"),
    ([{"index": 0, "role": "PRIMARY", "name": "nome-demasiado-longo", "psk": "AQ=="}], "11 bytes"),
])
def test_invalid_channels_write_nothing(iface, channels, expected):
    result = cw.save_channels(iface, channels)
    assert result["saved"] == []
    assert any(expected in e for e in result["errors"]), result["errors"]
    assert iface.localNode.sent == []


# ── PSK em várias formas ─────────────────────────────────────────────────────

KEY = bytes(range(32))


@pytest.mark.parametrize("text", [
    base64.b64encode(KEY).decode(),                                  # padrão
    "base64:" + base64.b64encode(KEY).decode(),                      # CLI
    base64.urlsafe_b64encode(KEY).decode().rstrip("="),              # link de canal
    " " + base64.b64encode(KEY).decode()[:20] + " \n"
        + base64.b64encode(KEY).decode()[20:] + " ",                 # colado no iPhone
    "0x" + KEY.hex(),
    KEY.hex(),
])
def test_psk_accepts_the_formats_other_apps_use(text):
    assert cw.parse_psk(text) == KEY


def test_psk_error_names_the_bad_characters():
    with pytest.raises(ValueError, match="'!'"):
        cw.parse_psk("abc!def")
