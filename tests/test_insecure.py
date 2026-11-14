"""kb add --no-check-certificate: fetching from an HTTPS server whose certificate doesn't verify."""

import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner

from kb import content
from kb.cli import main


@pytest.fixture
def tls_site(tmp_path):
    """An HTTPS server with a self-signed certificate, which no CA bundle trusts."""
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    key, crt = tmp_path / "key.pem", tmp_path / "crt.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-keyout", key, "-out", crt], check=True, capture_output=True)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><head><title>Over TLS</title></head><body><h1>Over TLS</h1><p>Hi.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"https://127.0.0.1:{server.server_address[1]}/page.html"
    server.shutdown()


def test_fetch_verifies_unless_told(kb, tls_site):
    with pytest.raises(content.FetchError, match="CERTIFICATE_VERIFY_FAILED"):
        content.fetch_info(kb, tls_site)
    with content.no_check_certificate():
        f = content.fetch_info(kb, tls_site)
    assert b"Over TLS" in f.path.read_bytes()
    assert content.INSECURE is False                      # only within


def test_add_no_check_certificate(root, tls_site):
    r = CliRunner().invoke(main, ["-C", str(root), "add", tls_site, "--no-page", "--no-extract",
                                  "--set", "doctype=note", "--creators", "A. Person"])
    assert r.exit_code != 0 and "CERTIFICATE_VERIFY_FAILED" in r.output
    r = CliRunner().invoke(main, ["-C", str(root), "add", tls_site, "--no-check-certificate", "--no-page",
                                  "--no-extract", "--set", "doctype=note", "--creators", "A. Person", "--json"])
    assert r.exit_code == 0, r.output
    assert content.INSECURE is False


def test_monolith_k(monkeypatch):
    from kb import transforms
    seen = []
    monkeypatch.setattr(transforms, "_run", lambda argv, **kw: seen.append(argv) or
                        type("R", (), {"stderr": ""})())
    m = transforms.Monolith("monolith", content.MARKUP, "text/html", ".html", fidelity="lossless", purpose="page")
    m.run("a.html", "b.html", "text/html")
    with content.no_check_certificate():
        m.run("a.html", "b.html", "text/html")
    assert "-k" not in seen[0] and "-k" in seen[1]
