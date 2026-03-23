/**
 * DroneOpsSync — scan, upload, and cleanup DJI flight logs.
 *
 * Scans known DJI log directories on external storage, uploads new files
 * to DroneOps Command via the device-upload API, and optionally deletes
 * synced logs from the controller after confirmed transfer.
 */

import { Filesystem, Directory, Encoding } from '@capacitor/filesystem';
import { Preferences } from '@capacitor/preferences';

// ── Config keys ────────────────────────────────────────────────────────
export const PREF_SERVER_URL = 'serverUrl';
export const PREF_LAN_URL = 'lanUrl';
export const PREF_API_KEY = 'apiKey';
export const PREF_AUTO_DELETE = 'autoDelete';
export const PREF_SYNCED_HASHES = 'syncedHashes';

// ── Default URLs ───────────────────────────────────────────────────────
// Cloud: works from anywhere (cell data, hotel wifi, job site)
export const DEFAULT_SERVER_URL = 'https://droneops.barnardhq.com';
// LAN: faster when on-site, works even if internet is down
export const DEFAULT_LAN_URL = 'http://192.168.50.20:3080';

// ── DJI log paths (relative to ExternalStorage = /storage/emulated/0/) ─
export const DJI_LOG_PATHS = [
  'DJI/dji.go.v5/FlightRecord',          // DJI Fly v2 (Go 5 engine)
  'DJI/dji.go.v4/FlightRecord',          // DJI Fly v1 (Go 4 engine)
  'DJI/com.dji.industry.pilot/FlightRecord', // DJI Pilot (enterprise)
  'DJI/com.dji.industry.pilot2/FlightRecord', // DJI Pilot 2
];

// File extensions we care about
const LOG_EXTENSIONS = new Set(['txt', 'csv', 'dat', 'log']);

// ── Types ──────────────────────────────────────────────────────────────
export interface LogFile {
  /** Display name */
  name: string;
  /** Full path relative to ExternalStorage */
  path: string;
  /** File size in bytes (approximate from base64) */
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
  const [urlRes, lanRes, keyRes, delRes] = await Promise.all([
    Preferences.get({ key: PREF_SERVER_URL }),
    Preferences.get({ key: PREF_LAN_URL }),
    Preferences.get({ key: PREF_API_KEY }),
    Preferences.get({ key: PREF_AUTO_DELETE }),
  ]);
  return {
    serverUrl: urlRes.value || '',
    lanUrl: lanRes.value || '',
    apiKey: keyRes.value || '',
    autoDelete: delRes.value === 'true',
  };
}

export async function saveConfig(
  serverUrl: string,
  lanUrl: string,
  apiKey: string,
  autoDelete: boolean,
) {
  await Promise.all([
    Preferences.set({ key: PREF_SERVER_URL, value: serverUrl }),
    Preferences.set({ key: PREF_LAN_URL, value: lanUrl }),
    Preferences.set({ key: PREF_API_KEY, value: apiKey }),
    Preferences.set({ key: PREF_AUTO_DELETE, value: String(autoDelete) }),
  ]);
}

/** Track which file paths have already been synced (avoid re-uploading) */
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

// ── Filesystem helpers ─────────────────────────────────────────────────
async function requestPermissions(): Promise<boolean> {
  try {
    const perms = await Filesystem.checkPermissions();
    if (perms.publicStorage === 'granted') return true;
    const req = await Filesystem.requestPermissions();
    return req.publicStorage === 'granted';
  } catch {
    // On web/dev, permissions API may not exist
    return true;
  }
}

/** Check if a directory exists */
async function dirExists(path: string): Promise<boolean> {
  try {
    await Filesystem.readdir({ path, directory: Directory.ExternalStorage });
    return true;
  } catch {
    return false;
  }
}

// ── Scan ───────────────────────────────────────────────────────────────
export async function scanForLogs(
  onProgress?: (msg: string) => void,
): Promise<LogFile[]> {
  const granted = await requestPermissions();
  if (!granted) throw new Error('Storage permission denied. Please grant access in device settings.');

  const synced = await getSyncedPaths();
  const found: LogFile[] = [];

  for (const basePath of DJI_LOG_PATHS) {
    onProgress?.(`Scanning ${basePath}...`);
    if (!(await dirExists(basePath))) continue;

    // Recursively scan (DJI nests logs in date subfolders)
    await scanDir(basePath, basePath, synced, found);
  }

  return found;
}

