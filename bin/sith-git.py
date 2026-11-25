#!/usr/bin/env python3
"""Backend for the Sith Git OWL app (~/.config/omalauncher/widgets/sith-git.owl).

Usage: sith-git.py status | use <path> | add <path> | forget <path> | scan | init <path>
       clone <url> | stage <file> | unstage <file> | stage-all | unstage-all
       discard <file> | commit <msg> | pull | push | fetch | checkout <branch>
       branch <name> | stash | stash-pop | remote <url> | ident <name> <email>
Contract: every call prints exactly one JSON object on stdout and exits 0.
"""
import json, os, re, shutil, subprocess, sys, time
from urllib.parse import urlsplit
from pathlib import Path

APP = "sith-git"
HOME = Path.home()
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / APP
STATE = STATE_DIR / "state.json"
CLONE_DIR = HOME  # clones land straight in $HOME/<name>
DEFAULTS = {"repos": [], "cur": "", "last": {"op": "", "ok": True, "msg": "", "ts": 0}}
NET_TIMEOUT = 120
CLONE_TIMEOUT = 300
PRIVATE_MSG = "private repo or wrong name — log in with `gh auth login`"
ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", LC_ALL="C",
           GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15")


def load():
    st = json.loads(json.dumps(DEFAULTS))
    try:
        d = json.loads(STATE.read_text())
        if isinstance(d, dict):
            st.update({k: d[k] for k in DEFAULTS if k in d})
    except Exception:
        pass
    st["repos"] = [r for r in st["repos"] if isinstance(r, str)]
    return st


def save(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    os.replace(tmp, STATE)


def git(repo, *args, timeout=8):
    """Run git in repo; returns (ok, combined output)."""
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           timeout=timeout, env=ENV, stdin=subprocess.DEVNULL)
        out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
        return p.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def norm_path(p):
    p = (p or "").strip()
    if not p:
        return None
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = str(HOME / p)
    return os.path.realpath(p)


def is_repo(path):
    return bool(path) and os.path.isdir(path) and git(path, "rev-parse", "--git-dir", timeout=3)[0]


def safe_file(repo, f):
    """A path inside the repo, relative, no escaping."""
    f = (f or "").strip()
    if not f or f.startswith("-") or "\0" in f:
        return None
    full = os.path.realpath(os.path.join(repo, f))
    if not (full == repo or full.startswith(repo.rstrip("/") + "/")):
        return None
    return os.path.relpath(full, repo)


def valid_branch(repo, name):
    name = (name or "").strip()
    if not name or name.startswith("-") or len(name) > 120:
        return None
    ok, _ = git(repo, "check-ref-format", "--branch", name, timeout=3)
    return name if ok else None


def add_repo(st, path):
    if path not in st["repos"]:
        st["repos"] = (st["repos"] + [path])[-40:]
    st["cur"] = path


def last(st, op, ok, msg):
    msg = (msg or ("done" if ok else "failed")).strip()
    st["last"] = {"op": op, "ok": bool(ok), "msg": msg[-1500:], "ts": int(time.time())}


BAD_CHARS = re.compile(r"[\s;$`|&<>\\'\"(){}*?!#]")
CUT_SEGS = {"tree", "blob", "-", "commit", "commits", "issues", "pulls", "pull", "releases",
            "raw", "src", "wiki", "actions", "tags", "branches", "compare", "archive"}
TWO_SEG_HOSTS = {"github.com", "codeberg.org", "bitbucket.org", "gitea.com"}
NAME_OK = re.compile(r"^[A-Za-z0-9_][\w.-]*$")


