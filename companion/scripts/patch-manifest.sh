#!/bin/bash
# Patches the Capacitor-generated AndroidManifest.xml for DJI RC Pro compatibility.
# Run after `npx cap add android` and before building.

MANIFEST="android/app/src/main/AndroidManifest.xml"
RES_XML="android/app/src/main/res/xml"
NET_SEC="$RES_XML/network_security_config.xml"

if [ ! -f "$MANIFEST" ]; then
  echo "ERROR: $MANIFEST not found. Run 'npx cap add android' first."
  exit 1
fi

echo "Patching AndroidManifest.xml..."

# Add requestLegacyExternalStorage to <application> tag
if ! grep -q "requestLegacyExternalStorage" "$MANIFEST"; then
  sed -i 's|<application|<application android:requestLegacyExternalStorage="true"|' "$MANIFEST"
  echo "  + Added requestLegacyExternalStorage=true"
else
  echo "  = requestLegacyExternalStorage already present"
fi

# Add networkSecurityConfig to <application> tag (allows cleartext HTTP to LAN IPs)
if ! grep -q "networkSecurityConfig" "$MANIFEST"; then
  sed -i 's|<application|<application android:networkSecurityConfig="@xml/network_security_config"|' "$MANIFEST"
  echo "  + Added networkSecurityConfig reference"
else
  echo "  = networkSecurityConfig already present"
fi

# Add INTERNET permission if missing
if ! grep -q "android.permission.INTERNET" "$MANIFEST"; then
  sed -i '/<manifest/a\    <uses-permission android:name="android.permission.INTERNET" />' "$MANIFEST"
  echo "  + Added INTERNET permission"
else
  echo "  = INTERNET permission already present"
fi

# Add READ_EXTERNAL_STORAGE permission if missing
if ! grep -q "READ_EXTERNAL_STORAGE" "$MANIFEST"; then
  sed -i '/<manifest/a\    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />' "$MANIFEST"
  echo "  + Added READ_EXTERNAL_STORAGE permission"
else
  echo "  = READ_EXTERNAL_STORAGE already present"
fi

# Add WRITE_EXTERNAL_STORAGE permission if missing
if ! grep -q "WRITE_EXTERNAL_STORAGE" "$MANIFEST"; then
  sed -i '/<manifest/a\    <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" />' "$MANIFEST"
  echo "  + Added WRITE_EXTERNAL_STORAGE permission"
else
  echo "  = WRITE_EXTERNAL_STORAGE already present"
fi

# Create network_security_config.xml — allows cleartext HTTP to private/LAN IPs
echo "Creating network security config..."
mkdir -p "$RES_XML"
cat > "$NET_SEC" << 'XMLEOF'
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <!-- Allow cleartext (HTTP) to local/private network IPs for LAN sync -->
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">10.0.0.0</domain>
        <domain includeSubdomains="true">172.16.0.0</domain>
        <domain includeSubdomains="true">192.168.0.0</domain>
        <domain includeSubdomains="true">localhost</domain>
    </domain-config>
    <!-- Block cleartext to everything else (force HTTPS for cloud/tunnel) -->
    <base-config cleartextTrafficPermitted="false">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>
</network-security-config>
XMLEOF
echo "  + Created $NET_SEC (cleartext allowed for private IPs only)"

echo "Done. Manifest patched for Android 10 / DJI RC Pro."
