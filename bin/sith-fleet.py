#!/usr/bin/env python3
"""Sith Fleet 369 + Lord Vader Chat Bubble backend for Omalauncher.

Commands:
  sith-fleet.py status
  sith-fleet.py save <category> <prompt...>
  sith-fleet.py inject <category> <prompt...>
  sith-fleet.py launch <category> [prompt...]
  sith-fleet.py resume
  sith-fleet.py board
  sith-fleet.py wake <agent>
  sith-fleet.py chat-status
  sith-fleet.py chat-send <category> <scope_path> <message...>
  sith-fleet.py chat-clear
"""
import json
import os
import sys
import time

HOME = os.path.expanduser("~")
FLEET_DIR = os.path.join(HOME, ".config", "omalauncher", "fleet")
FLEET_FILE = os.path.join(FLEET_DIR, "fleet-369.json")
CHAT_FILE = os.path.join(FLEET_DIR, "vader-chat.json")
META_CACHE_FILE = "/tmp/sith-fleet-meta.json"
META_CACHE_TTL = 60.0
VALID_CATS = ("all", "aethera", "leads", "builders", "debuggers", "tester")


def load_fleet():
    if not os.path.isfile(FLEET_FILE):
        return {"ok": False, "msg": f"Missing {FLEET_FILE}"}
    with open(FLEET_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_fleet(data):
    os.makedirs(FLEET_DIR, exist_ok=True)
    tmp = FLEET_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, FLEET_FILE)


def load_chat():
    if not os.path.isfile(CHAT_FILE):
        return {
            "busy": False,
            "messages": [
                {
                    "id": "init-vader",
                    "role": "vader",
                    "tag": "LORD VADER · FLEET 369",
                    "ts": time.strftime("%H:%M"),
                    "text": "The 31-agent Sith Fleet (15 Claude, 16 Google incl. Aethera) is bound to $HOME and /sdcard. Select a target and speak your directive.",
                }
            ],
        }
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"busy": False, "messages": []}


def save_chat(chat):
    os.makedirs(FLEET_DIR, exist_ok=True)
    tmp = CHAT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(chat, f, indent=2)
    os.replace(tmp, CHAT_FILE)


def find_bin(name, fallback=None):
    import shutil
    found = shutil.which(name)
    if found:
        return found
    if fallback and os.path.isfile(str(fallback)):
        return str(fallback)
    return name


def find_agy():
    return find_bin("agy", os.path.join(HOME, ".local/share/mise/installs/antigravity-cli/latest/agy"))


def extra_dir_args():
    args = []
    for p in ("/sdcard", "/mnt/external"):
        try:
            if os.path.isdir(p):
                with os.scandir(p) as it:
                    if any(True for _ in it):
                        args.extend(["--add-dir", p])
        except OSError:
            pass
    return args


def sdcard_dirs():
    items = [
        {"label": "/sdcard", "path": "/sdcard"},
        {"label": "$HOME", "path": HOME},
    ]
    preferred = ("OBSIDIAN", "Download", "Documents", "INFERNAL", "CodeOnTheGoProjects", "Music", "DCIM", "Pictures")
    seen = set()
    if os.path.isdir("/sdcard"):
        for name in preferred:
            p = "/sdcard/" + name
            if os.path.isdir(p):
                items.append({"label": name, "path": p})
                seen.add(name)
        try:
            with os.scandir("/sdcard") as it:
                extra = sorted(
                    (e.name for e in it if not e.name.startswith(".") and e.name not in seen and e.name != "Android" and e.is_dir())
                )
            for name in extra:
                if len(items) >= 12:
                    break
                items.append({"label": name, "path": "/sdcard/" + name})
                seen.add(name)
        except OSError:
            pass
    return items


def dormant_agents():
    import shutil
    specs = (
        ("claude",  "CLAUDE CODE", "Anthropic CLI",   os.path.join(HOME, ".local/share/mise/installs/claude/latest/claude")),
        ("agy",     "AGY FRESH",   "New Antigravity", os.path.join(HOME, ".local/share/mise/installs/antigravity-cli/latest/agy")),
        ("godmode", "G0DM0D3",     "Local Web Chat",  os.path.join(HOME, ".local/bin/godmode.py")),
    )
    out = []
    for key, label, sub, fb in specs:
        ready = os.path.isfile(fb) or bool(shutil.which(key))
        out.append({"id": key, "label": label, "sub": sub, "ready": ready})
    return out


