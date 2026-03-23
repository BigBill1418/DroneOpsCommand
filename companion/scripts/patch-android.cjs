#!/usr/bin/env node
/**
 * Patches the Capacitor-generated Android project for DroneOpsSync.
 *
 * - Adds network_security_config.xml (cleartext HTTP for LAN IPs)
 * - Patches AndroidManifest.xml with required attributes & permissions
 *
 * Cross-platform (Windows + macOS + Linux). Run after `npx cap sync android`.
 */

const fs = require('fs');
const path = require('path');

const ANDROID_MAIN = path.join(__dirname, '..', 'android', 'app', 'src', 'main');
const MANIFEST = path.join(ANDROID_MAIN, 'AndroidManifest.xml');
const RES_XML = path.join(ANDROID_MAIN, 'res', 'xml');
const NET_SEC = path.join(RES_XML, 'network_security_config.xml');

// ── Preflight ────────────────────────────────────────────────────────
if (!fs.existsSync(MANIFEST)) {
  console.error(`ERROR: ${MANIFEST} not found. Run "npx cap add android" first.`);
  process.exit(1);
}

console.log('Patching Android project for DroneOpsSync...\n');

// ── 1. Create network_security_config.xml ────────────────────────────
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

// ── 2. Patch AndroidManifest.xml ─────────────────────────────────────
let manifest = fs.readFileSync(MANIFEST, 'utf8');
let changed = false;

// Add networkSecurityConfig to <application>
if (!manifest.includes('networkSecurityConfig')) {
  manifest = manifest.replace('<application', '<application android:networkSecurityConfig="@xml/network_security_config"');
  console.log('  + Added networkSecurityConfig reference');
  changed = true;
}

// Add usesCleartextTraffic to <application> (belt-and-suspenders for HTTP on LAN IPs)
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
  'android.permission.MANAGE_EXTERNAL_STORAGE',  // "All Files Access" — needed to read Android/data/ on API 30+
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

console.log('\nDone. Ready to build.');
