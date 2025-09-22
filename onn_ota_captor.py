#!/usr/bin/env python3
"""
onn_ota_captor.py
=================
Capture Google/Onn OTA URLs from `adb logcat`, optionally trigger a fresh update check,
and download the resulting OTA ZIP (e.g., for Onn 4K Box YOC). Works on Win/macOS/Linux.

WHAT IT DOES
------------
- Tails multi-buffer logcat, extracts OTA URLs like:
    https://android.googleapis.com/packages/ota-api/package/<HASH>.zip
  (and any `payload.bin` / `payload.zip`).
- One-shot mode: set up logs → open System Update → wait for first URL → prompt to download → exit.

REQUIREMENTS
------------
- Python 3.8+ and `adb` on PATH (or `--adb "C:\\path\\to\\adb.exe"`).
- Device connected (USB or `adb connect <ip>:5555`).

USAGE
-----
# All-in-one, prompts to download when it sees the first OTA URL:
python onn_ota_captor.py oneshot

# Same but auto-download without prompt:
python onn_ota_captor.py oneshot --auto-download

# Split workflow:
python onn_ota_captor.py capture
python onn_ota_captor.py probe
python onn_ota_captor.py download-latest

OUTPUT
------
- ota_raw_YYYYmmdd_HHMMSS.log   — full logcat
- ota_urls_YYYYmmdd_HHMMSS.txt  — deduped OTA/payload URLs
- Downloaded file: ota_<hash>.zip (or update.zip if no hash match)

"""
from rich.console import Console
from rich.spinner import Spinner
from time import sleep
from datetime import datetime
import os
import pyfiglet
import argparse, datetime as _dt, os, re, shutil, subprocess, sys, threading, queue, urllib.request

# === Splash Variables ===
title = "OTA CAPTOR"
ascii_font = "modular"
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
sleep_time = 5

def splash_screen(title, ascii_font, timestamp, sleep_time=5):
    os.system('cls' if os.name == 'nt' else 'clear')

    # Title Rule
    console.rule(f"[bold cyan]{title}[/bold cyan]")

    # Generate ASCII art from title (or any string)
    ascii_art = pyfiglet.figlet_format(title, font=ascii_font)
    console.print(ascii_art, style="bold green")

    # Timestamp and Launch Rule
    console.print(f"[dim]Started at: {timestamp}[/]\n")
    console.rule(f"[bold cyan] LAUNCHING [/bold cyan]")

    # Spinner Delay
    with console.status("[bold yellow]Loading...[/]", spinner="dots"):
        sleep(sleep_time)

def clear_console():
    try:
        console.clear()
        return
    except Exception:
        pass

    import os, sys
    os.system('cls' if sys.platform.startswith('win') else 'clear')
    
# ---------- Optional Rich TUI ----------
try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    _RICH = True
    console = Console()
except Exception:
    _RICH = False
    class _Plain:
        def print(self, *a, **k): print(*a)
        def rule(self, *a, **k): print("="*78)
    console = _Plain()

def info(m): console.print(f"[bold cyan][*][/bold cyan] {m}" if _RICH else f"[*] {m}")
def ok(m):   console.print(f"[bold green][+][/bold green] {m}" if _RICH else f"[+] {m}")
def warn(m): console.print(f"[bold yellow][!][/bold yellow] {m}" if _RICH else f"[!] {m}")
def err(m):  console.print(f"[bold red][-][/bold red] {m}" if _RICH else f"[-] {m}")

# ---------- Regex (stops at .zip/.bin; excludes trailing bracket/quote) ----------
URL_REGEX = re.compile(
    r'(https?://[^\s\]]*?(?:packages/ota-api/package/[A-Fa-f0-9]+\.zip|payload\.(?:bin|zip))(?:\?[^\s\]]*)?)'
)

TAGS_TO_VERBOSE = ["UpdateEngine","update_engine","UpdateEngineDaemon","DownloadManager","cronet","NetworkDownloader"]

def ts(): return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

def ensure_adb(adb_path: str) -> str:
    info("Resolving adb executable…")
    if adb_path and os.path.isfile(adb_path): return adb_path
    resolved = shutil.which("adb")
    if not resolved: raise FileNotFoundError("adb not found in PATH; install platform-tools or pass --adb")
    return resolved

