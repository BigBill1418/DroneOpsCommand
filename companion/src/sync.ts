/**
 * DroneOpsSync — scan, upload, and cleanup DJI flight logs.
 *
 * Uses a native FileScanner plugin (java.io.File) to directly read the
 * filesystem. No SAF, no MANAGE_EXTERNAL_STORAGE, no content providers.
 * Works on Android 10 (DJI controllers) with targetSdk 29.
 *
 * LAN-only — connects directly to the DroneOpsCommand server on your network.
 */

import { Preferences } from '@capacitor/preferences';
import { registerPlugin } from '@capacitor/core';
import { Capacitor } from '@capacitor/core';

// ── Native plugin interface ────────────────────────────────────────────
interface FileScannerPlugin {
  scanPaths(opts: { paths: string[] }): Promise<{
    files: LogFile[];
    errors: string[];
  }>;
  readFile(opts: { path: string }): Promise<{ data: string; size: number }>;
  deleteFile(opts: { path: string }): Promise<{ deleted: boolean }>;
  checkAccess(opts: { path: string }): Promise<{
    exists: boolean;
    readable: boolean;
    isDirectory: boolean;
    sdkVersion: number;
    storagePath: string;
  }>;
}

const FileScanner = registerPlugin<FileScannerPlugin>('FileScanner');

// ── Config keys ────────────────────────────────────────────────────────
export const PREF_SERVER_URL = 'serverUrl';
export const PREF_API_KEY = 'apiKey';
export const PREF_AUTO_DELETE = 'autoDelete';
export const PREF_SYNCED_HASHES = 'syncedHashes';

// ── Default URL ───────────────────────────────────────────────────────
export const DEFAULT_SERVER_URL = 'http://192.168.50.20:3080';

// ── DJI log paths (relative to /storage/emulated/0/) ──────────────────
// All paths scanned — no public/restricted distinction needed on Android 10.
export const DJI_LOG_PATHS = [
  // Enterprise controllers (RC Plus, RC Plus 2) — DJI Pilot
  'DJI/com.dji.industry.pilot/FlightRecord',
  'DJI/com.dji.industry.pilot2/FlightRecord',
  // Consumer (RC Pro, RC 2, phones) — DJI Fly
  'Android/data/dji.go.v5/files/FlightRecord',
  'Android/data/dji.go.v4/files/FlightRecord',
  // Legacy public paths
  'DJI/dji.go.v5/FlightRecord',
  'DJI/dji.go.v4/FlightRecord',
];

// ── Types ──────────────────────────────────────────────────────────────
export interface LogFile {
  name: string;
  /** Path relative to /storage/emulated/0/ */
  path: string;
  size: number;
  /** Which DJI log directory it came from */
  source: string;
}

export interface SyncResult {
  imported: number;
  skipped: number;
  errors: string[];
  files: LogFile[];
}

export interface HealthResult {
  status: string;
  device_label: string;
  parser_available: boolean;
  upload_endpoint: string;
}

// ── Preferences helpers ────────────────────────────────────────────────
export async function getConfig() {
  const [urlRes, keyRes, delRes] = await Promise.all([
    Preferences.get({ key: PREF_SERVER_URL }),
    Preferences.get({ key: PREF_API_KEY }),
    Preferences.get({ key: PREF_AUTO_DELETE }),
  ]);
  return {
    serverUrl: urlRes.value || '',
    apiKey: keyRes.value || '',
    autoDelete: delRes.value === 'true',
  };
}

export async function saveConfig(
  serverUrl: string,
  apiKey: string,
  autoDelete: boolean,
) {
  await Promise.all([
    Preferences.set({ key: PREF_SERVER_URL, value: serverUrl }),
    Preferences.set({ key: PREF_API_KEY, value: apiKey }),
    Preferences.set({ key: PREF_AUTO_DELETE, value: String(autoDelete) }),
  ]);
}

/** Track which file paths have already been synced */
async function getSyncedPaths(): Promise<Set<string>> {
  const res = await Preferences.get({ key: PREF_SYNCED_HASHES });
  if (!res.value) return new Set();
  try { return new Set(JSON.parse(res.value)); } catch { return new Set(); }
}

async function addSyncedPaths(paths: string[]) {
  const existing = await getSyncedPaths();
  paths.forEach((p) => existing.add(p));
  await Preferences.set({ key: PREF_SYNCED_HASHES, value: JSON.stringify([...existing]) });
}

// ── Diagnostic: check storage access ──────────────────────────────────
export async function checkStorageAccess(): Promise<{
  sdkVersion: number;
  storagePath: string;
  accessible: boolean;
  pathResults: { path: string; exists: boolean; readable: boolean }[];
}> {
  if (!Capacitor.isNativePlatform()) {
    return { sdkVersion: 0, storagePath: '/dev', accessible: true, pathResults: [] };
  }

  const rootInfo = await FileScanner.checkAccess({ path: '' });
  const pathResults: { path: string; exists: boolean; readable: boolean }[] = [];

  for (const p of DJI_LOG_PATHS) {
    try {
      const info = await FileScanner.checkAccess({ path: p });
      pathResults.push({ path: p, exists: info.exists, readable: info.readable });
    } catch {
      pathResults.push({ path: p, exists: false, readable: false });
    }
  }

  return {
    sdkVersion: rootInfo.sdkVersion,
    storagePath: rootInfo.storagePath,
    accessible: rootInfo.readable,
    pathResults,
  };
}

