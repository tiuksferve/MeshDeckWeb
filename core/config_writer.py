"""
core/config_writer.py — Leitura e escrita da configuração do nó, sem Qt.

Escreve a configuração do rádio a pedido do servidor web:

  * a escrita é uma transacção (beginSettingsTransaction → writeConfig por
    secção alterada → commitSettingsTransaction), para o firmware reiniciar
    uma só vez no fim em vez de perder as secções seguintes ao primeiro
    reinício;
  * o setattr é feito sobre iface.localNode — o mesmo objecto que o
    writeConfig copia para a mensagem admin —, nunca sobre uma cópia;
  * proto3 não serializa um bool False igual ao default: o "double-set"
    (True e depois False) força o campo a ir na mensagem;
  * só se enviam campos que mudaram face ao que o nó tem.

Os valores trocados com o browser são JSON: enums pelo nome curto que o
esquema de core/config_defs.py usa, bytes em base64, IPs IPv4 em notação
decimal com pontos.
"""

import base64
import binascii
import inspect
import ipaddress
import os
import threading
from typing import Dict, List, Optional, Tuple

from google.protobuf import descriptor as _descriptor
from meshtastic.protobuf import admin_pb2

from constants import logger
from core.config_defs import MESHTASTIC_CONFIG_DEFS, SECTION_WRITE_NAME, _resolve_sub_obj
from i18n import tr

FD = _descriptor.FieldDescriptor
_INT_TYPES = {
    FD.TYPE_INT32, FD.TYPE_INT64, FD.TYPE_UINT32, FD.TYPE_UINT64,
    FD.TYPE_SINT32, FD.TYPE_SINT64, FD.TYPE_FIXED32, FD.TYPE_FIXED64,
    FD.TYPE_SFIXED32, FD.TYPE_SFIXED64,
}
CANNED_FIELD = "__canned_messages__"
CANNED_MAX_CHARS = 200
CHANNEL_SLOTS = 8
ROLE_NAMES = {0: "DISABLED", 1: "PRIMARY", 2: "SECONDARY"}
ROLE_NUMBERS = {v: k for k, v in ROLE_NAMES.items()}


# ── utilitários ──────────────────────────────────────────────────────────────

def _call_with_timeout(fn, timeout: float = 3.0):
    """Algumas chamadas da biblioteca (get_canned_message) fazem polling sem
    limite à espera do nó; aqui desiste-se ao fim de `timeout` segundos."""
    result = {}

    def run():
        try:
            result["value"] = fn()
        except Exception as e:  # noqa: BLE001 — é reportado a quem chama
            result["error"] = e

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.warning("%s sem resposta do nó ao fim de %ss", fn, timeout)
        return None
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _is_ipv4_field(field_name: str) -> bool:
    return field_name.startswith("ipv4_config.")


def _ip_to_text(value: int) -> str:
    # O firmware guarda o IPv4 como fixed32 com os octetos pela ordem do
    # endereço em little-endian (192.168.1.10 → 0x0A01A8C0).
    return str(ipaddress.IPv4Address(int(value).to_bytes(4, "little")))


def _ip_from_text(text: str) -> int:
    return int.from_bytes(ipaddress.IPv4Address(text.strip() or "0.0.0.0").packed, "little")


def _walk(obj, field_name: str):
    """Devolve (objecto que contém o campo, nome do campo) para nomes com
    pontos (ex: ipv4_config.ip), ou (None, None) se o caminho não existir."""
    parts = field_name.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part, None)
        if obj is None:
            return None, None
    return obj, parts[-1]


def _field_desc(obj, name: str):
    try:
        return obj.DESCRIPTOR.fields_by_name.get(name)
    except AttributeError:
        return None


def _enum_name(desc, number: int, choices: Optional[list]) -> object:
    """Número do enum → nome, preferindo a forma que está na lista do esquema
    (ex: ROLE_CLIENT → CLIENT quando a lista diz CLIENT)."""
    value = desc.enum_type.values_by_number.get(number)
    if value is None:
        return number
    full = value.name
    if choices:
        if full in choices:
            return full
        for choice in choices:
            if full.endswith("_" + choice) or full == choice:
                return choice
    return full