def parse_clone(raw):
    """Turn what the user pasted into (clone_url, repo_name) or (None, error)."""
    raw = (raw or "").strip()
    if not raw:
        return None, "paste a repo: user/repo or a URL"
    if BAD_CHARS.search(raw) or len(raw) > 300:
        return None, "that doesn't look like a repo (no spaces or ; $ ` allowed)"
    if re.match(r"^[\w.-]+/[\w.-]+$", raw) and not raw.startswith((".", "-")):
        raw = f"https://github.com/{raw}"
    elif re.match(r"^(www\.)?[\w-]+(\.[\w-]+)+/", raw) and "@" not in raw.split("/")[0]:
        raw = "https://" + raw
    if raw.startswith("http://"):
        raw = "https://" + raw[len("http://"):]
    m = re.match(r"^(?:ssh://)?git@([\w.-]+)[:/]([\w./~-]+?)(?:\.git)?/?$", raw)
    if m:
        host, path = m.group(1).lower(), m.group(2).strip("/")
        segs = [x for x in path.split("/") if x]
        if len(segs) < 2:
            return None, "need owner/repo in the URL"
        return f"git@{host}:{'/'.join(segs)}.git", re.sub(r"\.git$", "", segs[-1])
    if not raw.startswith("https://"):
        return None, "use user/repo, an https:// URL or git@host:user/repo"
    u = urlsplit(raw)
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or u.username or u.password:
        return None, "bad host in URL"
    segs = [x for x in u.path.split("/") if x]
    for i, seg in enumerate(segs):
        if i >= 2 and seg in CUT_SEGS:
            segs = segs[:i]
            break
    if host in TWO_SEG_HOSTS:
        segs = segs[:2]
    if len(segs) < 2:
        return None, "need owner/repo in the URL"
    segs[-1] = re.sub(r"\.git$", "", segs[-1])
    if not all(re.match(r"^[\w.~-]+$", x) for x in segs):
        return None, "odd characters in the repo path"
    name = segs[-1]
    port = f":{u.port}" if u.port else ""
    return f"https://{host}{port}/{'/'.join(segs)}.git", name


