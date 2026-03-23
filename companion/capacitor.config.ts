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
    CapacitorHttp: {
      // Route all fetch() through native HTTP layer — bypasses WebView
      // mixed-content blocks and CORS when talking to LAN servers over HTTP
      enabled: true,
    },
  },
};

export default config;
