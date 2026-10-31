import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kb import forge, githooks, secrets
from kb.errors import KBError
from kb.records import KB

from conftest import commit, git, make_store, observation


# -- secrets ---------------------------------------------------------------------

def test_secret_sources(tmp_path, monkeypatch):
    warnings = []
    w = warnings.append
    assert secrets.resolve({"command": "printf '%s\\n'", "id": "tok-123"}, w) == "tok-123"
    assert secrets.resolve("cmd:printf 'abc\\nsecond line\\n'", w) == "abc"     # first line only
    f = tmp_path / "token"
    f.write_text("filetok\n")
    f.chmod(0o600)
    assert secrets.resolve(f"file:{f}", w) == "filetok" and warnings == []
    f.chmod(0o644)
    assert secrets.resolve({"file": str(f)}, w) == "filetok"
    assert "more open than 0600" in warnings[-1]
    monkeypatch.setenv("MY_TOKEN", "envtok")
    assert secrets.resolve("env:MY_TOKEN", w) == "envtok"
    assert secrets.resolve("value:plain", w) == "plain" and "prefer a command" in warnings[-1]
    with pytest.raises(KBError, match="failed"):
        secrets.resolve("cmd:false", w)
    with pytest.raises(KBError, match="cmd:"):
        secrets.parse("justatoken")
    with pytest.raises(KBError, match="exactly one"):
        secrets.parse({"command": "x", "file": "y"})
    tok, spec = secrets.first_available(["env:UNSET_VAR_XYZ", "env:MY_TOKEN"], w)
    assert tok == "envtok" and spec.value == "MY_TOKEN"
    assert "envtok" not in secrets.mask("envtok-long-secret")


# -- forge clients against a fake server --------------------------------------------

class FakeForge(BaseHTTPRequestHandler):
    calls: list = []
    prs: dict = {}

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        FakeForge.calls.append((method, self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") not in ("token good", "Bearer good"):
            return self._send(401, {"message": "bad credentials"})
        path = self.path.split("?")[0].removeprefix("/api/v1").removeprefix("/api/v3")
        if path == "/user":
            return self._send(200, {"login": "tester"})
        parts = path.strip("/").split("/")
        if parts[:1] == ["repos"] and parts[3] == "pulls":
            if method == "POST" and len(parts) == 4:
                num = len(FakeForge.prs) + 1
                pr = {"number": num, "title": body["title"], "state": "open", "head": {"ref": body["head"]},
                      "base": {"ref": body["base"]}, "html_url": f"https://forge.test/pr/{num}",
                      "user": {"login": "tester"}}
                FakeForge.prs[num] = pr
                return self._send(201, pr)
            if method == "GET" and len(parts) == 4:
                return self._send(200, list(FakeForge.prs.values()))
            num = int(parts[4])
            pr = FakeForge.prs[num]
            if len(parts) == 6 and parts[5] == "merge":
                pr["state"], pr["merged"] = "closed", True
                return self._send(200, {"merged": True, "sha": "abc"})
            if method == "PATCH":
                pr["state"] = body["state"]
            return self._send(200, pr)
        if parts[:1] == ["repos"] and parts[3] == "issues":
            return self._send(201, {"id": 7, "html_url": "https://forge.test/c/7"})
        return self._send(404, {"message": "not found"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_PUT(self):
        self._handle("PUT")

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    FakeForge.calls, FakeForge.prs = [], {}
    srv = HTTPServer(("127.0.0.1", 0), FakeForge)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.parametrize("ftype,auth", [("gitea", "token good"), ("forgejo", "token good"), ("github", "Bearer good")])
def test_forge_pr_lifecycle(server, ftype, auth, monkeypatch):
    api = server + ("/api/v3" if ftype == "github" else "/api/v1")
    fc = forge.ForgeConfig("t", ftype, "forge.test", api, {"command": "printf '%s\\n'", "id": "good"})
    c = forge.client(fc)
    assert c.whoami() == "tester"
    pr = c.pr_create("o", "r", "kb/ks-1", "main", "Push from home", "body")
    assert pr["number"] == 1 and pr["head"] == "kb/ks-1" and pr["url"].endswith("/pr/1")
    assert [p["number"] for p in c.pr_list("o", "r")] == [1]
    assert c.pr_merge("o", "r", 1, "squash")["merged"]
    assert c.pr_comment("o", "r", 1, "thanks")["id"] == 7
    assert c.pr_close("o", "r", 1)["state"] == "closed"
    assert all(a == auth for _, _, a, _ in FakeForge.calls)
    merge_call = [b for m, p, _, b in FakeForge.calls if p.endswith("/merge")][0]
    assert merge_call == ({"merge_method": "squash"} if ftype == "github" else {"Do": "squash"})


def test_forge_errors(server):
    fc = forge.ForgeConfig("t", "gitea", "forge.test", server + "/api/v1", "value:wrong")
    with pytest.raises(KBError, match="HTTP 401.*kb forge auth t"):
        forge.client(fc).whoami()
    gogs = forge.client(forge.ForgeConfig("g", "gogs", "gogs.test", server + "/api/v1", "value:good"))
    with pytest.raises(KBError, match="no pull requests"):
        gogs.pr_create("o", "r", "a", "b", "t")
    with pytest.raises(KBError, match="no token"):
        forge.client(forge.ForgeConfig("n", "gitea", "x.test", server))


def test_remote_parsing_and_config(tmp_path):
    assert forge.parse_remote("git@github.com:owner/repo.git") == ("github.com", "owner", "repo")
    assert forge.parse_remote("https://git.example.org/grp/kb") == ("git.example.org", "grp", "kb")
    assert forge.parse_remote("ssh://git@git.example.org:2222/grp/kb.git") == ("git.example.org", "grp", "kb")
    with pytest.raises(KBError, match="refusing"):
        forge.add_config("w", "forgejo", "git.example.org", token="value:abc")
    p = forge.add_config("w", "forgejo", "git.example.org", token="cmd:pass show kb/forge/w")
    from kb.config import _read
    raw = _read(p)
    assert raw["forges"]["w"]["token"] == {"command": "pass show kb/forge/w"}
    fc = forge.for_host(raw, "git.example.org")
    assert fc.api == "https://git.example.org/api/v1" and fc.token_page.endswith("/user/settings/applications")
    assert forge.for_host(raw, "github.com").api == "https://api.github.com"
    with pytest.raises(KBError, match="already"):
        forge.add_config("w", "gitea", "x")


# -- git hooks on exchange repositories ----------------------------------------------

def _bundle_file(kb, tmp_path):
    from kb import bundle
    from kb.push import make_kset
    o = observation(kb)
    ks = make_kset(kb, [o.id])
    return bundle.write(bundle.make(kb, ks.id), tmp_path)


def test_git_hooks_install_keep_user_hooks(tmp_path):
    repo = tmp_path / "ex"
    githooks.init_exchange(repo)
    hooks = githooks.hooks_dir(repo)
    text = (hooks / "pre-commit").read_text()
    (hooks / "pre-commit").write_text(text.replace("#!/bin/sh\n", "#!/bin/sh\necho user-hook\n", 1))
    githooks.install(repo)                                          # idempotent, keeps the user's line
    text = (hooks / "pre-commit").read_text()
    assert text.count(githooks.BEGIN) == 1 and "user-hook" in text and "hook git-pre-commit" in text
    assert githooks.status(repo)["pre-push"] == "installed"
    githooks.remove(repo)
    assert "user-hook" in (hooks / "pre-commit").read_text() and githooks.BEGIN not in (hooks / "pre-commit").read_text()


def test_exchange_hooks_guard_bundles(kb, tmp_path):
    repo = tmp_path / "ex"
    githooks.init_exchange(repo)
    commit(repo, "init")
    f = _bundle_file(kb, tmp_path)
    dest = repo / "bundles" / f.name
    dest.write_bytes(f.read_bytes())
    git(repo, "add", "-A")
    r = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "a bundle"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    dest.write_text(dest.read_text().replace("Noisy run", "Calm run"))   # editing a bundle: refused
    git(repo, "add", "-A")
    r = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "sneaky"], capture_output=True, text=True)
    assert r.returncode != 0 and "immutable" in r.stderr
    git(repo, "reset", "-q", "--hard")
    bad = repo / "bundles" / "x.kbundle.json"
    bad.write_text('{"kb_bundle": 1, "id": "nope"}')                                # a corrupt new bundle: refused
    git(repo, "add", "-A")
    r = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "bad"], capture_output=True, text=True)
    assert r.returncode != 0 and "checksum" in r.stderr


