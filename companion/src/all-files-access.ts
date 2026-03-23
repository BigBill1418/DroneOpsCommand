/**
 * TypeScript wrapper for the AllFilesAccess native Capacitor plugin.
 *
 * Handles MANAGE_EXTERNAL_STORAGE permission on Android 11+.
 * On web/dev, always reports granted (no-op).
 */

import { registerPlugin } from '@capacitor/core';
import { Capacitor } from '@capacitor/core';

interface AllFilesAccessPlugin {
  isGranted(): Promise<{ granted: boolean }>;
  request(): Promise<void>;
}

const AllFilesAccess = registerPlugin<AllFilesAccessPlugin>('AllFilesAccess');

/** Check if "All Files Access" is granted (always true on web/pre-Android 11) */
export async function isAllFilesAccessGranted(): Promise<boolean> {
  if (!Capacitor.isNativePlatform()) return true;
  try {
    const result = await AllFilesAccess.isGranted();
    return result.granted;
  } catch {
    return true; // Fail open on platforms that don't need it
  }
}

/** Open the system Settings page where the user can grant "All Files Access" */
export async function requestAllFilesAccess(): Promise<void> {
  if (!Capacitor.isNativePlatform()) return;
  await AllFilesAccess.request();
}
