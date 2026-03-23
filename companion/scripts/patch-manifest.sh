#!/bin/bash
# Patches the Capacitor-generated AndroidManifest.xml for DJI RC Pro compatibility.
# Run after `npx cap add android` and before building.

MANIFEST="android/app/src/main/AndroidManifest.xml"

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

echo "Done. Manifest patched for Android 10 / DJI RC Pro."
