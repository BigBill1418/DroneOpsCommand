/**
 * DroneOpsSync — scan, upload, and cleanup DJI flight logs.
 *
 * Scans known DJI log directories on external storage, uploads new files
 * to DroneOps Command via the device-upload API, and optionally deletes
 * synced logs from the controller after confirmed transfer.
 *
 * LAN-only — connects directly to the DroneOpsCommand server on your network.
 *
 * File access strategy:
 *   - Android 10-11 (RC Pro): Legacy storage via requestLegacyExternalStorage
 *   - Android 12+: MANAGE_EXTERNAL_STORAGE for public DJI/ paths,
 *     SAF folder picker for Android/data/ paths (only way to access them)
 */

import { Filesystem, Directory } from '@capacitor/filesystem';
import { Preferences } from '@capacitor/preferences';
import {
  isAllFilesAccessGranted,
  getPersistedFolders,
  listSAFFiles,
  readSAFFile,
  type SAFFile,
} from './all-files-access';

// ── Config keys ────────────────────────────────────────────────────────
export const PREF_SERVER_URL = 'serverUrl';
export const PREF_API_KEY = 'apiKey';
export const PREF_AUTO_DELETE = 'autoDelete';
export const PREF_SYNCED_HASHES = 'syncedHashes';

// ── Default URL ───────────────────────────────────────────────────────
export const DEFAULT_SERVER_URL = 'http://192.168.50.20:3080';

// ── DJI log paths (relative to ExternalStorage = /storage/emulated/0/) ─
// Verified paths from actual Barnard HQ fleet devices:
//   4TD  (RC Plus 2) → DJI/com.dji.industry.pilot/FlightRecord
//   M30T (RC Plus)   → DJI/com.dji.industry.pilot/FlightRecord
//   M3P  (RC Pro)    → Android/data/dji.go.v5/files/FlightRecord
//   M5P  (RC 2)      → Android/data/dji.go.v5/files/FlightRecord
//   Phone (S25 Ultra) → Android/data/dji.go.v5/files/FlightRecord

/** Public paths — accessible with normal storage permissions */
export const PUBLIC_LOG_PATHS = [
  'DJI/com.dji.industry.pilot/FlightRecord',   // 4TD, M30T — RC Plus, RC Plus 2
  'DJI/com.dji.industry.pilot2/FlightRecord',  // DJI Pilot 2
  'DJI/dji.go.v5/FlightRecord',                // Legacy DJI Fly v2
  'DJI/dji.go.v4/FlightRecord',                // Legacy DJI Fly v1
];

/** Restricted paths — require MANAGE_EXTERNAL_STORAGE on Android 11,
 *  or SAF folder access on Android 12+ */
export const RESTRICTED_LOG_PATHS = [
  'Android/data/dji.go.v5/files/FlightRecord',  // M3P, M5P, phones — RC Pro, RC 2, Android
  'Android/data/dji.go.v4/files/FlightRecord',  // DJI Fly v1 (Go 4 engine)
];

// All paths combined for legacy access
export const DJI_LOG_PATHS = [...PUBLIC_LOG_PATHS, ...RESTRICTED_LOG_PATHS];

// File extensions we care about
const LOG_EXTENSIONS = new Set(['txt', 'csv', 'dat', 'log']);