def clone_run(url, dest):
    """git clone in its own process group so a timeout kills git-remote-https/index-pack too."""
    import signal
    try:
        p = subprocess.Popen(["git", "-C", str(CLONE_DIR), "clone", "--", url, str(dest)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             text=True, env=ENV, start_new_session=True)
    except Exception as e:
        return False, str(e)
    try:
        out, err = p.communicate(timeout=CLONE_TIMEOUT)
        return p.returncode == 0, (out + "\n" + err).strip()
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            p.kill()
        p.communicate()
        return False, f"timed out after {CLONE_TIMEOUT}s"

def norm_remote(url):
    """Comparable form of a remote: host/owner/repo, lowercase, no scheme/.git."""
    url = (url or "").strip().lower()
    m = re.match(r"^(?:ssh://)?git@([\w.-]+)[:/](.+)$", url)
    if m:
        url = m.group(1) + "/" + m.group(2)
    url = re.sub(r"^[a-z]+://", "", url)
    url = re.sub(r"^[^@/]+@", "", url)
    url = re.sub(r"^www\.", "", url)
    return re.sub(r"(\.git)?/*$", "", url)


def git_err(out):
    """The one git line worth showing."""
    low = (out or "").lower()
    if any(k in low for k in ("could not read username", "terminal prompts disabled",
                              "authentication failed", "repository not found",
                              "not found", "permission denied (publickey)", "403")):
        return PRIVATE_MSG
    if "timed out" in low:
        return f"timed out after {CLONE_TIMEOUT}s — slow network or huge repo"
    if "could not resolve host" in low:
        return "no internet (could not resolve host)"
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    pick = [l for l in lines if l.lower().startswith(("fatal:", "error:"))] or lines
    return (pick[-1] if pick else "clone failed")[:200]


def has_git(path):
    g = os.path.join(path, ".git")
    return os.path.isdir(g) or os.path.isfile(g)


def head_branch(path):
    """Branch from .git/HEAD without spawning git (cheap for status)."""
    try:
        g = os.path.join(path, ".git")
        if os.path.isfile(g):
            m = re.match(r"gitdir:\s*(.+)", open(g).read().strip())
            g = os.path.join(path, m.group(1)) if m else g
        h = open(os.path.join(g, "HEAD")).read().strip()
        return h.split("refs/heads/", 1)[1] if h.startswith("ref:") and "refs/heads/" in h else "HEAD"
    except Exception:
        return ""


def list_repos(st):
    """Saved repos + git repos sitting directly in the clone dir, newest first."""
    seen, out = set(), []
    cands = list(st["repos"])
    try:
        with os.scandir(CLONE_DIR) as it:
            for e in it:
                if len(cands) >= 80:
                    break
                if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                    cands.append(e.path)
    except OSError:
        pass
    for p in cands:
        rp = os.path.realpath(p)
        if rp in seen or not os.path.isdir(rp) or not has_git(rp) or head_branch(rp) == ".invalid":
            continue  # .invalid HEAD = a clone still in progress
        seen.add(rp)
        try:
            mt = os.stat(os.path.join(rp, ".git")).st_mtime
        except OSError:
            mt = 0
        out.append((mt, rp))
    out.sort(reverse=True)
    return [p for _, p in out[:40]]


def light_info(path):
    info = repo_info("")
    if path and os.path.isdir(path) and has_git(path):
        info.update({"valid": True, "path": path, "name": os.path.basename(path), "branch": head_branch(path)})
    return info


def repo_info(path):
    info = {"valid": False, "path": path, "name": os.path.basename(path) if path else "",
            "branch": "", "upstream": "", "ahead": 0, "behind": 0, "files": [],
            "nStaged": 0, "nChanged": 0, "commits": [], "branches": [], "remotes": [],
            "stashes": 0, "detached": False, "empty": False}
    if not is_repo(path):
        return info
    info["valid"] = True
    ok, out = git(path, "status", "--porcelain=v1", "-b", "--untracked-files=all")
    lines = out.splitlines() if ok else []
    if lines and lines[0].startswith("## "):
        head = lines.pop(0)[3:]
        m = re.match(r"(No commits yet on |Initial commit on )?([^.\s]+)(?:\.\.\.(\S+))?(?: \[(.*)\])?", head)
        if m:
            info["empty"] = bool(m.group(1))
            info["branch"] = m.group(2)
            info["upstream"] = m.group(3) or ""
            br = m.group(4) or ""
            a = re.search(r"ahead (\d+)", br)
            b = re.search(r"behind (\d+)", br)
            info["ahead"] = int(a.group(1)) if a else 0
            info["behind"] = int(b.group(1)) if b else 0
        if head.startswith("HEAD (no branch)"):
            info["detached"] = True
            info["branch"] = "HEAD"
    files = []
    for ln in lines[:300]:
        if len(ln) < 4:
            continue
        x, y, f = ln[0], ln[1], ln[3:]
        if " -> " in f:
            f = f.split(" -> ", 1)[1]
        f = f.strip('"')
        untracked = x == "?"
        staged = (not untracked) and x not in (" ", "?")
        unstaged = untracked or y not in (" ",)
        code = "?" if untracked else (x if staged else y)
        label = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied",
                 "U": "conflict", "?": "new"}.get(code, code)
        files.append({"path": f, "x": x, "y": y, "staged": staged, "unstaged": unstaged,
                      "code": code, "label": label})
    info["files"] = files
    info["nStaged"] = sum(1 for f in files if f["staged"])
    info["nChanged"] = sum(1 for f in files if f["unstaged"])
    if not info["empty"]:
        ok, out = git(path, "log", "-n", "25", "--pretty=format:%h\x1f%s\x1f%an\x1f%ar")
        if ok:
            for ln in out.splitlines():
                p = ln.split("\x1f")
                if len(p) == 4:
                    info["commits"].append({"h": p[0], "s": p[1][:120], "a": p[2][:40], "t": p[3]})
    ok, out = git(path, "branch", "--format=%(HEAD)%(refname:short)")
    if ok:
        for ln in out.splitlines()[:60]:
            ln = ln.strip()
            if ln:
                cur_b = ln.startswith("*")
                info["branches"].append({"name": ln[1:] if cur_b else ln, "cur": cur_b})
    ok, out = git(path, "remote", "-v")
    if ok:
        seen = set()
        for ln in out.splitlines():
            p = ln.split()
            if len(p) >= 2 and p[0] not in seen:
                seen.add(p[0])
                info["remotes"].append({"name": p[0], "url": p[1]})
    ok, out = git(path, "stash", "list")
    info["stashes"] = len(out.splitlines()) if ok and out else 0
    return info


def ident():
    name = email = ""
    for f in (HOME / ".gitconfig", HOME / ".config/git/config"):
        try:
            sect = ""
            for ln in f.read_text(errors="ignore").splitlines():
                t = ln.strip()
                if t.startswith("["):
                    sect = t.strip("[]").strip().lower()
                elif sect == "user" and "=" in t:
                    k, v = (x.strip() for x in t.split("=", 1))
                    if k.lower() == "name" and not name:
                        name = v.strip('"')
                    elif k.lower() == "email" and not email:
                        email = v.strip('"')
        except OSError:
            pass
    return {"name": name, "email": email}


