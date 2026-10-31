"""Git forges: GitHub, Gitea, Forgejo and Gogs, through their REST APIs.

So that people and LLMs can open, list, merge and close pull requests on
remote stores from the command line.  Forges are configured in the user's
config (tokens are personal), matched to a repository by the host of its git
remote:

    [forges.work]
    type = "forgejo"                 # github | gitea | forgejo | gogs
    host = "git.example.org"
    token = {command = "pass show", id = "kb/forge/work"}

github.com needs no table: its token comes from GH_TOKEN or GITHUB_TOKEN
unless one is configured.  The API token is separate from git's own
credentials, which ``git push`` still uses.
"""

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from . import secrets
from .config import user_config_path, _read
from .errors import KBError

TYPES = ("github", "gitea", "forgejo", "gogs")
MERGE_METHODS = ("merge", "squash", "rebase")


@dataclass
class ForgeConfig:
    name: str
    type: str
    host: str
    api: str
    token: object = None        # a secret spec (see kb.secrets)

    @property
    def web(self) -> str:
        return f"https://{self.host}"

    @property
    def token_page(self) -> str:
        if self.type == "github":
            return f"{self.web}/settings/personal-access-tokens/new"
        return f"{self.web}/user/settings/applications"

    @property
    def scopes(self) -> str:
        if self.type == "github":
            return ("fine-grained token for the exchange repositories: Contents read/write, "
                    "Pull requests read/write, Metadata read")
        if self.type == "gogs":
            return "an access token (Gogs tokens are not scoped)"
        return "token scopes write:repository and read:user"

    def token_specs(self, override=None) -> list:
        specs = [override] if override else []
        specs += [f"env:KB_FORGE_TOKEN_{re.sub(r'[^A-Z0-9]', '_', self.name.upper())}", "env:KB_FORGE_TOKEN"]
        if self.token is not None:
            specs.append(self.token)
        if self.type == "github":
            specs += ["env:GH_TOKEN", "env:GITHUB_TOKEN"]
        elif self.type in ("gitea", "forgejo"):
            specs += ["env:GITEA_TOKEN", "env:FORGEJO_TOKEN"]
        return specs


def default_api(ftype: str, host: str) -> str:
    if ftype == "github":
        return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    return f"https://{host}/api/v1"


def configured(raw: dict) -> list[ForgeConfig]:
    out = []
    for name, f in raw.get("forges", {}).items():
        ftype = f.get("type", "")
        if ftype not in TYPES:
            raise KBError(f"forge {name}: type must be one of {', '.join(TYPES)}")
        host = f.get("host") or ""
        if not host:
            raise KBError(f"forge {name}: needs a host")
        out.append(ForgeConfig(name, ftype, host, f.get("api") or default_api(ftype, host), f.get("token")))
    if not any(f.host == "github.com" for f in out):
        out.append(ForgeConfig("github", "github", "github.com", default_api("github", "github.com")))
    return out


def by_name(raw: dict, name: str) -> ForgeConfig:
    for f in configured(raw):
        if f.name == name:
            return f
    raise KBError(f"no forge {name!r}; configure it with kb forge add")


def for_host(raw: dict, host: str) -> ForgeConfig:
    for f in configured(raw):
        if f.host == host:
            return f
    raise KBError(f"no forge configured for host {host}; kb forge add NAME --type ... --host {host}")


_SCP = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>[^/].*)$")


def parse_remote(url: str) -> tuple[str, str, str]:
    """(host, owner, repo) of a git remote URL (https, ssh://, or scp-like)."""
    if "://" in url:
        u = urllib.parse.urlparse(url)
        host, path = u.hostname or "", u.path
    else:
        m = _SCP.match(url)
        if not m:
            raise KBError(f"cannot parse git remote {url!r}")
        host, path = m["host"], m["path"]
    parts = [p for p in path.strip("/").removesuffix(".git").split("/") if p]
    if len(parts) < 2:
        raise KBError(f"git remote {url!r} does not name owner/repo")
    return host, parts[-2], parts[-1]


def remote_url(root: Path, remote: str = "origin") -> str:
    r = subprocess.run(["git", "-C", str(root), "remote", "get-url", remote], capture_output=True, text=True)
    if r.returncode != 0:
        raise KBError(f"{root}: no git remote {remote!r}")
    return r.stdout.strip()


