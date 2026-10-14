"""
server/tls.py — Certificados para servir o MeshDeckWeb por HTTPS na rede local.

Os browsers só dão acesso a sensores (bússola, orientação) em páginas
seguras, e um servidor na LAN não pode ter um certificado público. A
solução habitual é uma autoridade de certificação (CA) local:

  * meshdeck-ca.crt — a CA, criada uma vez e reutilizada. É ela que se
    instala como confiável no iPhone/Android/PC; enquanto não mudar, os
    dispositivos não precisam de voltar a instalar nada.
  * server.crt / server.key — o certificado do servidor, assinado pela CA,
    com os IPs e nomes por onde se acede. Pode ser regenerado (outro IP,
    validade a terminar) sem tocar na CA.

A validade do certificado do servidor fica abaixo dos 398 dias que o iOS e
o macOS aceitam; a da CA é longa, como é habitual.
"""

import datetime
import ipaddress
import os
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_CERT = "meshdeck-ca.crt"
CA_KEY = "meshdeck-ca.key"
SERVER_CERT = "server.crt"
SERVER_KEY = "server.key"

CA_DAYS = 3650
SERVER_DAYS = 397


def tls_dir(config_dir: str) -> str:
    path = os.path.join(config_dir, "tls")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def local_addresses() -> list:
    """IPs IPv4 desta máquina (sem loopback), pela tabela de rotas do sistema."""
    found = set()
    try:
        # Sem enviar nada: só para saber qual o IP da interface de saída.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in found if not ip.startswith("127."))


def default_names(extra=()) -> list:
    """Nomes e IPs a incluir no certificado: os indicados, os IPs locais,
    o nome da máquina (e .local, para mDNS) e localhost."""
    host = socket.gethostname()
    names = list(extra) + local_addresses() + [host, f"{host}.local", "localhost", "127.0.0.1"]
    out = []
    for name in names:
        name = (name or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def _write_private(path: str, data: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _load_or_create_ca(directory: str):
    cert_path = os.path.join(directory, CA_CERT)
    key_path = os.path.join(directory, CA_KEY)
    if os.path.exists(cert_path) and os.path.exists(key_path):
        with open(key_path, "rb") as fh:
            key = serialization.load_pem_private_key(fh.read(), password=None)
        with open(cert_path, "rb") as fh:
            cert = x509.load_pem_x509_certificate(fh.read())
        return cert, key, False

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"MeshDeckWeb CA ({socket.gethostname()})"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MeshDeckWeb"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=True,
            content_commitment=False, key_encipherment=False, data_encipherment=False,
            key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_private(key_path, _key_pem(key))
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key, True


def _san(names) -> x509.SubjectAlternativeName:
    entries = []
    for name in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            entries.append(x509.DNSName(name))
    return x509.SubjectAlternativeName(entries)


def make_certificates(config_dir: str, names) -> dict:
    """Cria (ou reutiliza) a CA e emite um certificado de servidor novo para
    `names`. Devolve os caminhos e se a CA foi criada agora."""
    directory = tls_dir(config_dir)
    ca_cert, ca_key, ca_created = _load_or_create_ca(directory)

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=SERVER_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_encipherment=False, key_cert_sign=False, crl_sign=False,
            content_commitment=False, data_encipherment=False, key_agreement=False,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(_san(names), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path = os.path.join(directory, SERVER_CERT)
    key_path = os.path.join(directory, SERVER_KEY)
    _write_private(key_path, _key_pem(key))
    with open(cert_path, "wb") as fh:
        # A cadeia completa (servidor + CA) evita erros em clientes mais exigentes.
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
        fh.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    return {
        "cert": cert_path, "key": key_path,
        "ca": os.path.join(directory, CA_CERT), "ca_created": ca_created,
        "names": list(names), "expires": (now + datetime.timedelta(days=SERVER_DAYS)).date().isoformat(),
    }


def ca_path(config_dir: str):
    """Caminho da CA local, se existir (para a servir em /ca.crt)."""
    path = os.path.join(config_dir, "tls", CA_CERT)
    return path if os.path.exists(path) else None