def status(st, full=False):
    repos = list_repos(st)
    cur = st["cur"] if st["cur"] in repos else (repos[0] if repos else "")
    gh_hosts = HOME / ".config/gh/hosts.yml"
    try:
        authed = gh_hosts.is_file() and any(k in gh_hosts.read_text(errors="ignore") for k in ("oauth_token", "user:"))
    except OSError:
        authed = False
    return {"ok": True, "ts": int(time.time()),
            "repos": [{"path": r, "name": os.path.basename(r), "valid": True} for r in repos],
            "cur": cur, "repo": repo_info(cur) if full else light_info(cur), "ident": ident(),
            "gh": {"installed": shutil.which("gh") is not None, "authed": authed},
            "lazygit": shutil.which("lazygit") is not None,
            "last": st["last"]}


def scan():
    found = []
    roots = [HOME, Path("/sdcard")]
    skip = {".cache", "node_modules", ".local", ".npm", ".cargo", ".rustup", "Android", ".git"}
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dp, dn, fn in os.walk(root):
            if len(Path(dp).parts) - base_depth >= 4:
                dn[:] = []
            if ".git" in dn or ".git" in fn:
                found.append(os.path.realpath(dp))
                dn[:] = [d for d in dn if d not in skip]
            dn[:] = [d for d in dn if d not in skip and not d.startswith(".") or d in (".config",)]
            if len(found) >= 40:
                break
    return found


