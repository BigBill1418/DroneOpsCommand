import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.barnardhq.droneopssync',
  appName: 'DroneOpsSync',
  webDir: 'dist',
  android: {
    // Allow HTTP for LAN connections (cleartext to local IPs)
    allowMixedContent: true,
  },
  server: {
    // Capacitor WebView origin — needed so fetch() works to any host
    androidScheme: 'https',
    cleartext: true,
  },
  plugins: {
    Filesystem: {
      // Request legacy external storage access (required for Android 10 / DJI RC Pro)
      requestLegacyExternalStorage: true,
    },
  },
};

export default config;