def coerce_value(obj, field_name: str, value) -> Tuple[object, Optional[str]]:
    """Converte o valor vindo do browser para o tipo do campo protobuf.
    Devolve (valor, erro). Espelha ConfigTab._coerce_value do desktop."""
    if value is None:
        return None, None
    desc = _field_desc(obj, field_name)
    if desc is None:
        return value, None

    if desc.type == FD.TYPE_ENUM:
        enum_type = desc.enum_type
        if isinstance(value, str):
            found = enum_type.values_by_name.get(value)
            if found is not None:
                return found.number, None
            upper = value.upper()
            for candidate in enum_type.values:
                if candidate.name.upper() == upper or candidate.name.upper().endswith("_" + upper):
                    return candidate.number, None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None, tr("valor inválido '{value}'", value=value)
        if any(v.number == number for v in enum_type.values):
            return number, None
        return None, tr("valor inválido '{value}'", value=value)

    if desc.type == FD.TYPE_BOOL:
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "sim"), None
        return bool(value), None

    if desc.type in _INT_TYPES:
        if isinstance(value, bool):
            return int(value), None
        try:
            return int(float(value)), None
        except (TypeError, ValueError):
            return None, tr("número inteiro inválido '{value}'", value=value)

    if desc.type in (FD.TYPE_FLOAT, FD.TYPE_DOUBLE):
        try:
            return float(value), None
        except (TypeError, ValueError):
            return None, tr("número inválido '{value}'", value=value)

    if desc.type == FD.TYPE_STRING:
        return str(value), None

    if desc.type == FD.TYPE_BYTES:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value), None
        try:
            return base64.b64decode(str(value), validate=True), None
        except ValueError:
            return None, tr("base64 inválido '{value}'", value=value)

    return value, None


def _to_json(obj, name: str, value, field_type: str, choices) -> object:
    desc = _field_desc(obj, name)
    if desc is not None and desc.type == FD.TYPE_ENUM and isinstance(value, int):
        return _enum_name(desc, value, choices if field_type == "combo" else None)
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, float):
        return round(value, 6)
    return value


# ── leitura ──────────────────────────────────────────────────────────────────