def main(argv):
    st = load()
    act = argv[1] if len(argv) > 1 else "status"
    arg = argv[2] if len(argv) > 2 else ""
    cur = st["cur"]

    if act == "status":
        return status(st, full=(arg == "full"))

    if act in ("use", "add"):
        p = norm_path(arg)
        if not is_repo(p):
            last(st, act, False, f"not a git repo: {arg}")
        else:
            top = git(p, "rev-parse", "--show-toplevel", timeout=3)[1].splitlines()[0]
            add_repo(st, top)
            last(st, act, True, f"opened {os.path.basename(top)}")
    elif act == "forget":
        p = norm_path(arg)
        st["repos"] = [r for r in st["repos"] if r != p]
        if st["cur"] == p:
            st["cur"] = st["repos"][0] if st["repos"] else ""
        last(st, act, True, "removed from list (files untouched)")
    elif act == "scan":
        found = scan()
        for r in found:
            if r not in st["repos"]:
                st["repos"].append(r)
        if not st["cur"] and st["repos"]:
            st["cur"] = st["repos"][0]
        last(st, act, True, f"found {len(found)} repo(s)")
    elif act == "init":
        p = norm_path(arg)
        if not p or not p.startswith(str(HOME)):
            last(st, act, False, "pick a folder inside your home")
        else:
            os.makedirs(p, exist_ok=True)
            ok, out = git(p, "init", "-b", "main")
            if ok:
                add_repo(st, p)
            last(st, act, ok, out)
    elif act == "clone":
        url, name = parse_clone(arg)
        if not url:
            last(st, act, False, name)
        elif not NAME_OK.match(name):
            last(st, act, False, "odd repo name")
        else:
            CLONE_DIR.mkdir(parents=True, exist_ok=True)
            dest, n = CLONE_DIR / name, 1
            done = False
            while dest.exists():
                if dest.is_dir() and has_git(str(dest)):
                    ok_r, rem = git(dest, "remote", "get-url", "origin", timeout=3)
                    if ok_r and norm_remote(rem.splitlines()[0] if rem else "") == norm_remote(url):
                        add_repo(st, str(dest))
                        last(st, act, True, f"already here → {dest}")
                        done = True
                        break
                n += 1
                if n > 50:
                    break
                dest = CLONE_DIR / f"{name}-{n}"
            if not done:
                if dest.exists():
                    last(st, act, False, "too many folders with that name")
                else:
                    # Full clone on purpose: --filter=blob:none would make later log -p/blame/checkout
                    # need the network, which breaks offline use on the phone.
                    ok, out = clone_run(url, dest)
                    if ok and has_git(str(dest)):
                        add_repo(st, str(dest))
                        last(st, act, True, f"cloned {dest.name} → {dest}")
                    else:
                        # dest did not exist before this clone, so whatever is there is our half-clone
                        if dest.exists() and os.path.realpath(dest).startswith(str(CLONE_DIR)):
                            shutil.rmtree(dest, ignore_errors=True)
                        last(st, act, False, git_err(out))
    elif act == "ident":
        name = arg.strip()[:80]
        email = (argv[3] if len(argv) > 3 else "").strip()[:120]
        if not name or not re.match(r"^[^@\s]+@[^@\s]+$", email):
            last(st, act, False, "need a name and a valid email")
        else:
            subprocess.run(["git", "config", "--global", "user.name", name], timeout=3)
            subprocess.run(["git", "config", "--global", "user.email", email], timeout=3)
            subprocess.run(["git", "config", "--global", "init.defaultBranch", "main"], timeout=3)
            last(st, act, True, "identity saved")
    else:
        # everything below acts on the current repo
        if not is_repo(cur):
            last(st, act, False, "open a repo first")
            save(st)
            return status(st)
        if act in ("stage", "unstage", "discard"):
            f = safe_file(cur, arg)
            if not f:
                ok, out = False, "bad file path"
            elif act == "stage":
                ok, out = git(cur, "add", "--", f)
            elif act == "unstage":
                ok, out = git(cur, "restore", "--staged", "--", f)
                if not ok:  # no commits yet
                    ok, out = git(cur, "rm", "--cached", "-q", "--", f)
            else:
                tracked = git(cur, "ls-files", "--error-unmatch", "--", f, timeout=3)[0]
                ok, out = git(cur, "restore", "--staged", "--worktree", "--", f) if tracked else (False, "untracked file: delete it yourself to be safe")
            last(st, act, ok, out or {"stage": "staged", "unstage": "unstaged", "discard": "discarded changes in"}[act] + f" {f}")
        elif act == "stage-all":
            ok, out = git(cur, "add", "-A")
            last(st, act, ok, out or "staged everything")
        elif act == "unstage-all":
            ok, out = git(cur, "reset", "-q")
            last(st, act, ok, out or "unstaged everything")
        elif act == "commit":
            msg = arg.strip()[:2000]
            if not msg:
                ok, out = False, "write a commit message"
            elif not ident()["name"]:
                ok, out = False, "set your name & email in SETUP first"
            else:
                ok, out = git(cur, "commit", "-m", msg)
            last(st, act, ok, out)
        elif act in ("pull", "fetch"):
            ok, out = git(cur, *(["pull", "--ff-only"] if act == "pull" else ["fetch", "--all", "--prune"]), timeout=NET_TIMEOUT)
            last(st, act, ok, out or f"{act} done")
        elif act == "push":
            info = repo_info(cur)
            if not info["remotes"]:
                ok, out = False, "no remote — add one in BRANCHES › REMOTE"
            elif info["upstream"]:
                ok, out = git(cur, "push", timeout=NET_TIMEOUT)
            else:
                ok, out = git(cur, "push", "-u", info["remotes"][0]["name"], info["branch"], timeout=NET_TIMEOUT)
            last(st, act, ok, out or "pushed")
        elif act == "checkout":
            b = valid_branch(cur, arg)
            ok, out = git(cur, "switch", b) if b else (False, "bad branch name")
            last(st, act, ok, out)
        elif act == "branch":
            b = valid_branch(cur, arg)
            ok, out = git(cur, "switch", "-c", b) if b else (False, "bad branch name")
            last(st, act, ok, out)
        elif act == "stash":
            ok, out = git(cur, "stash", "push", "-u", "-m", f"sith-git {time.strftime('%Y-%m-%d %H:%M')}")
            last(st, act, ok, out)
        elif act == "stash-pop":
            ok, out = git(cur, "stash", "pop")
            last(st, act, ok, out)
        elif act == "remote":
            url = arg.strip()
            if not re.match(r"^(https://|git@|ssh://)[\w.@:/~+-]+$", url):
                ok, out = False, "use an https:// or git@ URL"
            else:
                has = git(cur, "remote", "get-url", "origin", timeout=3)[0]
                ok, out = git(cur, "remote", "set-url" if has else "add", "origin", url)
            last(st, act, ok, out or "origin set")
        else:
            return {"ok": False, "error": f"unknown action {act!r}"}
    save(st)
    return status(st)


if __name__ == "__main__":
    try:
        out = main(sys.argv)
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, separators=(",", ":")))
