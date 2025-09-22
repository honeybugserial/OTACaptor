# OTA CAPTOR

Capture Google/Onn OTA URLs from `adb logcat`, optionally trigger a fresh update check, and download the resulting OTA ZIP (e.g., for Onn 4K Box YOC).

## What It Does
- Tails multi-buffer logcat and extracts OTA URLs like:  
  `https://android.googleapis.com/packages/ota-api/package/<HASH>.zip`  
  (and any `payload.bin` / `payload.zip`).
- One-shot mode: set up logs → open System Update → wait for first URL → prompt to download → exit.

## Requirements
- Python 3.8+ and `adb` on PATH (or pass `--adb "C:\path\to\adb.exe"`).
- Device connected (USB or `adb connect <ip>:5555`).

## Usage

**All-in-one (prompts to download when it sees the first OTA URL):**
```bash
python onn_ota_captor.py oneshot
```

**Same, but auto-download without prompt:**
```bash
python onn_ota_captor.py oneshot --auto-download
```

**Split workflow:**
```bash
python onn_ota_captor.py capture
python onn_ota_captor.py probe
python onn_ota_captor.py download-latest
```

## Output
- `ota_raw_YYYYmmdd_HHMMSS.log` — full logcat
- `ota_urls_YYYYmmdd_HHMMSS.txt` — deduped OTA/payload URLs
- Downloaded file: `ota_<hash>.zip` (or `update.zip` if no hash match)
