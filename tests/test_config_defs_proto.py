"""
O esquema de configuração (core/config_defs.py) tem de acompanhar os
protobufs da biblioteca meshtastic instalada.

Se a biblioteca for actualizada e o firmware ganhar campos, opções de enum
ou módulos novos, estes testes falham e dizem o que falta — em vez de a
interface ficar silenciosamente desactualizada.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from meshtastic.protobuf import localonly_pb2

from core.config_defs import MESHTASTIC_CONFIG_DEFS, _resolve_sub_obj

ROOTS = {"localConfig": localonly_pb2.LocalConfig(),
         "moduleConfig": localonly_pb2.LocalModuleConfig()}

# Campos do proto que ficam de fora de propósito.
EXCLUDED = {
    "localConfig.security": {"private_key",   # a chave privada nunca vai para o browser
                             "admin_key"},    # lista de chaves: editor próprio, ainda não existe
    "localConfig.lora": {"ignore_incoming"},  # lista de nós
    "moduleConfig.remotehardware": {"available_pins"},  # lista de pinos
}


def _repeated(field):
    return field.is_repeated if hasattr(field, "is_repeated") else field.label == 3


def _deprecated(field):
    return field.GetOptions().deprecated


def _proto_fields(message, prefix=""):
    """{nome com pontos: descriptor} — submensagens achatadas como no esquema."""
    out = {}
    for field in message.DESCRIPTOR.fields:
        name = prefix + field.name
        if field.message_type is not None and not _repeated(field):
            out.update(_proto_fields(getattr(message, field.name), name + "."))
        else:
            out[name] = field
    return out


def _sections():
    for sec_key, fields in MESHTASTIC_CONFIG_DEFS.items():
        root = ROOTS[sec_key.split(".", 1)[0]]
        yield sec_key, fields, _resolve_sub_obj(root, sec_key)


@pytest.mark.parametrize("sec_key", list(MESHTASTIC_CONFIG_DEFS))
def test_section_matches_the_protobuf(sec_key):
    fields = MESHTASTIC_CONFIG_DEFS[sec_key]
    sub = _resolve_sub_obj(ROOTS[sec_key.split(".", 1)[0]], sec_key)
    assert sub is not None, f"{sec_key} não existe no proto"
    proto = _proto_fields(sub)
    ours = {f[1] for f in fields} - {"__canned_messages__"}

    stale = ours - set(proto)
    assert not stale, f"{sec_key}: campos que já não existem no firmware: {sorted(stale)}"

    missing = {name for name, f in proto.items()
               if name not in ours and not _deprecated(f)} - EXCLUDED.get(sec_key, set())
    assert not missing, f"{sec_key}: campos novos do firmware por mostrar: {sorted(missing)}"

    for _label, name, kind, choices in fields:
        if kind != "combo":
            continue
        enum_values = [v.name for v in proto[name].enum_type.values]
        def matches(proto_name, choice):
            return proto_name == choice or proto_name.endswith("_" + choice)
        new = [v for v in enum_values if not any(matches(v, c) for c in choices)]
        invalid = [c for c in choices if not any(matches(v, c) for v in enum_values)]
        assert not new, f"{sec_key}.{name}: opções novas por mostrar: {new}"
        assert not invalid, f"{sec_key}.{name}: opções que já não existem: {invalid}"


def test_every_firmware_section_is_in_the_schema():
    covered = {sub.DESCRIPTOR.full_name for _k, _f, sub in _sections() if sub is not None}
    for root_name, root in ROOTS.items():
        for field in root.DESCRIPTOR.fields:
            if field.message_type is None or field.name == "version":
                continue
            assert field.message_type.full_name in covered, \
                f"secção {root_name}.{field.name} do firmware não está no esquema"