// ── Scan ───────────────────────────────────────────────────────────────
export async function scanForLogs(
  onProgress?: (msg: string) => void,
): Promise<{ files: LogFile[]; errors: string[] }> {
  const synced = await getSyncedPaths();

  if (!Capacitor.isNativePlatform()) {
    return { files: [], errors: ['Not running on device'] };
  }

  onProgress?.('Scanning DJI flight log folders...');
  const result = await FileScanner.scanPaths({ paths: DJI_LOG_PATHS });

  // Filter out already-synced files
  const newFiles = result.files.filter((f) => !synced.has(f.path));

  onProgress?.(`Found ${newFiles.length} new log file${newFiles.length !== 1 ? 's' : ''}`);
  return { files: newFiles, errors: result.errors };
}

// ── Health check ───────────────────────────────────────────────────────
export async function checkHealth(serverUrl: string, apiKey: string): Promise<HealthResult> {
  const url = `${serverUrl.replace(/\/+$/, '')}/api/flight-library/device-health`;
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: 'GET',
      headers: { 'X-Device-Api-Key': apiKey },
    });
  } catch (err: any) {
    throw new Error(`Cannot reach server at ${serverUrl} — check IP address, port, and that the server is running`);
  }
  if (!resp.ok) {
    let body = '';
    try { body = await resp.text(); } catch { /* ignore */ }
    const detail = body ? ` — ${body.slice(0, 200)}` : '';
    if (resp.status === 401) throw new Error('Invalid or revoked API key');
    if (resp.status === 403) throw new Error(`Access denied (403)${detail}`);
    throw new Error(`Server returned ${resp.status}${detail}`);
  }
  return resp.json();
}

// ── Upload ─────────────────────────────────────────────────────────────
export async function uploadLogs(
  serverUrl: string,
  apiKey: string,
  files: LogFile[],
  onProgress?: (uploaded: number, total: number, currentFile: string) => void,
): Promise<SyncResult> {
  const url = `${serverUrl.replace(/\/+$/, '')}/api/flight-library/device-upload`;
  const result: SyncResult = { imported: 0, skipped: 0, errors: [], files: [] };
  const syncedPaths: string[] = [];

  const batchSize = 5;
  let uploaded = 0;

  for (let i = 0; i < files.length; i += batchSize) {
    const batch = files.slice(i, i + batchSize);
    const formData = new FormData();

    for (const file of batch) {
      onProgress?.(uploaded, files.length, file.name);
      try {
        // Read file via native plugin (java.io.File — no content provider)
        const fileData = await FileScanner.readFile({ path: file.path });
        const binary = atob(fileData.data);
        const bytes = new Uint8Array(binary.length);
        for (let j = 0; j < binary.length; j++) bytes[j] = binary.charCodeAt(j);
        const blob = new Blob([bytes], { type: 'application/octet-stream' });
        formData.append('files', blob, file.name);
      } catch (err: any) {
        result.errors.push(`${file.name}: failed to read — ${err.message || 'unknown error'}`);
      }
    }

    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'X-Device-Api-Key': apiKey },
        body: formData,
      });

      if (!resp.ok) {
        if (resp.status === 401) throw new Error('Invalid or revoked API key');
        let body = '';
        try { body = await resp.text(); } catch { /* ignore */ }
        result.errors.push(`Upload failed: ${resp.status}${body ? ' — ' + body.slice(0, 200) : ''}`);
        continue;
      }

      const data = await resp.json();
      result.imported += data.imported || 0;
      result.skipped += data.skipped || 0;
      if (data.errors) result.errors.push(...data.errors);

      for (const file of batch) {
        syncedPaths.push(file.path);
        result.files.push(file);
      }
    } catch (err: any) {
      if (err.message?.includes('API key')) throw err;
      result.errors.push(`Upload failed: ${err.message}`);
    }

    uploaded += batch.length;
    onProgress?.(uploaded, files.length, '');
  }

  if (syncedPaths.length > 0) {
    await addSyncedPaths(syncedPaths);
  }

  return result;
}

// ── Delete synced logs ─────────────────────────────────────────────────
export async function deleteSyncedFiles(files: LogFile[]): Promise<{ deleted: number; errors: string[] }> {
  let deleted = 0;
  const errors: string[] = [];

  for (const file of files) {
    try {
      const result = await FileScanner.deleteFile({ path: file.path });
      if (result.deleted) deleted++;
    } catch (err: any) {
      errors.push(`${file.name}: ${err.message || 'delete failed'}`);
    }
  }

  return { deleted, errors };
}

// ── Format helpers ─────────────────────────────────────────────────────
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}
