# DroneOpsSync — Build & Deploy Guide

Companion app for DroneOps Command. Syncs DJI flight logs from your controller
or phone directly to your server — no browser file picker needed.

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

### Step 2: Update your server URL

Edit `src/sync.ts` and change `DEFAULT_SERVER_URL` to your actual DroneOps Command URL:

```typescript
export const DEFAULT_SERVER_URL = 'https://your-actual-domain.com';
```

This is the URL you access DroneOps Command at (your Cloudflare tunnel URL or LAN IP).

### Step 3: Build the web app

```bash
npm run build
```

This creates the `dist/` folder with the compiled React app.

### Step 4: Initialize the Android project (first time only)

```bash
npx cap add android
```

### Step 5: Patch the Android manifest for DJI RC Pro

```bash
bash scripts/patch-manifest.sh
```

This adds the storage permissions and `requestLegacyExternalStorage="true"` needed
for Android 10 (DJI RC Pro, RC 2, etc.).

### Step 6: Copy web assets into Android project

```bash
npx cap copy android
npx cap sync android
```

### Step 7: Build the APK

```bash
cd android
./gradlew assembleDebug
```

First build downloads Gradle dependencies (~2-5 min). Subsequent builds are fast (~15 sec).

**Your APK is at:**
```
android/app/build/outputs/apk/debug/app-debug.apk
```

### Quick rebuild (after code changes)

```bash
npm run build && npx cap copy android && cd android && ./gradlew assembleDebug && cd ..
```

Or use the npm script:
```bash
npm run cap:build
```

---

## Deploy to your device

### Option A: USB cable (fastest)

```bash
# With device connected via USB and USB debugging enabled:
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

### Option B: File transfer

1. Copy `app-debug.apk` to your device (USB, email, cloud drive, etc.)
2. Open the APK on the device
3. Tap "Install" (you may need to allow "Install from unknown sources")

### Option C: ADB over Wi-Fi (DJI RC Pro)

```bash
# On the RC Pro, enable Developer Options → USB Debugging → ADB over network
# Note the IP address shown

adb connect 192.168.x.x:5555
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

---

## First run setup

1. Open **DroneOpsSync** on your device
2. The app shows the **FIRST TIME SETUP** screen
3. Enter your **Server URL** (pre-filled with default — verify it's correct)
4. Go to DroneOps Command → **Settings → Device Access**
5. Generate a new API key with a label like "RC Pro" or "Field Phone"
6. Copy the key and paste it into the app's **Device API Key** field
7. Tap **TEST CONNECTION** to verify
8. Enable **Auto-delete after sync** if you want logs removed after upload
9. Tap **SAVE & START SYNC**

The app will immediately scan your DJI flight log folders, upload any new logs,
and tell you exactly how many new flights were added to DroneOps Command.

---

## How it works

1. **Scans** these DJI log directories on the device:
   - `DJI/dji.go.v5/FlightRecord/` (DJI Fly v2)
   - `DJI/dji.go.v4/FlightRecord/` (DJI Fly v1)
   - `DJI/com.dji.industry.pilot/FlightRecord/` (DJI Pilot)
   - `DJI/com.dji.industry.pilot2/FlightRecord/` (DJI Pilot 2)

2. **Uploads** new log files to `/api/flight-library/device-upload` using your API key
   - Duplicates are automatically detected by file hash and skipped
   - Files are uploaded in batches of 5

3. **Verifies** the upload response — shows imported count, skipped duplicates, any errors

4. **Cleans up** (optional) — deletes synced log files from the controller to free storage

---

## Compatible devices

| Device | Android | Status |
|--------|---------|--------|
| DJI RC Pro | 10 (API 29) | Full support |
| DJI RC 2 | 10 (API 29) | Full support |
| DJI RC Pro Enterprise | 10 (API 29) | Full support |
| DJI RC-N1/N2 (phone required) | varies | Use on connected phone |
| Any Android phone | 6+ (API 23+) | Full support |
| DJI Smart Controller | 9 (API 28) | Full support |

---

## Troubleshooting

**"Storage permission denied"**
→ Go to device Settings → Apps → DroneOpsSync → Permissions → Storage → Allow

**"Invalid or revoked API key"**
→ Generate a new key in DroneOps Command Settings → Device Access

**"flight-parser service unavailable"**
→ The parser container isn't running. On your server: `docker compose up -d flight-parser`

**"No new flight logs found"**
→ All logs have already been synced. Fly a new mission and try again.

**Build fails with "SDK not found"**
→ Set `ANDROID_HOME` environment variable: `export ANDROID_HOME=~/android-sdk`

**Gradle build fails**
→ Make sure JDK 17 is installed and `JAVA_HOME` is set correctly