class Client:
    """A minimal REST client; subclasses map the operations onto each API."""

    def __init__(self, fc: ForgeConfig, token: str | None):
        self.fc = fc
        self.token = token

    def _headers(self) -> dict:
        return {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "kb"}

    def request(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        url = self.fc.api.rstrip("/") + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            hint = " (check the token and its scopes: kb forge auth " + self.fc.name + ")" if exc.code in (401, 403) else ""
            raise KBError(f"{self.fc.name}: {method} {path}: HTTP {exc.code}: {detail}{hint}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise KBError(f"{self.fc.name}: cannot reach {self.fc.api}: {exc}") from None
        return json.loads(text) if text.strip() else {}

    # Operations: whoami, pr_create, pr_list, pr_get, pr_merge, pr_close, pr_comment.

    def whoami(self) -> str:
        return self.request("GET", "/user").get("login", "")

    def _pr(self, d: dict) -> dict:
        return {"number": d.get("number"), "title": d.get("title"), "state": d.get("state"),
                "merged": bool(d.get("merged") or d.get("merged_at")),
                "head": (d.get("head") or {}).get("ref"), "base": (d.get("base") or {}).get("ref"),
                "url": d.get("html_url") or d.get("url"), "author": (d.get("user") or {}).get("login")}

    def pr_create(self, owner, repo, head, base, title, body="") -> dict:
        return self._pr(self.request("POST", f"/repos/{owner}/{repo}/pulls",
                                     {"head": head, "base": base, "title": title, "body": body}))

    def pr_list(self, owner, repo, state="open") -> list[dict]:
        return [self._pr(d) for d in self.request("GET", f"/repos/{owner}/{repo}/pulls", query={"state": state})]

    def pr_get(self, owner, repo, number) -> dict:
        return self._pr(self.request("GET", f"/repos/{owner}/{repo}/pulls/{number}"))

    def pr_close(self, owner, repo, number) -> dict:
        return self._pr(self.request("PATCH", f"/repos/{owner}/{repo}/pulls/{number}", {"state": "closed"}))

    def pr_comment(self, owner, repo, number, body) -> dict:
        d = self.request("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body})
        return {"id": d.get("id"), "url": d.get("html_url")}

    def pr_merge(self, owner, repo, number, method="merge", message=None) -> dict:
        raise NotImplementedError


class GitHub(Client):
    def _headers(self):
        h = super()._headers() | {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def pr_merge(self, owner, repo, number, method="merge", message=None):
        body = {"merge_method": method}
        if message:
            body["commit_title"] = message
        d = self.request("PUT", f"/repos/{owner}/{repo}/pulls/{number}/merge", body)
        return {"number": number, "merged": bool(d.get("merged")), "sha": d.get("sha"), "message": d.get("message")}


class Gitea(Client):
    """Gitea and Forgejo (Forgejo keeps Gitea's API)."""

    def _headers(self):
        h = super()._headers()
        if self.token:
            h["Authorization"] = f"token {self.token}"
        return h

    def pr_merge(self, owner, repo, number, method="merge", message=None):
        body = {"Do": method}
        if message:
            body["MergeTitleField"] = message
        self.request("POST", f"/repos/{owner}/{repo}/pulls/{number}/merge", body)
        return {"number": number, "merged": True}


class Gogs(Gitea):
    """Gogs has repositories and issues in its API, but no pull requests."""

    def _no_prs(self, *a, **k):
        raise KBError(f"{self.fc.name}: the Gogs API has no pull requests; push the branch and open it on "
                      f"{self.fc.web}, or move to Gitea/Forgejo")

    pr_create = pr_list = pr_get = pr_merge = pr_close = _no_prs


def client(fc: ForgeConfig, token_override=None, need_token: bool = True) -> Client:
    try:
        token, _ = secrets.first_available(fc.token_specs(token_override))
    except KBError:
        if need_token:
            raise KBError(f"no token for forge {fc.name}: kb forge auth {fc.name} explains how to set one up") from None
        token = None
    cls = {"github": GitHub, "gitea": Gitea, "forgejo": Gitea, "gogs": Gogs}[fc.type]
    return cls(fc, token)


@dataclass
class Repo:
    forge: ForgeConfig
    owner: str
    name: str


def repo_for(raw: dict, root: Path, remote: str = "origin", forge: str | None = None,
             repo: str | None = None) -> Repo:
    """The forge repository a store's clone pushes to (or one named explicitly)."""
    if repo:
        if not forge:
            raise KBError("--repo needs --forge")
        owner, _, name = repo.partition("/")
        if not name:
            raise KBError("--repo is OWNER/NAME")
        return Repo(by_name(raw, forge), owner, name)
    host, owner, name = parse_remote(remote_url(root, remote))
    fc = by_name(raw, forge) if forge else for_host(raw, host)
    return Repo(fc, owner, name)


def add_config(name: str, ftype: str, host: str, api: str | None = None, token=None) -> Path:
    """Append a [forges.NAME] table to the user's config (tokens are personal, never in a store)."""
    if ftype not in TYPES:
        raise KBError(f"type must be one of {', '.join(TYPES)}")
    path = user_config_path()
    existing = _read(path)
    if name in existing.get("forges", {}):
        raise KBError(f"forge {name} already in {path}; edit it there")
    entry = {"type": ftype, "host": host}
    if api:
        entry["api"] = api
    if token is not None:
        s = secrets.parse(token)
        if s.scheme == "value":
            raise KBError("refusing to write a token into the config file; store it with pass and give "
                          "--token-from 'cmd:pass show NAME' (or a 0600 file: --token-from file:PATH)")
        entry["token"] = ({"command": s.value, "id": s.id} if s.scheme == "command" and s.id
                          else {s.scheme: s.value})
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + sep + tomli_w.dumps({"forges": {name: entry}}))
    return path
