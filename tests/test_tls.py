"""
Testes de server/tls.py e da rota /ca.crt — HTTPS com CA local.
"""

import ipaddress
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from core.settings import Settings
from server import tls
from server.app import create_app
from server.auth import ServerConfig


def load(path):
    with open(path, "rb") as fh:
        return x509.load_pem_x509_certificate(fh.read())


def test_server_certificate_is_signed_by_the_ca_and_covers_the_names(tmp_path):
    result = tls.make_certificates(str(tmp_path), ["192.168.1.120", "meshdeck.local"])
    cert, ca = load(result["cert"]), load(result["ca"])

    assert cert.issuer == ca.subject
    ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes,
                           ec.ECDSA(cert.signature_hash_algorithm))   # lança se não for da CA
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("192.168.1.120") in san.get_values_for_type(x509.IPAddress)
    assert "meshdeck.local" in san.get_values_for_type(x509.DNSName)
    # iOS recusa certificados de servidor com mais de 825 dias, e os públicos com mais de 398.
    assert (cert.not_valid_after - cert.not_valid_before).days <= 398
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    assert oct(os.stat(result["key"]).st_mode & 0o777) == "0o600"


def test_regenerating_keeps_the_ca_so_devices_stay_trusted(tmp_path):
    first = tls.make_certificates(str(tmp_path), ["10.0.0.1"])
    ca_before = load(first["ca"]).fingerprint(hashes.SHA256())
    second = tls.make_certificates(str(tmp_path), ["10.0.0.2"])
    ca_after = load(second["ca"]).fingerprint(hashes.SHA256())
    assert first["ca_created"] is True and second["ca_created"] is False
    assert ca_before == ca_after


def make_client(tmp_path):
    config = ServerConfig(Settings(filename="server.json", directory=str(tmp_path)))
    app, _socketio, _mesh = create_app(config)
    return config, app.test_client()


def test_ca_download_is_public_and_only_when_it_exists(tmp_path):
    _config, client = make_client(tmp_path)
    assert client.get("/ca.crt").status_code == 404

    tls.make_certificates(str(tmp_path), ["192.168.1.120"])
    response = client.get("/ca.crt")      # sem login
    assert response.status_code == 200
    assert response.mimetype == "application/x-x509-ca-cert"
    assert b"BEGIN CERTIFICATE" in response.data
    assert b"PRIVATE KEY" not in response.data


def test_page_learns_the_https_port_only_when_tls_is_on(tmp_path):
    """A página em HTTP usa-o para dizer onde a bússola funciona."""
    config, client = make_client(tmp_path)
    assert client.get("/api/whoami").get_json()["https_port"] is None

    result = tls.make_certificates(str(tmp_path), ["192.168.1.120"])
    config.set_tls(result["cert"], result["key"])
    config.set_https_port(9443)
    assert client.get("/api/whoami").get_json()["https_port"] == 9443

    config.clear_tls()
    assert client.get("/api/whoami").get_json()["https_port"] is None