# -- a push that lands as a pull request ------------------------------------------------

def test_push_as_pull_request(tmp_path, server):
    from kb import bundle, federation, push
    bare = tmp_path / "exchange.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    ex = tmp_path / "exchange"
    githooks.init_exchange(ex)
    url = "https://forge.test/lab/group-exchange.git"
    git(ex, "remote", "add", "origin", url)
    git(ex, "config", f"url.{bare}.pushInsteadOf", url)
    git(ex, "checkout", "-q", "-b", "main")
    commit(ex, "init")
    git(ex, "push", "-q", "origin", "main")
    forge.add_config("lab", "gitea", "forge.test", api=server + "/api/v1", token="cmd:printf '%s\\n' good")
    group = make_store(tmp_path / "group", "group", "grp")
    home = make_store(tmp_path / "home", "home", "home", access="group")
    federation.add(home, "grp", "up", path=str(group.root), mode="pr", exchange=str(ex))
    o = observation(KB.open(home.root))
    r = push.push(KB.open(home.root), "grp", [o.id])
    assert r["pr"]["number"] == 1 and r["pr"]["base"] == "main" and r["branch"].startswith("kb/ks-")
    body = [c for c in FakeForge.calls if c[0] == "POST" and c[1].endswith("/pulls")][0][3]["body"]
    assert "Noisy run" in body and "What accepting it would do" in body           # reviewers see a rendering
    assert git(ex, "branch", "--show-current").strip() == "main"
    assert r["branch"] in git(bare, "branch", "--list")
    assert o.id not in KB.open(group.root).records                                # waits for review on the forge
    # After the PR is merged, the group store imports the bundle.
    git(ex, "merge", "-q", "--ff-only", r["branch"])
    out = bundle.import_dir(KB.open(group.root), ex, accept_all=True)
    assert out[0]["status"] == "accepted" and o.id in KB.open(group.root).records
    assert bundle.import_dir(KB.open(group.root), ex)[0]["status"] == "accepted"   # idempotent
