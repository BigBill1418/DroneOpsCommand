#!/usr/bin/env node
/**
 * Patches the Capacitor-generated Android project for DroneOpsSync.
 *
 * - Lowers targetSdkVersion to 29 (enables requestLegacyExternalStorage on Android 10)
 * - Adds network_security_config.xml (cleartext HTTP for LAN IPs)
 * - Patches AndroidManifest.xml with required attributes & permissions
 * - Patches MainActivity to register FileScannerPlugin
 *
 * Cross-platform (Windows + macOS + Linux). Run after `npx cap sync android`.
 */

const fs = require('fs');
const path = require('path');

const ANDROID_DIR = path.join(__dirname, '..', 'android');
const ANDROID_MAIN = path.join(ANDROID_DIR, 'app', 'src', 'main');
const MANIFEST = path.join(ANDROID_MAIN, 'AndroidManifest.xml');
const RES_XML = path.join(ANDROID_MAIN, 'res', 'xml');
const NET_SEC = path.join(RES_XML, 'network_security_config.xml');
const VARIABLES_GRADLE = path.join(ANDROID_DIR, 'variables.gradle');

// ── Preflight ────────────────────────────────────────────────────────
if (!fs.existsSync(MANIFEST)) {
  console.error(`ERROR: ${MANIFEST} not found. Run "npx cap add android" first.`);
  process.exit(1);
}

console.log('Patching Android project for DroneOpsSync...\n');

// ── 1. Lower targetSdkVersion to 29 ─────────────────────────────────
// This enables requestLegacyExternalStorage on Android 10, giving full
// filesystem access including Android/data/. Critical for DJI RC Pro.
if (fs.existsSync(VARIABLES_GRADLE)) {
  let vars = fs.readFileSync(VARIABLES_GRADLE, 'utf8');
  const original = vars;
  vars = vars.replace(/targetSdkVersion\s*=\s*\d+/, 'targetSdkVersion = 29');
  if (vars !== original) {
    fs.writeFileSync(VARIABLES_GRADLE, vars, 'utf8');
    console.log('  + Set targetSdkVersion = 29 (enables legacy storage)');
  } else {
    console.log('  = targetSdkVersion already set');
  }
}

// ── 2. Create network_security_config.xml ────────────────────────────
fs.mkdirSync(RES_XML, { recursive: true });

fs.writeFileSync(NET_SEC, `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <!--
      Allow cleartext (HTTP) globally. DroneOpsSync is a LAN-only app that
      connects to a local server via IP address.
    -->
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>
</network-security-config>
`, 'utf8');
console.log('  + Created network_security_config.xml');

// ── 3. Patch AndroidManifest.xml ─────────────────────────────────────
let manifest = fs.readFileSync(MANIFEST, 'utf8');
let changed = false;

// Add networkSecurityConfig to <application>
if (!manifest.includes('networkSecurityConfig')) {
  manifest = manifest.replace('<application', '<application android:networkSecurityConfig="@xml/network_security_config"');
  console.log('  + Added networkSecurityConfig reference');
  changed = true;
}

// Add usesCleartextTraffic to <application>
if (!manifest.includes('usesCleartextTraffic')) {
  manifest = manifest.replace('<application', '<application android:usesCleartextTraffic="true"');
  console.log('  + Added usesCleartextTraffic');
  changed = true;
}

// Add requestLegacyExternalStorage to <application>
if (!manifest.includes('requestLegacyExternalStorage')) {
  manifest = manifest.replace('<application', '<application android:requestLegacyExternalStorage="true"');
  console.log('  + Added requestLegacyExternalStorage');
  changed = true;
}

// Add permissions if missing
const permissions = [
  'android.permission.INTERNET',
  'android.permission.READ_EXTERNAL_STORAGE',
  'android.permission.WRITE_EXTERNAL_STORAGE',
];

for (const perm of permissions) {
  if (!manifest.includes(perm)) {
    manifest = manifest.replace(
      '</manifest>',
      `    <uses-permission android:name="${perm}" />\n</manifest>`
    );
    console.log(`  + Added ${perm.split('.').pop()} permission`);
    changed = true;
  }
}

// Remove MANAGE_EXTERNAL_STORAGE if present (not needed with targetSdk 29)
if (manifest.includes('MANAGE_EXTERNAL_STORAGE')) {
  manifest = manifest.replace(/\s*<uses-permission android:name="android\.permission\.MANAGE_EXTERNAL_STORAGE"\s*\/>/, '');
  console.log('  - Removed MANAGE_EXTERNAL_STORAGE (not needed)');
  changed = true;
}

