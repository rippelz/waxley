#!/usr/bin/env python3
"""Local stand-in for the Roblox web services Studio talks to.

Runs inside Waxley's network namespace (loopback only):
  * DNS on 127.0.0.1:53 (and 127.0.0.53) answers every name with 127.0.0.1, so nothing resolves to the real internet.
  * HTTPS on 127.0.0.1:443 (and HTTP on :80) with per-host certificates minted
    from a local CA that is appended to Waxley's ssl/cacert.pem.
  * Every request is logged; routes.py decides the response.
"""
import datetime
import gzip
import importlib
import json
import os
import socket
import socketserver
import ssl
import struct
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import routes  # noqa: E402

DATA = os.environ.get("WAXLEY_DATA", os.path.expanduser("~/.local/share/waxley"))
CERTS = os.path.join(DATA, "certs")
LOG = os.environ.get("WAXLEY_LOG") or os.path.join(DATA, "run", "requests.log")
log_lock = threading.Lock()


def log(line):
    with log_lock, open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now():%H:%M:%S} {line}\n")


# ---------- certificates ----------

def _write_key(path, key):
    with open(path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))


def load_ca():
    os.makedirs(CERTS, exist_ok=True)
    kp, cp = os.path.join(CERTS, "ca.key"), os.path.join(CERTS, "ca.pem")
    if not os.path.exists(cp):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Waxley local CA")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
                .sign(key, hashes.SHA256()))
        _write_key(kp, key)
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


def context_for(host):
    host = (host or "www.roblox.com").lower()
    with _ctx_lock:
        if host in _ctx_cache:
            return _ctx_cache[host]
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
                .issuer_name(CA_CERT.subject).public_key(_leaf_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=825))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(CA_KEY, hashes.SHA256()))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.set_alpn_protocols(["http/1.1"])
        with tempfile.NamedTemporaryFile("wb", delete=False) as cf, tempfile.NamedTemporaryFile("wb", delete=False) as kf:
            cf.write(cert.public_bytes(serialization.Encoding.PEM))
            kf.write(_leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
        ctx.load_cert_chain(cf.name, kf.name)
        os.unlink(cf.name)
        os.unlink(kf.name)
        _ctx_cache[host] = ctx
        return ctx


def sni_callback(sslobj, server_name, _ctx):
    sslobj.context = context_for(server_name)


# ---------- HTTP ----------

_routes_mtime = [os.path.getmtime(routes.__file__)]


def reload_routes():
    m = os.path.getmtime(routes.__file__)
    if m != _routes_mtime[0]:
        _routes_mtime[0] = m
        importlib.reload(routes)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def handle_any(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        try:
            reload_routes()  # edit routes.py without restarting Studio
            status, headers, payload = routes.handle(self.command, host, self.path, self.headers, body)
        except Exception:
            traceback.print_exc()
            status, headers, payload = 500, {}, b""
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(payload, str):
            payload = payload.encode()
        log(f"{status} {self.command} {host}{self.path} ({len(body)}b in, {len(payload)}b out)")
        if body and len(body) < 8192 and os.environ.get("WAXLEY_DEBUG"):
            with log_lock, open(LOG.replace("requests.log", "bodies.log"), "ab") as f:
                f.write(f"{self.command} {host}{self.path}\n".encode() + body + b"\n\n")
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = handle_any


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve_https():
    srv = Server(("127.0.0.1", 443), Handler)
    base = context_for("www.roblox.com")  # default cert; SNI swaps in the right host
    base.sni_callback = sni_callback
    srv.socket = base.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    srv.serve_forever()


def serve_http():
    Server(("127.0.0.1", 80), Handler).serve_forever()


# ---------- DNS: everything -> 127.0.0.1 ----------

class DNS(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        try:
            qid, flags, qd = struct.unpack(">HHH", data[:6])
            i, labels = 12, []
            while data[i]:
                labels.append(data[i + 1:i + 1 + data[i]].decode(errors="replace"))
                i += data[i] + 1
            qtype = struct.unpack(">H", data[i + 1:i + 3])[0]
            question = data[12:i + 5]
            name = ".".join(labels)
            answers = b""
            if qtype == 1:  # A
                answers = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton("127.0.0.1")
            log(f"DNS {name} type={qtype}")
            sock.sendto(struct.pack(">HHHHHH", qid, 0x8180, 1, 1 if answers else 0, 0, 0) + question + answers,
                        self.client_address)
        except Exception:
            traceback.print_exc()


def serve_dns(addr="127.0.0.1"):
    socketserver.ThreadingUDPServer((addr, 53), DNS).serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["--ca"]:
        print(CA_CERT.public_bytes(serialization.Encoding.PEM).decode(), end="")
        sys.exit()
    for fn in (serve_dns, serve_http):
        threading.Thread(target=fn, daemon=True).start()
    # flatpak apps (Sober) get a copy of the host resolv.conf: systemd-resolved's stub address.
    # A separate socket, so replies come from the address that was asked.
    threading.Thread(target=serve_dns, args=("127.0.0.53",), daemon=True).start()
    open(LOG, "w").close()  # fresh request log per session
    log("mockblox up")
    serve_https()
