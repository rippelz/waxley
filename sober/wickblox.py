#!/usr/bin/env python3
"""mockblox for wick: the same DNS/HTTPS stand-in, with wick_routes.py answering first.

wick_routes.handle() covers what only Sober needs (its remote manifest, the mobile app's
endpoints, the game join) and hands everything else to Waxley's routes.py. Both are reloaded
when they change, so they can be edited while Sober runs.

Certificates come from wick's own CA (~/.local/share/waxley/wick/certs): the Android engine
verifies in OpenSSL's strict X.509 mode, which needs key identifiers (SKID on the CA, AKID on
each leaf) that mockblox's certificates don't have.

  wickblox.py         serve (inside wick's namespace)
  wickblox.py --ca    print wick's CA certificate (PEM)
"""
import datetime
import os
import ssl
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import mockblox  # noqa: E402
import wick_routes  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402

CERTS = os.path.join(mockblox.DATA, "wick", "certs")


def key_usage(**on):
    names = ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment",
             "key_agreement", "key_cert_sign", "crl_sign", "encipher_only", "decipher_only")
    return x509.KeyUsage(**{n: on.get(n, False) for n in names})


def load_ca():
    os.makedirs(CERTS, exist_ok=True)
    kp, cp = os.path.join(CERTS, "ca.key"), os.path.join(CERTS, "ca.pem")
    if not os.path.exists(cp):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wick local CA")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(key_usage(key_cert_sign=True, crl_sign=True), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .sign(key, hashes.SHA256()))
        mockblox._write_key(kp, key)
        with open(cp, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(kp, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), None)
    with open(cp, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    return key, cert


CA_KEY, CA_CERT = load_ca()
_ctx_cache = {}
_ctx_lock = threading.Lock()
_leaf_key = ec.generate_private_key(ec.SECP256R1())


def leaf_cert(host):
    now = datetime.datetime.now(datetime.timezone.utc)
    return (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(CA_CERT.subject).public_key(_leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=397))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(key_usage(digital_signature=True), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(_leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(CA_KEY.public_key()), critical=False)
            .sign(CA_KEY, hashes.SHA256()))


def context_for(host):
    host = (host or "www.roblox.com").lower()
    with _ctx_lock:
        if host in _ctx_cache:
            return _ctx_cache[host]
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.set_alpn_protocols(["http/1.1"])
        with tempfile.NamedTemporaryFile("wb", delete=False) as cf, tempfile.NamedTemporaryFile("wb", delete=False) as kf:
            cf.write(leaf_cert(host).public_bytes(serialization.Encoding.PEM))
            kf.write(_leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
        ctx.load_cert_chain(cf.name, kf.name)
        os.unlink(cf.name)
        os.unlink(kf.name)
        _ctx_cache[host] = ctx
        return ctx


def sni_callback(sslobj, server_name, _ctx):
    sslobj.context = context_for(server_name)
    sslobj._wick_sni = server_name


class Handler(mockblox.Handler):
    """mockblox's handler, plus a log line for every TLS handshake that fails (WICK_TLSLOG=1)."""

    def setup(self):
        super().setup()
        if os.environ.get("WICK_TLSLOG") and isinstance(self.request, ssl.SSLSocket):
            try:
                self.request.do_handshake()
                mockblox.log(f"TLS ok {self.request.server_hostname or getattr(self.request, '_wick_sni', '?')} "
                             f"{self.request.version()} {self.request.selected_alpn_protocol()}")
            except Exception as e:
                mockblox.log(f"TLS FAIL sni={getattr(self.request, '_wick_sni', '?')} {type(e).__name__}: {e}")
                raise

    def handle_any(self):
        if os.environ.get("WICK_TLSLOG"):
            mockblox.log(f"UA {self.headers.get('Host')} {self.headers.get('User-Agent')}")
        super().handle_any()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = handle_any


if __name__ == "__main__":
    if sys.argv[1:] == ["--ca"]:
        print(CA_CERT.public_bytes(serialization.Encoding.PEM).decode(), end="")
        sys.exit()
    # mockblox looks these up at call time: its HTTPS server mints certs from wick's CA, and its
    # handler calls routes.handle() and reloads `routes` when that file changes
    mockblox.context_for = context_for
    mockblox.sni_callback = sni_callback
    mockblox.Handler = Handler
    mockblox.CA_KEY, mockblox.CA_CERT = CA_KEY, CA_CERT
    mockblox.routes = wick_routes
    mockblox._routes_mtime[0] = os.path.getmtime(wick_routes.__file__)
    for fn in (mockblox.serve_dns, mockblox.serve_http):
        threading.Thread(target=fn, daemon=True).start()
    threading.Thread(target=mockblox.serve_dns, args=("127.0.0.53",), daemon=True).start()
    open(mockblox.LOG, "w").close()
    mockblox.log("wickblox up")
    mockblox.serve_https()
