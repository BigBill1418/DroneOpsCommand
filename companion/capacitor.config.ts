import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.barnardhq.droneopssync',
  appName: 'DroneOpsSync',
  webDir: 'dist',
  android: {
    // Allow HTTP for local development; in production use HTTPS via tunnel
    allowMixedContent: true,
  },
  plugins: {
    Filesystem: {
      // Request legacy external storage access (required for Android 10 / DJI RC Pro)
      requestLegacyExternalStorage: true,
    },
  },
};

export default config;