def get_cached_meta():
    now = time.time()
    try:
        if os.path.isfile(META_CACHE_FILE):
            with open(META_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if now - cached.get("ts", 0) < META_CACHE_TTL and "dormant" in cached and "sdcard" in cached:
                return cached["dormant"], cached["sdcard"], bool(cached.get("storage_bound", True))
    except Exception:
        pass
    dorm = dormant_agents()
    sdc = sdcard_dirs()
    bound = os.path.isdir("/sdcard")
    try:
        tmp = f"{META_CACHE_FILE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": now, "dormant": dorm, "sdcard": sdc, "storage_bound": bound}, f)
        os.replace(tmp, META_CACHE_FILE)
    except Exception:
        pass
    return dorm, sdc, bound


def build_status(data, msg="", op=""):
    cats = data.get("categories", {})
    prompts = {k: cats.get(k, {}).get("prompt", "") for k in cats}
    cats_out = {}
    for k, v in cats.items():
        agents = v.get("agents", [])
        cats_out[k] = {
            "label": v.get("label", k.upper()),
            "short": v.get("short", k.upper()),
            "count": v.get("count", len(agents)),
            "subtitle": v.get("subtitle", ""),
            "prompt": v.get("prompt", ""),
            "preview": agents[:10],
            "more": max(0, len(agents) - 10),
        }
    dorm, sdc, bound = get_cached_meta()
    return {
        "ok": True,
        "op": op,
        "msg": msg,
        "name": data.get("name", "Sith Dual-Fleet 369"),
        "directive": data.get("directive", "MAGIC DIRECTIVE 369 BINARY"),
        "conversation_id": data.get("conversation_id", ""),
        "swarm_on": bool(data.get("swarm_on", True)),
        "counts": data.get("counts", {}),
        "prompts": prompts,
        "categories": cats_out,
        "dormant": dorm,
        "sdcard": sdc,
        "storage_bound": bound,
        "history": data.get("history", [])[:4],
        "ts": int(time.time() * 1000),
    }


def format_directive(data, cat, prompt_text, scope_path="/sdcard"):
    if not data.get("swarm_on", True):
        return f"[SOLO | {scope_path}]\n{prompt_text}\n(Swarm OFF: handle solo directly, do not wake any swarm agents, be concise.)"
    if cat == "aethera":
        targets = "@aethera"
    elif cat == "all":
        targets = "@all"
    else:
        agents = data["categories"][cat].get("agents", [])
        targets = " ".join(f"@{a['name']}" for a in agents)
    return f"[{cat.upper()} | {scope_path}] {targets}\n{prompt_text}\n(Be concise, batch tool calls, wake only targeted agents.)"


