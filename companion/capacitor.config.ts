import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.barnardhq.droneopssync',
  appName: 'DroneOpsSync',
  webDir: 'dist',
  android: {
    // Allow HTTP for LAN connections (cleartext to local IPs)
    allowMixedContent: true,
    // DJI RC Pro is physically fixed in landscape; a rotate reflow would
    // momentarily hide the "device not paired" banner and destroy the
    // Capacitor WebView. The manifest patch (scripts/patch-android.cjs)
    // is what actually enforces this at the OS level — this field is
    // informational for any Capacitor 6+ tooling that reads it.
    // See ADR-0002 §5.
    orientation: 'landscape',
  },
  server: {
    // Capacitor WebView origin — needed so fetch() works to any host
    androidScheme: 'https',
    cleartext: true,
  },
  plugins: {
    CapacitorHttp: {
      // Route all fetch() through native HTTP layer — bypasses WebView
      // mixed-content blocks and CORS when talking to LAN servers over HTTP
      enabled: true,
    },
  },
};

export default config;
