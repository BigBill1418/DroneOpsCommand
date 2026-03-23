# DroneOpsSync — Build & Deploy Guide

Companion app for DroneOps Command. Syncs DJI flight logs from your controller
or phone directly to your server over LAN — no browser file picker needed.

Uses a native file scanner (java.io.File) for direct filesystem access.
No SAF, no MANAGE_EXTERNAL_STORAGE — just reads the files.

---

## Prerequisites (one-time setup)

### 1. Install JDK 17

```bash
# Ubuntu/Debian
sudo apt install openjdk-17-jdk

# macOS (Homebrew)
brew install openjdk@17

# Windows — download from https://adoptium.net/
```

Verify: `java -version` should show 17.x.

### 2. Install Android SDK (command-line tools only — NO Android Studio needed)

```bash
# Create SDK directory
mkdir -p ~/android-sdk/cmdline-tools

# Download command-line tools (Linux — swap URL for macOS/Windows)
cd /tmp
wget https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip commandlinetools-linux-11076708_latest.zip
mv cmdline-tools ~/android-sdk/cmdline-tools/latest

# Set environment variables (add to ~/.bashrc or ~/.zshrc)
export ANDROID_HOME=~/android-sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools

# Accept licenses and install required SDK components
sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-33" "build-tools;33.0.2"
```

### 3. Install Node.js (18+)

If you already run DroneOps Command, you have this. Otherwise:
```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install nodejs
```

---

## Build the APK

### Step 1: Install dependencies

```bash
cd companion/
npm install
```

### Step 2: Update your server URL (optional)

Edit `src/sync.ts` and change `DEFAULT_SERVER_URL` to your actual server:

```typescript
export const DEFAULT_SERVER_URL = 'http://192.168.1.50:3080';
```

You can also change this in the app after install.

### Step 3: Build

```bash
npm run cap:build
```

This runs: build web app → copy to Android → patch manifest → assemble APK.

**Your APK is at:**
```
android/app/build/outputs/apk/debug/app-debug.apk
```

### Manual build steps (if npm script fails)

```bash
npm run build                          # Build React app → dist/
npx cap copy android                   # Copy dist/ into Android project
npx cap sync android                   # Sync Capacitor plugins
node scripts/patch-android.cjs         # Patch targetSdk, permissions, cleartext
cd android && ./gradlew assembleDebug  # Build APK
```

---

## Deploy to your device

### Option A: USB cable (fastest)

```bash
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

### Option B: File transfer

1. Copy `app-debug.apk` to your device (USB, email, etc.)
2. Open the APK on the device
3. Tap "Install" (allow "Install from unknown sources" if prompted)

### Option C: ADB over Wi-Fi (DJI RC Pro)

```bash
# On the RC Pro, enable Developer Options → USB Debugging → ADB over network
adb connect 192.168.x.x:5555
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

---

## First run setup

1. Open **DroneOpsSync** on your device
2. Enter your **Server URL** (LAN IP + port of your DroneOpsCommand server)
3. Go to DroneOps Command → **Settings → Device Access** and generate an API key
4. Paste the key into the app
5. Tap **TEST CONNECTION** to verify
6. Tap **SAVE & START SYNC**

---

## How it works

1. **Scans** DJI log directories using native `java.io.File` API:
   - `DJI/com.dji.industry.pilot/FlightRecord/` (DJI Pilot — 4TD, M30T)
   - `DJI/com.dji.industry.pilot2/FlightRecord/` (DJI Pilot 2)
   - `Android/data/dji.go.v5/files/FlightRecord/` (DJI Fly — M3P, M5P, phones)
   - `Android/data/dji.go.v4/files/FlightRecord/` (DJI Fly v1)

2. **Uploads** new log files via `/api/flight-library/device-upload`

3. **Tracks** synced files locally to avoid re-uploading duplicates

4. **Cleans up** (optional) — deletes synced logs from device

---

## Diagnostic mode

If syncing doesn't work, go to **Settings → RUN DIAGNOSTIC**. This checks:
- Android SDK version
- Storage root path
- Whether each DJI log path exists and is readable

This helps identify if the issue is file access (permission problem) vs network
(can't reach server) vs data (no log files on device).

---

## Compatible devices

| Device | Android | Status |
|--------|---------|--------|
| DJI RC Pro | 10 (API 29) | Full support |
| DJI RC 2 | 10 (API 29) | Full support (if sideloading enabled) |
| DJI RC Pro Enterprise | 10 (API 29) | Full support |
| DJI Smart Controller | 9 (API 28) | Full support |
| Any Android phone | 6+ (API 23+) | Full support |
| DJI RC-N1/N2 | N/A | No screen — use connected phone |

---

## Troubleshooting

**"Storage permission denied"**
→ Go to device Settings → Apps → DroneOpsSync → Permissions → Storage → Allow

**"Invalid or revoked API key"**
→ Generate a new key in DroneOps Command Settings → Device Access

**"flight-parser service unavailable"**
→ The parser container isn't running. On your server: `docker compose up -d flight-parser`

**Diagnostic shows paths as "NOT FOUND"**
→ Normal if that DJI app isn't installed. Only the paths matching your drone/app matter.

**Diagnostic shows paths as "DENIED"**
→ Storage permission not granted, or targetSdkVersion wasn't patched to 29.
  Rebuild with: `node scripts/patch-android.cjs && cd android && ./gradlew assembleDebug`

**Build fails with "SDK not found"**
→ Set `ANDROID_HOME`: `export ANDROID_HOME=~/android-sdk`
