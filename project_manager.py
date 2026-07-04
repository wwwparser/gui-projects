"""
Project Manager GUI
-------------------
Окно со списком всех проектов в одной корневой папке. Позволяет запускать
Claude Code / Codex CLI в один клик в Windows Terminal, искать по проектам,
закреплять важные, вести задачи и авто-описания через DeepSeek.

Конфигурация через переменные окружения (см. .env.example):
  PROJECTS_ROOT           — корневая папка со всеми проектами (обязательно)
  CLAUDE_SCRIPT           — .cmd/.ps1 запуска Claude Code (опционально)
  CLAUDE_FRESH_SCRIPT     — .cmd/.ps1 запуска Claude Code в fresh-режиме
  CODEX_SCRIPT            — .bat/.ps1 запуска Codex CLI (опционально)
  DEEPSEEK_API_KEY        — ключ DeepSeek для авто-описаний (опционально)

Запуск:  python project_manager.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Iterable

try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Minimal .env loader — reads KEY=VALUE lines from a .env next to the script/exe.
    Values already set in the process environment win over .env."""
    base = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    env_path = base / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


_load_dotenv()

# Where all projects live. Required — falls back to ~/Projects if unset so the app
# still launches, but the user should point PROJECTS_ROOT at the real folder.
PROJECTS_ROOT = Path(os.environ.get("PROJECTS_ROOT") or (Path.home() / "Projects"))

# Optional wrapper scripts that launch Claude Code / Codex CLI in the tab's cwd.
# If unset or missing, the app still opens the tab but doesn't auto-start the agent.
CLAUDE_SCRIPT = Path(os.environ.get("CLAUDE_SCRIPT", ""))
CLAUDE_FRESH_SCRIPT = Path(os.environ.get("CLAUDE_FRESH_SCRIPT", ""))
CODEX_SCRIPT = Path(os.environ.get("CODEX_SCRIPT", ""))

# Persistent data directory: %APPDATA%\ProjectManager so settings survive any rebuild
# of the PyInstaller --onedir bundle (which wipes dist/ProjectManager/ each time).
_appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
APP_DIR = Path(_appdata) / "ProjectManager"
try:
    APP_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    # Fall back to the exe / script dir if AppData isn't writable for some reason.
    APP_DIR = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parent)

# Backups land outside %APPDATA% so a wipe of one location doesn't take the other.
_userprofile = os.environ.get("USERPROFILE") or str(Path.home())
BACKUP_DIR = Path(_userprofile) / "Documents" / "ProjectManager-Backups"

CACHE_FILE = APP_DIR / "projects_cache.json"
SETTINGS_FILE = APP_DIR / "settings.json"
TASKS_FILE = APP_DIR / "tasks.json"       # per-project notes & task tracker
TERMINAL_PRESETS_FILE = APP_DIR / "terminal_presets.json"   # named multi-tab sessions

_DATA_FILES = ("projects_cache.json", "settings.json", "tasks.json", "terminal_presets.json")


def _legacy_data_dirs() -> list[Path]:
    """Locations the app used in earlier versions — checked once at startup to migrate."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        dirs.append(exe_dir)
        dirs.append(exe_dir.parent)  # old `dist/` location
    dirs.append(Path(__file__).resolve().parent)
    return dirs


def _migrate_legacy_data() -> None:
    """Copy any pre-existing JSONs from old locations into the new APP_DIR.
    Runs once per file: only fills in what's missing in APP_DIR."""
    import shutil as _sh
    for legacy in _legacy_data_dirs():
        if legacy == APP_DIR or not legacy.exists():
            continue
        for fname in _DATA_FILES:
            src = legacy / fname
            dst = APP_DIR / fname
            if src.is_file() and not dst.exists():
                try:
                    _sh.copy2(src, dst)
                except Exception:
                    pass