// ── Types ──────────────────────────────────────────────────────────────
export interface LogFile {
  /** Display name */
  name: string;
  /** Full path relative to ExternalStorage, or SAF URI */
  path: string;
  /** File size in bytes */
  size: number;
  /** Which DJI log directory it came from */
  source: string;
  /** If from SAF, the document URI for reading */
  safUri?: string;
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

export interface ScanResult {
  files: LogFile[];
  /** True if restricted Android/data/ paths were inaccessible (needs SAF) */
  restrictedBlocked: boolean;
  /** True if SAF folders were used to find files */
  usedSAF: boolean;
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
): Promise<ScanResult> {
  await requestPermissions();

  const allFilesGranted = await isAllFilesAccessGranted();
  const synced = await getSyncedPaths();
  const found: LogFile[] = [];
  let restrictedBlocked = false;
  let usedSAF = false;

  // 1. Scan public paths (always accessible with storage permission)
  for (const basePath of PUBLIC_LOG_PATHS) {
    onProgress?.(`Scanning ${basePath}...`);
    if (!(await dirExists(basePath))) continue;
    await scanDir(basePath, basePath, synced, found);
  }

  // 2. Try restricted paths via direct file access
  //    Works on: Android 10-11 (legacy storage), Android 11 with MANAGE_EXTERNAL_STORAGE
  for (const basePath of RESTRICTED_LOG_PATHS) {
    onProgress?.(`Scanning ${basePath}...`);
    if (!(await dirExists(basePath))) {
      restrictedBlocked = true;
      continue;
    }
    await scanDir(basePath, basePath, synced, found);
    restrictedBlocked = false; // At least one restricted path was accessible
  }

  // 3. If restricted paths were blocked, try SAF-granted folders
  if (restrictedBlocked) {
    onProgress?.('Checking saved folder access...');
    const safFolders = await getPersistedFolders();

    for (const folder of safFolders) {
      // Check if this SAF folder covers a DJI path
      const isDjiFolder = folder.path &&
        (folder.path.includes('dji.go.v5') || folder.path.includes('dji.go.v4') ||
         folder.path.includes('FlightRecord') || folder.path.includes('com.dji'));

      if (!isDjiFolder) continue;

      onProgress?.(`Reading ${folder.path} via folder access...`);
      try {
        const safFiles = await listSAFFiles(folder.uri);
        for (const sf of safFiles) {
          if (synced.has(sf.uri)) continue;
          found.push({
            name: sf.name,
            path: sf.uri, // Use URI as the unique path
            size: sf.size,
            source: folder.path || 'SAF folder',
            safUri: sf.uri,
          });
        }
        usedSAF = true;
      } catch {
        // SAF folder access failed — permission may have been revoked
      }
    }
  }

  return { files: found, restrictedBlocked: restrictedBlocked && !usedSAF, usedSAF };
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
    if (resp.status === 403) throw new Error(`Access denied (403) at ${url}${detail}`);
    if (resp.status === 422) throw new Error(`Validation error (422) — server may not have received the API key header${detail}`);
    throw new Error(`Server returned ${resp.status} at ${url}${detail}`);
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
        let blob: Blob;

        if (file.safUri) {
          // Read via SAF plugin (for Android/data/ files)
          const safData = await readSAFFile(file.safUri);
          if (!safData) {
            result.errors.push(`${file.name}: failed to read via folder access`);
            continue;
          }
          const binary = atob(safData.data);
          const bytes = new Uint8Array(binary.length);
          for (let j = 0; j < binary.length; j++) bytes[j] = binary.charCodeAt(j);
          blob = new Blob([bytes], { type: 'application/octet-stream' });
        } else {
          // Read via Capacitor Filesystem (standard paths)
          const fileData = await Filesystem.readFile({
            path: file.path,
            directory: Directory.ExternalStorage,
          });

          if (typeof fileData.data === 'string') {
            const binary = atob(fileData.data);
            const bytes = new Uint8Array(binary.length);
            for (let j = 0; j < binary.length; j++) bytes[j] = binary.charCodeAt(j);
            blob = new Blob([bytes], { type: 'application/octet-stream' });
          } else {
            blob = fileData.data;
          }
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
        let body = '';
        try { body = await resp.text(); } catch { /* ignore */ }
        result.errors.push(`Batch upload failed: ${resp.status} at ${url}${body ? ' — ' + body.slice(0, 200) : ''}`);
        continue;
      }

      const data = await resp.json();
      result.imported += data.imported || 0;
      result.skipped += data.skipped || 0;
      if (data.errors) result.errors.push(...data.errors);

      for (const file of batch) {
        syncedPaths.push(file.safUri || file.path);
        result.files.push(file);
      }
    } catch (err: any) {
      if (err.message?.includes('API key')) throw err;
      result.errors.push(`Upload batch failed: ${err.message}`);
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
    // Can't delete SAF files (no write permission requested)
    if (file.safUri) continue;

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