// ── Landscape orientation lock (ADR-0002 §5) ─────────────────────────
// DJI RC Pro is physically locked in landscape. A twist-of-wrist rotate
// event that re-created the MainActivity would destroy the Capacitor
// WebView, hide the "device not paired" red banner, and interrupt an
// in-progress upload. Lock every <activity> to sensorLandscape so the
// OS refuses to flip orientation; add configChanges so any device that
// *does* send a config event doesn't recreate the activity.
//
// Rationale for sensorLandscape vs landscape: DJI RC Pro and DJI RC 2
// are always landscape but can be flipped 180° in some mounts. sensorLandscape
// allows both landscape-left and landscape-right but never portrait.
{
  const activityRe = /<activity\b([^>]*?)\/?>/g;
  let activitiesPatched = 0;
  manifest = manifest.replace(activityRe, (match, attrs) => {
    let newAttrs = attrs;
    if (!/android:screenOrientation=/.test(newAttrs)) {
      newAttrs = ` android:screenOrientation="sensorLandscape"${newAttrs}`;
    }
    // Ensure configChanges covers orientation+screenSize so OS doesn't
    // recreate the activity on device-layout events.
    if (!/android:configChanges=/.test(newAttrs)) {
      newAttrs = ` android:configChanges="orientation|screenSize|keyboardHidden|screenLayout"${newAttrs}`;
    } else {
      // Already present — make sure the required tokens are set.
      newAttrs = newAttrs.replace(
        /android:configChanges="([^"]*)"/,
        (_m, existing) => {
          const tokens = new Set(existing.split('|').map((t) => t.trim()).filter(Boolean));
          ['orientation', 'screenSize', 'keyboardHidden', 'screenLayout'].forEach((t) => tokens.add(t));
          return `android:configChanges="${[...tokens].join('|')}"`;
        },
      );
    }
    activitiesPatched++;
    // Preserve self-closing vs open form.
    const trailing = match.endsWith('/>') ? '/>' : '>';
    return `<activity${newAttrs}${trailing}`;
  });
  if (activitiesPatched > 0) {
    console.log(`  + Locked ${activitiesPatched} activity/ies to sensorLandscape + configChanges`);
    changed = true;
  }
}

// Final safety check — no <activity> should declare android:screenOrientation="portrait".
// If we find one, it's a regression from a future Capacitor upgrade.
if (/android:screenOrientation="portrait"/.test(manifest)) {
  console.error('  ! FATAL: an <activity> still declares screenOrientation="portrait" — DJI RC Pro is landscape-only.');
  process.exit(1);
}

if (changed) {
  fs.writeFileSync(MANIFEST, manifest, 'utf8');
  console.log('\n  Manifest saved.');
} else {
  console.log('\n  Manifest already patched — no changes needed.');
}

// ── 4. Install FileScannerPlugin.java ────────────────────────────────
// This is the native plugin that uses java.io.File to scan DJI log folders.
// Must be installed by the patch script because android/ is gitignored.
const JAVA_DIR = path.join(ANDROID_MAIN, 'java', 'com', 'barnardhq', 'droneopssync');
const PLUGIN_FILE = path.join(JAVA_DIR, 'FileScannerPlugin.java');