def run(adb: str, args, check=True):
    cmd = [adb] + (args if isinstance(args, list) else [args])
    res = subprocess.run(cmd)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed ({res.returncode}): {' '.join(cmd)}")
    return res

def set_verbose_tags(adb: str):
    info("Setting log tags to VERBOSE (best-effort)…")
    for t in TAGS_TO_VERBOSE:
        try: run(adb, ["shell","setprop",f"log.tag.{t}","VERBOSE"], check=False)
        except Exception: pass

def clear_logcat(adb: str):
    info("Clearing logcat buffers…")
    subprocess.run([adb,"logcat","-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def open_system_update_ui(adb: str):
    info("Opening System Update screen on device…")
    run(adb, ["shell","am","start","-a","android.settings.SYSTEM_UPDATE_SETTINGS"], check=False)

def nudge_jobs(adb: str):
    info("Nudging GMS/Framework jobs to trigger update checks…")
    for c in (
        ["shell","pm","clear","com.google.android.gms"],
        ["shell","cmd","jobscheduler","run","-f","com.google.android.tv.framework","999"],
        ["shell","cmd","jobscheduler","run","-f","com.google.android.gms","999"],
        ["shell","cmd","jobscheduler","run","-f","com.google.android.gms.update","999"],
    ):
        run(adb, c, check=False)

def sanitize_url(u: str) -> str:
    return u.strip().strip("]'\"")

def friendly_name_from_url(url: str) -> str:
    m = re.search(r'/package/([A-Fa-f0-9]+)\.zip(?:\?|$)', url)
    if m: return f"ota_{m.group(1)}.zip"
    tail = url.rstrip('/').split('/')[-1] or "update.zip"
    return tail if tail.endswith(".zip") else "update.zip"

def download_with_progress(url: str, out_path: str):
    url = sanitize_url(url)
    ok(f"Downloading {url} -> {out_path}")
    try:
        with urllib.request.urlopen(url) as resp:
            total = resp.length or 0
            chunk = 1024 * 256
            if _RICH and total:
                with Progress(SpinnerColumn(), BarColumn(), TextColumn("{task.percentage:>3.0f}%"),
                              TimeElapsedColumn(), console=console) as prog:
                    t = prog.add_task("download", total=total)
                    with open(out_path, "wb") as f:
                        while True:
                            buf = resp.read(chunk)
                            if not buf: break
                            f.write(buf); prog.update(t, advance=len(buf))
            else:
                with open(out_path, "wb") as f:
                    while True:
                        buf = resp.read(chunk)
                        if not buf: break
                        f.write(buf)
        ok(f"Saved: {out_path}")
    except Exception as e:
        err(f"Download failed: {e}")

# ---------- Capture ----------
import threading, queue, subprocess

class OneShotController:
    def __init__(self):
        self.first_url = None
        self.hit_event = threading.Event()
    def on_new_url(self, url: str):
        url = sanitize_url(url)
        if not self.hit_event.is_set():
            self.first_url = url
            self.hit_event.set()

class OtaCaptor:
    def __init__(self, adb: str, auto_download: bool = False, on_new_url=None):
        self.adb = adb
        self.auto_download = auto_download
        self.on_new_url = on_new_url
        self._stamp = ts()
        self.log_file = f"ota_raw_{self._stamp}.log"
        self.url_file = f"ota_urls_{self._stamp}.txt"
        self._seen = set()
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self.proc = None

    def start(self):
        set_verbose_tags(self.adb)
        clear_logcat(self.adb)
        cmd = [self.adb,"logcat","-b","main","-b","system","-b","events","-b","radio","-v","time"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
        # threads
        threading.Thread(target=self._writer_thread, daemon=True).start()
        threading.Thread(target=self._parser_thread, daemon=True).start()
        ok(f"Capturing logs -> {self.log_file}"); console.rule("LOG CAPTURE STARTED") if _RICH else None
        ok(f"Extracted URLs -> {self.url_file}")
        info("Press Ctrl+C to stop.")
        try:
            while self.proc.poll() is None:
                b = self.proc.stdout.readline()
                if not b: break
                try: line = b.decode("utf-8","replace")
                except Exception: line = b.decode(errors="replace")
                self._queue.put(line)
        except KeyboardInterrupt:
            warn("Interrupted")
        finally:
            self.stop()

    def stop(self):
        if not self._stop.is_set(): self._stop.set()
        try:
            if self.proc and self.proc.poll() is None: self.proc.terminate()
        except Exception: pass

    def _writer_thread(self):
        with open(self.log_file,"a",encoding="utf-8",errors="replace") as f:
            while not self._stop.is_set():
                try: line = self._queue.get(timeout=0.2)
                except queue.Empty: continue
                f.write(line); f.flush()

    def _parser_thread(self):
        with open(self.url_file,"a",encoding="utf-8") as outf:
            while not self._stop.is_set():
                try: line = self._queue.get(timeout=0.2)
                except queue.Empty: continue
                self._queue.put(line)  # also let writer handle it
                for m in URL_REGEX.finditer(line):
                    url = sanitize_url(m.group(1))
                    if url not in self._seen:
                        self._seen.add(url)
                        outf.write(url + "\n"); outf.flush()
                        ok(f"NEW OTA URL: {url}")
                        if self.on_new_url:
                            try: self.on_new_url(url)
                            except Exception: pass
                        if self.auto_download:
                            name = friendly_name_from_url(url)
                            download_with_progress(url, name)

# ---------- CLI ----------
def cmd_capture(args):
    adb = ensure_adb(args.adb)
    OtaCaptor(adb, auto_download=args.auto_download).start()

def cmd_probe(args):
    adb = ensure_adb(args.adb)
    set_verbose_tags(adb); nudge_jobs(adb); open_system_update_ui(adb)
    info("On the TV, press 'Check for updates'. Watch your capture session for a URL.")

def cmd_download_latest(args):
    files = [f for f in os.listdir(".") if f.startswith("ota_urls_") and f.endswith(".txt")]
    if not files: return warn("No ota_urls_*.txt found.")
    latest = max(files, key=os.path.getmtime)
    lines = [ln.strip() for ln in open(latest,"r",encoding="utf-8") if ln.strip()]
    if not lines: return warn(f"URL list empty: {latest}")
    url = sanitize_url(lines[-1])
    out = friendly_name_from_url(url)
    download_with_progress(url, out)

def cmd_oneshot(args):
    adb = ensure_adb(args.adb)
    controller = OneShotController()
    cap = OtaCaptor(adb, auto_download=False, on_new_url=controller.on_new_url)
    t = threading.Thread(target=cap.start, daemon=True); t.start()
    nudge_jobs(adb); clear_logcat(adb); open_system_update_ui(adb)
    info("On the TV, press 'Check for updates' now.")
    timeout = args.timeout if args.timeout > 0 else 300
    info(f"Waiting up to {timeout} seconds for the first OTA URL…")
    if not controller.hit_event.wait(timeout=timeout):
        warn("No OTA URL captured within timeout."); cap.stop(); return
    ok(f"First OTA URL: {controller.first_url}")
    if args.auto_download:
        name = friendly_name_from_url(controller.first_url)
        download_with_progress(controller.first_url, name)
    else:
        try: choice = input("Download now? [Y/n]: ").strip().lower()
        except Exception: choice = "y"
        if choice in ("","y","yes"):
            name = friendly_name_from_url(controller.first_url)
            download_with_progress(controller.first_url, name)
    cap.stop()

def main():
    clear_console()
    splash_screen(title, ascii_font, timestamp, sleep_time)
    ap = argparse.ArgumentParser(description="Capture OTA URLs from adb logcat, probe updates, download ZIPs.")
    ap.add_argument("--adb", help="Path to adb (default: from PATH)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("capture", help="Tail logcat, extract OTA URLs, optionally auto-download")
    p.add_argument("--auto-download", action="store_true"); p.set_defaults(func=cmd_capture)

    p = sub.add_parser("probe", help="Nudge services and open System Update UI")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("download-latest", help="Download the last URL from the newest ota_urls_*.txt")
    p.set_defaults(func=cmd_download_latest)

    p = sub.add_parser("oneshot", help="Capture + probe; exit after first URL (optionally auto-download)")
    p.add_argument("--auto-download", action="store_true")
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(func=cmd_oneshot)

    args = ap.parse_args()
    try: args.func(args)
    except KeyboardInterrupt: warn("Interrupted")
    except Exception as e: err(f"Error: {e}"); sys.exit(1)

if __name__ == "__main__":
    main()
