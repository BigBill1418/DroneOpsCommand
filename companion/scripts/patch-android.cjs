#!/usr/bin/env node
/**
 * Patches the Capacitor-generated Android project for DroneOpsSync.
 *
 * - Lowers targetSdkVersion to 29 (enables requestLegacyExternalStorage on Android 10-11)
 * - Adds network_security_config.xml (cleartext HTTP for LAN IPs)
 * - Patches AndroidManifest.xml with required attributes & permissions
 * - Installs AllFilesAccessPlugin (SAF folder picker + MANAGE_EXTERNAL_STORAGE)
 * - Adds documentfile dependency for SAF DocumentFile API
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
const APP_BUILD_GRADLE = path.join(ANDROID_DIR, 'app', 'build.gradle');

// ── Preflight ────────────────────────────────────────────────────────
if (!fs.existsSync(MANIFEST)) {
  console.error(`ERROR: ${MANIFEST} not found. Run "npx cap add android" first.`);
  process.exit(1);
}

console.log('Patching Android project for DroneOpsSync...\n');

// ── 1. Lower targetSdkVersion to 29 ─────────────────────────────────
// This enables requestLegacyExternalStorage on Android 10-11, giving full
// access to all files including Android/data/. Critical for DJI RC Pro
// (which runs Android 10) and any Android 11 devices.
// compileSdkVersion stays at 34 so we can use modern APIs.
if (fs.existsSync(VARIABLES_GRADLE)) {
  let vars = fs.readFileSync(VARIABLES_GRADLE, 'utf8');
  const original = vars;

  // Lower targetSdkVersion to 29
  vars = vars.replace(/targetSdkVersion\s*=\s*\d+/, 'targetSdkVersion = 29');

  if (vars !== original) {
    fs.writeFileSync(VARIABLES_GRADLE, vars, 'utf8');
    console.log('  + Set targetSdkVersion = 29 (enables legacy storage on Android 10-11)');
  } else {
    console.log('  = targetSdkVersion already set');
  }
}

// ── 2. Add documentfile dependency for SAF ──────────────────────────
// The DocumentFile API is needed for SAF folder access on Android 12+
if (fs.existsSync(APP_BUILD_GRADLE)) {
  let gradle = fs.readFileSync(APP_BUILD_GRADLE, 'utf8');
  if (!gradle.includes('documentfile')) {
    gradle = gradle.replace(
      /dependencies\s*\{/,
      `dependencies {\n    implementation "androidx.documentfile:documentfile:1.0.1"`
    );
    fs.writeFileSync(APP_BUILD_GRADLE, gradle, 'utf8');
    console.log('  + Added androidx.documentfile dependency');
  }
}

// ── 3. Create network_security_config.xml ────────────────────────────
fs.mkdirSync(RES_XML, { recursive: true });

fs.writeFileSync(NET_SEC, `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <!--
      Allow cleartext (HTTP) globally. DroneOpsSync is a LAN-only app that
      connects to a local server via IP address. Android's <domain> tags only
      match hostnames, not IP addresses or CIDR ranges, so we must permit
      cleartext at the base-config level for IP-based connections to work.
    -->
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>
</network-security-config>
`, 'utf8');
console.log('  + Created network_security_config.xml');

// ── 4. Patch AndroidManifest.xml ─────────────────────────────────────
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
  'android.permission.MANAGE_EXTERNAL_STORAGE',  // "All Files Access" — fallback for Android 11
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

if (changed) {
  fs.writeFileSync(MANIFEST, manifest, 'utf8');
  console.log('\n  Manifest saved.');
} else {
  console.log('\n  Manifest already patched — no changes needed.');
}

// ── 5. Install AllFilesAccessPlugin (native Capacitor plugin) ─────────
const JAVA_DIR = path.join(ANDROID_MAIN, 'java', 'com', 'barnardhq', 'droneopssync');
const PLUGIN_SRC = path.join(JAVA_DIR, 'AllFilesAccessPlugin.java');
const MAIN_ACTIVITY = path.join(JAVA_DIR, 'MainActivity.java');

// Copy plugin if missing or outdated (check for SAF support marker)
const pluginExists = fs.existsSync(PLUGIN_SRC);
const pluginHasSAF = pluginExists && fs.readFileSync(PLUGIN_SRC, 'utf8').includes('pickFolder');

if (!pluginHasSAF) {
  const pluginCode = `package com.barnardhq.droneopssync;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.Intent;
import android.content.UriPermission;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.util.Base64;

import androidx.activity.result.ActivityResult;
import androidx.documentfile.provider.DocumentFile;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.List;

@CapacitorPlugin(name = "AllFilesAccess")
public class AllFilesAccessPlugin extends Plugin {

    @PluginMethod()
    public void isGranted(PluginCall call) {
        JSObject ret = new JSObject();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            ret.put("granted", Environment.isExternalStorageManager());
        } else {
            ret.put("granted", true);
        }
        ret.put("sdkVersion", Build.VERSION.SDK_INT);
        ret.put("needsSAF", Build.VERSION.SDK_INT >= 31);
        call.resolve(ret);
    }

    @PluginMethod()
    public void request(PluginCall call) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            try {
                Intent intent = new Intent(
                    Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:" + getActivity().getPackageName())
                );
                getActivity().startActivity(intent);
                call.resolve();
            } catch (Exception e) {
                try {
                    Intent fallback = new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION);
                    getActivity().startActivity(fallback);
                    call.resolve();
                } catch (Exception e2) {
                    call.reject("Could not open settings: " + e2.getMessage());
                }
            }
        } else {
            call.resolve();
        }
    }

    @PluginMethod()
    public void pickFolder(PluginCall call) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        intent.addFlags(
            Intent.FLAG_GRANT_READ_URI_PERMISSION |
            Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
        );
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            String initialPath = call.getString("initialPath", "Android/data");
            try {
                Uri initialUri = Uri.parse(
                    "content://com.android.externalstorage.documents/document/primary:" +
                    Uri.encode(initialPath));
                intent.putExtra(DocumentsContract.EXTRA_INITIAL_URI, initialUri);
            } catch (Exception ignored) {}
        }
        startActivityForResult(call, intent, "folderPickResult");
    }

    @ActivityCallback
    private void folderPickResult(PluginCall call, ActivityResult result) {
        if (call == null) return;
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null) {
            call.reject("Folder selection cancelled");
            return;
        }
        Uri treeUri = result.getData().getData();
        if (treeUri == null) {
            call.reject("No folder selected");
            return;
        }
        try {
            getContext().getContentResolver().takePersistableUriPermission(
                treeUri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (Exception ignored) {}

        JSObject ret = new JSObject();
        ret.put("uri", treeUri.toString());
        call.resolve(ret);
    }

    @PluginMethod()
    public void getPersistedFolders(PluginCall call) {
        ContentResolver resolver = getContext().getContentResolver();
        List<UriPermission> perms = resolver.getPersistedUriPermissions();
        JSArray folders = new JSArray();
        for (UriPermission perm : perms) {
            if (perm.isReadPermission()) {
                JSObject obj = new JSObject();
                obj.put("uri", perm.getUri().toString());
                String uriStr = perm.getUri().toString();
                if (uriStr.contains("primary%3A")) {
                    obj.put("path", Uri.decode(uriStr.split("primary%3A")[1]));
                } else {
                    obj.put("path", uriStr);
                }
                folders.put(obj);
            }
        }
        JSObject ret = new JSObject();
        ret.put("folders", folders);
        call.resolve(ret);
    }

    @PluginMethod()
    public void listSAFFiles(PluginCall call) {
        String uriStr = call.getString("uri");
        if (uriStr == null) { call.reject("Missing uri"); return; }
        try {
            Uri treeUri = Uri.parse(uriStr);
            DocumentFile dir = DocumentFile.fromTreeUri(getContext(), treeUri);
            if (dir == null || !dir.exists()) { call.reject("Folder not accessible"); return; }
            JSArray files = new JSArray();
            listFilesRecursive(dir, "", files);
            JSObject ret = new JSObject();
            ret.put("files", files);
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("Failed to list files: " + e.getMessage());
        }
    }

    private void listFilesRecursive(DocumentFile dir, String pathPrefix, JSArray out) {
        if (dir == null) return;
        DocumentFile[] children = dir.listFiles();
        if (children == null) return;
        for (DocumentFile child : children) {
            if (child.isDirectory()) {
                String sub = pathPrefix.isEmpty() ? child.getName() : pathPrefix + "/" + child.getName();
                listFilesRecursive(child, sub, out);
            } else {
                String name = child.getName();
                if (name == null) continue;
                String lower = name.toLowerCase();
                if (!lower.endsWith(".txt") && !lower.endsWith(".csv") &&
                    !lower.endsWith(".dat") && !lower.endsWith(".log")) continue;
                JSObject file = new JSObject();
                file.put("name", name);
                file.put("path", pathPrefix.isEmpty() ? name : pathPrefix + "/" + name);
                file.put("size", child.length());
                file.put("uri", child.getUri().toString());
                out.put(file);
            }
        }
    }

    @PluginMethod()
    public void readSAFFile(PluginCall call) {
        String uriStr = call.getString("uri");
        if (uriStr == null) { call.reject("Missing uri"); return; }
        try {
            Uri fileUri = Uri.parse(uriStr);
            InputStream is = getContext().getContentResolver().openInputStream(fileUri);
            if (is == null) { call.reject("Could not open file"); return; }
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int len;
            while ((len = is.read(chunk)) != -1) buffer.write(chunk, 0, len);
            is.close();
            JSObject ret = new JSObject();
            ret.put("data", Base64.encodeToString(buffer.toByteArray(), Base64.NO_WRAP));
            ret.put("size", buffer.size());
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("Failed to read file: " + e.getMessage());
        }
    }

    @PluginMethod()
    public void releaseSAFPermission(PluginCall call) {
        String uriStr = call.getString("uri");
        if (uriStr == null) { call.reject("Missing uri"); return; }
        try {
            getContext().getContentResolver().releasePersistableUriPermission(
                Uri.parse(uriStr), Intent.FLAG_GRANT_READ_URI_PERMISSION);
            call.resolve();
        } catch (Exception e) {
            call.reject("Failed: " + e.getMessage());
        }
    }
}
`;
  fs.writeFileSync(PLUGIN_SRC, pluginCode, 'utf8');
  console.log('  + Created AllFilesAccessPlugin.java (with SAF folder picker)');
}

// Patch MainActivity to register the plugin
if (fs.existsSync(MAIN_ACTIVITY)) {
  let mainAct = fs.readFileSync(MAIN_ACTIVITY, 'utf8');
  if (!mainAct.includes('AllFilesAccessPlugin')) {
    mainAct = `package com.barnardhq.droneopssync;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(AllFilesAccessPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
`;
    fs.writeFileSync(MAIN_ACTIVITY, mainAct, 'utf8');
    console.log('  + Patched MainActivity to register AllFilesAccessPlugin');
  }
}

console.log('\nDone. Ready to build.');