// Always write the plugin (ensures it's up to date)
fs.mkdirSync(JAVA_DIR, { recursive: true });
fs.writeFileSync(PLUGIN_FILE, `package com.barnardhq.droneopssync;

import android.os.Environment;
import android.util.Base64;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/**
 * Native file scanner for DroneOpsSync.
 * Uses java.io.File directly — no Content Providers, no SAF, no MediaStore.
 * Works on Android 10 (DJI controllers) with targetSdk 29 + requestLegacyExternalStorage.
 */
@CapacitorPlugin(
    name = "FileScanner",
    permissions = {
        @Permission(strings = { "android.permission.READ_EXTERNAL_STORAGE" }, alias = "storage"),
        @Permission(strings = { "android.permission.WRITE_EXTERNAL_STORAGE" }, alias = "storageWrite")
    }
)
public class FileScannerPlugin extends Plugin {

    private static final Set<String> LOG_EXTENSIONS = new HashSet<>(
        Arrays.asList("txt", "csv", "dat", "log")
    );

    private File getStorageRoot() {
        return Environment.getExternalStorageDirectory();
    }

    @PluginMethod()
    public void scanPaths(PluginCall call) {
        JSArray pathsArg = call.getArray("paths");
        if (pathsArg == null) { call.reject("Missing paths"); return; }

        File root = getStorageRoot();
        JSArray files = new JSArray();
        JSArray errors = new JSArray();

        try {
            for (int i = 0; i < pathsArg.length(); i++) {
                String relPath = pathsArg.getString(i);
                File dir = new File(root, relPath);
                if (!dir.exists()) continue;
                if (!dir.isDirectory()) { errors.put(relPath + ": not a directory"); continue; }
                if (!dir.canRead()) { errors.put(relPath + ": permission denied"); continue; }
                scanDirectory(dir, relPath, relPath, files);
            }
        } catch (Exception e) {
            errors.put("Scan error: " + e.getMessage());
        }

        JSObject ret = new JSObject();
        ret.put("files", files);
        ret.put("errors", errors);
        call.resolve(ret);
    }

    private void scanDirectory(File dir, String relativePath, String source, JSArray out) {
        File[] children = dir.listFiles();
        if (children == null) return;
        for (File child : children) {
            if (child.isDirectory()) {
                scanDirectory(child, relativePath + "/" + child.getName(), source, out);
            } else if (child.isFile() && child.canRead()) {
                String name = child.getName();
                String ext = getExtension(name);
                if (!LOG_EXTENSIONS.contains(ext)) continue;
                JSObject file = new JSObject();
                file.put("name", name);
                file.put("path", relativePath + "/" + name);
                file.put("size", child.length());
                file.put("source", source);
                out.put(file);
            }
        }
    }

    @PluginMethod()
    public void readFile(PluginCall call) {
        String relPath = call.getString("path");
        if (relPath == null) { call.reject("Missing path"); return; }

        File file = new File(getStorageRoot(), relPath);
        if (!file.exists() || !file.canRead()) { call.reject("File not readable: " + relPath); return; }

        try {
            FileInputStream fis = new FileInputStream(file);
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int len;
            while ((len = fis.read(chunk)) != -1) buffer.write(chunk, 0, len);
            fis.close();

            JSObject ret = new JSObject();
            ret.put("data", Base64.encodeToString(buffer.toByteArray(), Base64.NO_WRAP));
            ret.put("size", buffer.size());
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("Failed to read: " + e.getMessage());
        }
    }

    @PluginMethod()
    public void deleteFile(PluginCall call) {
        String relPath = call.getString("path");
        if (relPath == null) { call.reject("Missing path"); return; }
        File file = new File(getStorageRoot(), relPath);
        JSObject ret = new JSObject();
        ret.put("deleted", file.exists() && file.delete());
        call.resolve(ret);
    }

    @PluginMethod()
    public void checkAccess(PluginCall call) {
        String relPath = call.getString("path", "");
        File target = relPath.isEmpty() ? getStorageRoot() : new File(getStorageRoot(), relPath);
        JSObject ret = new JSObject();
        ret.put("exists", target.exists());
        ret.put("readable", target.canRead());
        ret.put("isDirectory", target.isDirectory());
        ret.put("sdkVersion", android.os.Build.VERSION.SDK_INT);
        ret.put("storagePath", getStorageRoot().getAbsolutePath());
        call.resolve(ret);
    }

    private String getExtension(String name) {
        int dot = name.lastIndexOf('.');
        return dot >= 0 ? name.substring(dot + 1).toLowerCase() : "";
    }
}
`, 'utf8');
console.log('  + Installed FileScannerPlugin.java');

// Remove old AllFilesAccessPlugin if it exists
const oldPlugin = path.join(JAVA_DIR, 'AllFilesAccessPlugin.java');
if (fs.existsSync(oldPlugin)) {
  fs.unlinkSync(oldPlugin);
  console.log('  - Removed old AllFilesAccessPlugin.java');
}

// ── 5. Patch MainActivity to register FileScannerPlugin ──────────────
const MAIN_ACTIVITY = path.join(JAVA_DIR, 'MainActivity.java');

if (fs.existsSync(MAIN_ACTIVITY)) {
  let mainAct = fs.readFileSync(MAIN_ACTIVITY, 'utf8');
  if (!mainAct.includes('FileScannerPlugin')) {
    mainAct = `package com.barnardhq.droneopssync;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(FileScannerPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
`;
    fs.writeFileSync(MAIN_ACTIVITY, mainAct, 'utf8');
    console.log('  + Patched MainActivity to register FileScannerPlugin');
  }
}

console.log('\nDone. Ready to build.');
