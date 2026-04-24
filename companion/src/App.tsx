import { useEffect, useRef, useState } from 'react';
import {
  type LogFile,
  type SyncResult,
  type HealthResult,
  type PairingState,
  type PreflightResult,
  getConfig,
  saveConfig,
  scanForLogs,
  checkHealth,
  checkPairing,
  preflightHealth,
  uploadLogs,
  deleteSyncedFiles,
  checkStorageAccess,
  formatBytes,
  DEFAULT_SERVER_URL,
} from './sync';

type View = 'loading' | 'setup' | 'syncing' | 'done' | 'error' | 'settings' | 'diagnostic';

export default function App() {
  // Config
  const [serverUrl, setServerUrl] = useState(DEFAULT_SERVER_URL);
  const [apiKey, setApiKey] = useState('');
  const [autoDelete, setAutoDelete] = useState(false);

  // State
  const [view, setView] = useState<View>('loading');
  const [statusMsg, setStatusMsg] = useState('');
  const [progressMsg, setProgressMsg] = useState('');
  const [progress, setProgress] = useState(0);

  // Results
  const [foundFiles, setFoundFiles] = useState<LogFile[]>([]);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [healthResult, setHealthResult] = useState<HealthResult | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [deleteResult, setDeleteResult] = useState<{ deleted: number } | null>(null);
  const [scanErrors, setScanErrors] = useState<string[]>([]);

  // Diagnostics
  const [diagInfo, setDiagInfo] = useState<any>(null);

  // Settings test
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  const [testMsg, setTestMsg] = useState('');

  // Persistent pairing / preflight banner (ADR-0002 §5 layers 1+2).
  // Any non-null value renders a red banner at the top of the app with
  // actionable copy so the operator never has to open diag-log to see
  // that uploads are blocked.
  const [warning, setWarning] = useState<string | null>(null);

  const hasRun = useRef(false);

  // ── Boot: load config, auto-sync ──────────────────────────────────
  useEffect(() => {
    if (hasRun.current) return;
    hasRun.current = true;

    (async () => {
      const cfg = await getConfig();
      if (cfg.serverUrl) setServerUrl(cfg.serverUrl);
      if (cfg.apiKey) setApiKey(cfg.apiKey);
      setAutoDelete(cfg.autoDelete);

      // ADR-0002 §5 layer 1 — if Capacitor Preferences were wiped or the
      // device was never paired, surface it loudly instead of running
      // auto-sync that can only fail.
      const pairing = checkPairing({ serverUrl: cfg.serverUrl, apiKey: cfg.apiKey });
      if (!pairing.paired) {
        setWarning(pairingMessage(pairing));
        setView('setup');
        return;
      }

      await runSync(cfg.serverUrl, cfg.apiKey, cfg.autoDelete);
    })();
  }, []);

  // ── Sync pipeline ─────────────────────────────────────────────────
  async function runSync(url: string, key: string, autoDel: boolean) {
    setView('syncing');
    setProgress(0);
    setDeleteResult(null);
    setScanErrors([]);

    try {
      // Step 1: Preflight health gate (ADR-0002 §5 layer 2).
      // Structured preflight — NEVER attempt uploads against a server
      // we can't reach or a key the server has already revoked. The
      // 2026-04-23 class of failure was the companion repeatedly trying
      // to POST against a WebView-relative URL (empty serverUrl) and
      // eating the error silently. Here we bail out with a clear banner
      // BEFORE any upload attempt.
      setStatusMsg('CONNECTING');
      setProgressMsg(`Connecting to ${url.replace(/^https?:\/\//, '')}...`);
      const pre = await preflightHealth(url, key);
      if (!pre.ok) {
        setWarning(preflightMessage(pre));
        throw new Error(pre.message);
      }
      setWarning(null);
      setHealthResult(pre.health);
      setProgress(10);

      if (!pre.health.parser_available) {
        setProgressMsg('Warning: flight parser offline — uploads may fail');
        await delay(1500);
      }

      // Step 2: Scan for logs
      setStatusMsg('SCANNING');
      setProgressMsg('Scanning DJI flight log folders...');
      const scanResult = await scanForLogs((msg) => setProgressMsg(msg));
      const files = scanResult.files;
      setFoundFiles(files);
      setScanErrors(scanResult.errors);
      setProgress(30);

      if (files.length === 0) {
        setSyncResult({ imported: 0, skipped: 0, errors: [], files: [] });
        setView('done');
        return;
      }

      setProgressMsg(`Found ${files.length} log file${files.length !== 1 ? 's' : ''}`);
      await delay(500);

      // Step 3: Upload
      setStatusMsg('UPLOADING');
      const result = await uploadLogs(url, key, files, (uploaded, total, currentFile) => {
        const pct = 30 + Math.round((uploaded / total) * 60);
        setProgress(pct);
        if (currentFile) {
          setProgressMsg(`Uploading ${currentFile} (${uploaded + 1}/${total})`);
        }
      });
      setSyncResult(result);
      setProgress(95);

      // Step 4: Auto-delete if enabled
      if (autoDel && result.files.length > 0) {
        setStatusMsg('CLEANING UP');
        setProgressMsg('Removing synced logs from controller...');
        const del = await deleteSyncedFiles(result.files);
        setDeleteResult(del);
      }

      setProgress(100);
      setView('done');
    } catch (err: any) {
      setErrorMsg(err.message || 'Unknown error');
      setView('error');
    }
  }

  // ── Settings: test connection ─────────────────────────────────────
  async function testConnection() {
    setTestStatus('testing');
    setTestMsg('');
    try {
      const h = await checkHealth(serverUrl.trim(), apiKey);
      setTestStatus('ok');
      setTestMsg(`Connected — "${h.device_label}", parser ${h.parser_available ? 'online' : 'offline'}`);
    } catch (err: any) {
      setTestStatus('fail');
      setTestMsg(err.message || 'Connection failed');
    }
  }

  // ── Settings: save and sync ───────────────────────────────────────
  async function saveAndSync() {
    if (!serverUrl.trim() || !apiKey.trim()) return;
    try {
      await saveConfig(serverUrl.trim(), apiKey.trim(), autoDelete);
    } catch (err: any) {
      // validateServerUrl throws on plaintext public URLs (ADR-0002).
      // Surface to the test-banner area so the operator sees it next to
      // the URL field instead of blank-reloading.
      setTestStatus('fail');
      setTestMsg(err.message || 'Invalid server URL');
      return;
    }
    setWarning(null);
    hasRun.current = false;
    setView('loading');
    await runSync(serverUrl.trim(), apiKey.trim(), autoDelete);
  }

  // ── Diagnostics ───────────────────────────────────────────────────
  async function runDiagnostic() {
    setDiagInfo(null);
    setView('diagnostic');
    try {
      const info = await checkStorageAccess();
      setDiagInfo(info);
    } catch (err: any) {
      setDiagInfo({ error: err.message });
    }
  }

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div className="app">
      {/* Header */}
      <div className="header">
        <img src="/icon.svg" alt="" className="header-icon" />
        <h1>DRONE<span>OPS</span> SYNC</h1>
        {view !== 'setup' && view !== 'settings' && view !== 'diagnostic' && (
          <button className="header-settings" onClick={() => setView('settings')}>
            SETTINGS
          </button>
        )}
      </div>

      <div className="content">
        {/* ── WARNING BANNER (ADR-0002 §5 layer 1+2) ────────── */}
        {warning && (
          <div className="card warning-banner" role="alert" aria-live="polite">
            <div className="status-title" style={{ color: 'var(--red)' }}>
              DEVICE NOT PAIRED
            </div>
            <div className="status-detail" style={{ color: 'var(--red)' }}>
              {warning}
            </div>
            {view !== 'setup' && view !== 'settings' && (
              <button
                className="btn btn-outline"
                style={{ marginTop: 12 }}
                onClick={() => setView('settings')}
              >
                OPEN SETTINGS
              </button>
            )}
          </div>
        )}

        {/* ── LOADING ──────────────────────────────────────── */}
        {view === 'loading' && (
          <div className="card status-box">
            <div className="spinner" />
            <div className="status-title">INITIALIZING</div>
            <div className="status-detail">Loading configuration...</div>
          </div>
        )}

        {/* ── SETUP / SETTINGS ─────────────────────────────── */}
        {(view === 'setup' || view === 'settings') && (
          <>
            <div className="card">
              <div className="card-title">
                {view === 'setup' ? 'FIRST TIME SETUP' : 'CONNECTION SETTINGS'}
              </div>

              <div className="input-group">
                <label>Server URL (LAN)</label>
                <input
                  type="url"
                  placeholder="http://192.168.x.x:3080"
                  value={serverUrl}
                  onChange={(e) => { setServerUrl(e.target.value); setTestStatus('idle'); }}
                />
              </div>

              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', marginBottom: 16, lineHeight: 1.5 }}>
                Enter the LAN IP and port of your DroneOpsCommand server.
                Connect to the same network as the server before syncing.
              </div>

              <div className="input-group">
                <label>Device API Key</label>
                <input
                  type="password"
                  placeholder="Paste key from Settings → Device Access"
                  value={apiKey}
                  onChange={(e) => { setApiKey(e.target.value); setTestStatus('idle'); }}
                />
              </div>

              <button
                className="btn btn-outline"
                onClick={testConnection}
                disabled={!serverUrl.trim() || !apiKey.trim() || testStatus === 'testing'}
                style={{ marginBottom: 12 }}
              >
                {testStatus === 'testing' ? 'TESTING...' : 'TEST CONNECTION'}
              </button>

              {(testStatus === 'ok' || testStatus === 'fail') && (
                <div style={{ marginBottom: 12 }}>
                  <span className={testStatus === 'ok' ? 'badge badge-ok' : 'badge badge-err'}>
                    {testMsg}
                  </span>
                </div>
              )}

              <button
                className="btn btn-primary"
                onClick={saveAndSync}
                disabled={!serverUrl.trim() || !apiKey.trim()}
              >
                {view === 'setup' ? 'SAVE & START SYNC' : 'SAVE & SYNC NOW'}
              </button>
            </div>

            <div className="card">
              <div className="card-title">SYNC OPTIONS</div>
              <div className="toggle-row">
                <div>
                  <div className="toggle-label">Auto-delete after sync</div>
                  <div className="toggle-hint">Remove log files from controller after confirmed upload</div>
                </div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={autoDelete}
                    onChange={(e) => setAutoDelete(e.target.checked)}
                  />
                  <span className="toggle-track" />
                </label>
              </div>
            </div>

            {view === 'settings' && (
              <>
                <button
                  className="btn btn-outline"
                  onClick={() => setView('done')}
                  style={{ marginBottom: 8 }}
                >
                  BACK
                </button>
                <button
                  className="btn btn-outline"
                  onClick={runDiagnostic}
                >
                  RUN DIAGNOSTIC
                </button>
              </>
            )}
          </>
        )}

        {/* ── SYNCING ─────────────────────────────────────── */}
        {view === 'syncing' && (
          <div className="card status-box">
            <div className="spinner" />
            <div className="status-title pulse">{statusMsg}</div>
            <div className="status-detail">{progressMsg}</div>
            <div className="progress-bar">
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <div className="status-detail" style={{ marginTop: 8 }}>
              {progress}%
            </div>
          </div>
        )}

        {/* ── DONE ────────────────────────────────────────── */}
        {view === 'done' && syncResult && (
          <>
            <div className="card result-banner">
              {syncResult.imported > 0 ? (
                <>
                  <div className="result-count">{syncResult.imported}</div>
                  <div className="result-label">
                    NEW FLIGHT{syncResult.imported !== 1 ? 'S' : ''} SYNCED
                  </div>
                  <div className="result-detail">
                    DroneOps Command has been updated with {syncResult.imported} new flight
                    {syncResult.imported !== 1 ? 's' : ''}
                    {syncResult.skipped > 0 && ` — ${syncResult.skipped} duplicate${syncResult.skipped !== 1 ? 's' : ''} skipped`}
                  </div>
                  {deleteResult && (
                    <div className="result-detail" style={{ marginTop: 8 }}>
                      {deleteResult.deleted > 0
                        ? `${deleteResult.deleted} log file${deleteResult.deleted !== 1 ? 's' : ''} cleaned up from controller`
                        : 'No files deleted'}
                    </div>
                  )}
                </>
              ) : foundFiles.length === 0 ? (
                <>
                  <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.6 }}>&#10003;</div>
                  <div className="result-label">ALL SYNCED</div>
                  <div className="result-detail">
                    No new flight logs found on this device.
                    <br />All logs are up to date in DroneOps Command.
                  </div>
                </>
              ) : (
                <>
                  <div className="result-count" style={{ color: 'var(--text-muted)' }}>0</div>
                  <div className="result-label">NEW FLIGHTS</div>
                  <div className="result-detail">
                    {syncResult.skipped} duplicate{syncResult.skipped !== 1 ? 's' : ''} skipped
                    — these logs were already in DroneOps Command
                  </div>
                </>
              )}
            </div>

            {/* Scan errors (e.g. permission denied on a path) */}
            {scanErrors.length > 0 && (
              <div className="card">
                <div className="card-title" style={{ color: 'var(--orange)' }}>
                  SCAN NOTES ({scanErrors.length})
                </div>
                <div className="file-list">
                  {scanErrors.map((e, i) => (
                    <div className="file-item" key={i}>
                      <span className="name" style={{ color: 'var(--orange)' }}>{e}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* File details */}
            {foundFiles.length > 0 && (
              <div className="card">
                <div className="card-title">
                  PROCESSED FILES ({foundFiles.length})
                </div>
                <div className="file-list">
                  {foundFiles.map((f, i) => (
                    <div className="file-item" key={i}>
                      <span className="name">{f.name}</span>
                      <span className="size">{formatBytes(f.size)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Upload errors */}
            {syncResult.errors.length > 0 && (
              <div className="card">
                <div className="card-title" style={{ color: 'var(--red)' }}>
                  ERRORS ({syncResult.errors.length})
                </div>
                <div className="file-list">
                  {syncResult.errors.map((e, i) => (
                    <div className="file-item" key={i}>
                      <span className="name status-err">{e}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Connection info */}
            {healthResult && (
              <div className="card">
                <div className="card-title">CONNECTION</div>
                <div className="file-item">
                  <span className="name">Device</span>
                  <span className="badge badge-ok">{healthResult.device_label}</span>
                </div>
                <div className="file-item">
                  <span className="name">Server</span>
                  <span className="badge badge-ok">
                    {serverUrl.replace(/^https?:\/\//, '')}
                  </span>
                </div>
                <div className="file-item" style={{ borderBottom: 'none' }}>
                  <span className="name">Parser</span>
                  <span className={`badge ${healthResult.parser_available ? 'badge-ok' : 'badge-warn'}`}>
                    {healthResult.parser_available ? 'ONLINE' : 'OFFLINE'}
                  </span>
                </div>
              </div>
            )}

            <button
              className="btn btn-primary"
              onClick={() => {
                hasRun.current = false;
                setFoundFiles([]);
                setSyncResult(null);
                setDeleteResult(null);
                runSync(serverUrl, apiKey, autoDelete);
              }}
            >
              SYNC AGAIN
            </button>
          </>
        )}

        {/* ── ERROR ───────────────────────────────────────── */}
        {view === 'error' && (
          <div className="card status-box">
            <div style={{ fontSize: 48, marginBottom: 16 }}>&#9888;</div>
            <div className="status-title" style={{ color: 'var(--red)' }}>SYNC FAILED</div>
            <div className="status-detail" style={{ color: 'var(--red)' }}>
              {errorMsg}
            </div>
            <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <button
                className="btn btn-primary"
                onClick={() => {
                  hasRun.current = false;
                  setErrorMsg('');
                  runSync(serverUrl, apiKey, autoDelete);
                }}
              >
                RETRY
              </button>
              <button className="btn btn-outline" onClick={() => setView('settings')}>
                CHECK SETTINGS
              </button>
              <button className="btn btn-outline" onClick={runDiagnostic}>
                RUN DIAGNOSTIC
              </button>
            </div>
          </div>
        )}

        {/* ── DIAGNOSTIC ──────────────────────────────────── */}
        {view === 'diagnostic' && (
          <>
            <div className="card">
              <div className="card-title">STORAGE DIAGNOSTIC</div>
              {!diagInfo ? (
                <div style={{ textAlign: 'center', padding: 20 }}>
                  <div className="spinner" />
                  <div className="status-detail">Checking file access...</div>
                </div>
              ) : diagInfo.error ? (
                <div className="status-detail" style={{ color: 'var(--red)' }}>
                  Error: {diagInfo.error}
                </div>
              ) : (
                <>
                  <div className="file-item">
                    <span className="name">Android SDK</span>
                    <span className="badge badge-ok">API {diagInfo.sdkVersion}</span>
                  </div>
                  <div className="file-item">
                    <span className="name">Storage Root</span>
                    <span className="badge badge-ok">{diagInfo.storagePath}</span>
                  </div>
                  <div className="file-item">
                    <span className="name">Root Accessible</span>
                    <span className={`badge ${diagInfo.accessible ? 'badge-ok' : 'badge-err'}`}>
                      {diagInfo.accessible ? 'YES' : 'NO'}
                    </span>
                  </div>

                  <div className="card-title" style={{ marginTop: 16 }}>DJI LOG PATHS</div>
                  {diagInfo.pathResults.map((p: any, i: number) => (
                    <div className="file-item" key={i}>
                      <span className="name" style={{ fontSize: 10 }}>
                        {p.path.replace('Android/data/', 'A/d/').replace('DJI/', 'DJI/')}
                      </span>
                      <span className={`badge ${p.readable ? 'badge-ok' : p.exists ? 'badge-warn' : 'badge-err'}`}>
                        {p.readable ? 'OK' : p.exists ? 'DENIED' : 'NOT FOUND'}
                      </span>
                    </div>
                  ))}
                </>
              )}
            </div>

            <button className="btn btn-outline" onClick={() => setView('settings')}>
              BACK TO SETTINGS
            </button>
          </>
        )}
      </div>

      {/* Footer */}
      <div className="footer">
        DRONEOPSSYNC v2.62.1 — BARNARD HQ
      </div>
    </div>
  );
}

function delay(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

/** Banner copy for an unpaired device. Keep operator-friendly. */
function pairingMessage(pairing: PairingState): string {
  if (pairing.paired) return '';
  if (pairing.reason === 'missing_url') {
    return 'Server URL is not set. Open Settings and enter the DroneOps server URL.';
  }
  if (pairing.reason === 'missing_key') {
    return 'API key is not set. Open Settings → paste the key from DroneOps → Settings → Device Access.';
  }
  return `Server URL is not valid${pairing.detail ? ` (${pairing.detail})` : ''}. Open Settings.`;
}

/** Banner copy for a failed preflight health check. */
function preflightMessage(pre: PreflightResult & { ok: false }): string {
  if (pre.code === 'invalid_key' || pre.code === 'revoked_key') {
    return 'Server rejected the API key. Open Settings → Device Access on the server, copy the key, and re-paste it here.';
  }
  if (pre.code === 'unreachable') {
    return pre.message;
  }
  return pre.message || `Server error${pre.status ? ` (HTTP ${pre.status})` : ''}. Try again; if it persists, check the DroneOps server status.`;
}
