/**
 * TypeScript wrapper for the AllFilesAccess native Capacitor plugin.
 *
 * Three tiers of file access:
 *   1. Legacy storage (Android 10-11): requestLegacyExternalStorage — full access
 *   2. MANAGE_EXTERNAL_STORAGE (Android 11+): broad access, but NOT Android/data/
 *   3. SAF folder picker (Android 12+): user picks Android/data/dji.go.v5 folder
 *
 * On web/dev, all checks return true (no-op).
 */

import { registerPlugin } from '@capacitor/core';
import { Capacitor } from '@capacitor/core';

export interface SAFFile {
  name: string;
  path: string;
  size: number;
  uri: string;
}

export interface SAFFolder {
  uri: string;
  path: string;
}

interface AllFilesAccessPlugin {
  isGranted(): Promise<{ granted: boolean; sdkVersion: number; needsSAF: boolean }>;
  request(): Promise<void>;
  pickFolder(opts?: { initialPath?: string }): Promise<{ uri: string }>;
  getPersistedFolders(): Promise<{ folders: SAFFolder[] }>;
  listSAFFiles(opts: { uri: string }): Promise<{ files: SAFFile[] }>;
  readSAFFile(opts: { uri: string }): Promise<{ data: string; size: number }>;
  releaseSAFPermission(opts: { uri: string }): Promise<void>;
}

const AllFilesAccess = registerPlugin<AllFilesAccessPlugin>('AllFilesAccess');

/** Check if "All Files Access" (MANAGE_EXTERNAL_STORAGE) is granted */
export async function isAllFilesAccessGranted(): Promise<boolean> {
  if (!Capacitor.isNativePlatform()) return true;
  try {
    const result = await AllFilesAccess.isGranted();
    return result.granted;
  } catch {
    return true;
  }
}

/** Get device info — SDK version and whether SAF is needed for Android/data/ */
export async function getDeviceStorageInfo(): Promise<{
  sdkVersion: number;
  needsSAF: boolean;
  manageGranted: boolean;
}> {
  if (!Capacitor.isNativePlatform()) {
    return { sdkVersion: 0, needsSAF: false, manageGranted: true };
  }
  try {
    const result = await AllFilesAccess.isGranted();
    return {
      sdkVersion: result.sdkVersion,
      needsSAF: result.needsSAF,
      manageGranted: result.granted,
    };
  } catch {
    return { sdkVersion: 0, needsSAF: false, manageGranted: true };
  }
}

/** Open the system Settings page for MANAGE_EXTERNAL_STORAGE */
export async function requestAllFilesAccess(): Promise<void> {
  if (!Capacitor.isNativePlatform()) return;
  await AllFilesAccess.request();
}

/** Launch the SAF folder picker — user selects a folder and grants read access */
export async function pickSAFFolder(initialPath?: string): Promise<string | null> {
  if (!Capacitor.isNativePlatform()) return null;
  try {
    const result = await AllFilesAccess.pickFolder({ initialPath });
    return result.uri;
  } catch {
    return null; // User cancelled
  }
}

/** Get all persisted SAF folder permissions */
export async function getPersistedFolders(): Promise<SAFFolder[]> {
  if (!Capacitor.isNativePlatform()) return [];
  try {
    const result = await AllFilesAccess.getPersistedFolders();
    return result.folders || [];
  } catch {
    return [];
  }
}

/** List all log files in a SAF-granted folder (recursive) */
export async function listSAFFiles(uri: string): Promise<SAFFile[]> {
  if (!Capacitor.isNativePlatform()) return [];
  try {
    const result = await AllFilesAccess.listSAFFiles({ uri });
    return result.files || [];
  } catch {
    return [];
  }
}

/** Read a file from a SAF URI — returns base64 data */
export async function readSAFFile(uri: string): Promise<{ data: string; size: number } | null> {
  if (!Capacitor.isNativePlatform()) return null;
  try {
    return await AllFilesAccess.readSAFFile({ uri });
  } catch {
    return null;
  }
}
