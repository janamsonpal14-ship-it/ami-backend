# AMICO — Chambers Office Management System

Private, local-first legal practice management system built with **FastAPI + SQLite (WAL)** in `Back end/` and **Flutter (macOS / iOS)** in `front end/`.

---

## 1. Quick Start (macOS Laptop)

### Terminal 1 — Start the Local Backend Server
```zsh
cd "/Users/janamsonpal/Desktop/Back end"
./run_server.sh
```
For connecting your **iPhone or iPad** over the same private Wi-Fi network, run:
```zsh
cd "/Users/janamsonpal/Desktop/Back end"
./run_server.sh --lan
```
To stop the backend server safely, press `Ctrl + C` in Terminal 1.

### Terminal 2 — Run the Desktop App (macOS)
```zsh
cd "/Users/janamsonpal/Desktop/front end"
/Users/janamsonpal/development/flutter/bin/flutter run -d macos
```

---

## 2. Running on iPhone / iPad
1. Install **Xcode** from the Mac App Store, open it once to accept the license, then run:
   ```zsh
   sudo xcodebuild -runFirstLaunch
   ```
2. Connect your iPhone or iPad via USB and tap **Trust This Computer**, and enable **Developer Mode** on the device under **Settings -> Privacy & Security -> Developer Mode**.
3. Open `front end/ios/Runner.xcworkspace` in Xcode once to select your **Apple ID** under **Signing & Capabilities**.
4. Start the backend with `./run_server.sh --lan`, then run:
   ```zsh
   cd "/Users/janamsonpal/Desktop/front end"
   /Users/janamsonpal/development/flutter/bin/flutter run
   ```
5. On the iPhone/iPad login screen, click **Server Settings** and enter `http://<YOUR_MAC_IP>:8000` (as printed by `./run_server.sh --lan`).

---

## 3. Demo Credentials (Synthetic Data Only)
- **Login ID:** `demo_advocate`
- **Password:** `DemoPassword@2026`

All preloaded clients, matters, hearings, and memos are fictional Mumbai demo records (`is_demo=True`).

---

## 4. Where Your Data Lives & Two-Way Backups
- **Database:** `Back end/data/amico.db` (SQLite WAL mode)
- **Uploaded Files:** `Back end/data/uploads/` (`letterheads/`, `bhc_calendars/`, `bare_acts/`)
- **Automatic Snapshots:** `Back end/data/snapshots/` (created automatically before BHC PDF imports, Excel imports, Demo resets, and Backup restores)

### Recommended Weekly Backup Routine
1. Open the **Settings (gear icon) -> Backup & Snapshots** tab.
2. Click **Export Full Backup (.zip)** and save the `.zip` archive to an encrypted external drive or private backup folder.
3. To restore on the same or a new Mac, open **Settings -> Backup & Snapshots -> Restore Backup (.zip)** (SHA-256 checksums and `PRAGMA integrity_check` are verified automatically before restoring, after taking a safety snapshot of current data).

---

## 5. Security Readiness Checklist Before Real Client Data
Open **Settings -> Security & Users** and ensure every item is green:
1. Run **Start Real Practice (Clear Demo)** (type `START FRESH`), keep your BHC calendar holidays & uploaded letterheads if desired, and create your private Admin Login ID + strong password (this automatically retires `demo_advocate`).
2. Keep macOS **FileVault** full-disk encryption turned on.
3. Only use `--lan` mode over your private WPA2/WPA3 home or office Wi-Fi (or over a private WireGuard/Tailscale tunnel with HTTPS when away from chambers).
4. **Bare Acts & Strict Local Legal Search (`Law` tab):**
   - Upload verified Bare Acts (`.pdf` or `.txt`) or add text statutes in **Law -> Bare Acts** for local section-level indexing and snippet search.
   - **Legal Research** runs offline against your local Bare Acts by default and never accesses private client, matter, hearing, or fee memo tables. Admin may optionally opt in to an external OpenAI-compatible or Gemini API key inside **Law -> Legal Research -> Configure AI Provider** (API keys are masked in status responses and daily query budgets are enforced).

---

## 6. Automated Test Commands
- **Backend (`pytest`):**
  ```zsh
  cd "/Users/janamsonpal/Desktop/Back end"
  .venv/bin/pytest -v
  ```
- **Frontend (`flutter test`):**
  ```zsh
  cd "/Users/janamsonpal/Desktop/front end"
  /Users/janamsonpal/development/flutter/bin/flutter test
  ```