def read_canned_messages(node) -> str:
    for attr in ("cannedPluginMessage", "cannedPluginMessageMessages",
                 "_cannedMessageModuleMessages"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    getter = getattr(node, "get_canned_message", None)
    if callable(getter):
        try:
            value = _call_with_timeout(getter, timeout=3.0)
            if isinstance(value, str):
                return value.strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("get_canned_message falhou: %s", e)
    return ""


def read_config(iface) -> dict:
    """Valores actuais do nó local para todas as secções do esquema, mais o
    dono (nomes, licença) e a informação só de leitura do dispositivo."""
    node = iface.localNode
    sections: Dict[str, dict] = {}
    for sec_key, fields in MESHTASTIC_CONFIG_DEFS.items():
        config_attr = sec_key.split(".", 1)[0]
        cfg_root = getattr(node, config_attr, None)
        sub = _resolve_sub_obj(cfg_root, sec_key) if cfg_root is not None else None
        values = {}
        for _label, field_name, field_type, extra in fields:
            if field_name == CANNED_FIELD:
                values[field_name] = read_canned_messages(node)
                continue
            if sub is None:
                continue
            obj, name = _walk(sub, field_name)
            if obj is None or not hasattr(obj, name):
                continue
            raw = getattr(obj, name)
            if _is_ipv4_field(field_name) and isinstance(raw, int):
                values[field_name] = _ip_to_text(raw)
            else:
                values[field_name] = _to_json(obj, name, raw, field_type, extra)
        sections[sec_key] = values

    info = iface.getMyNodeInfo() or {}
    user = info.get("user", {}) or {}
    return {
        "owner": {
            "long_name": user.get("longName", ""),
            "short_name": user.get("shortName", ""),
            "is_licensed": bool(user.get("isLicensed", False)),
        },
        "device": {
            "id": user.get("id", ""),
            "hw_model": user.get("hwModel", ""),
            "firmware": str(info.get("firmwareVersion", "") or
                            getattr(getattr(iface, "metadata", None), "firmware_version", "") or ""),
        },
        "sections": sections,
    }


def read_channels(iface) -> List[dict]:
    """Canais do nó com todos os campos que o editor mostra. Os slots
    DISABLED vazios ficam de fora (a lista é o que o utilizador vê)."""
    out = []
    for ch in list(getattr(iface.localNode, "channels", None) or []):
        role = int(getattr(ch, "role", 0))
        settings = ch.settings
        if role == 0 and not settings.name and not settings.psk:
            continue
        module = settings.module_settings
        out.append({
            "index": int(ch.index),
            "role": ROLE_NAMES.get(role, str(role)),
            "name": settings.name,
            "psk": base64.b64encode(bytes(settings.psk)).decode("ascii") if settings.psk else "",
            "uplink_enabled": bool(settings.uplink_enabled),
            "downlink_enabled": bool(settings.downlink_enabled),
            "is_muted": bool(getattr(module, "is_muted", False)),
            "position_precision": int(getattr(module, "position_precision", 0)),
        })
    return out


# ── escrita: configuração ────────────────────────────────────────────────────

def _writeconfig_supports(node, write_name: str) -> bool:
    """Node.writeConfig só conhece as secções da versão da biblioteca, e para
    um nome desconhecido chama our_exit() — que termina o processo. Para não
    arriscar, só se lhe passam nomes que aparecem no seu código."""
    try:
        return f'"{write_name}"' in inspect.getsource(type(node).writeConfig)
    except (OSError, TypeError, AttributeError):
        return False


def _write_section(node, sec_key: str, write_name: str):
    if _writeconfig_supports(node, write_name):
        node.writeConfig(write_name)
        return
    # Secção mais recente do que a biblioteca (ex: statusmessage, tak):
    # a mesma mensagem admin que o writeConfig montaria.
    root = sec_key.split(".", 1)[0]
    message = admin_pb2.AdminMessage()
    target = message.set_config if root == "localConfig" else message.set_module_config
    getattr(target, write_name).CopyFrom(getattr(getattr(node, root), write_name))
    node._sendAdmin(message)


def _set_field(obj, name: str, value):
    setattr(obj, name, value)
    # proto3 omite um bool False igual ao default: força-se a presença.
    if isinstance(value, bool) and value is False:
        try:
            if name not in [f.name for f, _ in obj.ListFields()]:
                setattr(obj, name, True)
                setattr(obj, name, False)
        except AttributeError:
            pass


def save_config(iface, sections: Dict[str, dict], owner: Optional[dict] = None) -> dict:
    """Aplica as alterações e grava-as numa só transacção.

    sections: {sec_key: {campo: valor}} — só os campos alterados no browser;
    os que coincidem com o nó são ignorados aqui também. owner: dicionário
    opcional com long_name / short_name / is_licensed alterados.

    Devolve {saved: [nomes das secções gravadas], owner: bool,
    canned: bool, errors: [...]}.
    """
    node = iface.localNode
    errors: List[str] = []
    pending: Dict[str, List[Tuple[object, str, object]]] = {}
    canned_text = None

    # 1. Validar e converter tudo antes de tocar no nó.
    for sec_key, fields in (sections or {}).items():
        if sec_key not in MESHTASTIC_CONFIG_DEFS:
            errors.append(tr("{section}: secção desconhecida", section=sec_key))
            continue
        schema = {f[1]: f for f in MESHTASTIC_CONFIG_DEFS[sec_key]}
        config_attr = sec_key.split(".", 1)[0]
        cfg_root = getattr(node, config_attr, None)
        sub = _resolve_sub_obj(cfg_root, sec_key) if cfg_root is not None else None
        for field_name, value in (fields or {}).items():
            spec = schema.get(field_name)
            if spec is None:
                errors.append(tr("{field}: campo desconhecido", field=f"{sec_key}.{field_name}"))
                continue
            field_type = spec[2]
            if field_type in ("readonly", "readonly_with_note"):
                continue
            if field_name == CANNED_FIELD:
                text = "|".join(p.strip() for p in str(value or "").replace("\n", "|").split("|") if p.strip())
                if len(text) > CANNED_MAX_CHARS:
                    errors.append(tr("{section}: mensagens pré-definidas com {n} caracteres (máx. {max})",
                                     section=sec_key, n=len(text), max=CANNED_MAX_CHARS))
                elif text != read_canned_messages(node):
                    canned_text = text
                continue
            if sub is None:
                errors.append(tr("{section}: secção não disponível neste nó", section=sec_key))
                break
            obj, name = _walk(sub, field_name)
            if obj is None or not hasattr(obj, name):
                errors.append(tr("{field}: não existe neste firmware", field=f"{sec_key}.{field_name}"))
                continue
            if _is_ipv4_field(field_name):
                try:
                    coerced, err = _ip_from_text(str(value)), None
                except ValueError:
                    coerced, err = None, tr("IPv4 inválido '{value}'", value=value)
            else:
                coerced, err = coerce_value(obj, name, value)
            if err:
                errors.append(f"{sec_key}.{field_name}: {err}")
                continue
            if field_type in ("spin_int", "spin_float") and isinstance(spec[3], tuple):
                lo, hi = spec[3]
                if not (lo <= coerced <= hi):
                    errors.append(tr("{field}: {value} fora de {lo}–{hi}", field=f"{sec_key}.{field_name}",
                                      value=coerced, lo=lo, hi=hi))
                    continue
            if getattr(obj, name) == coerced:
                continue
            pending.setdefault(sec_key, []).append((obj, name, coerced))

    owner = owner or {}
    owner_changes = {k: owner[k] for k in ("long_name", "short_name", "is_licensed") if k in owner}
    # setOwner chama our_exit() (termina o processo) com nomes vazios: valida-se antes.
    for key, message in (("long_name", "Nome longo: não pode ficar vazio"),
                         ("short_name", "Nome curto: não pode ficar vazio")):
        if key in owner_changes and not str(owner_changes[key]).strip():
            errors.append(tr(message))
    if "short_name" in owner_changes and len(str(owner_changes["short_name"]).strip()) > 4:
        errors.append(tr("Nome curto: máximo 4 caracteres"))

    if errors:
        # Nada é gravado com erros de validação: uma configuração a meio é
        # pior do que nenhuma.
        return {"saved": [], "owner": False, "canned": False, "errors": errors}

    saved: List[str] = []
    # 2. Uma transacção: o firmware só reinicia no commit.
    if pending:
        try:
            node.beginSettingsTransaction()
        except Exception as e:  # noqa: BLE001 — firmwares antigos não têm
            logger.debug("beginSettingsTransaction indisponível: %s", e)
        for sec_key, changes in pending.items():
            try:
                for obj, name, value in changes:
                    _set_field(obj, name, value)
                write_name = SECTION_WRITE_NAME.get(sec_key, sec_key.split(".", 1)[1])
                _write_section(node, sec_key, write_name)
                saved.append(write_name)
                logger.info("writeConfig(%s) com %d campo(s)", write_name, len(changes))
            except Exception as e:  # noqa: BLE001
                logger.error("writeConfig(%s) falhou: %s", sec_key, e)
                errors.append(f"{sec_key}: {e}")
        try:
            node.commitSettingsTransaction()
        except Exception as e:  # noqa: BLE001
            logger.debug("commitSettingsTransaction: %s", e)

    canned_saved = False
    if canned_text is not None:
        try:
            node.set_canned_message(canned_text)
            canned_saved = True
        except Exception as e:  # noqa: BLE001
            errors.append(tr("mensagens pré-definidas: {err}", err=e))

    owner_saved = False
    if owner_changes:
        current = (iface.getMyNodeInfo() or {}).get("user", {}) or {}
        try:
            node.setOwner(
                long_name=str(owner_changes.get("long_name", current.get("longName", ""))),
                short_name=str(owner_changes.get("short_name", current.get("shortName", ""))),
                is_licensed=bool(owner_changes.get("is_licensed", current.get("isLicensed", False))),
            )
            owner_saved = True
        except Exception as e:  # noqa: BLE001
            errors.append(f"setOwner: {e}")

    return {"saved": saved, "owner": owner_saved, "canned": canned_saved, "errors": errors}


# ── escrita: canais ──────────────────────────────────────────────────────────

def parse_psk(text: str) -> bytes:
    """PSK como as apps a mostram: base64, ou default / none / random, ou
    hexadecimal (com ou sem 0x). Só aceita os tamanhos que o firmware aceita.

    O base64 é lido com tolerância, porque a mesma chave chega em várias
    formas: com o prefixo "base64:" do CLI, no alfabeto URL-safe (- e _) dos
    links de canal, sem o "=" final, ou com espaços e quebras de linha
    (incluindo o espaço não separável que o iPhone mete ao colar)."""
    text = "".join((text or "").split())
    lower = text.lower()
    if lower in ("", "none"):
        return b"" if lower == "" else bytes([0])
    if lower == "default":
        return bytes([1])
    if lower == "random":
        return os.urandom(32)
    if lower.startswith("base64:"):
        text = text[7:]
    if lower.startswith("0x"):
        try:
            key = bytes.fromhex(text[2:])
        except ValueError:
            raise ValueError(tr("hexadecimal inválido")) from None
    elif len(text) in (32, 64) and all(c in "0123456789abcdefABCDEF" for c in text):
        # 16 ou 32 bytes em hex sem 0x; em base64 esses tamanhos teriam 24 ou
        # 44 caracteres, por isso não há ambiguidade.
        key = bytes.fromhex(text)
    else:
        b64 = text.replace("-", "+").replace("_", "/").rstrip("=")
        b64 += "=" * (-len(b64) % 4)
        try:
            key = base64.b64decode(b64, validate=True)
        except (ValueError, binascii.Error):
            bad = sorted({c for c in b64 if not (c.isascii() and (c.isalnum() or c in "+/="))})
            detail = tr(" (caracteres inválidos: {chars})", chars=' '.join(repr(c) for c in bad)) if bad else ""
            raise ValueError(tr("não é base64 válido") + detail) from None
    if len(key) not in (0, 1, 16, 32):
        raise ValueError(tr("PSK com {n} bytes (aceites: 1, 16 ou 32)", n=len(key)))
    return key


def save_channels(iface, channels: List[dict]) -> dict:
    """Aplica a lista de canais vinda do editor e grava só os slots que
    mudaram. Slots que desapareceram da lista ficam DISABLED e vazios; o
    canal 0 (PRIMARY) não pode ser removido."""
    node = iface.localNode
    slots = list(getattr(node, "channels", None) or [])
    errors: List[str] = []
    wanted: Dict[int, dict] = {}

    for ch in channels or []:
        try:
            index = int(ch["index"])
        except (KeyError, TypeError, ValueError):
            errors.append(tr("canal sem índice válido: {ch}", ch=repr(ch)))
            continue
        if not 0 <= index < min(CHANNEL_SLOTS, len(slots)):
            errors.append(tr("canal {index}: índice fora de 0–{max}", index=index,
                             max=min(CHANNEL_SLOTS, len(slots)) - 1))
            continue
        if ch.get("role") not in ROLE_NUMBERS:
            errors.append(tr("canal {index}: papel inválido {role}", index=index, role=repr(ch.get('role'))))
            continue
        try:
            ch["_psk"] = parse_psk(ch.get("psk", ""))
        except ValueError as e:
            errors.append(tr("canal {index}: PSK inválida ({err})", index=index, err=e))
            continue
        if len((ch.get("name") or "").encode()) > 11:
            errors.append(tr("canal {index}: nome com mais de 11 bytes", index=index))
            continue
        wanted[index] = ch

    if 0 not in wanted or wanted[0].get("role") != "PRIMARY":
        errors.append(tr("o canal 0 tem de existir e ser PRIMARY"))
    if sum(1 for c in wanted.values() if c.get("role") == "PRIMARY") > 1:
        errors.append(tr("só pode haver um canal PRIMARY"))
    if errors:
        return {"saved": [], "errors": errors}

    saved = []
    for index, ch_obj in enumerate(slots[:CHANNEL_SLOTS]):
        # Compara-se o que muda de facto: tocar num submessage do protobuf
        # (mesmo para lhe dar o valor que já tem) marca-o como presente e
        # altera a serialização, o que faria regravar canais intactos.
        before = ch_obj.SerializeToString()
        data = wanted.get(index)
        if data is None:
            if ch_obj.role == 0 and not ch_obj.HasField("settings"):
                continue
            ch_obj.role = 0
            ch_obj.ClearField("settings")
        else:
            muted = bool(data.get("is_muted"))
            precision = max(0, min(32, int(data.get("position_precision") or 0)))
            current_module = ch_obj.settings.module_settings
            if (ch_obj.role == ROLE_NUMBERS[data["role"]]
                    and ch_obj.settings.name == (data.get("name") or "").strip()
                    and ch_obj.settings.psk == data["_psk"]
                    and ch_obj.settings.uplink_enabled == bool(data.get("uplink_enabled"))
                    and ch_obj.settings.downlink_enabled == bool(data.get("downlink_enabled"))
                    and current_module.is_muted == muted
                    and current_module.position_precision == precision):
                ch_obj.ParseFromString(before)   # desfaz presenças criadas pela leitura
                continue
            settings = ch_obj.settings
            ch_obj.role = ROLE_NUMBERS[data["role"]]
            settings.name = (data.get("name") or "").strip()
            settings.psk = data["_psk"]
            settings.uplink_enabled = bool(data.get("uplink_enabled"))
            settings.downlink_enabled = bool(data.get("downlink_enabled"))
            if muted or precision or settings.HasField("module_settings"):
                settings.module_settings.is_muted = muted
                settings.module_settings.position_precision = precision
        if ch_obj.SerializeToString() == before:
            continue
        try:
            node.writeChannel(index)
            saved.append(index)
            logger.info("writeChannel(%d) OK", index)
        except Exception as e:  # noqa: BLE001
            errors.append(tr("canal {index}: {err}", index=index, err=e))
    return {"saved": saved, "errors": errors}