def _daily_backup(keep: int = 14) -> None:
    """Snapshot all JSONs into BACKUP_DIR/<YYYY-MM-DD>/ once per day; keep last `keep`."""
    import shutil as _sh
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        today_dir = BACKUP_DIR / today
        if not today_dir.exists():
            today_dir.mkdir(parents=True, exist_ok=True)
            for fname in _DATA_FILES:
                src = APP_DIR / fname
                if src.is_file():
                    try:
                        _sh.copy2(src, today_dir / fname)
                    except Exception:
                        pass
        # Prune: keep newest `keep` dated subdirs.
        snaps = sorted(
            [d for d in BACKUP_DIR.iterdir() if d.is_dir() and len(d.name) == 10],
            reverse=True,
        )
        for old in snaps[keep:]:
            try:
                _sh.rmtree(old, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


_migrate_legacy_data()
_daily_backup()

# Windows Terminal tab colors — stable palette for "color by name" hashing.
_TAB_PALETTE = [
    "#E53935", "#1E88E5", "#43A047", "#FB8C00",
    "#8E24AA", "#00897B", "#F4511E", "#3949AB",
    "#7CB342", "#D81B60", "#039BE5", "#6D4C41",
]


def color_for_name(name: str) -> str:
    h = 0
    for ch in name or "":
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _TAB_PALETTE[h % len(_TAB_PALETTE)]


def load_terminal_presets() -> list[dict[str, Any]]:
    if TERMINAL_PRESETS_FILE.is_file():
        try:
            data = json.loads(TERMINAL_PRESETS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_terminal_presets(presets: list[dict[str, Any]]) -> None:
    try:
        TERMINAL_PRESETS_FILE.write_text(
            json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def find_wt_exe() -> str | None:
    """Locate wt.exe. App Execution Alias on PATH is normal, but PATH from
    a subprocess context may miss it — also check WindowsApps and the installed
    AppX package directory directly."""
    import shutil as _sh
    found = _sh.which("wt") or _sh.which("wt.exe")
    if found:
        return found
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        alias = Path(local_app) / "Microsoft" / "WindowsApps" / "wt.exe"
        if alias.exists():
            return str(alias)
    appx_root = Path(r"C:\Program Files\WindowsApps")
    if appx_root.exists():
        try:
            for sub in appx_root.glob("Microsoft.WindowsTerminal_*"):
                cand = sub / "wt.exe"
                if cand.exists():
                    return str(cand)
        except (PermissionError, OSError):
            pass
    return None


def discover_wt_project_tabs() -> list[dict[str, Any]]:
    """Find all powershell.exe processes that look like WT tabs we launched
    (via the configured Claude script) and extract the project path argument
    from each. Returns [{pid, path, name}, ...] sorted by PID (= creation order
    ≈ initial tab order)."""
    # Derive the CommandLine filter from the configured launcher's filename stem.
    # e.g. CLAUDE_SCRIPT=...\Claude-BypassProxy.cmd → filter "*Claude-*BypassProxy*"
    stem = (CLAUDE_SCRIPT.stem or "Claude-BypassProxy")
    # Turn "Claude-BypassProxy" into a loose glob so the "Fresh" variant matches too.
    parts = stem.split("-", 1)
    filter_glob = ("*" + parts[0] + "-*" + parts[1] + "*") if len(parts) == 2 else f"*{stem}*"
    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" "
        f"| Where-Object {{ $_.CommandLine -like '{filter_glob}' }} "
        "| Sort-Object ProcessId "
        "| Select-Object ProcessId, CommandLine "
        "| ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []
    out = (result.stdout or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]

    tabs: list[dict[str, Any]] = []
    # Extract the project path argument: it's the last quoted/unquoted token
    # after the `.ps1` script. PowerShell wraps paths with spaces in quotes.
    rx = re.compile(r"\.ps1[\"']?\s+[\"']?(?P<path>[A-Za-z]:\\[^\"']+?)[\"']?\s*$")
    for proc in data:
        cmdline = (proc.get("CommandLine") or "").strip()
        if not cmdline:
            continue
        m = rx.search(cmdline)
        if not m:
            continue
        path = m.group("path").strip().rstrip("\\")
        if not path:
            continue
        tabs.append({
            "pid": proc.get("ProcessId"),
            "path": path,
            "name": Path(path).name,
        })
    return tabs


def resolve_tab_titles(picks: list[dict[str, Any]]) -> list[str]:
    """Same title-derivation logic as build_wt_command. Returns one title per pick."""
    out: list[str] = []
    for p in picks:
        name = p.get("name") or Path(p["path"]).name
        custom = (p.get("tab_title") or "").strip()
        out.append((custom or name or "")[:48])
    return out


def build_restore_titles_command(titles: list[str]) -> list[str]:
    """Build a `wt` invocation that walks tabs 0..N-1 in the last-used window and
    renames each to the corresponding title. Used to recover from apps (like Claude
    /resume) that rewrite the tab title via the console API, bypassing
    --suppressApplicationTitle."""
    wt_path = find_wt_exe() or "wt"
    argv: list[str] = [wt_path, "-w", "last"]
    for i, t in enumerate(titles):
        if i > 0:
            argv.append(";")
        argv += ["focus-tab", "--target", str(i), ";", "rename-tab", "--title", t]
    return argv


def build_wt_command(picks: list[dict[str, Any]], fresh: bool, auto_claude: bool) -> list[str]:
    """Build argv for `wt` opening one tab per project. Pass to subprocess.Popen without shell.

    Each pick must have 'path' and 'name'. Optional 'tab_color' overrides the hashed color.
    """
    base = CLAUDE_FRESH_SCRIPT if fresh else CLAUDE_SCRIPT
    # Accept either a .ps1 or .cmd path in the env var — try the sibling with
    # the other extension too, so the user only has to set one.
    script_ps1 = base.with_suffix(".ps1") if base.name else Path()
    script_cmd = base.with_suffix(".cmd") if base.name else Path()
    use_ps1 = script_ps1.is_file()
    use_cmd = (not use_ps1) and script_cmd.is_file()

    wt_path = find_wt_exe() or "wt"
    # `-w last` targets the most-recently-used WT window, so subsequent
    # "add tab" actions land in the same window the user is working in.
    # If no WT window exists yet, a new one is created.
    argv: list[str] = [wt_path, "-w", "last"]
    for i, p in enumerate(picks):
        path = p["path"]
        name = p.get("name") or Path(path).name
        color = p.get("tab_color") or color_for_name(name)
        custom = (p.get("tab_title") or "").strip()
        title = (custom or name or "")[:48]

        if i > 0:
            argv.append(";")     # Windows Terminal command separator
        argv += ["new-tab", "--suppressApplicationTitle",
                 "-d", path, "--title", title, "--tabColor", color]

        if auto_claude and use_ps1:
            argv += ["powershell", "-NoExit", "-ExecutionPolicy", "Bypass",
                     "-File", str(script_ps1), path]
        elif auto_claude and use_cmd:
            argv += ["cmd", "/k", str(script_cmd), path]
        else:
            argv += ["powershell", "-NoExit"]
    return argv



NO_PROJECT_KEY = "__no_project__"          # synthetic project for free-standing tasks
# NO_PROJECT_LABEL is resolved at runtime via tr("no_project") — see locales/*.json

TASK_TYPES = ["💡 идея", "✅ задача", "🐞 баг", "🔬 исследование", "🛠 доработка", "📚 заметка", "📦 другое"]
TASK_TYPE_DEFAULT = "✅ задача"

CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
CODEX_SESSIONS_DIR = CODEX_HOME / "sessions"
CODEX_INDEX_FILE = CODEX_HOME / "session_index.jsonl"

DEFAULT_FONT_SIZE = 12
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 28

# DeepSeek (OpenAI-compatible). Set DEEPSEEK_API_KEY in .env or the environment
# to enable auto-descriptions; the app still works without it (no AI features).
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_CONCURRENCY = 4
DEEPSEEK_TIMEOUT = 120
DEEPSEEK_MAX_CONTEXT_CHARS = 24000   # how much per project we send

# Files/dirs we read to derive an automatic project description.
README_CANDIDATES = [
    "README.md", "README.MD", "README.rst", "README.txt", "README",
    "readme.md", "readme.txt", "Readme.md",
    "ОПИСАНИЕ.md", "описание.md", "DESCRIPTION.md",
]
META_FILES = ["pyproject.toml", "package.json", "requirements.txt", "setup.py", "Cargo.toml", "go.mod", "composer.json"]
SKIP_DIRS = {".git", ".idea", ".vscode", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", ".next"}

LANG_HINTS = {
    ".py": "Python", ".js": "JS", ".ts": "TS", ".tsx": "TSX", ".jsx": "JSX",
    ".php": "PHP", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".cs": "C#", ".cpp": "C++", ".c": "C", ".rb": "Ruby", ".sh": "Shell",
    ".ps1": "PowerShell", ".html": "HTML", ".vue": "Vue", ".dart": "Dart",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRANSLIT_MAP = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y","к":"k",
    "л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts",
    "ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
}


def slugify(text: str, max_len: int = 60) -> str:
    s = (text or "").strip().lower()
    out = []
    for ch in s:
        if ch in _TRANSLIT_MAP:
            out.append(_TRANSLIT_MAP[ch])
        elif ch.isalnum() and ord(ch) < 128:
            out.append(ch)
        elif ch in (" ", "-", "_", "/", "\\", ".", ",", ":", ";", "!", "?"):
            out.append("-")
        # everything else dropped
    s = "".join(out)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "idea"
    return s[:max_len].strip("-")


def unique_folder(base: Path, name: str) -> Path:
    target = base / name
    if not target.exists():
        return target
    i = 2
    while (base / f"{name}-{i}").exists():
        i += 1
    return base / f"{name}-{i}"


def fmt_size(n: int | float | None) -> str:
    if n is None or n < 0:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    s = float(n); i = 0
    while s >= 1024 and i < len(units) - 1:
        s /= 1024; i += 1
    if i == 0:
        return f"{int(s)} {units[i]}"
    return f"{s:.1f} {units[i]}"


def compute_folder_size(folder: Path, max_files: int = 200_000) -> int:
    total = 0
    scanned = 0
    try:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git")]
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
                scanned += 1
                if scanned > max_files:
                    return total
    except Exception:
        pass
    return total


def fmt_dt(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def safe_read_text(path: Path, limit: int = 4000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def extract_source_intro(path: Path, max_chars: int = 600) -> str:
    """Pull a meaningful intro from a source file: module docstring or top block of comments."""
    text = safe_read_text(path, 4000)
    if not text:
        return ""
    ext = path.suffix.lower()
    # Python module docstring
    if ext == ".py":
        m = re.match(r'\s*(?:#![^\n]*\n)?\s*(?:from __future__[^\n]*\n)?\s*([\'"]{3})(.+?)\1', text, re.S)
        if m:
            return re.sub(r"\s+", " ", m.group(2)).strip()[:max_chars]
    # JSDoc / leading block comment for js/ts/go/rs/java/cpp
    if ext in {".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".cs", ".php", ".kt"}:
        m = re.match(r"\s*/\*+(.+?)\*/", text, re.S)
        if m:
            body = re.sub(r"^\s*\*+\s?", "", m.group(1), flags=re.M)
            return re.sub(r"\s+", " ", body).strip()[:max_chars]
    # Leading hash/slash comments (any language)
    lines: list[str] = []
    for raw in text.splitlines()[:30]:
        s = raw.strip()
        if not s:
            if lines:
                break
            continue
        if s.startswith("#!"):
            continue
        if s.startswith("#"):
            lines.append(s.lstrip("# ").strip())
        elif s.startswith("//"):
            lines.append(s.lstrip("/ ").strip())
        else:
            if lines:
                break
            # we don't accept code as description
            break
    if lines:
        return re.sub(r"\s+", " ", " ".join(lines))[:max_chars]
    return ""


def first_meaningful_lines(text: str, max_lines: int = 6, max_chars: int = 320) -> str:
    """Pick the first non-empty, non-badge lines from a README."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # skip markdown headers shorter than 3 chars, image/badge lines
        if line.startswith("![") or "shields.io" in line or line.startswith("---"):
            continue
        if line.startswith("#"):
            line = line.lstrip("# ").strip()
            if not line:
                continue
        out.append(line)
        if len(out) >= max_lines:
            break
    joined = " ".join(out)
    joined = re.sub(r"\s+", " ", joined)
    if len(joined) > max_chars:
        joined = joined[:max_chars - 1] + "…"
    return joined


# ---------------------------------------------------------------------------
# Recent-edit detection (local, fast)
# ---------------------------------------------------------------------------

_SOURCE_EXTS = set(LANG_HINTS.keys()) | {".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".html", ".css", ".sql"}


def collect_recent_edits(folder: Path, top_n: int = 5, max_files: int = 800) -> tuple[float, list[tuple[str, float]]]:
    """Walk the folder (skipping SKIP_DIRS), return (latest_mtime, top_n recent files)."""
    latest = 0.0
    rows: list[tuple[str, float]] = []
    scanned = 0
    try:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                scanned += 1
                if scanned > max_files:
                    break
                ext = os.path.splitext(fname)[1].lower()
                if ext and ext not in _SOURCE_EXTS:
                    continue
                p = os.path.join(root, fname)
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                if mt > latest:
                    latest = mt
                rel = os.path.relpath(p, folder)
                rows.append((rel.replace("\\", "/"), mt))
            if scanned > max_files:
                break
    except Exception:
        pass
    rows.sort(key=lambda r: r[1], reverse=True)
    return latest, rows[:top_n]


def gather_project_context(folder: Path, max_chars: int = DEEPSEEK_MAX_CONTEXT_CHARS) -> str:
    """Build a compact text context to send to an LLM: tree + key file contents."""
    chunks: list[str] = [f"# Project: {folder.name}", f"# Path: {folder}"]

    # 1. README first
    readme_text = ""
    readme_name = ""
    for cand in README_CANDIDATES:
        p = folder / cand
        if p.is_file():
            readme_text = safe_read_text(p, 8000)
            readme_name = cand
            break
    if readme_text:
        chunks.append(f"\n## {readme_name}\n{readme_text.strip()}")

    # 2. Metadata files
    for meta in META_FILES:
        p = folder / meta
        if p.is_file():
            chunks.append(f"\n## {meta}\n{safe_read_text(p, 4000).strip()}")

    # 3. Recent + entry files (cap)
    _, recent_files = collect_recent_edits(folder, top_n=20)

    # Tree (top + 2 levels) — file list
    tree_lines: list[str] = []
    try:
        for root, dirs, files in os.walk(folder):
            depth = len(Path(root).relative_to(folder).parts)
            if depth > 2:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            rel_root = os.path.relpath(root, folder)
            if rel_root != ".":
                tree_lines.append(f"  {rel_root}/")
            for f in files[:60]:
                tree_lines.append(f"    {os.path.join(rel_root, f).replace(chr(92), '/')}".replace("./", ""))
            if len(tree_lines) > 200:
                tree_lines.append("  …(обрезано)")
                break
    except Exception:
        pass
    if tree_lines:
        chunks.append("\n## Структура файлов\n" + "\n".join(tree_lines))

    # 4. Key file contents — entrypoints + most recent source files
    entry_names = {"main.py", "app.py", "bot.py", "run.py", "server.py", "index.js", "index.ts", "main.go", "main.rs"}
    picked: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for name in entry_names:
        p = folder / name
        if p.is_file() and p not in seen:
            picked.append((name, p))
            seen.add(p)
    for rel, _mt in recent_files:
        p = folder / rel
        ext = p.suffix.lower()
        if ext in LANG_HINTS and p not in seen:
            picked.append((rel, p))
            seen.add(p)
        if len(picked) >= 6:
            break

    for rel, p in picked:
        body = safe_read_text(p, 3500)
        if not body.strip():
            continue
        chunks.append(f"\n## {rel}\n```\n{body.strip()}\n```")

    text = "\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(обрезано)"
    return text


# ---------------------------------------------------------------------------
# DeepSeek client
# ---------------------------------------------------------------------------

class DeepSeekError(RuntimeError):
    pass


def deepseek_chat(messages: list[dict[str, str]], temperature: float = 0.2, max_tokens: int = 900) -> str:
    if not DEEPSEEK_API_KEY:
        raise DeepSeekError("DEEPSEEK_API_KEY не задан")
    body = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=DEEPSEEK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            err = ""
        raise DeepSeekError(f"HTTP {e.code}: {err}") from e
    except urllib.error.URLError as e:
        raise DeepSeekError(f"Сеть: {e.reason}") from e
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise DeepSeekError(f"Неожиданный ответ: {str(data)[:400]}")


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)


def parse_llm_json(text: str) -> dict[str, Any]:
    """Tolerantly extract a JSON object from LLM output (handles ```json fences)."""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    try:
        return json.loads(s)
    except Exception:
        m = _JSON_BLOCK_RE.search(s)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}


SYSTEM_PROMPT = (
    "Ты — старший разработчик. Анализируешь содержимое каталога проекта (структуру файлов и "
    "ключевые исходники), кратко и по делу описываешь, что это за проект, как он работает, "
    "что реализовано, и определяешь стадию готовности. Отвечай по-русски."
)

USER_INSTRUCTIONS = """Верни СТРОГО JSON-объект без префиксов и без markdown-обёрток вида ```.
Поля:
{
  "short": "одна строка для таблицы, до 90 символов, конкретно ЧТО это и стек. Пример: 'Python-парсеры Avito, 4 модуля'",
  "full":  "развёрнутое описание (4-8 предложений): что реализовано, как работает, ключевые модули/зависимости",
  "stage": "одно из: 'идея', 'черновик', 'в разработке', 'MVP', 'рабочий проект', 'заброшен/архив'",
  "stage_reason": "1-2 короткие причины, почему такая стадия"
}
Не выдумывай функции, которых нет в коде. Если данных мало — так и напиши в full."""


def deepseek_analyze_project(folder: Path) -> dict[str, Any]:
    """Returns dict with keys short, full, stage, stage_reason. Raises DeepSeekError on failure."""
    ctx = gather_project_context(folder)
    if not ctx.strip():
        raise DeepSeekError("пустой контекст")
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_INSTRUCTIONS + "\n\n=== CONTEXT ===\n" + ctx},
    ]
    raw = deepseek_chat(msgs)
    parsed = parse_llm_json(raw)
    short = (parsed.get("short") or "").strip()
    full = (parsed.get("full") or "").strip()
    stage = (parsed.get("stage") or "").strip()
    reason = (parsed.get("stage_reason") or "").strip()
    if not short and not full:
        # Fall back: use raw as full
        full = raw.strip()[:1200]
        short = full[:90]
    return {"short": short, "full": full, "stage": stage, "stage_reason": reason, "raw": raw}


def analyze_project(folder: Path) -> dict[str, Any]:
    """Heuristic, fast, offline analysis of a project folder."""
    info: dict[str, Any] = {"name": folder.name, "path": str(folder)}

    try:
        stat = folder.stat()
        info["mtime"] = stat.st_mtime
    except Exception:
        info["mtime"] = 0.0

    # find README
    readme_text = ""
    for cand in README_CANDIDATES:
        p = folder / cand
        if p.is_file():
            readme_text = safe_read_text(p)
            info["readme"] = cand
            break

    description = ""
    if readme_text:
        description = first_meaningful_lines(readme_text)

    # If no description: derive from package.json / pyproject.toml description fields
    if not description:
        pkg = folder / "package.json"
        if pkg.is_file():
            try:
                data = json.loads(safe_read_text(pkg, 8000) or "{}")
                desc = data.get("description") or ""
                name = data.get("name") or ""
                if desc or name:
                    description = f"{name}: {desc}".strip(": ")
            except Exception:
                pass

    if not description:
        pyproj = folder / "pyproject.toml"
        if pyproj.is_file():
            text = safe_read_text(pyproj, 8000)
            m = re.search(r'^description\s*=\s*"(.*?)"', text, re.M)
            if m:
                description = m.group(1)

    # Если README/мета-файлы ничего не дали — анализируем исходники проекта.
    if not description:
        entry_names = ["main.py", "app.py", "bot.py", "run.py", "server.py",
                       "index.js", "index.ts", "main.go", "main.rs", "main.php"]
        candidates: list[Path] = []
        for n in entry_names:
            p = folder / n
            if p.is_file():
                candidates.append(p)
        if not candidates:
            # take first few source files at top level
            try:
                src_files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in LANG_HINTS]
                src_files.sort(key=lambda p: (0 if p.suffix.lower() == ".py" else 1, p.name.lower()))
                candidates.extend(src_files[:3])
            except Exception:
                pass
        if not candidates:
            # one level deeper
            try:
                for sub in folder.iterdir():
                    if sub.is_dir() and sub.name not in SKIP_DIRS and not sub.name.startswith("."):
                        for p in sub.iterdir():
                            if p.is_file() and p.suffix.lower() in LANG_HINTS:
                                candidates.append(p)
                                if len(candidates) >= 3:
                                    break
                    if len(candidates) >= 3:
                        break
            except Exception:
                pass

        intros: list[str] = []
        for c in candidates[:3]:
            intro = extract_source_intro(c)
            if intro:
                intros.append(f"{c.name}: {intro}")
        if intros:
            description = " · ".join(intros)[:600]

    # languages by file extensions (look 2 levels deep, cap scanning)
    lang_counter: dict[str, int] = {}
    scanned = 0
    main_files: list[str] = []
    try:
        for entry in folder.iterdir():
            if scanned > 400:
                break
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    continue
                try:
                    for sub in entry.iterdir():
                        scanned += 1
                        if scanned > 400:
                            break
                        if sub.is_file():
                            ext = sub.suffix.lower()
                            if ext in LANG_HINTS:
                                lang_counter[LANG_HINTS[ext]] = lang_counter.get(LANG_HINTS[ext], 0) + 1
                except Exception:
                    pass
            else:
                scanned += 1
                ext = entry.suffix.lower()
                if ext in LANG_HINTS:
                    lang_counter[LANG_HINTS[ext]] = lang_counter.get(LANG_HINTS[ext], 0) + 1
                if entry.name.lower() in {"main.py", "app.py", "index.js", "server.py", "bot.py", "run.py"}:
                    main_files.append(entry.name)
    except Exception:
        pass

    top_langs = sorted(lang_counter.items(), key=lambda kv: -kv[1])[:3]
    info["langs"] = ", ".join(k for k, _ in top_langs)

    # If still nothing — derive a hint from name + langs + main file
    if not description:
        bits = []
        if main_files:
            bits.append(f"Entry: {main_files[0]}")
        if top_langs:
            bits.append("Stack: " + info["langs"])
        # heuristic from name
        n = folder.name.lower()
        keywords = {
            "parser": "парсер", "avito": "Avito", "wb": "Wildberries",
            "youtube": "YouTube", "telegram": "Telegram", "wp": "WordPress",
            "wordpress": "WordPress", "bot": "бот", "ai": "AI", "gpt": "GPT-проект",
            "vps": "сервер/VPS", "vpn": "VPN", "scraper": "скрапер", "crawler": "краулер",
            "whisper": "Whisper STT", "translate": "перевод", "tts": "TTS-синтез",
            "drive2": "drive2.ru", "drom": "drom.ru", "litres": "litres",
            "yamaps": "Яндекс.Карты", "zabbix": "Zabbix", "n8n": "n8n",
        }
        hits = [v for k, v in keywords.items() if k in n]
        if hits:
            bits.insert(0, "Темы: " + ", ".join(sorted(set(hits))))
        description = " · ".join(bits) if bits else "Описание не найдено (нет README)."

    info["description"] = description

    # Recent edits (local, fast)
    latest, recent = collect_recent_edits(folder, top_n=5)
    if latest > info["mtime"]:
        info["mtime"] = latest
    info["last_edit_mtime"] = latest
    info["recent_files"] = [{"path": rel, "mtime": mt} for rel, mt in recent]
    # Defaults for fields the DeepSeek pass will fill in
    info.setdefault("short_desc", "")
    info.setdefault("full_desc", "")
    info.setdefault("stage", "")
    info.setdefault("stage_reason", "")
    info.setdefault("analyzed_by", "heuristic")
    info.setdefault("analyzed_at", 0.0)
    return info


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cache() -> dict[str, dict[str, Any]]:
    if CACHE_FILE.is_file():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("cache save failed:", e)


def load_tasks_db() -> dict[str, dict[str, Any]]:
    """tasks.json structure:
       { "<project_path>": {"notes": "...", "tasks": [ {...} ]} }
    """
    if TASKS_FILE.is_file():
        try:
            return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_tasks_db(db: dict[str, dict[str, Any]]) -> None:
    try:
        TASKS_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("tasks save failed:", e)


def get_project_entry(db: dict[str, dict[str, Any]], path: str) -> dict[str, Any]:
    entry = db.get(path)
    if entry is None:
        entry = {"notes": "", "tasks": []}
        db[path] = entry
    entry.setdefault("notes", "")
    entry.setdefault("tasks", [])
    return entry


def new_task_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def parse_user_dt(s: str) -> float | None:
    """Accept '2026-05-16 14:30' / '2026-05-16' / '14:30' / '2h' / '30m' / '1d' relative forms."""
    s = (s or "").strip()
    if not s:
        return None
    # relative
    m = re.match(r"^\+?(\d+)\s*([mhdMHD])$", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        mult = {"m": 60, "h": 3600, "d": 86400}[unit]
        return time.time() + n * mult
    # time only HH:MM → today at that time (or tomorrow if past)
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        now = datetime.now()
        t = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if t <= now:
            t = t.replace(day=t.day + 1) if t.day < 28 else t
        return t.timestamp()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except Exception:
            continue
    return None


def fmt_user_dt(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def schedule_windows_reminder(task_title: str, project_name: str, when_ts: float, task_id: str) -> tuple[bool, str]:
    """Schedule a system-level Windows notification via schtasks + mshta popup.
       Returns (ok, message). Fires even if our app is closed.
    """
    try:
        when = datetime.fromtimestamp(when_ts)
        if when <= datetime.now():
            return False, "Дата напоминания в прошлом."
        # mshta popup — works on all Windows editions
        msg = task_title.replace('"', "'").replace("\n", " ")
        prj = project_name.replace('"', "'")
        # Build a single-line VBScript to launch a MsgBox
        vbs = (
            f'CreateObject(\\"WScript.Shell\\").Popup '
            f'\\"{msg}\\\\n\\\\nProject: {prj}\\", 0, '
            f'\\"Project Manager — напоминание\\", 64'
        )
        cmd_to_run = f'mshta vbscript:Execute("{vbs}":close)'
        tn = f"PM-Reminder-{task_id}"
        st = when.strftime("%H:%M")
        sd = when.strftime("%d/%m/%Y")
        # /f: overwrite if exists; /sc once: one-time
        cmd = [
            "schtasks", "/Create", "/F",
            "/SC", "ONCE",
            "/TN", tn,
            "/TR", cmd_to_run,
            "/ST", st,
            "/SD", sd,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "schtasks error").strip()[:300]
        return True, f"Создано: {tn} ({sd} {st})"
    except Exception as e:
        return False, str(e)


def cancel_windows_reminder(task_id: str) -> None:
    try:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", f"PM-Reminder-{task_id}"],
                       capture_output=True, text=True, shell=False)
    except Exception:
        pass


def load_settings() -> dict[str, Any]:
    if SETTINGS_FILE.is_file():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_settings(s: dict[str, Any]) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Internationalization (i18n)
# ---------------------------------------------------------------------------

# (code, native display name) — order defines the language selector order.
LANGUAGES = [
    ("ru", "Русский"),
    ("en", "English"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("zh", "中文"),
]
DEFAULT_LANG = "ru"
CURRENT_LANG = DEFAULT_LANG
_TRANSLATIONS: dict[str, dict[str, str]] = {}


def _locales_dir() -> Path:
    """Folder holding <code>.json translation files. Works from source and from
    a PyInstaller bundle (sys._MEIPASS / exe dir)."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "locales")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "locales")
    candidates.append(Path(__file__).resolve().parent / "locales")
    candidates.append(APP_DIR / "locales")
    for c in candidates:
        if c.is_dir():
            return c
    return Path(__file__).resolve().parent / "locales"


def load_translations() -> None:
    """Read every <code>.json from the locales dir into memory."""
    global _TRANSLATIONS
    _TRANSLATIONS = {}
    ldir = _locales_dir()
    for code, _name in LANGUAGES:
        path = ldir / f"{code}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _TRANSLATIONS[code] = data if isinstance(data, dict) else {}
        except Exception:
            _TRANSLATIONS[code] = {}


def set_language(code: str) -> None:
    global CURRENT_LANG
    CURRENT_LANG = code if code in dict(LANGUAGES) else DEFAULT_LANG


def tr(key: str, **kwargs: Any) -> str:
    """Translate `key` into the current language. Falls back to Russian, then
    to the key itself. Optional kwargs are substituted via str.format."""
    table = _TRANSLATIONS.get(CURRENT_LANG) or {}
    s = table.get(key)
    if s is None:
        s = (_TRANSLATIONS.get(DEFAULT_LANG) or {}).get(key)
    if s is None:
        s = key
    if kwargs:
        try:
            s = s.format(**kwargs)
        except Exception:
            pass
    return s


load_translations()


# ---------------------------------------------------------------------------
# Codex session search
# ---------------------------------------------------------------------------

def parse_codex_index() -> list[dict[str, Any]]:
    """Parse ~/.codex/session_index.jsonl → list of {id, thread_name, updated_at, path?}"""
    items: list[dict[str, Any]] = []
    if not CODEX_INDEX_FILE.is_file():
        return items
    try:
        with CODEX_INDEX_FILE.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return items


def find_session_file(session_id: str) -> Path | None:
    """Resolve a session id to its jsonl file on disk."""
    if not CODEX_SESSIONS_DIR.is_dir():
        return None
    # Codex stores at ~/.codex/sessions/<year>/<month>/<day>/rollout-*-<uuid>.jsonl
    needle = session_id.lower()
    for path in CODEX_SESSIONS_DIR.rglob(f"*{session_id}*.jsonl"):
        return path
    # fallback exhaustive (slow)
    for path in CODEX_SESSIONS_DIR.rglob("*.jsonl"):
        if needle in path.name.lower():
            return path
    return None


def search_codex(keywords: list[str], match_all: bool, search_content: bool, max_results: int = 200) -> list[dict[str, Any]]:
    """Search Codex sessions. Returns list of dicts with id, title, updated_at, path, snippet."""
    keywords = [k.lower() for k in keywords if k]
    if not keywords:
        return []

    results: list[dict[str, Any]] = []
    index = parse_codex_index()
    seen_ids: set[str] = set()

    # Build id → (title, updated_at) map for fast lookup during content search
    id_to_meta: dict[str, dict[str, str]] = {}
    for item in index:
        sid = (item.get("id") or "").strip()
        if not sid:
            continue
        id_to_meta[sid] = {
            "title": (item.get("thread_name") or "").strip(),
            "updated_at": (item.get("updated_at") or "").strip(),
        }

    # First: title match from index (cheap)
    for item in index:
        title = (item.get("thread_name") or "").strip()
        title_l = title.lower()
        if match_all:
            ok = all(k in title_l for k in keywords)
        else:
            ok = any(k in title_l for k in keywords)
        if ok:
            sid = item.get("id") or ""
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            results.append({
                "id": sid,
                "title": title,
                "updated_at": item.get("updated_at") or "",
                "match": "title",
                "snippet": "",
                "path": "",
            })

    # Second: content match (heavier)
    if search_content and len(results) < max_results and CODEX_SESSIONS_DIR.is_dir():
        files = list(CODEX_SESSIONS_DIR.rglob("*.jsonl"))
        # newest first
        files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        for path in files:
            if len(results) >= max_results:
                break
            # extract session id from filename if possible
            m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", path.name)
            sid = m.group(1) if m else path.stem
            if sid in seen_ids:
                continue
            try:
                snippet = ""
                hits = 0
                title = ""
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        ll = line.lower()
                        local_hits = sum(1 for k in keywords if k in ll)
                        cond = (local_hits == len(keywords)) if match_all else (local_hits > 0)
                        if cond:
                            hits += 1
                            if not snippet:
                                # extract message text if it's a JSON message
                                try:
                                    obj = json.loads(line)
                                    raw = json.dumps(obj, ensure_ascii=False)
                                except Exception:
                                    raw = line
                                # Find a window around the first keyword hit
                                low = raw.lower()
                                pos = min((low.find(k) for k in keywords if k in low), default=0)
                                start = max(0, pos - 80)
                                snippet = raw[start:start + 280]
                                snippet = re.sub(r"\s+", " ", snippet).strip()
                            if hits >= 3:
                                break
                        if not title:
                            try:
                                obj = json.loads(line)
                                if isinstance(obj, dict):
                                    t = obj.get("thread_name") or obj.get("title")
                                    if isinstance(t, str):
                                        title = t
                            except Exception:
                                pass
                if hits:
                    mtime = path.stat().st_mtime if path.exists() else 0
                    meta = id_to_meta.get(sid, {})
                    final_title = title or meta.get("title") or "(без названия)"
                    updated = meta.get("updated_at") or (
                        datetime.fromtimestamp(mtime).isoformat() if mtime else ""
                    )
                    results.append({
                        "id": sid,
                        "title": final_title,
                        "updated_at": updated,
                        "match": "content",
                        "snippet": snippet,
                        "path": str(path),
                    })
                    seen_ids.add(sid)
            except Exception:
                continue

    # Final dedupe by id, prefer rows with a real title and a known path.
    deduped: dict[str, dict[str, Any]] = {}
    for r in results:
        sid = r.get("id") or ""
        if sid not in deduped:
            deduped[sid] = r
            continue
        existing = deduped[sid]
        # Prefer row with non-empty title
        if existing["title"] in ("", "(без названия)") and r["title"] not in ("", "(без названия)"):
            deduped[sid] = r
        # Prefer row with a resolved path
        elif not existing.get("path") and r.get("path"):
            existing["path"] = r["path"]
            if not existing.get("snippet"):
                existing["snippet"] = r.get("snippet", "")

    results = list(deduped.values())

    # Sort by updated_at descending
    results.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return results[:max_results]


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ProjectManagerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        set_language(self.settings.get("language", DEFAULT_LANG))
        self.title(tr("app_title"))
        self.geometry("1400x800")

        self.font_size = int(self.settings.get("font_size", DEFAULT_FONT_SIZE))

        self.cache: dict[str, dict[str, Any]] = load_cache()
        self.tasks_db: dict[str, dict[str, Any]] = load_tasks_db()
        self.projects: list[dict[str, Any]] = []
        self.filtered: list[dict[str, Any]] = []
        self._fired_reminders: set[str] = set()

        self._build_fonts()
        self._build_style()
        self._build_icons()
        self._build_ui()
        self._apply_font_size()

        self.after(50, self.load_projects_async)
        # Reminder poller
        self.after(2000, self._reminder_tick)

    # ---- fonts / style ----
    def _build_fonts(self) -> None:
        from tkinter import font as tkfont
        self.base_font = tkfont.Font(family="Segoe UI", size=self.font_size)
        self.bold_font = tkfont.Font(family="Segoe UI", size=self.font_size, weight="bold")
        self.tree_font = tkfont.Font(family="Segoe UI", size=self.font_size)
        self.heading_font = tkfont.Font(family="Segoe UI", size=self.font_size, weight="bold")
        self.mono_font = tkfont.Font(family="Consolas", size=self.font_size)

    def _build_icons(self) -> None:
        size = max(14, int(self.font_size * 1.4))
        self.icon_size = size
        if _HAS_PIL:
            self.icon_folder = self._pil_to_photo(self._draw_folder(size, pinned=False))
            self.icon_folder_pinned = self._pil_to_photo(self._draw_folder(size, pinned=True))
        else:
            self.icon_folder = None
            self.icon_folder_pinned = None

    @staticmethod
    def _pil_to_photo(img: "Image.Image") -> tk.PhotoImage:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))

    @staticmethod
    def _draw_folder(size: int, pinned: bool) -> "Image.Image":
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # Folder body (yellow)
        margin = max(1, size // 12)
        top = int(size * 0.32)
        body_top = int(size * 0.42)
        tab_right = int(size * 0.55)
        body = (margin, body_top, size - margin - 1, size - margin - 2)
        tab = (margin, top, tab_right, body_top + 1)
        d.rectangle(tab, fill=(245, 184, 28, 255), outline=(170, 110, 0, 255))
        d.rectangle(body, fill=(255, 205, 55, 255), outline=(170, 110, 0, 255))
        # Front lip — slightly lighter strip on top
        d.rectangle((body[0], body[1], body[2], body[1] + max(1, size // 10)),
                    fill=(255, 220, 90, 255))
        if pinned:
            # Red pin badge in top-right corner
            r = max(3, size // 4)
            cx = size - r - 1
            cy = r
            d.ellipse((cx - r, cy - r, cx + r, cy + r),
                      fill=(220, 35, 35, 255), outline=(140, 0, 0, 255))
            # small needle below the head
            d.line((cx, cy + r - 1, cx, min(size - 1, cy + r + 3)),
                   fill=(90, 90, 90, 255), width=max(1, size // 14))
        return img

    def _build_style(self) -> None:
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("vista")
        except Exception:
            pass

    def _apply_font_size(self) -> None:
        self.base_font.configure(size=self.font_size)
        self.bold_font.configure(size=self.font_size)
        self.tree_font.configure(size=self.font_size)
        self.heading_font.configure(size=self.font_size)
        self.mono_font.configure(size=max(MIN_FONT_SIZE, self.font_size - 1))

        row_h = int(self.font_size * 2.0)
        self.style.configure("Treeview", font=self.tree_font, rowheight=row_h)
        self.style.configure("Treeview.Heading", font=self.heading_font)
        self.style.configure("TButton", font=self.base_font, padding=(8, 4))
        self.style.configure("TLabel", font=self.base_font)
        self.style.configure("TEntry", font=self.base_font)
        self.style.configure("TCheckbutton", font=self.base_font)
        self.style.configure("TNotebook.Tab", font=self.base_font, padding=(12, 6))
        self.style.configure("TRadiobutton", font=self.base_font)

        # Apply to existing widgets that ignore styles (Text, Entry already use style font option)
        for w in self._font_followers:
            try:
                w.configure(font=self.base_font)
            except Exception:
                pass
        # Save setting
        self.settings["font_size"] = self.font_size
        save_settings(self.settings)
        # update font label
        try:
            self.font_size_label.config(text=tr("font_size", n=self.font_size))
        except Exception:
            pass

    def change_font(self, delta: int) -> None:
        new = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, self.font_size + delta))
        if new != self.font_size:
            self.font_size = new
            self._apply_font_size()

    # ---- ui ----
    def _build_ui(self) -> None:
        self._font_followers: list[tk.Widget] = []

        # top bar: two rows
        topbar = ttk.Frame(self, padding=(8, 4))
        topbar.pack(side="top", fill="x")
        row1 = ttk.Frame(topbar); row1.pack(side="top", fill="x")
        row2 = ttk.Frame(topbar); row2.pack(side="top", fill="x", pady=(4, 0))

        # row1: font + language + status
        ttk.Label(row1, text=tr("font")).pack(side="left")
        ttk.Button(row1, text="A−", width=3, command=lambda: self.change_font(-1)).pack(side="left", padx=2)
        ttk.Button(row1, text="A+", width=3, command=lambda: self.change_font(1)).pack(side="left", padx=2)
        self.font_size_label = ttk.Label(row1, text=tr("font_size", n=self.font_size))
        self.font_size_label.pack(side="left", padx=(6, 12))

        ttk.Label(row1, text=tr("lang_label")).pack(side="left")
        self._lang_names = [name for _code, name in LANGUAGES]
        self._lang_code_by_name = {name: code for code, name in LANGUAGES}
        cur_name = dict(LANGUAGES).get(CURRENT_LANG, LANGUAGES[0][1])
        self.lang_var = tk.StringVar(value=cur_name)
        lang_cb = ttk.Combobox(row1, textvariable=self.lang_var, values=self._lang_names,
                               state="readonly", width=10)
        lang_cb.pack(side="left", padx=(4, 12))
        lang_cb.bind("<<ComboboxSelected>>",
                     lambda _e: self.change_language(self._lang_code_by_name.get(self.lang_var.get(), DEFAULT_LANG)))

        self.status_var = tk.StringVar(value=tr("status_loading"))
        ttk.Label(row1, textvariable=self.status_var).pack(side="right")

        # row2: actions
        ttk.Button(row2, text=tr("btn_scan"), command=lambda: self.load_projects_async(force=True)).pack(side="left", padx=2)
        ttk.Button(row2, text=tr("btn_folder"), command=self.open_root).pack(side="left", padx=2)
        ttk.Button(row2, text=tr("btn_data"), command=self.open_data_dir).pack(side="left", padx=2)
        ttk.Button(row2, text=tr("btn_new"), command=self.create_new_project_dialog).pack(side="left", padx=2)
        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(row2, text=tr("btn_ds_new"), command=lambda: self.deepseek_scan_async(only_new=True)).pack(side="left", padx=2)
        ttk.Button(row2, text=tr("btn_ds_all"), command=lambda: self.deepseek_scan_async(only_new=False)).pack(side="left", padx=2)
        self.ds_cancel_btn = ttk.Button(row2, text=tr("btn_stop"), command=self.deepseek_cancel, state="disabled")
        self.ds_cancel_btn.pack(side="left", padx=2)
        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=6)
        self.terminal_btn = ttk.Button(row2, text=tr("btn_open_tabs", n=0),
                                       command=self.open_terminal_dialog)
        self.terminal_btn.pack(side="left", padx=2)
        ttk.Button(row2, text=tr("btn_restore_titles"),
                   command=self.restore_tab_titles_action).pack(side="left", padx=2)
        ttk.Button(row2, text=tr("btn_help"), command=self.open_help_dialog).pack(side="left", padx=2)

        # notebook with two tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._build_projects_tab()
        self._build_tasks_tab()
        self._build_codex_tab()

        # bind Ctrl + / Ctrl - for font
        self.bind("<Control-plus>", lambda e: self.change_font(1))
        self.bind("<Control-equal>", lambda e: self.change_font(1))
        self.bind("<Control-KP_Add>", lambda e: self.change_font(1))
        self.bind("<Control-minus>", lambda e: self.change_font(-1))
        self.bind("<Control-KP_Subtract>", lambda e: self.change_font(-1))
        self.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.bind("<F1>", lambda e: self.open_help_dialog())

    def _on_ctrl_wheel(self, event: tk.Event) -> None:
        self.change_font(1 if event.delta > 0 else -1)

    # ---- Projects tab ----
    def _build_projects_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=tr("tab_projects"))

        # search bar (two rows)
        barwrap = ttk.Frame(tab, padding=(4, 4))
        barwrap.pack(side="top", fill="x")
        bar = ttk.Frame(barwrap); bar.pack(side="top", fill="x")
        bar2 = ttk.Frame(barwrap); bar2.pack(side="top", fill="x", pady=(4, 0))

        ttk.Label(bar, text=tr("search_label")).pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.apply_filter())
        ent = ttk.Entry(bar, textvariable=self.filter_var)
        ent.pack(side="left", padx=6, fill="x", expand=True)
        ent.configure(font=self.base_font)
        self._font_followers.append(ent)
        # Hint: focus to clear hint text via FocusIn handler-less; simple placeholder via initial text trick is fragile,
        # so just rely on label above.

        self.count_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.count_var).pack(side="right", padx=(8, 0))

        ttk.Button(bar2, text=tr("btn_run_claude"), command=lambda: self.launch_selected("claude")).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("btn_run_codex"), command=lambda: self.launch_selected("codex")).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("btn_explorer"), command=self.open_selected_in_explorer).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("btn_pin"), command=self.toggle_pin_selected).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("btn_regenerate"), command=self.regenerate_selected_description).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("btn_ds_analyze"), command=self.deepseek_analyze_selected).pack(side="left", padx=2)

        # paned: left = tree, right = preview
        paned = ttk.Panedwindow(tab, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        cols = ("date", "title", "size", "langs", "description")
        self.tree = ttk.Treeview(left, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text=tr("col_folder"), command=lambda: self.sort_by("name"))
        self.tree.heading("date", text=tr("col_modified"), command=lambda: self.sort_by("date"))
        self.tree.heading("title", text=tr("col_title"), command=lambda: self.sort_by("title"))
        self.tree.heading("size", text=tr("col_size"), command=lambda: self.sort_by("size"))
        self.tree.heading("langs", text=tr("col_stack"), command=lambda: self.sort_by("langs"))
        self.tree.heading("description", text=tr("col_description"))
        self.tree.column("#0", width=260, anchor="w", stretch=False)
        self.tree.column("date", width=130, anchor="w", stretch=False)
        self.tree.column("title", width=140, anchor="w", stretch=False)
        self.tree.column("size", width=80, anchor="e", stretch=False)
        self.tree.column("langs", width=100, anchor="w", stretch=False)
        self.tree.column("description", width=620, anchor="w")

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_select_project)
        self.tree.bind("<Double-1>", lambda e: self.launch_selected("claude"))
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        # Drag-and-drop reorder for pinned rows
        self._drag_path: str | None = None
        self._drag_started = False
        self.tree.bind("<ButtonPress-1>", self._on_tree_press, add="+")
        self.tree.bind("<B1-Motion>", self._on_tree_drag, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release, add="+")
        # Keyboard shortcuts: Alt+Up / Alt+Down move pinned project
        self.tree.bind("<Alt-Up>", lambda e: (self.move_pinned_selected(-1), "break")[1])
        self.tree.bind("<Alt-Down>", lambda e: (self.move_pinned_selected(1), "break")[1])

        # Context menu
        self.ctx_menu = tk.Menu(self, tearoff=0, font=self.base_font)
        self.ctx_menu.add_command(label=tr("ctx_pin"), command=self.toggle_pin_selected)
        self.ctx_menu.add_command(label=tr("ctx_move_up"), command=lambda: self.move_pinned_selected(-1))
        self.ctx_menu.add_command(label=tr("ctx_move_down"), command=lambda: self.move_pinned_selected(1))
        self.ctx_menu.add_command(label=tr("ctx_terminal_pick"), command=self.toggle_terminal_pick)
        self.ctx_menu.add_command(label=tr("ctx_open_term_resume"),
                                  command=lambda: self.open_in_terminal_selected(fresh=False))
        self.ctx_menu.add_command(label=tr("ctx_open_term_fresh"),
                                  command=lambda: self.open_in_terminal_selected(fresh=True))
        self.ctx_menu.add_command(label=tr("ctx_edit_title"),
                                  command=self.edit_terminal_title_selected)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label=tr("ctx_run_claude"), command=lambda: self.launch_selected("claude"))
        self.ctx_menu.add_command(label=tr("ctx_run_codex"), command=lambda: self.launch_selected("codex"))
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label=tr("ctx_ds_analyze"), command=self.deepseek_analyze_selected)
        self.ctx_menu.add_command(label=tr("ctx_recalc_size"), command=self.recalc_size_selected)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label=tr("ctx_notes"), command=self.open_notes_dialog)
        self.ctx_menu.add_command(label=tr("ctx_add_task"), command=self.add_task_for_selected)
        self.ctx_menu.add_command(label=tr("ctx_project_tasks"), command=self.show_project_tasks)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label=tr("ctx_explorer"), command=self.open_selected_in_explorer)

        # right side preview
        prv_top = ttk.Frame(right, padding=(8, 6))
        prv_top.pack(fill="x")
        self.preview_title = ttk.Label(prv_top, text="—", font=self.bold_font)
        self.preview_title.pack(anchor="w")
        self.preview_path = ttk.Label(prv_top, text="", foreground="#555")
        self.preview_path.pack(anchor="w")

        self.preview_text = tk.Text(right, wrap="word", height=10, font=self.base_font)
        self.preview_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.preview_text.configure(state="disabled")
        self._font_followers.append(self.preview_text)

        self._sort_state = {"col": "date", "asc": False}

    # ---- Codex tab ----
    def _build_codex_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=tr("tab_codex"))

        bar = ttk.Frame(tab, padding=(8, 8))
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text=tr("codex_keywords")).pack(side="left")
        self.codex_query = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.codex_query, width=50)
        ent.pack(side="left", padx=6)
        ent.configure(font=self.base_font)
        self._font_followers.append(ent)
        ent.bind("<Return>", lambda e: self.run_codex_search())

        self.codex_match_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text=tr("codex_match_all"), variable=self.codex_match_all).pack(side="left", padx=4)

        self.codex_search_content = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=tr("codex_search_content"), variable=self.codex_search_content).pack(side="left", padx=4)

        ttk.Button(bar, text=tr("codex_find"), command=self.run_codex_search).pack(side="left", padx=8)

        self.codex_status = tk.StringVar(value=tr("codex_index", path=CODEX_INDEX_FILE))
        ttk.Label(bar, textvariable=self.codex_status).pack(side="right")

        paned = ttk.Panedwindow(tab, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        cols = ("title", "updated_at", "match", "id")
        self.codex_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        self.codex_tree.heading("title", text=tr("codex_col_title"))
        self.codex_tree.heading("updated_at", text=tr("codex_col_date"))
        self.codex_tree.heading("match", text=tr("codex_col_where"))
        self.codex_tree.heading("id", text=tr("codex_col_id"))
        self.codex_tree.column("title", width=380)
        self.codex_tree.column("updated_at", width=170)
        self.codex_tree.column("match", width=80)
        self.codex_tree.column("id", width=300)

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.codex_tree.yview)
        self.codex_tree.configure(yscrollcommand=vsb.set)
        self.codex_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.codex_tree.bind("<<TreeviewSelect>>", self.on_select_codex)
        self.codex_tree.bind("<Double-1>", lambda e: self.open_selected_codex_file())

        prv_top = ttk.Frame(right, padding=(8, 6))
        prv_top.pack(fill="x")
        self.codex_preview_title = ttk.Label(prv_top, text="—", font=self.bold_font)
        self.codex_preview_title.pack(anchor="w")
        self.codex_preview_path = ttk.Label(prv_top, text="", foreground="#555")
        self.codex_preview_path.pack(anchor="w")

        btns = ttk.Frame(right, padding=(8, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text=tr("codex_open_json"), command=self.open_selected_codex_file).pack(side="left", padx=2)
        ttk.Button(btns, text=tr("codex_copy_id"), command=self.copy_codex_id).pack(side="left", padx=2)
        ttk.Button(btns, text=tr("codex_resume"), command=self.resume_in_codex).pack(side="left", padx=2)

        self.codex_preview_text = tk.Text(right, wrap="word", height=10, font=self.mono_font)
        self.codex_preview_text.pack(fill="both", expand=True, padx=8, pady=(8, 8))
        self.codex_preview_text.configure(state="disabled")
        self._font_followers.append(self.codex_preview_text)

        self.codex_results: list[dict[str, Any]] = []

    # ---- project scanning ----
    def load_projects_async(self, force: bool = False) -> None:
        self.status_var.set(tr("st_scanning"))
        t = threading.Thread(target=self._scan_thread, args=(force,), daemon=True)
        t.start()

    def _scan_thread(self, force: bool) -> None:
        if not PROJECTS_ROOT.is_dir():
            self.after(0, lambda: messagebox.showerror(
                tr("error"), tr("mb_folder_not_found", path=PROJECTS_ROOT)))
            return

        try:
            folders = [
                p for p in PROJECTS_ROOT.iterdir()
                if p.is_dir()
                and not p.name.startswith(".")
                and p.name not in SKIP_DIRS
            ]
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                tr("error"), tr("mb_read_error", path=PROJECTS_ROOT, err=e)))
            return
        folders.sort(key=lambda p: p.name.lower())
        total = len(folders)

        projects: list[dict[str, Any]] = []
        # Use cache to avoid re-analyzing unchanged folders
        new_cache = dict(self.cache) if not force else {}
        done = 0

        def analyze_one(folder: Path) -> dict[str, Any]:
            try:
                mtime = folder.stat().st_mtime
            except Exception:
                mtime = 0.0
            cached = new_cache.get(str(folder))
            if cached and not force and abs(cached.get("mtime", 0) - mtime) < 1.0 and cached.get("description"):
                return cached
            info = analyze_project(folder)
            return info

        with ThreadPoolExecutor(max_workers=8) as ex:
            for info in ex.map(analyze_one, folders):
                projects.append(info)
                new_cache[info["path"]] = info
                done += 1
                if done % 10 == 0 or done == total:
                    self.after(0, lambda d=done, t=total: self.status_var.set(
                        tr("st_analyzing", done=d, total=t)))

        self.cache = new_cache
        save_cache(self.cache)
        self.projects = projects
        self.after(0, lambda: (self.apply_filter(),
                               self.status_var.set(tr("st_ready", n=len(projects)))))
        # Kick off background size computation
        threading.Thread(target=self._size_scan_thread, daemon=True).start()

    def _size_scan_thread(self) -> None:
        targets = [p for p in self.projects if not p.get("size_bytes") or p.get("size_mtime") != p.get("mtime")]
        total = len(targets)
        if total == 0:
            return
        done = 0
        for p in targets:
            try:
                size = compute_folder_size(Path(p["path"]))
            except Exception:
                size = -1
            p["size_bytes"] = size
            p["size_mtime"] = p.get("mtime", 0)
            self.cache[p["path"]] = p
            done += 1
            if done % 10 == 0 or done == total:
                self.after(0, lambda d=done, t=total: self._on_size_progress(d, t))
        save_cache(self.cache)
        self.after(0, lambda: self.status_var.set(tr("st_sizes_done", n=total)))

    def _on_size_progress(self, done: int, total: int) -> None:
        # Light refresh: only update size column for visible rows
        for p in self.filtered:
            iid = p["path"]
            if self.tree.exists(iid):
                vals = list(self.tree.item(iid, "values"))
                if len(vals) >= 3:
                    vals[2] = fmt_size(p.get("size_bytes"))
                    self.tree.item(iid, values=vals)
        self.status_var.set(tr("st_sizes_progress", done=done, total=total))

    def apply_filter(self) -> None:
        q = self.filter_var.get().strip().lower()
        if q:
            terms = [t for t in q.split() if t]
            def hit(p: dict[str, Any]) -> bool:
                hay = " ".join([
                    p.get("name", ""),
                    p.get("description", ""),
                    p.get("short_desc", ""),
                    p.get("full_desc", ""),
                    p.get("stage", ""),
                    p.get("langs", ""),
                ]).lower()
                return all(t in hay for t in terms)
            self.filtered = [p for p in self.projects if hit(p)]
        else:
            self.filtered = list(self.projects)
        self._apply_sort()
        self._render_tree()

    def _apply_sort(self) -> None:
        col = self._sort_state["col"]
        asc = self._sort_state["asc"]
        def key(p: dict[str, Any]):
            if col == "date":
                return p.get("mtime", 0)
            if col == "size":
                return p.get("size_bytes") or -1
            if col == "langs":
                return (p.get("langs") or "").lower()
            if col == "title":
                titles_map = self.settings.get("terminal_titles", {})
                return (titles_map.get(p.get("path"), "")).lower()
            return (p.get(col) or "").lower()
        self.filtered.sort(key=key, reverse=not asc)
        # Pinned go on top, in the explicit order from settings["pinned"].
        pinned = self.settings.get("pinned", [])
        pin_index = {path: i for i, path in enumerate(pinned)}
        def order(p: dict[str, Any]):
            idx = pin_index.get(p.get("path"))
            return (0, idx) if idx is not None else (1, 0)
        self.filtered.sort(key=order)

    def sort_by(self, col: str) -> None:
        if self._sort_state["col"] == col:
            self._sort_state["asc"] = not self._sort_state["asc"]
        else:
            self._sort_state["col"] = col
            self._sort_state["asc"] = True
        self._apply_sort()
        self._render_tree()

    def _render_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        pinned_set = set(self.settings.get("pinned", []))
        picks_set = set(self.settings.get("terminal_picks", []))
        titles_map = self.settings.get("terminal_titles", {})
        for p in self.filtered:
            short = p.get("short_desc") or p.get("description", "")
            is_pinned = p.get("path") in pinned_set
            is_pick = p.get("path") in picks_set
            tags = ("pinned",) if is_pinned else (("terminal",) if is_pick else ())
            icon = self.icon_folder_pinned if is_pinned else self.icon_folder
            size_str = fmt_size(p.get("size_bytes")) if "size_bytes" in p else "…"
            display_name = ("🖥 " if is_pick else "") + p.get("name", "")
            self.tree.insert("", "end", iid=p["path"],
                text=display_name,
                image=icon if icon is not None else "",
                values=(
                    fmt_dt(p.get("mtime", 0)),
                    titles_map.get(p.get("path"), ""),
                    size_str,
                    p.get("langs", ""),
                    short,
                ),
                tags=tags,
            )
        self.tree.tag_configure("pinned", background="#fff3c4")
        self.tree.tag_configure("terminal", background="#d9f0ff")
        self.count_var.set(tr("count_shown", shown=len(self.filtered), total=len(self.projects)))
        self._update_terminal_btn()

    def _update_terminal_btn(self) -> None:
        n = len(self.settings.get("terminal_picks", []))
        if hasattr(self, "terminal_btn"):
            try:
                self.terminal_btn.config(text=tr("btn_open_tabs", n=n))
            except Exception:
                pass

    def change_language(self, code: str) -> None:
        """Switch the UI language and rebuild the whole window in place."""
        if code == CURRENT_LANG:
            return
        set_language(code)
        self.settings["language"] = code
        save_settings(self.settings)
        # Tear down and rebuild every widget; instance state (projects, cache,
        # tasks_db, settings) lives on `self` and survives the rebuild.
        for w in self.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self.title(tr("app_title"))
        self._build_ui()
        self._apply_font_size()
        self.apply_filter()
        lang_name = dict(LANGUAGES).get(code, code)
        self.status_var.set(tr("st_lang_changed", lang=lang_name))

    def open_help_dialog(self) -> None:
        """Show the multilingual help window: topic list on the left, text on the right.
        Help content lives in help/<lang>.md (fallback en -> ru)."""
        base_dirs: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            base_dirs.append(Path(meipass) / "help")
        if getattr(sys, "frozen", False):
            base_dirs.append(Path(sys.executable).resolve().parent / "help")
        base_dirs.append(Path(__file__).resolve().parent / "help")
        text = ""
        for code in (CURRENT_LANG, "en", "ru"):
            for d in base_dirs:
                f = d / f"{code}.md"
                if f.is_file():
                    try:
                        text = f.read_text(encoding="utf-8")
                    except Exception:
                        text = ""
                if text:
                    break
            if text:
                break

        # Split the markdown into (title, body) topics by '## ' headers.
        topics: list[tuple[str, str]] = []
        cur_title: str | None = None
        cur_lines: list[str] = []
        for line in (text or tr("help_not_found")).splitlines():
            if line.startswith("## "):
                if cur_title is not None:
                    topics.append((cur_title, "\n".join(cur_lines).strip()))
                cur_title = line[3:].strip()
                cur_lines = []
            elif line.startswith("# "):
                continue
            else:
                cur_lines.append(line)
        if cur_title is not None:
            topics.append((cur_title, "\n".join(cur_lines).strip()))
        if not topics:
            topics = [(tr("help_topics"), text or tr("help_not_found"))]

        dlg = tk.Toplevel(self)
        dlg.title(tr("help_title"))
        try:
            sw = self.winfo_screenwidth(); sh = self.winfo_screenheight()
            w = min(960, int(sw * 0.7)); h = min(680, int(sh * 0.8))
            dlg.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        except Exception:
            dlg.geometry("960x680")
        dlg.transient(self)

        btns = ttk.Frame(dlg, padding=(8, 6))
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text=tr("close"), command=dlg.destroy).pack(side="right")

        paned = ttk.Panedwindow(dlg, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=8)
        left = ttk.Frame(paned); right = ttk.Frame(paned)
        paned.add(left, weight=1); paned.add(right, weight=3)

        lb = tk.Listbox(left, font=self.base_font, exportselection=False)
        lb.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(left, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=lsb.set); lsb.pack(side="right", fill="y")
        for t, _b in topics:
            lb.insert("end", t)

        txt = tk.Text(right, wrap="word", font=self.base_font, padx=10, pady=8)
        txt.pack(side="left", fill="both", expand=True)
        rsb = ttk.Scrollbar(right, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=rsb.set); rsb.pack(side="right", fill="y")

        def _show(idx: int) -> None:
            if 0 <= idx < len(topics):
                txt.configure(state="normal")
                txt.delete("1.0", "end")
                txt.insert("1.0", topics[idx][1])
                txt.configure(state="disabled")

        lb.bind("<<ListboxSelect>>",
                lambda _e: (_show(lb.curselection()[0]) if lb.curselection() else None))
        lb.selection_set(0)
        _show(0)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def on_select_project(self, _evt: tk.Event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        path = sel[0]
        p = self.cache.get(path) or next((x for x in self.projects if x["path"] == path), None)
        if not p:
            return
        pinned = path in set(self.settings.get("pinned", []))
        title = ("📌 " if pinned else "") + p.get("name", "")
        self.preview_title.config(text=title)
        self.preview_path.config(text=p.get("path", ""))
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")

        recent = p.get("recent_files") or []
        recent_lines = []
        for r in recent[:5]:
            recent_lines.append(f"   • {fmt_dt(r.get('mtime', 0))}  {r.get('path','')}")

        stage = p.get("stage") or "—"
        reason = p.get("stage_reason") or ""
        analyzed_by = p.get("analyzed_by", "heuristic")
        analyzed_at = p.get("analyzed_at", 0)
        analyzed_note = (
            tr("prv_analyzed_ds", dt=fmt_dt(analyzed_at)) if analyzed_by == "deepseek" and analyzed_at
            else tr("prv_analyzed_heur")
        )

        full = p.get("full_desc") or p.get("description") or ""
        short = p.get("short_desc") or ""
        dash = tr("dash")

        blocks = [
            f"{tr('prv_path')} {p.get('path','')}",
            f"{tr('prv_readme')} {p.get('readme', dash)}",
            f"{tr('prv_stack')} {p.get('langs', dash)}",
            f"{tr('prv_modified')} {fmt_dt(p.get('mtime', 0))}",
            f"{tr('prv_stage')} {stage}" + (f"  ({reason})" if reason else ""),
            f"{tr('prv_analysis')} {analyzed_note}",
            "",
        ]
        if short:
            blocks += [tr("prv_short_desc"), f"   {short}", ""]
        blocks += [tr("prv_full_desc"), full or dash, ""]
        if recent_lines:
            blocks += [tr("prv_recent")]
            blocks += recent_lines
        if p.get("last_error"):
            blocks += ["", tr("prv_ds_error", err=p["last_error"])]

        self.preview_text.insert("1.0", "\n".join(blocks))
        self.preview_text.configure(state="disabled")

    def get_selected_project(self) -> dict[str, Any] | None:
        sel = self.tree.selection()
        if not sel:
            return None
        path = sel[0]
        return self.cache.get(path) or next((x for x in self.projects if x["path"] == path), None)

    # ---- actions ----
    def launch_selected(self, tool: str) -> None:
        p = self.get_selected_project()
        if not p:
            messagebox.showinfo(tr("mb_select_title"), tr("mb_select_project"))
            return
        script = CLAUDE_SCRIPT if tool == "claude" else CODEX_SCRIPT
        if not script.is_file():
            messagebox.showerror(tr("mb_script_not_found"),
                                 tr("mb_script_not_found_msg", path=script))
            return
        try:
            # Launch in a NEW console window so the user sees an interactive session.
            # cmd /k keeps the window open if the script returns.
            subprocess.Popen(
                ["cmd", "/c", "start", "", "cmd", "/k", str(script), p["path"]],
                cwd=p["path"],
            )
            self.status_var.set(tr("st_launched", tool=tool, name=p["name"]))
        except Exception as e:
            messagebox.showerror(tr("mb_launch_error"), str(e))

    def open_selected_in_explorer(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        try:
            os.startfile(p["path"])  # type: ignore[attr-defined]
        except Exception as e:
            messagebox.showerror(tr("error"), str(e))

    def open_root(self) -> None:
        try:
            os.startfile(str(PROJECTS_ROOT))  # type: ignore[attr-defined]
        except Exception as e:
            messagebox.showerror(tr("error"), str(e))

    def open_data_dir(self) -> None:
        try:
            os.startfile(str(APP_DIR))  # type: ignore[attr-defined]
            self.status_var.set(tr("st_data_dir", dir=APP_DIR))
        except Exception as e:
            messagebox.showerror(tr("error"), str(e))

    def _on_tree_right_click(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        if row_id:
            self.tree.selection_set(row_id)
            self.tree.focus(row_id)
            self.on_select_project(event)  # type: ignore[arg-type]
            try:
                self.ctx_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.ctx_menu.grab_release()

    def toggle_pin_selected(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        pinned = list(self.settings.get("pinned", []))
        path = p["path"]
        if path in pinned:
            pinned.remove(path)
            self.status_var.set(tr("st_unpinned", name=p["name"]))
        else:
            pinned.insert(0, path)
            self.status_var.set(tr("st_pinned", name=p["name"]))
        self.settings["pinned"] = pinned
        save_settings(self.settings)
        self.apply_filter()
        if self.tree.exists(path):
            self.tree.selection_set(path)
            self.tree.see(path)
            self.on_select_project(None)  # type: ignore[arg-type]

    def move_pinned_selected(self, delta: int) -> None:
        """Move the currently selected pinned project up (-1) or down (+1) within the pinned list."""
        sel = self.tree.selection()
        if not sel:
            return
        path = sel[0]
        pinned = list(self.settings.get("pinned", []))
        if path not in pinned:
            self.status_var.set(tr("st_move_pinned_only"))
            return
        i = pinned.index(path)
        j = i + delta
        if j < 0 or j >= len(pinned):
            return
        pinned[i], pinned[j] = pinned[j], pinned[i]
        self.settings["pinned"] = pinned
        save_settings(self.settings)
        self.apply_filter()
        if self.tree.exists(path):
            self.tree.selection_set(path)
            self.tree.see(path)

    def _on_tree_press(self, event: tk.Event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell" and region != "tree":
            self._drag_path = None
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            self._drag_path = None
            return
        pinned = self.settings.get("pinned", [])
        if iid not in pinned:
            self._drag_path = None
            return
        self._drag_path = iid
        self._drag_started = False

    def _on_tree_drag(self, event: tk.Event) -> None:
        if not self._drag_path:
            return
        target = self.tree.identify_row(event.y)
        if not target or target == self._drag_path:
            return
        pinned = list(self.settings.get("pinned", []))
        if target not in pinned:
            return
        try:
            self.tree.config(cursor="hand2")
        except Exception:
            pass
        self._drag_started = True
        i = pinned.index(self._drag_path)
        j = pinned.index(target)
        pinned.insert(j, pinned.pop(i))
        self.settings["pinned"] = pinned
        # Live update without rescanning: just re-apply sort/render
        self._apply_sort()
        self._render_tree()
        if self.tree.exists(self._drag_path):
            self.tree.selection_set(self._drag_path)
            self.tree.see(self._drag_path)

    def _on_tree_release(self, _event: tk.Event) -> None:
        try:
            self.tree.config(cursor="")
        except Exception:
            pass
        if self._drag_path and self._drag_started:
            save_settings(self.settings)
            self.status_var.set(tr("st_pin_order_saved"))
        self._drag_path = None
        self._drag_started = False

    def toggle_terminal_pick(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        picks = list(self.settings.get("terminal_picks", []))
        path = p["path"]
        if path in picks:
            picks.remove(path)
            self.status_var.set(tr("st_terminal_unmarked", name=p["name"]))
        else:
            picks.append(path)
            self.status_var.set(tr("st_terminal_marked", name=p["name"]))
        self.settings["terminal_picks"] = picks
        save_settings(self.settings)
        self.apply_filter()
        if self.tree.exists(path):
            self.tree.selection_set(path)
            self.tree.see(path)

    def restore_tab_titles_action(self) -> None:
        """Detect WT tabs launched via our Claude-BypassProxy scripts and rename each
        back to its configured title (project name or settings.terminal_titles override)."""
        def worker():
            tabs = discover_wt_project_tabs()
            self.after(0, lambda: self._restore_tab_titles_apply(tabs))

        self.status_var.set(tr("st_restore_searching"))
        threading.Thread(target=worker, daemon=True).start()

    def _restore_tab_titles_apply(self, tabs: list[dict[str, Any]]) -> None:
        if not tabs:
            messagebox.showinfo(tr("mb_restore_title"), tr("mb_restore_none"))
            self.status_var.set(tr("st_restore_none"))
            return
        titles_map = self.settings.get("terminal_titles", {})
        picks: list[dict[str, Any]] = []
        for t in tabs:
            path = t["path"]
            entry = self.cache.get(path)
            name = (entry.get("name") if entry else t["name"]) or path
            picks.append({
                "path": path,
                "name": name,
                "tab_title": titles_map.get(path, ""),
            })
        titles = resolve_tab_titles(picks)
        argv = build_restore_titles_command(titles)
        try:
            subprocess.Popen(argv)
            self.status_var.set(tr("st_restore_done", n=len(titles)))
        except FileNotFoundError:
            messagebox.showerror(tr("mb_wt_not_found_title"), tr("mb_wt_not_found"))
        except Exception as e:
            messagebox.showerror(tr("mb_restore_error"), str(e))

    def open_in_terminal_selected(self, fresh: bool = False) -> None:
        """Open the selected project as a new tab in the persistent 'pm-tabs' WT window.
        Uses the per-project saved tab title from terminal_titles, if any."""
        p = self.get_selected_project()
        if not p:
            return
        titles = self.settings.get("terminal_titles", {})
        pick = {
            "path": p["path"],
            "name": p.get("name") or Path(p["path"]).name,
            "tab_title": titles.get(p["path"], ""),
        }
        argv = build_wt_command([pick], fresh=fresh, auto_claude=True)
        try:
            subprocess.Popen(argv)
            self.status_var.set(tr("st_tab_opened", name=pick["name"]))
        except FileNotFoundError:
            messagebox.showerror(tr("mb_wt_not_found_title"), tr("mb_wt_not_found"))
        except Exception as e:
            messagebox.showerror(tr("mb_launch_error"), str(e))

    def edit_terminal_title_selected(self) -> None:
        """Edit the WT tab title override for the selected project (stored in settings)."""
        from tkinter import simpledialog
        p = self.get_selected_project()
        if not p:
            return
        path = p["path"]
        titles = dict(self.settings.get("terminal_titles", {}))
        cur = titles.get(path, "")
        new = simpledialog.askstring(
            tr("title_dlg_title"),
            tr("title_dlg_prompt", name=p.get("name", path)),
            parent=self, initialvalue=cur,
        )
        if new is None:
            return
        new = new.strip()
        if new:
            titles[path] = new
            self.status_var.set(tr("st_title_set", title=new))
        else:
            titles.pop(path, None)
            self.status_var.set(tr("st_title_cleared"))
        self.settings["terminal_titles"] = titles
        save_settings(self.settings)
        self._render_tree()
        if self.tree.exists(path):
            self.tree.selection_set(path)
            self.tree.see(path)

    def open_terminal_dialog(self) -> None:
        picks_paths = list(self.settings.get("terminal_picks", []))
        presets = load_terminal_presets()

        dlg = tk.Toplevel(self)
        dlg.title(tr("term_dlg_title"))
        try:
            sw = self.winfo_screenwidth(); sh = self.winfo_screenheight()
            w = min(900, int(sw * 0.6)); h = min(760, int(sh * 0.8))
            x = (sw - w) // 2; y = (sh - h) // 2
            dlg.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            dlg.geometry("900x760")
        dlg.minsize(820, 600)
        dlg.transient(self)

        # Reserve space at the bottom for the action buttons and options BEFORE
        # packing the (expandable) project list, so they can't get clipped if the
        # window is short.
        btns = ttk.Frame(dlg, padding=10)
        btns.pack(fill="x", side="bottom")
        opts = ttk.LabelFrame(dlg, text="Параметры запуска", padding=8)
        opts.pack(fill="x", side="bottom", padx=10, pady=(0, 4))

        # presets row
        pr_row = ttk.Frame(dlg, padding=(10, 10, 10, 4))
        pr_row.pack(fill="x")
        ttk.Label(pr_row, text=tr("term_preset")).pack(side="left")
        preset_names = [tr("term_preset_current")] + [pr.get("name", "?") for pr in presets]
        preset_var = tk.StringVar(value=preset_names[0])
        preset_cb = ttk.Combobox(pr_row, textvariable=preset_var, values=preset_names,
                                 state="readonly", width=42)
        preset_cb.pack(side="left", padx=6)

        # list of picks
        lst_frame = ttk.LabelFrame(dlg, text=tr("term_list_frame"), padding=8)
        lst_frame.pack(fill="both", expand=True, padx=10, pady=4)
        lst = ttk.Treeview(lst_frame, columns=("title",), show="tree headings",
                           selectmode="extended", height=12)
        lst.heading("#0", text=tr("term_col_project"))
        lst.heading("title", text=tr("term_col_tab_title"))
        lst.column("#0", width=460, anchor="w")
        lst.column("title", width=260, anchor="w")
        lst.pack(side="left", fill="both", expand=True)
        lst_vsb = ttk.Scrollbar(lst_frame, orient="vertical", command=lst.yview)
        lst.configure(yscrollcommand=lst_vsb.set); lst_vsb.pack(side="right", fill="y")

        work_paths = list(picks_paths)
        titles: dict[str, str] = dict(self.settings.get("terminal_titles", {}))
        _color_tags: set[str] = set()
        _state = {"dirty": False, "loaded_preset": None}  # name of preset currently loaded, or None

        def _set_dirty(flag: bool) -> None:
            _state["dirty"] = flag
            try:
                dlg.title(("● " if flag else "") + tr("term_dlg_title"))
            except Exception:
                pass
            _update_save_btn()

        def _mark_dirty() -> None:
            if not _state["dirty"]:
                _set_dirty(True)

        def _project_name(path: str) -> str:
            entry = self.cache.get(path)
            return (entry.get("name") if entry else Path(path).name) or path

        def _refresh_list() -> None:
            lst.delete(*lst.get_children())
            for path in work_paths:
                name = _project_name(path)
                color = color_for_name(name)
                tag = f"c_{color.lstrip('#')}"
                if tag not in _color_tags:
                    lst.tag_configure(tag, foreground=color)
                    _color_tags.add(tag)
                lst.insert("", "end", iid=path,
                           text=f"●  {name}    [{path}]",
                           values=(titles.get(path, ""),),
                           tags=(tag,))

        _refresh_list()

        def _edit_title(path: str) -> None:
            if path not in work_paths:
                return
            cur = titles.get(path, "")
            try:
                bbox = lst.bbox(path, "title")
            except Exception:
                bbox = None
            if not bbox:
                # Fallback: simple dialog
                from tkinter import simpledialog
                new = simpledialog.askstring(
                    tr("term_tab_title_dlg"),
                    tr("term_tab_title_prompt", name=_project_name(path)),
                    parent=dlg, initialvalue=cur,
                )
                if new is None:
                    return
                new = new.strip()
                if new:
                    titles[path] = new
                else:
                    titles.pop(path, None)
                _refresh_list()
                return
            x, y, w, h = bbox
            ent_var = tk.StringVar(value=cur)
            entry_widget = ttk.Entry(lst, textvariable=ent_var)
            entry_widget.place(x=x, y=y, width=w, height=h)
            entry_widget.focus_set()
            entry_widget.select_range(0, "end")

            def _commit(_e=None) -> None:
                new = ent_var.get().strip()
                old = titles.get(path, "")
                if new:
                    titles[path] = new
                else:
                    titles.pop(path, None)
                if new != old:
                    _mark_dirty()
                entry_widget.destroy()
                _refresh_list()
                if lst.exists(path):
                    lst.selection_set(path)

            def _cancel(_e=None) -> None:
                entry_widget.destroy()

            entry_widget.bind("<Return>", _commit)
            entry_widget.bind("<FocusOut>", _commit)
            entry_widget.bind("<Escape>", _cancel)

        def _on_lst_double(event: tk.Event) -> None:
            row = lst.identify_row(event.y)
            col = lst.identify_column(event.x)
            if row and col == "#1":
                _edit_title(row)

        lst.bind("<Double-1>", _on_lst_double)

        def _load_preset(*_a) -> None:
            nonlocal work_paths
            name = preset_var.get()
            if name == preset_names[0]:
                work_paths = list(picks_paths)
                titles.clear()
                titles.update(self.settings.get("terminal_titles", {}))
                _state["loaded_preset"] = None
            else:
                pr = next((x for x in presets if x.get("name") == name), None)
                if pr:
                    work_paths = list(pr.get("projects", []))
                    titles.clear()
                    titles.update(pr.get("titles", {}))
                    if "fresh" in pr:
                        mode_var.set("fresh" if pr["fresh"] else "resume")
                    if "auto_claude" in pr:
                        auto_var.set(bool(pr["auto_claude"]))
                    _state["loaded_preset"] = name
            _refresh_list()
            _set_dirty(False)
        preset_cb.bind("<<ComboboxSelected>>", _load_preset)

        # list management
        list_btns = ttk.Frame(dlg, padding=(10, 0))
        list_btns.pack(fill="x")

        def _selected_indices() -> list[int]:
            sel_paths = set(lst.selection())
            return [i for i, p in enumerate(work_paths) if p in sel_paths]

        def _remove_sel() -> None:
            idxs = _selected_indices()
            if not idxs:
                return
            for i in sorted(idxs, reverse=True):
                work_paths.pop(i)
            _refresh_list()
            _mark_dirty()

        def _up() -> None:
            idxs = _selected_indices()
            if not idxs or idxs[0] == 0:
                return
            for i in idxs:
                work_paths[i-1], work_paths[i] = work_paths[i], work_paths[i-1]
            new_sel = [work_paths[i-1] for i in idxs]
            _refresh_list()
            for p in new_sel:
                if lst.exists(p):
                    lst.selection_add(p)
            _mark_dirty()

        def _down() -> None:
            idxs = _selected_indices()
            if not idxs or idxs[-1] >= len(work_paths) - 1:
                return
            for i in reversed(idxs):
                work_paths[i], work_paths[i+1] = work_paths[i+1], work_paths[i]
            new_sel = [work_paths[i+1] for i in idxs]
            _refresh_list()
            for p in new_sel:
                if lst.exists(p):
                    lst.selection_add(p)
            _mark_dirty()

        def _open_add_dialog() -> None:
            existing = set(work_paths)
            candidates = sorted(
                (p for p in self.projects if p.get("path") and p["path"] not in existing),
                key=lambda p: (p.get("name") or "").lower(),
            )
            if not candidates:
                messagebox.showinfo(tr("term_add_title"),
                                    tr("term_add_all_in"), parent=dlg)
                return

            add_dlg = tk.Toplevel(dlg)
            add_dlg.title(tr("term_add_title"))
            add_dlg.transient(dlg)
            add_dlg.geometry("640x500")

            top = ttk.Frame(add_dlg, padding=(10, 10, 10, 4)); top.pack(fill="x")
            ttk.Label(top, text=tr("term_filter")).pack(side="left")
            q_var = tk.StringVar()
            q_entry = ttk.Entry(top, textvariable=q_var)
            q_entry.pack(side="left", fill="x", expand=True, padx=6)
            q_entry.focus_set()

            mid = ttk.Frame(add_dlg, padding=(10, 0)); mid.pack(fill="both", expand=True)
            add_lst = tk.Listbox(mid, selectmode="extended", font=self.base_font)
            add_lst.pack(side="left", fill="both", expand=True)
            self._font_followers.append(add_lst)
            vsb = ttk.Scrollbar(mid, orient="vertical", command=add_lst.yview)
            add_lst.configure(yscrollcommand=vsb.set); vsb.pack(side="right", fill="y")

            visible: list[dict[str, Any]] = []

            def _refill(*_a) -> None:
                q = q_var.get().strip().lower()
                add_lst.delete(0, "end")
                visible.clear()
                for p in candidates:
                    name = (p.get("name") or "")
                    path = p.get("path", "")
                    if q and q not in name.lower() and q not in path.lower():
                        continue
                    visible.append(p)
                    add_lst.insert("end", f"●  {name}    [{path}]")
                    try:
                        add_lst.itemconfigure(len(visible) - 1,
                                              foreground=color_for_name(name))
                    except Exception:
                        pass

            q_var.trace_add("write", _refill)
            _refill()

            def _do_add() -> None:
                sel = list(add_lst.curselection())
                if not sel:
                    return
                added = 0
                for i in sel:
                    if 0 <= i < len(visible):
                        path = visible[i].get("path")
                        if path and path not in work_paths:
                            work_paths.append(path)
                            added += 1
                add_dlg.destroy()
                _refresh_list()
                if added:
                    _mark_dirty()

            add_lst.bind("<Double-1>", lambda _e: _do_add())
            add_lst.bind("<Return>", lambda _e: _do_add())
            q_entry.bind("<Return>",
                         lambda _e: (add_lst.selection_set(0) if not add_lst.curselection() and visible else None,
                                     _do_add())[1])

            bot = ttk.Frame(add_dlg, padding=10); bot.pack(fill="x", side="bottom")
            ttk.Button(bot, text=tr("cancel"), command=add_dlg.destroy).pack(side="right")
            ttk.Button(bot, text=tr("term_add_btn"),
                       command=_do_add).pack(side="right", padx=4)

            add_dlg.grab_set()
            add_dlg.wait_window()

        ttk.Button(list_btns, text="↑", width=3, command=_up).pack(side="left", padx=2)
        ttk.Button(list_btns, text="↓", width=3, command=_down).pack(side="left", padx=2)
        ttk.Button(list_btns, text=tr("term_remove"), command=_remove_sel).pack(side="left", padx=2)
        ttk.Button(list_btns, text=tr("term_add"), command=_open_add_dialog).pack(side="left", padx=2)

        # Right-click context menu on the picks list
        lst_menu = tk.Menu(lst, tearoff=0, font=self.base_font)
        lst_menu.add_command(label=tr("term_lm_add"), command=_open_add_dialog)
        lst_menu.add_command(label=tr("term_lm_edit_title"),
                             command=lambda: (lst.selection() and _edit_title(lst.selection()[0])))
        lst_menu.add_command(label=tr("term_lm_reset_title"),
                             command=lambda: ([titles.pop(p, None) for p in lst.selection()],
                                              _refresh_list(), _mark_dirty()))
        lst_menu.add_command(label=tr("term_lm_remove"), command=_remove_sel)
        lst_menu.add_separator()
        lst_menu.add_command(label=tr("term_lm_up"), command=_up)
        lst_menu.add_command(label=tr("term_lm_down"), command=_down)

        def _on_lst_right_click(event: tk.Event) -> None:
            row = lst.identify_row(event.y)
            if row:
                if row not in lst.selection():
                    lst.selection_set(row)
            try:
                lst_menu.tk_popup(event.x_root, event.y_root)
            finally:
                lst_menu.grab_release()

        lst.bind("<Button-3>", _on_lst_right_click)

        # options (frame already packed at the top of the function)
        mode_var = tk.StringVar(value="resume")
        ttk.Radiobutton(opts, text=tr("term_mode_fresh"),
                        variable=mode_var, value="fresh").pack(anchor="w")
        ttk.Radiobutton(opts, text=tr("term_mode_resume"),
                        variable=mode_var, value="resume").pack(anchor="w")
        auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text=tr("term_auto_claude"),
                        variable=auto_var).pack(anchor="w", pady=(4, 0))
        mode_var.trace_add("write", lambda *_a: _mark_dirty())
        auto_var.trace_add("write", lambda *_a: _mark_dirty())

        # action row (frame already packed at the top of the function)
        def _save_as_preset() -> None:
            from tkinter import simpledialog
            default = preset_var.get() if preset_var.get() != preset_names[0] else ""
            name = simpledialog.askstring(tr("term_save_preset_title"),
                                          tr("term_save_preset_prompt"),
                                          parent=dlg, initialvalue=default)
            if not name or not name.strip():
                return
            name = name.strip()
            new_preset = {
                "name": name,
                "projects": list(work_paths),
                "titles": {p: titles[p] for p in work_paths if p in titles},
                "fresh": mode_var.get() == "fresh",
                "auto_claude": bool(auto_var.get()),
            }
            out = [pr for pr in presets if pr.get("name") != name]
            out.append(new_preset)
            save_terminal_presets(out)
            presets.clear(); presets.extend(out)
            new_names = ["— текущий выбор —"] + [pr.get("name", "?") for pr in presets]
            preset_names.clear(); preset_names.extend(new_names)
            preset_cb.config(values=preset_names)
            preset_var.set(name)
            _state["loaded_preset"] = name
            _set_dirty(False)
            self.status_var.set(tr("st_preset_saved", name=name, n=len(work_paths)))

        def _delete_preset() -> None:
            name = preset_var.get()
            if name == preset_names[0]:
                return
            if not messagebox.askyesno(tr("term_delete_preset_title"),
                                       tr("term_delete_preset_confirm", name=name),
                                       parent=dlg):
                return
            out = [pr for pr in presets if pr.get("name") != name]
            save_terminal_presets(out)
            presets.clear(); presets.extend(out)
            new_names = ["— текущий выбор —"] + [pr.get("name", "?") for pr in presets]
            preset_names.clear(); preset_names.extend(new_names)
            preset_cb.config(values=preset_names)
            preset_var.set(preset_names[0])

        def _launch() -> None:
            if not work_paths:
                messagebox.showinfo(tr("term_empty_title"), tr("term_empty_msg"), parent=dlg)
                return
            picks_dicts = []
            for path in work_paths:
                entry = self.cache.get(path)
                picks_dicts.append({
                    "path": path,
                    "name": (entry.get("name") if entry else Path(path).name),
                    "tab_title": titles.get(path, ""),
                })
            argv = build_wt_command(picks_dicts,
                                    fresh=(mode_var.get() == "fresh"),
                                    auto_claude=bool(auto_var.get()))
            try:
                subprocess.Popen(argv)
                # Persist current-selection titles so they survive the next dialog open.
                self.settings["terminal_titles"] = {p: titles[p] for p in work_paths if p in titles}
                save_settings(self.settings)
                self.status_var.set(tr("st_terminal_opened", n=len(picks_dicts)))
                dlg.destroy()
            except FileNotFoundError:
                messagebox.showerror(tr("mb_wt_not_found_title"),
                                     tr("mb_wt_not_found_full"), parent=dlg)
            except Exception as e:
                messagebox.showerror(tr("mb_launch_error"), str(e), parent=dlg)

        def _save_current() -> None:
            name = _state["loaded_preset"]
            if not name:
                # No preset loaded — fall back to "Save as".
                _save_as_preset()
                return
            new_preset = {
                "name": name,
                "projects": list(work_paths),
                "titles": {p: titles[p] for p in work_paths if p in titles},
                "fresh": mode_var.get() == "fresh",
                "auto_claude": bool(auto_var.get()),
            }
            out = [pr for pr in presets if pr.get("name") != name]
            out.append(new_preset)
            save_terminal_presets(out)
            presets.clear(); presets.extend(out)
            self.status_var.set(tr("st_preset_updated", name=name, n=len(work_paths)))
            _set_dirty(False)

        save_btn = ttk.Button(btns, text=tr("term_save"), command=_save_current)
        save_btn.pack(side="left")

        def _update_save_btn() -> None:
            # Enable only when a preset is loaded and there are unsaved changes.
            try:
                if _state.get("loaded_preset") and _state.get("dirty"):
                    save_btn.state(["!disabled"])
                else:
                    save_btn.state(["disabled"])
            except Exception:
                pass

        ttk.Button(btns, text=tr("term_save_as"), command=_save_as_preset).pack(side="left", padx=4)
        ttk.Button(btns, text=tr("term_delete_preset"), command=_delete_preset).pack(side="left", padx=4)

        def _confirm_close() -> None:
            if _state["dirty"]:
                ans = messagebox.askyesnocancel(
                    tr("term_unsaved_title"),
                    tr("term_unsaved_msg"),
                    parent=dlg,
                )
                if ans is None:
                    return  # Cancel close
                if ans:
                    _save_current()
            dlg.destroy()

        ttk.Button(btns, text=tr("term_open"), command=_launch).pack(side="right")
        ttk.Button(btns, text=tr("cancel"), command=_confirm_close).pack(side="right", padx=4)
        dlg.protocol("WM_DELETE_WINDOW", _confirm_close)
        _update_save_btn()

    def deepseek_analyze_selected(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        self.status_var.set(tr("st_ds_running", name=p["name"]))

        def worker():
            try:
                res = deepseek_analyze_project(Path(p["path"]))
            except Exception as e:
                self.after(0, lambda: (
                    self.status_var.set(tr("st_ds_error", err=e)),
                    messagebox.showerror(tr("ds_title"), str(e)),
                ))
                return
            p["short_desc"] = res.get("short", "")
            p["full_desc"] = res.get("full", "")
            p["stage"] = res.get("stage", "")
            p["stage_reason"] = res.get("stage_reason", "")
            p["analyzed_by"] = "deepseek"
            p["analyzed_at"] = time.time()
            p.pop("last_error", None)
            self.cache[p["path"]] = p
            save_cache(self.cache)
            self.after(0, lambda: (
                self.apply_filter(),
                self.tree.selection_set(p["path"]) if self.tree.exists(p["path"]) else None,
                self.on_select_project(None),  # type: ignore[arg-type]
                self.status_var.set(tr("st_ds_done_one", name=p["name"])),
            ))

        threading.Thread(target=worker, daemon=True).start()

    def regenerate_selected_description(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        folder = Path(p["path"])
        info = analyze_project(folder)
        self.cache[info["path"]] = info
        save_cache(self.cache)
        # update in-memory list
        for i, x in enumerate(self.projects):
            if x["path"] == info["path"]:
                self.projects[i] = info
                break
        self.apply_filter()
        # re-select
        if self.tree.exists(info["path"]):
            self.tree.selection_set(info["path"])
            self.tree.see(info["path"])
        self.on_select_project(None)  # type: ignore[arg-type]

    # ---- codex search ----
    def run_codex_search(self) -> None:
        q = self.codex_query.get().strip()
        if not q:
            return
        kws = [t for t in re.split(r"\s+", q) if t]
        match_all = self.codex_match_all.get()
        search_content = self.codex_search_content.get()
        self.codex_status.set(tr("codex_searching"))
        self.codex_tree.delete(*self.codex_tree.get_children())
        self.update_idletasks()

        def worker():
            t0 = time.time()
            results = search_codex(kws, match_all, search_content)
            dt = time.time() - t0
            def update():
                self.codex_results = results
                for r in results:
                    self.codex_tree.insert("", "end", values=(
                        r["title"], r["updated_at"][:19].replace("T", " "), r["match"], r["id"]
                    ))
                self.codex_status.set(tr("codex_found", n=len(results), t=f"{dt:.1f}"))
            self.after(0, update)

        threading.Thread(target=worker, daemon=True).start()

    def on_select_codex(self, _evt: tk.Event) -> None:
        sel = self.codex_tree.selection()
        if not sel:
            return
        idx = self.codex_tree.index(sel[0])
        if idx < 0 or idx >= len(self.codex_results):
            return
        r = self.codex_results[idx]
        path = r.get("path")
        if not path:
            # try to resolve session file from id
            f = find_session_file(r["id"])
            path = str(f) if f else ""
            r["path"] = path
        self.codex_preview_title.config(text=r["title"])
        self.codex_preview_path.config(text=f"ID: {r['id']}    |    {path or '(файл не найден)'}")
        self.codex_preview_text.configure(state="normal")
        self.codex_preview_text.delete("1.0", "end")
        if r.get("snippet"):
            self.codex_preview_text.insert("end", tr("codex_match_snippet") + "\n")
            self.codex_preview_text.insert("end", r["snippet"] + "\n\n")
        # Show first few user/assistant lines from the session
        if path and Path(path).is_file():
            try:
                lines_to_show = []
                with Path(path).open("r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f):
                        if i > 60:
                            break
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        # try to extract text-ish content
                        content = obj.get("content") if isinstance(obj, dict) else None
                        role = obj.get("role") if isinstance(obj, dict) else None
                        if isinstance(content, str):
                            txt = content
                        elif isinstance(content, list):
                            txt = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        else:
                            continue
                        if not txt.strip():
                            continue
                        snippet = re.sub(r"\s+", " ", txt)[:300]
                        lines_to_show.append(f"[{role or 'msg'}] {snippet}")
                        if len(lines_to_show) >= 10:
                            break
                if lines_to_show:
                    self.codex_preview_text.insert("end", tr("codex_msg_preview") + "\n")
                    self.codex_preview_text.insert("end", "\n".join(lines_to_show))
            except Exception as e:
                self.codex_preview_text.insert("end", "\n" + tr("codex_read_fail", err=e))
        self.codex_preview_text.configure(state="disabled")

    def open_selected_codex_file(self) -> None:
        sel = self.codex_tree.selection()
        if not sel:
            return
        idx = self.codex_tree.index(sel[0])
        r = self.codex_results[idx]
        path = r.get("path") or (str(find_session_file(r["id"])) if find_session_file(r["id"]) else "")
        if path and Path(path).is_file():
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))
        else:
            messagebox.showinfo(tr("codex_file_not_found_title"),
                                tr("codex_file_not_found", id=r["id"]))

    def copy_codex_id(self) -> None:
        sel = self.codex_tree.selection()
        if not sel:
            return
        idx = self.codex_tree.index(sel[0])
        r = self.codex_results[idx]
        self.clipboard_clear()
        self.clipboard_append(r["id"])
        self.status_var.set(tr("st_copied_id", id=r["id"]))

    # ---- DeepSeek scan ----
    def deepseek_scan_async(self, only_new: bool) -> None:
        if getattr(self, "_ds_thread", None) and self._ds_thread.is_alive():
            messagebox.showinfo(tr("ds_title"), tr("ds_in_progress"))
            return
        # Determine target list
        if only_new:
            targets = [p for p in self.projects if p.get("analyzed_by") != "deepseek" or not p.get("full_desc")]
        else:
            targets = list(self.projects)
        if not targets:
            messagebox.showinfo(tr("ds_title"), tr("ds_nothing"))
            return
        if not messagebox.askyesno(tr("ds_title"), tr("ds_confirm", n=len(targets))):
            return

        self._ds_cancel = threading.Event()
        self._ds_done = 0
        self._ds_failed = 0
        self._ds_total = len(targets)
        self.ds_cancel_btn.configure(state="normal")
        self.status_var.set(tr("st_ds_progress", cur=0, total=self._ds_total, done=0, failed=0))

        def task(p: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
            if self._ds_cancel.is_set():
                return p, None, "cancelled"
            try:
                res = deepseek_analyze_project(Path(p["path"]))
                return p, res, None
            except Exception as e:
                return p, None, str(e)

        def worker():
            with ThreadPoolExecutor(max_workers=DEEPSEEK_CONCURRENCY) as ex:
                futures = [ex.submit(task, p) for p in targets]
                for fut in futures:
                    if self._ds_cancel.is_set():
                        # Best-effort: we can't truly cancel in-flight HTTP, but we stop processing further results.
                        pass
                    try:
                        proj, res, err = fut.result()
                    except Exception as e:
                        proj, res, err = None, None, str(e)
                    if proj is not None and res is not None:
                        proj["short_desc"] = res.get("short", "") or proj.get("short_desc", "")
                        proj["full_desc"] = res.get("full", "") or proj.get("full_desc", "")
                        proj["stage"] = res.get("stage", "") or proj.get("stage", "")
                        proj["stage_reason"] = res.get("stage_reason", "") or proj.get("stage_reason", "")
                        proj["analyzed_by"] = "deepseek"
                        proj["analyzed_at"] = time.time()
                        self.cache[proj["path"]] = proj
                        self._ds_done += 1
                    else:
                        self._ds_failed += 1
                        if proj is not None:
                            proj.setdefault("last_error", err or "")
                    # periodic UI/cache update
                    if (self._ds_done + self._ds_failed) % 3 == 0 or (self._ds_done + self._ds_failed) == self._ds_total:
                        self.after(0, self._ds_progress_tick)
            # Save cache + final update
            save_cache(self.cache)
            self.after(0, self._ds_finish)

        self._ds_thread = threading.Thread(target=worker, daemon=True)
        self._ds_thread.start()

    def _ds_progress_tick(self) -> None:
        total = getattr(self, "_ds_total", 0)
        done = getattr(self, "_ds_done", 0)
        failed = getattr(self, "_ds_failed", 0)
        self.status_var.set(tr("st_ds_progress", cur=done + failed, total=total, done=done, failed=failed))
        # refresh table so users see live updates
        self.apply_filter()

    def _ds_finish(self) -> None:
        save_cache(self.cache)
        self.ds_cancel_btn.configure(state="disabled")
        total = getattr(self, "_ds_total", 0)
        done = getattr(self, "_ds_done", 0)
        failed = getattr(self, "_ds_failed", 0)
        cancelled = getattr(self, "_ds_cancel", None) and self._ds_cancel.is_set()
        msg = tr("st_ds_finished", done=done, total=total, failed=failed)
        if cancelled:
            msg += tr("st_ds_cancelled_suffix")
        self.status_var.set(msg)
        self.apply_filter()

    def deepseek_cancel(self) -> None:
        ev = getattr(self, "_ds_cancel", None)
        if ev:
            ev.set()
            self.status_var.set(tr("st_ds_cancelling"))

    # ---- create new project ----
    def create_new_project_dialog(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title(tr("new_proj_title"))
        dlg.transient(self)
        dlg.grab_set()
        dlg.minsize(560, 360)

        pad = {"padx": 12, "pady": 6}

        # 1. Pack action buttons FIRST at the bottom so they are never cut off.
        btns = ttk.Frame(dlg, padding=(12, 10))
        btns.pack(side="bottom", fill="x")
        # placeholders – real commands assigned below
        cancel_btn = ttk.Button(btns, text=tr("cancel"))
        cancel_btn.pack(side="right", padx=(6, 0))
        create_btn = ttk.Button(btns, text=tr("new_proj_create"))
        create_btn.pack(side="right")

        # 2. Body
        body = ttk.Frame(dlg)
        body.pack(side="top", fill="both", expand=True)

        ttk.Label(body, text=tr("new_proj_root", path=PROJECTS_ROOT), font=self.base_font).pack(anchor="w", **pad)

        row = ttk.Frame(body)
        row.pack(fill="x", **pad)
        ttk.Label(row, text=tr("new_proj_name"), font=self.base_font).pack(side="left")
        name_var = tk.StringVar()
        ent = ttk.Entry(row, textvariable=name_var)
        ent.pack(side="left", padx=8, fill="x", expand=True)
        ent.configure(font=self.base_font)
        ent.focus_set()

        ttk.Label(body, text=tr("new_proj_prompt"), font=self.base_font).pack(anchor="w", **pad)
        prompt_var = tk.StringVar()
        ent2 = ttk.Entry(body, textvariable=prompt_var)
        ent2.pack(fill="x", padx=12)
        ent2.configure(font=self.base_font)

        tool_var = tk.StringVar(value="claude")
        rowt = ttk.Frame(body)
        rowt.pack(fill="x", **pad)
        ttk.Label(rowt, text=tr("new_proj_launch"), font=self.base_font).pack(side="left")
        ttk.Radiobutton(rowt, text="Claude", variable=tool_var, value="claude").pack(side="left", padx=8)
        ttk.Radiobutton(rowt, text="Codex", variable=tool_var, value="codex").pack(side="left", padx=8)
        ttk.Radiobutton(rowt, text=tr("new_proj_no_launch"), variable=tool_var, value="none").pack(side="left", padx=8)

        err_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=err_var, foreground="#b00", font=self.base_font).pack(anchor="w", **pad)

        def do_create():
            name = name_var.get().strip()
            if not name:
                err_var.set(tr("new_proj_err_name"))
                return
            # validate name
            if re.search(r'[<>:"/\\|?*]', name) or name in {".", ".."}:
                err_var.set(tr("new_proj_err_chars"))
                return
            target = PROJECTS_ROOT / name
            if target.exists():
                err_var.set(tr("new_proj_err_exists"))
                return
            try:
                target.mkdir(parents=True, exist_ok=False)
            except Exception as e:
                err_var.set(tr("new_proj_err_create", err=e))
                return

            tool = tool_var.get()
            dlg.destroy()

            # Add to projects list (lightweight info)
            info = analyze_project(target)
            self.cache[info["path"]] = info
            save_cache(self.cache)
            self.projects.append(info)
            self.apply_filter()
            if self.tree.exists(info["path"]):
                self.tree.selection_set(info["path"])
                self.tree.see(info["path"])
                self.on_select_project(None)  # type: ignore[arg-type]

            if tool in ("claude", "codex"):
                self._launch_for_path(tool, target, prompt_var.get().strip())

        cancel_btn.configure(command=dlg.destroy)
        create_btn.configure(command=do_create)
        dlg.bind("<Return>", lambda e: do_create())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _launch_for_path(self, tool: str, folder: Path, extra_prompt: str = "") -> None:
        script = CLAUDE_SCRIPT if tool == "claude" else CODEX_SCRIPT
        if not script.is_file():
            messagebox.showerror(tr("mb_script_not_found"), str(script))
            return
        cmd = ["cmd", "/c", "start", "", "cmd", "/k", str(script), str(folder)]
        if extra_prompt:
            # extra prompt is forwarded to claude/codex as a final positional arg
            cmd.append(extra_prompt)
        try:
            subprocess.Popen(cmd, cwd=str(folder))
        except Exception as e:
            messagebox.showerror(tr("mb_launch_error"), str(e))

    def resume_in_codex(self) -> None:
        sel = self.codex_tree.selection()
        if not sel:
            return
        idx = self.codex_tree.index(sel[0])
        r = self.codex_results[idx]
        if not CODEX_SCRIPT.is_file():
            messagebox.showerror(tr("mb_script_not_found"), str(CODEX_SCRIPT))
            return
        try:
            # First arg "" tells the script to use the default base dir prompt path,
            # subsequent args are forwarded to codex itself.
            subprocess.Popen(
                ["cmd", "/c", "start", "", "cmd", "/k", str(CODEX_SCRIPT), str(PROJECTS_ROOT), "resume", r["id"]],
            )
        except Exception as e:
            messagebox.showerror(tr("error"), str(e))


    # =====================================================================
    # Per-project notes & tasks
    # =====================================================================

    def open_notes_dialog(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        entry = get_project_entry(self.tasks_db, p["path"])

        dlg = tk.Toplevel(self)
        dlg.title(tr("notes_dlg_title", name=p["name"]))
        dlg.geometry("700x500")
        dlg.transient(self); dlg.grab_set()

        warn = ttk.Label(
            dlg,
            text=tr("notes_warn"),
            foreground="#a05a00",
        )
        warn.pack(anchor="w", padx=10, pady=(8, 0))
        warn.configure(font=self.base_font)

        text = tk.Text(dlg, wrap="word", font=self.mono_font)
        text.pack(fill="both", expand=True, padx=10, pady=8)
        text.insert("1.0", entry.get("notes", ""))

        btns = ttk.Frame(dlg, padding=(10, 6))
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text=tr("close"), command=dlg.destroy).pack(side="right", padx=6)

        def do_save():
            entry["notes"] = text.get("1.0", "end-1c")
            save_tasks_db(self.tasks_db)
            self.status_var.set(tr("st_notes_saved", name=p["name"]))
        ttk.Button(btns, text=tr("term_save"), command=do_save).pack(side="right")
        ttk.Button(btns, text=tr("notes_copy_all"), command=lambda: (
            self.clipboard_clear(),
            self.clipboard_append(text.get("1.0", "end-1c")),
            self.status_var.set(tr("st_copied_clipboard")),
        )).pack(side="left")

        dlg.bind("<Control-s>", lambda e: do_save())

    def add_task_for_selected(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        self._open_task_editor(p, task=None)

    def _open_task_editor(self, project: dict[str, Any], task: dict[str, Any] | None) -> None:
        # If we got a dict without "id" — treat as a fresh task with prefilled fields.
        is_new = task is None or not task.get("id")
        entry = get_project_entry(self.tasks_db, project["path"])

        dlg = tk.Toplevel(self)
        dlg.title(tr("task_new_title" if is_new else "task_edit_title", name=project["name"]))
        # Open large by default — comfortable for voice dictation into Description.
        try:
            sw = self.winfo_screenwidth(); sh = self.winfo_screenheight()
            w = min(1200, int(sw * 0.7))
            h = min(900, int(sh * 0.85))
            x = (sw - w) // 2; y = (sh - h) // 2
            dlg.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            dlg.geometry("1100x820")
        dlg.minsize(720, 600)
        # NOTE: deliberately NOT modal (no grab_set) — внешние утилиты вроде WhisperWriter
        # вставляют текст через SendInput/clipboard, а grab иногда блокирует возврат фокуса.
        dlg.transient(self)
        # Track which text widget had focus last — for the "Paste from clipboard" fallback.
        self._last_text_widget = None
        def _track_focus(event):
            w = event.widget
            if isinstance(w, (tk.Entry, ttk.Entry, tk.Text)):
                self._last_text_widget = w
        dlg.bind_all("<FocusIn>", _track_focus, add="+")

        # Pack buttons first at the bottom so they're never clipped
        btns = ttk.Frame(dlg, padding=(12, 8))
        btns.pack(fill="x", side="bottom")

        # Voice input hint + manual-paste fallback
        hint_row = ttk.Frame(dlg)
        hint_row.pack(side="bottom", fill="x", padx=12, pady=(0, 2))
        ttk.Label(hint_row, text=tr("task_voice_hint"),
                  foreground="#666").pack(side="left")
        def paste_clipboard_into_last():
            target = self._last_text_widget
            try:
                txt = dlg.clipboard_get()
            except Exception:
                return
            if not txt:
                return
            if isinstance(target, tk.Text):
                try: target.insert("insert", txt); target.focus_set()
                except Exception: pass
            elif isinstance(target, (tk.Entry, ttk.Entry)):
                try:
                    target.insert("insert", txt); target.focus_set()
                except Exception:
                    pass
            else:
                # default to title field
                ent_title.insert("insert", txt); ent_title.focus_set()
        ttk.Button(hint_row, text=tr("task_paste_clipboard"),
                   command=paste_clipboard_into_last).pack(side="right")

        err = tk.StringVar()
        ttk.Label(dlg, textvariable=err, foreground="#b00").pack(anchor="w", padx=12, side="bottom")

        form = ttk.Frame(dlg)
        form.pack(fill="both", expand=True, padx=12, pady=8)
        form.columnconfigure(1, weight=1)

        def row(r: int, label: str, widget_factory):
            ttk.Label(form, text=label, anchor="w").grid(row=r, column=0, sticky="w", pady=4, padx=(0, 8))
            w = widget_factory(form)
            w.grid(row=r, column=1, sticky="ew", pady=4)
            return w

        title_var = tk.StringVar(value=(task or {}).get("title", ""))
        ent_title = row(0, tr("task_f_title"), lambda p: ttk.Entry(p, textvariable=title_var, font=self.base_font))
        # Force focus into the title field after dialog is actually mapped — needed for Win+H voice input.
        def _grab_focus():
            try:
                dlg.lift(); dlg.focus_force()
                ent_title.focus_set()
                ent_title.icursor("end")
            except Exception:
                pass
        dlg.after(120, _grab_focus)

        type_var = tk.StringVar(value=(task or {}).get("type", TASK_TYPE_DEFAULT))
        row(1, tr("task_f_type"), lambda p: ttk.Combobox(p, textvariable=type_var, values=TASK_TYPES, state="readonly"))

        status_var = tk.StringVar(value=(task or {}).get("status", "todo"))
        row(2, tr("task_f_status"), lambda p: ttk.Combobox(p, textvariable=status_var, values=["todo", "doing", "done"], state="readonly"))

        priority_var = tk.StringVar(value=(task or {}).get("priority", "обычный"))
        row(3, tr("task_f_priority"), lambda p: ttk.Combobox(p, textvariable=priority_var,
                                                    values=["низкий", "обычный", "высокий", "🔥 срочно"], state="readonly"))

        existing_tags = ", ".join((task or {}).get("tags", []) or [])
        tags_var = tk.StringVar(value=existing_tags)
        row(4, tr("task_f_tags"), lambda p: ttk.Entry(p, textvariable=tags_var, font=self.base_font))

        due_var = tk.StringVar(value=fmt_user_dt((task or {}).get("due_at")) if task and task.get("due_at") else "")
        row(5, tr("task_f_due"), lambda p: ttk.Entry(p, textvariable=due_var, font=self.base_font))
        ttk.Label(form, text=tr("task_f_due_hint"),
                  foreground="#777").grid(row=6, column=1, sticky="w", pady=(0, 4))

        rem_var = tk.StringVar(value=fmt_user_dt((task or {}).get("reminder_at")) if task and task.get("reminder_at") else "")
        row(7, tr("task_f_reminder"), lambda p: ttk.Entry(p, textvariable=rem_var, font=self.base_font))

        sys_rem_var = tk.BooleanVar(value=bool((task or {}).get("system_reminder", False)))
        ttk.Checkbutton(form, text=tr("task_f_sys_reminder"),
                        variable=sys_rem_var).grid(row=8, column=0, columnspan=2, sticky="w", pady=6)

        # Folder name for idea (auto-derived from title if empty)
        existing_folder = (task or {}).get("idea_folder", "")
        folder_var = tk.StringVar(value=existing_folder)
        row(9, tr("task_f_folder"), lambda p: ttk.Entry(p, textvariable=folder_var, font=self.base_font))
        ttk.Label(form, text=tr("task_f_folder_hint"),
                  foreground="#777").grid(row=10, column=1, sticky="w", pady=(0, 4))

        ttk.Label(form, text=tr("task_f_desc"), anchor="w").grid(row=11, column=0, sticky="nw", pady=(8, 0))
        desc_text = tk.Text(form, wrap="word", height=18, font=self.base_font)
        desc_text.grid(row=11, column=1, sticky="nsew", pady=(8, 0))
        form.rowconfigure(11, weight=1)
        desc_text.insert("1.0", (task or {}).get("desc", ""))

        def do_save():
            title = title_var.get().strip()
            if not title:
                err.set(tr("task_err_no_title")); return
            due_ts = parse_user_dt(due_var.get())
            rem_ts = parse_user_dt(rem_var.get())
            if due_var.get().strip() and due_ts is None:
                err.set(tr("task_err_due")); return
            if rem_var.get().strip() and rem_ts is None:
                err.set(tr("task_err_reminder")); return

            if is_new:
                base = {"id": new_task_id(), "created_at": time.time()}
                if isinstance(task, dict):
                    base.update(task)         # carry preset fields (type, status, …)
                base["id"] = base.get("id") or new_task_id()
                t = base
            else:
                t = task
            tags = [s.strip() for s in re.split(r"[,;]", tags_var.get()) if s.strip()]
            t.update({
                "title": title,
                "type": type_var.get(),
                "tags": tags,
                "status": status_var.get(),
                "priority": priority_var.get(),
                "due_at": due_ts,
                "reminder_at": rem_ts,
                "system_reminder": sys_rem_var.get(),
                "desc": desc_text.get("1.0", "end-1c"),
                "idea_folder": folder_var.get().strip(),
            })
            if status_var.get() == "done" and not t.get("done_at"):
                t["done_at"] = time.time()
            if status_var.get() != "done":
                t["done_at"] = None

            if is_new:
                entry["tasks"].append(t)
            else:
                for i, ex in enumerate(entry["tasks"]):
                    if ex.get("id") == t["id"]:
                        entry["tasks"][i] = t
                        break

            # Windows system reminder
            if sys_rem_var.get() and rem_ts:
                ok, msg = schedule_windows_reminder(title, project["name"], rem_ts, t["id"])
                if ok:
                    t["system_reminder_scheduled"] = True
                    self.status_var.set("Windows-напоминание: " + msg)
                else:
                    messagebox.showwarning("Windows-напоминание не создано", msg)
                    t["system_reminder_scheduled"] = False
            else:
                if not sys_rem_var.get() and t.get("system_reminder_scheduled"):
                    cancel_windows_reminder(t["id"])
                    t["system_reminder_scheduled"] = False

            save_tasks_db(self.tasks_db)
            self._refresh_tasks_tab()
            dlg.destroy()

        ttk.Button(btns, text=tr("cancel"), command=dlg.destroy).pack(side="right", padx=6)
        ttk.Button(btns, text=tr("term_save"), command=do_save).pack(side="right")

        def launch_idea(tool: str):
            # Save task data first so latest form contents persist
            do_save_result = do_save_silent()
            if not do_save_result:
                return
            current_task, current_project = do_save_result
            self._launch_idea_in_agent(current_project, current_task, tool, dlg)

        def do_save_silent():
            """Like do_save but does not destroy dialog. Returns (task, project) or None."""
            title = title_var.get().strip()
            if not title:
                err.set(tr("task_err_no_title_launch")); return None
            due_ts = parse_user_dt(due_var.get())
            rem_ts = parse_user_dt(rem_var.get())
            if due_var.get().strip() and due_ts is None:
                err.set(tr("task_err_due")); return None
            if rem_var.get().strip() and rem_ts is None:
                err.set(tr("task_err_reminder")); return None
            nonlocal is_new
            if is_new:
                base = {"id": new_task_id(), "created_at": time.time()}
                if isinstance(task, dict):
                    base.update(task)
                base["id"] = base.get("id") or new_task_id()
                local_t = base
            else:
                local_t = task
            tags = [s.strip() for s in re.split(r"[,;]", tags_var.get()) if s.strip()]
            local_t.update({
                "title": title,
                "type": type_var.get(),
                "tags": tags,
                "status": status_var.get(),
                "priority": priority_var.get(),
                "due_at": due_ts,
                "reminder_at": rem_ts,
                "system_reminder": sys_rem_var.get(),
                "desc": desc_text.get("1.0", "end-1c"),
                "idea_folder": folder_var.get().strip(),
            })
            if is_new:
                entry["tasks"].append(local_t)
                is_new = False  # so subsequent saves update instead of duplicate
            else:
                for i, ex in enumerate(entry["tasks"]):
                    if ex.get("id") == local_t["id"]:
                        entry["tasks"][i] = local_t
                        break
            save_tasks_db(self.tasks_db)
            self._refresh_tasks_tab()
            return local_t, project

        ttk.Button(btns, text=tr("task_test_claude"), command=lambda: launch_idea("claude")).pack(side="left", padx=2)
        ttk.Button(btns, text=tr("task_test_codex"), command=lambda: launch_idea("codex")).pack(side="left", padx=2)

        if not is_new:
            ttk.Button(btns, text=tr("tasks_delete"), command=lambda: self._delete_task(project, task, dlg)).pack(side="left", padx=(12, 0))

        dlg.bind("<Return>", lambda e: do_save())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ------------------------------------------------------------------
    # Запустить идею в Claude / Codex (создаёт папку и стартует агент)
    # ------------------------------------------------------------------
    def _launch_idea_in_agent(self, project: dict[str, Any], task: dict[str, Any],
                              tool: str, dlg: tk.Toplevel | None) -> None:
        # 1. Решаем имя папки
        existing_link = task.get("linked_project_path")
        if existing_link and Path(existing_link).is_dir():
            target = Path(existing_link)
            reused = True
        else:
            raw = (task.get("idea_folder") or "").strip()
            if not raw:
                raw = "idea-" + slugify(task.get("title", ""))
            else:
                raw = slugify(raw) or "idea"
                if not raw.startswith("idea-") and not raw.startswith("idea"):
                    raw = "idea-" + raw
            target = unique_folder(PROJECTS_ROOT, raw)
            reused = False

        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Не удалось создать папку", f"{target}\n\n{e}")
            return

        # 2. Готовим / обновляем IDEA.md
        idea_md = target / "IDEA.md"
        tags_str = ", ".join(task.get("tags") or []) or "—"
        body = (
            f"# {task.get('title', '(без названия)')}\n\n"
            f"_Создано через Project Manager: {datetime.now().strftime('%Y-%m-%d %H:%M')}_  \n"
            f"_Тип: {task.get('type', '')}   Статус: {task.get('status', '')}   "
            f"Приоритет: {task.get('priority', '')}   Теги: {tags_str}_\n\n"
            f"## Описание идеи\n\n{task.get('desc', '').strip() or '(описание не заполнено)'}\n\n"
            f"## Задача для AI-агента ({tool})\n\n"
            f"1. Проанализируй идею выше, задай уточняющие вопросы если что-то неясно.\n"
            f"2. Предложи 2–3 варианта технической реализации с MVP-приоритетом.\n"
            f"3. Опиши стек, ключевые риски и оценку трудозатрат (часы/дни).\n"
            f"4. Сгенерируй структуру проекта (файлы/папки).\n"
            f"5. Если идея жизнеспособна — собери минимальный прототип прямо в этой папке.\n\n"
            f"---\n"
            f"_Path: {target}_\n"
        )
        try:
            if reused and idea_md.is_file():
                # don't overwrite; append a session marker so агент видит, что задача продолжается
                with idea_md.open("a", encoding="utf-8") as f:
                    f.write(f"\n---\n## Возобновление: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            else:
                idea_md.write_text(body, encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Не удалось записать IDEA.md", str(e))
            return

        # 3. Выбираем скрипт
        if tool == "claude":
            script = CLAUDE_SCRIPT if reused else CLAUDE_FRESH_SCRIPT
        elif tool == "codex":
            script = CODEX_SCRIPT
        else:
            messagebox.showerror("Tool", f"Неизвестный инструмент: {tool}"); return
        if not script.is_file():
            messagebox.showerror("Скрипт не найден", str(script)); return

        prompt = (
            "Прочитай IDEA.md в этой папке и выполни задачу из раздела "
            "«Задача для AI-агента». Начни с короткого плана."
        )

        # 4. Запуск
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", "cmd", "/k", str(script), str(target), prompt],
                cwd=str(target),
            )
        except Exception as e:
            messagebox.showerror("Ошибка запуска", str(e))
            return

        # 5. Связь задачи с проектом
        task["linked_project_path"] = str(target)

        # Если задача жила в "(без проекта)" — переносим её в новый проект
        no_entry = self.tasks_db.get(NO_PROJECT_KEY)
        if project["path"] == NO_PROJECT_KEY and no_entry:
            no_entry["tasks"] = [t for t in no_entry.get("tasks", []) if t.get("id") != task.get("id")]
            target_entry = get_project_entry(self.tasks_db, str(target))
            target_entry["tasks"].append(task)
        save_tasks_db(self.tasks_db)

        # 6. Добавляем новый проект в self.projects / cache
        if not reused:
            info = analyze_project(target)
            self.cache[info["path"]] = info
            save_cache(self.cache)
            # remove existing if any (refresh case)
            self.projects = [p for p in self.projects if p["path"] != info["path"]]
            self.projects.append(info)

        self._refresh_tasks_tab()
        self.apply_filter()
        if self.tree.exists(str(target)):
            self.tree.selection_set(str(target))
            self.tree.see(str(target))

        action = "продолжена" if reused else "запущена"
        self.status_var.set(f"Идея {action} в {tool.capitalize()}: {target.name}")

        if dlg is not None:
            dlg.destroy()

    def _delete_task(self, project: dict[str, Any], task: dict[str, Any], dlg: tk.Toplevel | None) -> None:
        if not messagebox.askyesno(tr("task_delete_confirm_title"), task.get("title", "")):
            return
        entry = get_project_entry(self.tasks_db, project["path"])
        entry["tasks"] = [t for t in entry["tasks"] if t.get("id") != task.get("id")]
        if task.get("system_reminder_scheduled"):
            cancel_windows_reminder(task["id"])
        save_tasks_db(self.tasks_db)
        self._refresh_tasks_tab()
        if dlg:
            dlg.destroy()

    def show_project_tasks(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        # Switch to tasks tab, filter by this project
        self.tasks_filter_project.set(p["name"])
        self.notebook.select(self.tasks_tab)
        self._refresh_tasks_tab()

    def recalc_size_selected(self) -> None:
        p = self.get_selected_project()
        if not p:
            return
        self.status_var.set(tr("st_size_calc", name=p["name"]))
        def worker():
            try:
                size = compute_folder_size(Path(p["path"]))
            except Exception:
                size = -1
            p["size_bytes"] = size
            p["size_mtime"] = p.get("mtime", 0)
            self.cache[p["path"]] = p
            save_cache(self.cache)
            def update():
                if self.tree.exists(p["path"]):
                    vals = list(self.tree.item(p["path"], "values"))
                    vals[2] = fmt_size(size)
                    self.tree.item(p["path"], values=vals)
                self.status_var.set(tr("st_size_result", name=p["name"], size=fmt_size(size)))
            self.after(0, update)
        threading.Thread(target=worker, daemon=True).start()

    # =====================================================================
    # Tasks tab
    # =====================================================================

    def _build_tasks_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=tr("tab_tasks"))
        self.tasks_tab = tab

        bar = ttk.Frame(tab, padding=(8, 6))
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text=tr("tasks_status")).pack(side="left")
        self.tasks_filter_status = tk.StringVar(value="все, кроме done")
        ttk.Combobox(bar, textvariable=self.tasks_filter_status,
                     values=["все", "todo", "doing", "done", "все, кроме done"],
                     state="readonly", width=18).pack(side="left", padx=4)
        self.tasks_filter_status.trace_add("write", lambda *a: self._refresh_tasks_tab())

        ttk.Label(bar, text=tr("tasks_type")).pack(side="left", padx=(8, 0))
        self.tasks_filter_type = tk.StringVar(value="все")
        ttk.Combobox(bar, textvariable=self.tasks_filter_type,
                     values=["все"] + TASK_TYPES, state="readonly", width=18).pack(side="left", padx=4)
        self.tasks_filter_type.trace_add("write", lambda *a: self._refresh_tasks_tab())

        ttk.Label(bar, text=tr("tasks_tag")).pack(side="left", padx=(8, 0))
        self.tasks_filter_tag = tk.StringVar(value="")
        ent_tag = ttk.Entry(bar, textvariable=self.tasks_filter_tag, width=14)
        ent_tag.pack(side="left", padx=4); ent_tag.configure(font=self.base_font)
        self.tasks_filter_tag.trace_add("write", lambda *a: self._refresh_tasks_tab())
        self._font_followers.append(ent_tag)

        ttk.Label(bar, text=tr("tasks_project")).pack(side="left", padx=(8, 0))
        self.tasks_filter_project = tk.StringVar(value="")
        ent = ttk.Entry(bar, textvariable=self.tasks_filter_project, width=20)
        ent.pack(side="left", padx=4); ent.configure(font=self.base_font)
        self.tasks_filter_project.trace_add("write", lambda *a: self._refresh_tasks_tab())
        self._font_followers.append(ent)

        ttk.Button(bar, text="↻", command=self._refresh_tasks_tab, width=3).pack(side="left", padx=6)
        ttk.Button(bar, text=tr("tasks_reset"), command=lambda: (
            self.tasks_filter_status.set("все, кроме done"),
            self.tasks_filter_type.set("все"),
            self.tasks_filter_tag.set(""),
            self.tasks_filter_project.set(""),
        )).pack(side="left", padx=2)

        self.tasks_count = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.tasks_count).pack(side="right")

        bar2 = ttk.Frame(tab, padding=(8, 0))
        bar2.pack(side="top", fill="x")
        ttk.Button(bar2, text=tr("tasks_new"), command=self._new_task_via_picker).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("tasks_new_idea"), command=self._new_idea_quick).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("tasks_new_no_project"), command=self._new_task_no_project).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("tasks_mark_done"), command=self._mark_task_done).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("tasks_edit"), command=self._edit_selected_task).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("tasks_delete"), command=self._delete_selected_task).pack(side="left", padx=2)
        ttk.Button(bar2, text=tr("tasks_test_reminder"),
                   command=lambda: self._show_reminder(tr("reminder_test_title"),
                                                       tr("reminder_test_msg"), tr("dash"))).pack(side="left", padx=8)

        cols = ("type", "status", "priority", "project", "title", "tags", "due", "reminder", "sys")
        self.tasks_tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse")
        self.tasks_tree.heading("type", text=tr("tasks_col_type"))
        self.tasks_tree.heading("status", text=tr("tasks_col_status"))
        self.tasks_tree.heading("priority", text=tr("tasks_col_priority"))
        self.tasks_tree.heading("project", text=tr("tasks_col_project"))
        self.tasks_tree.heading("title", text=tr("tasks_col_title"))
        self.tasks_tree.heading("tags", text=tr("tasks_col_tags"))
        self.tasks_tree.heading("due", text=tr("tasks_col_due"))
        self.tasks_tree.heading("reminder", text=tr("tasks_col_reminder"))
        self.tasks_tree.heading("sys", text=tr("tasks_col_sys"))
        self.tasks_tree.column("type", width=130, anchor="w", stretch=False)
        self.tasks_tree.column("status", width=70, anchor="w", stretch=False)
        self.tasks_tree.column("priority", width=100, anchor="w", stretch=False)
        self.tasks_tree.column("project", width=180, anchor="w", stretch=False)
        self.tasks_tree.column("title", width=320, anchor="w")
        self.tasks_tree.column("tags", width=160, anchor="w", stretch=False)
        self.tasks_tree.column("due", width=130, anchor="w", stretch=False)
        self.tasks_tree.column("reminder", width=130, anchor="w", stretch=False)
        self.tasks_tree.column("sys", width=80, anchor="center", stretch=False)
        self.tasks_tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.tasks_tree.bind("<Double-1>", lambda e: self._edit_selected_task())

        # row colors by status
        self.tasks_tree.tag_configure("done", foreground="#888")
        self.tasks_tree.tag_configure("overdue", background="#ffd1d1")
        self.tasks_tree.tag_configure("today", background="#fff3a0")
        self.tasks_tree.tag_configure("doing", background="#e0f0ff")

        self._task_row_map: dict[str, tuple[str, str]] = {}  # iid -> (project_path, task_id)
        self.after(100, self._refresh_tasks_tab)

    def _iter_filtered_tasks(self) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
        status_filter = self.tasks_filter_status.get()
        type_filter = self.tasks_filter_type.get()
        tag_filter = self.tasks_filter_tag.get().strip().lower()
        proj_filter = self.tasks_filter_project.get().strip().lower()
        for path, entry in self.tasks_db.items():
            if path == NO_PROJECT_KEY:
                project = {"name": tr("no_project"), "path": path}
            else:
                project = self.cache.get(path) or {"name": Path(path).name, "path": path}
            if proj_filter and proj_filter not in (project.get("name") or "").lower() and proj_filter not in path.lower():
                continue
            for t in entry.get("tasks", []):
                st = t.get("status", "todo")
                if status_filter == "все":
                    pass
                elif status_filter == "все, кроме done":
                    if st == "done":
                        continue
                elif st != status_filter:
                    continue
                tp = t.get("type", TASK_TYPE_DEFAULT)
                if type_filter != "все" and tp != type_filter:
                    continue
                if tag_filter:
                    tags = [x.lower() for x in (t.get("tags") or [])]
                    if not any(tag_filter in x for x in tags):
                        continue
                yield project, t

    def _refresh_tasks_tab(self) -> None:
        if not hasattr(self, "tasks_tree"):
            return
        self.tasks_tree.delete(*self.tasks_tree.get_children())
        self._task_row_map.clear()

        rows = list(self._iter_filtered_tasks())
        # Sort: overdue first, then by due_at asc, then by priority, then by title
        prio_rank = {"🔥 срочно": 0, "высокий": 1, "обычный": 2, "низкий": 3}
        def sort_key(item):
            _, t = item
            due = t.get("due_at") or float("inf")
            return (t.get("status") == "done", due, prio_rank.get(t.get("priority", "обычный"), 2))
        rows.sort(key=sort_key)

        now = time.time()
        for project, t in rows:
            iid = f"{project['path']}|{t['id']}"
            self._task_row_map[iid] = (project["path"], t["id"])
            tags = []
            st = t.get("status", "todo")
            due = t.get("due_at")
            if st == "done":
                tags.append("done")
            elif due:
                if due < now:
                    tags.append("overdue")
                elif due - now < 86400:
                    tags.append("today")
            if st == "doing":
                tags.append("doing")

            sys_mark = "✓" if t.get("system_reminder_scheduled") else ""
            self.tasks_tree.insert("", "end", iid=iid, values=(
                t.get("type", TASK_TYPE_DEFAULT),
                st,
                t.get("priority", ""),
                project.get("name", ""),
                t.get("title", ""),
                ", ".join(t.get("tags") or []),
                fmt_user_dt(t.get("due_at")) if t.get("due_at") else "",
                fmt_user_dt(t.get("reminder_at")) if t.get("reminder_at") else "",
                sys_mark,
            ), tags=tuple(tags))
        self.tasks_count.set(tr("tasks_count", n=len(rows)))

    def _selected_task(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        sel = self.tasks_tree.selection()
        if not sel:
            return None
        info = self._task_row_map.get(sel[0])
        if not info:
            return None
        path, task_id = info
        if path == NO_PROJECT_KEY:
            project = {"name": tr("no_project"), "path": path}
        else:
            project = self.cache.get(path) or {"name": Path(path).name, "path": path}
        entry = self.tasks_db.get(path, {"tasks": []})
        for t in entry.get("tasks", []):
            if t.get("id") == task_id:
                return project, t
        return None

    def _edit_selected_task(self) -> None:
        sel = self._selected_task()
        if not sel:
            return
        project, task = sel
        self._open_task_editor(project, task)

    def _mark_task_done(self) -> None:
        sel = self._selected_task()
        if not sel:
            return
        _, task = sel
        task["status"] = "done"
        task["done_at"] = time.time()
        if task.get("system_reminder_scheduled"):
            cancel_windows_reminder(task["id"])
            task["system_reminder_scheduled"] = False
        save_tasks_db(self.tasks_db)
        self._refresh_tasks_tab()

    def _delete_selected_task(self) -> None:
        sel = self._selected_task()
        if not sel:
            return
        project, task = sel
        self._delete_task(project, task, None)

    def _new_task_no_project(self) -> None:
        self._open_task_editor({"name": tr("no_project"), "path": NO_PROJECT_KEY}, task=None)

    def _new_idea_quick(self) -> None:
        # Открывает редактор с предустановленным типом "💡 идея" и без проекта.
        preset = {"type": "💡 идея", "status": "todo", "priority": "обычный"}
        self._open_task_editor({"name": tr("no_project"), "path": NO_PROJECT_KEY}, task=preset)

    def _new_task_via_picker(self) -> None:
        # Pick a project via small dialog
        dlg = tk.Toplevel(self); dlg.title(tr("tasks_pick_title")); dlg.geometry("420x400")
        dlg.transient(self); dlg.grab_set()
        ttk.Label(dlg, text=tr("tasks_pick_label"), font=self.base_font).pack(anchor="w", padx=10, pady=6)

        var = tk.StringVar()
        ent = ttk.Entry(dlg, textvariable=var); ent.configure(font=self.base_font)
        ent.pack(fill="x", padx=10); ent.focus_set()

        lb = tk.Listbox(dlg, font=self.base_font)
        lb.pack(fill="both", expand=True, padx=10, pady=8)

        all_projects = sorted(self.projects, key=lambda p: p["name"].lower())
        # Synthetic "no project" entry always at the top
        no_project = {"name": tr("no_project"), "path": NO_PROJECT_KEY}

        def refresh_list(*_):
            q = var.get().strip().lower()
            lb.delete(0, "end")
            lb.insert("end", tr("no_project"))
            for p in all_projects:
                if not q or q in p["name"].lower():
                    lb.insert("end", p["name"])
        var.trace_add("write", refresh_list)
        refresh_list()

        def pick():
            sel = lb.curselection()
            if not sel:
                return
            name = lb.get(sel[0])
            if name == tr("no_project"):
                p = no_project
            else:
                p = next((x for x in all_projects if x["name"] == name), None)
            if not p:
                return
            dlg.destroy()
            self._open_task_editor(p, task=None)
        ttk.Button(dlg, text=tr("tasks_pick_btn"), command=pick).pack(side="bottom", pady=8)
        lb.bind("<Double-1>", lambda e: pick())
        dlg.bind("<Return>", lambda e: pick())

    # =====================================================================
    # In-app reminder poller
    # =====================================================================

    def _reminder_tick(self) -> None:
        try:
            now = time.time()
            for path, entry in self.tasks_db.items():
                for t in entry.get("tasks", []):
                    rem = t.get("reminder_at")
                    if not rem or t.get("status") == "done":
                        continue
                    key = f"{path}|{t['id']}|{int(rem)}"
                    if key in self._fired_reminders:
                        continue
                    if rem <= now and now - rem < 86400:  # don't fire if older than 1 day
                        if path == NO_PROJECT_KEY:
                            project_name = tr("no_project")
                        else:
                            project = self.cache.get(path) or {"name": Path(path).name}
                            project_name = project.get("name", "")
                        self._show_reminder(t.get("title", ""), t.get("desc", ""), project_name)
                        self._fired_reminders.add(key)
        except Exception as e:
            print("reminder tick error:", e)
        self.after(30_000, self._reminder_tick)

    def _show_reminder(self, title: str, desc: str, project_name: str) -> None:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        try:
            self.deiconify(); self.lift(); self.focus_force()
        except Exception:
            pass
        win = tk.Toplevel(self)
        win.title(tr("reminder_title"))
        win.geometry("520x300")
        win.attributes("-topmost", True)
        ttk.Label(win, text=f"🔔 {title}", font=self.heading_font).pack(anchor="w", padx=14, pady=(14, 4))
        ttk.Label(win, text=tr("reminder_project", name=project_name)).pack(anchor="w", padx=14)
        ttk.Separator(win, orient="horizontal").pack(fill="x", padx=14, pady=8)
        t = tk.Text(win, wrap="word", height=8, font=self.base_font)
        t.pack(fill="both", expand=True, padx=14, pady=4)
        t.insert("1.0", desc or tr("reminder_no_desc"))
        t.configure(state="disabled")
        ttk.Button(win, text=tr("ok"), command=win.destroy).pack(side="bottom", pady=10)


def main() -> None:
    app = ProjectManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