def kill_vader_terminals(conv_id):
    """Shut down any running Vader fleet terminals / background chat workers to free RAM."""
    import subprocess
    patterns = [
        "sith-fleet.py _chat-worker",
        "sith-fleet.py resume",
        "sith-fleet.py launch",
        "sith-fleet.py board",
        f"--conversation {conv_id}",
        "omarchy.antigravity",
    ]
    for pat in patterns:
        subprocess.run(["pkill", "-TERM", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chat = load_chat()
    chat["busy"] = False
    save_chat(chat)


def cmd_swarm_toggle():
    data = load_fleet()
    if data.get("ok") is False:
        return data
    new_val = not bool(data.get("swarm_on", True))
    data["swarm_on"] = new_val
    data["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_fleet(data)
    if not new_val:
        kill_vader_terminals(data.get("conversation_id", ""))
        return cmd_chat_status(msg="Swarm OFF · Vader terminals shut down", op="swarm-toggle")
    return cmd_chat_status(msg="Swarm ON · 31 agents ready", op="swarm-toggle")


def effort_args(cat):
    return ["--effort", "high" if cat in ("leads", "aethera") else "medium"]


def cmd_status():
    data = load_fleet()
    if data.get("ok") is False:
        return data
    return build_status(data)


def cmd_chat_status(msg="", op=""):
    data = load_fleet()
    chat = load_chat()
    base = build_status(data, msg=msg, op=op) if data.get("ok") is not False else {"ok": True}
    base["busy"] = bool(chat.get("busy", False))
    base["messages"] = chat.get("messages", [])[-10:]
    return base


def cmd_save(cat, prompt_text):
    if cat not in ("all", "leads", "builders", "debuggers", "tester"):
        return {"ok": False, "msg": f"Unknown category: {cat}"}
    data = load_fleet()
    if data.get("ok") is False:
        return data
    data["categories"][cat]["prompt"] = prompt_text.strip()
    data["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_fleet(data)
    return build_status(data, msg=f"Saved {cat.upper()} prompt", op="save")


def cmd_inject(cat, prompt_text):
    if cat not in ("all", "leads", "builders", "debuggers", "tester"):
        return {"ok": False, "msg": f"Unknown category: {cat}"}
    data = load_fleet()
    if data.get("ok") is False:
        return data
    clean = prompt_text.strip()
    if clean:
        data["categories"][cat]["prompt"] = clean
    else:
        clean = data["categories"][cat].get("prompt", "")
    entry = {
        "ts": time.strftime("%H:%M:%S"),
        "cat": cat.upper(),
        "count": data["categories"][cat]["count"],
        "prompt": clean[:120],
    }
    hist = data.get("history", [])
    hist.insert(0, entry)
    data["history"] = hist[:20]
    data["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_fleet(data)
    return build_status(data, msg=f"Injected -> {cat.upper()} ({entry['count']})", op="inject")


def cmd_chat_send(cat, scope_path, message):
    import subprocess
    clean = message.strip()
    if not clean:
        return cmd_chat_status(msg="Enter a directive first", op="chat")
    chat = load_chat()
    msgs = chat.get("messages", [])
    scope_name = os.path.basename(scope_path.rstrip("/")) or scope_path
    msgs.append({
        "id": f"u-{int(time.time() * 1000)}",
        "role": "user",
        "tag": f"YOU -> {cat.upper()} · {scope_name}",
        "ts": time.strftime("%H:%M"),
        "text": clean[:600],
    })
    chat["messages"] = msgs[-20:]
    chat["busy"] = True
    save_chat(chat)

    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "_chat-worker", cat, scope_path, clean],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return cmd_chat_status(msg=f"Dispatched to {cat.upper()}", op="chat-send")


def cmd_chat_worker(cat, scope_path, message):
    import subprocess
    data = load_fleet()
    conv_id = data.get("conversation_id", "")
    directive = format_directive(data, cat, message, scope_path=scope_path)
    agy = find_agy()
    cmd = [agy, "--conversation", conv_id] + extra_dir_args() + effort_args(cat) + ["-p", directive, "--print-timeout", "180s"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=190)
        out = (proc.stdout or "").strip()
        if not out:
            out = (proc.stderr or "").strip() or "(Fleet completed with no text output)"
        reply = out[:1400]
    except subprocess.TimeoutExpired:
        reply = "Fleet turn timed out in bubble mode. Tap TERMINAL to view the live session."
    except Exception as e:
        reply = f"Dispatch error: {e}"

    chat = load_chat()
    msgs = chat.get("messages", [])
    msgs.append({
        "id": f"v-{int(time.time() * 1000)}",
        "role": "vader",
        "tag": f"LORD VADER · {cat.upper()}",
        "ts": time.strftime("%H:%M"),
        "text": reply,
    })
    chat["messages"] = msgs[-20:]
    chat["busy"] = False
    save_chat(chat)


def cmd_chat_clear():
    save_chat({
        "busy": False,
        "messages": [
            {
                "id": f"clr-{int(time.time() * 1000)}",
                "role": "vader",
                "tag": "LORD VADER · FLEET 369",
                "ts": time.strftime("%H:%M"),
                "text": "Channel cleared. 31 agents standing by with $HOME and /sdcard access.",
            }
        ],
    })
    return cmd_chat_status(msg="Chat cleared", op="chat-clear")


def exec_agy(args):
    agy = find_agy()
    os.execvp(agy, [agy] + extra_dir_args() + args)


def cmd_launch(cat, override_prompt=None, scope_path="/sdcard"):
    if cat not in VALID_CATS:
        print(f"Unknown category: {cat}", file=sys.stderr)
        sys.exit(1)
    data = load_fleet()
    if data.get("ok") is False:
        print(data.get("msg"), file=sys.stderr)
        sys.exit(1)
    prompt_text = (override_prompt or "").strip()
    if not prompt_text and cat in data.get("categories", {}):
        prompt_text = data["categories"][cat].get("prompt", "").strip()
    if prompt_text and cat in data.get("categories", {}):
        cmd_inject(cat, prompt_text)
    conv_id = data["conversation_id"]
    if prompt_text:
        directive = format_directive(data, cat, prompt_text, scope_path=scope_path)
        exec_agy(effort_args(cat) + ["--conversation", conv_id, "-i", directive])
    else:
        exec_agy(["--conversation", conv_id])


def cmd_resume():
    data = load_fleet()
    conv_id = data.get("conversation_id", "")
    if not conv_id:
        print("No saved conversation_id in fleet-369.json", file=sys.stderr)
        sys.exit(1)
    exec_agy(["--conversation", conv_id])


def cmd_board():
    data = load_fleet()
    conv_id = data.get("conversation_id", "")
    if not conv_id:
        print("No saved conversation_id in fleet-369.json", file=sys.stderr)
        sys.exit(1)
    exec_agy(["--conversation", conv_id, "-i", "/swarm board"])


def cmd_wake(agent_id):
    mapping = {
        "claude":  find_bin("claude",  os.path.join(HOME, ".local/share/mise/installs/claude/latest/claude")),
        "copilot": find_bin("copilot", os.path.join(HOME, ".local/share/mise/installs/copilot/latest/copilot")),
        "qwen":    find_bin("qwen",    os.path.join(HOME, ".local/share/mise/installs/qwen/latest/bin/qwen")),
        "agy":     find_agy(),
    }
    if agent_id == "agy":
        os.execvp(mapping["agy"], [mapping["agy"]] + extra_dir_args())
    elif agent_id in mapping:
        b = mapping[agent_id]
        os.execvp(b, [b])
    elif agent_id == "godmode":
        os.execvp(sys.executable, [sys.executable, os.path.join(HOME, ".local/bin/godmode.py"), "start"])
    else:
        print(f"Unknown dormant agent: {agent_id}", file=sys.stderr)
        sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "status":
        print(json.dumps(cmd_status()))
    elif args[0] == "chat-status":
        print(json.dumps(cmd_chat_status()))
    elif args[0] == "swarm-toggle":
        print(json.dumps(cmd_swarm_toggle()))
    elif args[0] == "chat-clear":
        print(json.dumps(cmd_chat_clear()))
    elif args[0] == "chat-send" and len(args) >= 4:
        print(json.dumps(cmd_chat_send(args[1].lower(), args[2], " ".join(args[3:]))))
    elif args[0] == "_chat-worker" and len(args) >= 4:
        cmd_chat_worker(args[1].lower(), args[2], " ".join(args[3:]))
    elif args[0] == "save" and len(args) >= 2:
        print(json.dumps(cmd_save(args[1].lower(), " ".join(args[2:]))))
    elif args[0] == "inject" and len(args) >= 2:
        print(json.dumps(cmd_inject(args[1].lower(), " ".join(args[2:]))))
    elif args[0] == "launch" and len(args) >= 2:
        cat = args[1].lower()
        text = " ".join(args[2:]) if len(args) > 2 else None
        cmd_launch(cat, text)
    elif args[0] == "wake" and len(args) >= 2:
        cmd_wake(args[1].lower())
    elif args[0] == "resume":
        cmd_resume()
    elif args[0] == "board":
        cmd_board()
    else:
        print(json.dumps({"ok": False, "msg": f"Invalid args: {args}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