async function scanDir(
  path: string,
  source: string,
  synced: Set<string>,
  out: LogFile[],
) {
  try {
    const result = await Filesystem.readdir({ path, directory: Directory.ExternalStorage });
    for (const entry of result.files) {
      const fullPath = `${path}/${entry.name}`;
      if (entry.type === 'directory') {
        await scanDir(fullPath, source, synced, out);
      } else {
        const ext = entry.name.split('.').pop()?.toLowerCase() || '';
        if (!LOG_EXTENSIONS.has(ext)) continue;
        if (synced.has(fullPath)) continue;
        out.push({
          name: entry.name,
          path: fullPath,
          size: entry.size || 0,
          source,
        });
      }
    }
  } catch {
    // Directory not readable — skip silently
  }
}

// ── Server resolution (LAN-first with cloud fallback) ─────────────────
export interface ResolvedServer {
  url: string;
  via: 'lan' | 'cloud';
}

/**
 * Try the LAN URL first (fast, no internet needed). If it doesn't respond
 * within 3 seconds, fall back to the cloud/tunnel URL. Returns whichever
 * answered the health check successfully.
 */
export async function resolveServerUrl(
  cloudUrl: string,
  lanUrl: string,
  apiKey: string,
  onStatus?: (msg: string) => void,
): Promise<ResolvedServer> {
  // If no LAN URL configured, go straight to cloud
  if (!lanUrl.trim()) {
    onStatus?.('Connecting via cloud...');
    await checkHealth(cloudUrl, apiKey);
    return { url: cloudUrl, via: 'cloud' };
  }

  // Try LAN first with a short timeout
  onStatus?.('Trying LAN connection...');
  try {
    await checkHealthWithTimeout(lanUrl, apiKey, 3000);
    return { url: lanUrl, via: 'lan' };
  } catch {
    // LAN unreachable — fall back to cloud
    onStatus?.('LAN unavailable — connecting via cloud...');
    await checkHealth(cloudUrl, apiKey);
    return { url: cloudUrl, via: 'cloud' };
  }
}

async function checkHealthWithTimeout(
  serverUrl: string,
  apiKey: string,
  timeoutMs: number,
): Promise<HealthResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const url = `${serverUrl.replace(/\/+$/, '')}/api/flight-library/device-health`;
    const resp = await fetch(url, {
      method: 'GET',
      headers: { 'X-Device-Api-Key': apiKey },
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`Status ${resp.status}`);
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

// ── Health check ───────────────────────────────────────────────────────
export async function checkHealth(serverUrl: string, apiKey: string): Promise<HealthResult> {
  const url = `${serverUrl.replace(/\/+$/, '')}/api/flight-library/device-health`;
  const resp = await fetch(url, {
    method: 'GET',
    headers: { 'X-Device-Api-Key': apiKey },
  });
  if (!resp.ok) {
    if (resp.status === 401) throw new Error('Invalid or revoked API key');
    throw new Error(`Server returned ${resp.status}`);
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

  // Upload in batches of 5 to avoid overwhelming the server
  const batchSize = 5;
  let uploaded = 0;

  for (let i = 0; i < files.length; i += batchSize) {
    const batch = files.slice(i, i + batchSize);
    const formData = new FormData();

    for (const file of batch) {
      onProgress?.(uploaded, files.length, file.name);
      try {
        const fileData = await Filesystem.readFile({
          path: file.path,
          directory: Directory.ExternalStorage,
        });

        // Convert base64 to Blob
        let blob: Blob;
        if (typeof fileData.data === 'string') {
          const binary = atob(fileData.data);
          const bytes = new Uint8Array(binary.length);
          for (let j = 0; j < binary.length; j++) bytes[j] = binary.charCodeAt(j);
          blob = new Blob([bytes], { type: 'application/octet-stream' });
        } else {
          blob = fileData.data;
        }

        formData.append('files', blob, file.name);
      } catch (err) {
        result.errors.push(`${file.name}: failed to read file`);
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
        result.errors.push(`Batch upload failed: server returned ${resp.status}`);
        continue;
      }

      const data = await resp.json();
      result.imported += data.imported || 0;
      result.skipped += data.skipped || 0;
      if (data.errors) result.errors.push(...data.errors);

      // Mark successfully processed files as synced
      for (const file of batch) {
        syncedPaths.push(file.path);
        result.files.push(file);
      }
    } catch (err: any) {
      if (err.message?.includes('API key')) throw err;
      result.errors.push(`Upload batch failed: ${err.message}`);
    }

    uploaded += batch.length;
    onProgress?.(uploaded, files.length, '');
  }

  // Persist synced paths
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
      await Filesystem.deleteFile({
        path: file.path,
        directory: Directory.ExternalStorage,
      });
      deleted++;
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
