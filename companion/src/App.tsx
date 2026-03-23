import { useEffect, useRef, useState } from 'react';
import {
  type LogFile,
  type SyncResult,
  type HealthResult,
  getConfig,
  saveConfig,
  scanForLogs,
  checkHealth,
  uploadLogs,
  deleteSyncedFiles,
  formatBytes,
  DEFAULT_SERVER_URL,
} from './sync';

// ── App states ─────────────────────────────────────────────────────────
type View = 'loading' | 'setup' | 'syncing' | 'done' | 'error' | 'settings';

export default function App() {
  // Config
  const [serverUrl, setServerUrl] = useState(DEFAULT_SERVER_URL);
  const [apiKey, setApiKey] = useState('');
  const [autoDelete, setAutoDelete] = useState(false);

  // State
  const [view, setView] = useState<View>('loading');
  const [statusMsg, setStatusMsg] = useState('');
  const [progressMsg, setProgressMsg] = useState('');
  const [progress, setProgress] = useState(0); // 0-100

  // Results
  const [foundFiles, setFoundFiles] = useState<LogFile[]>([]);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [healthResult, setHealthResult] = useState<HealthResult | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [deleteResult, setDeleteResult] = useState<{ deleted: number } | null>(null);

  // Settings test
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  const [testMsg, setTestMsg] = useState('');

  // Prevent double-run
  const hasRun = useRef(false);

  // ── Boot: load config and auto-sync ──────────────────────────────────
  useEffect(() => {
    if (hasRun.current) return;
    hasRun.current = true;

    (async () => {
      const cfg = await getConfig();
      if (cfg.serverUrl) setServerUrl(cfg.serverUrl);
      if (cfg.apiKey) setApiKey(cfg.apiKey);
      setAutoDelete(cfg.autoDelete);

      // If not configured, show setup
      if (!cfg.serverUrl || !cfg.apiKey) {
        setView('setup');
        return;
      }

      // Auto-sync
      await runSync(cfg.serverUrl, cfg.apiKey, cfg.autoDelete);
    })();
  }, []);

  // ── Sync pipeline ────────────────────────────────────────────────────
  async function runSync(url: string, key: string, autoDel: boolean) {
    setView('syncing');
    setProgress(0);
    setDeleteResult(null);

    try {
      // Step 1: Health check
      setStatusMsg('CONNECTING');
      setProgressMsg('Verifying server connection...');
      const health = await checkHealth(url, key);
      setHealthResult(health);
      setProgress(10);

      if (!health.parser_available) {
        setProgressMsg('Warning: flight parser offline — uploads may fail');
        await delay(1500);
      }

      // Step 2: Scan for logs
      setStatusMsg('SCANNING');
      setProgressMsg('Scanning DJI flight log folders...');
      const files = await scanForLogs((msg) => setProgressMsg(msg));
      setFoundFiles(files);
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

  // ── Settings: test connection ────────────────────────────────────────
  async function testConnection() {
    setTestStatus('testing');
    setTestMsg('');
    try {
      const health = await checkHealth(serverUrl, apiKey);
      setTestStatus('ok');
      setTestMsg(`Connected as "${health.device_label}" — parser ${health.parser_available ? 'online' : 'offline'}`);
    } catch (err: any) {
      setTestStatus('fail');
      setTestMsg(err.message || 'Connection failed');
    }
  }

  // ── Settings: save and sync ──────────────────────────────────────────
  async function saveAndSync() {
    if (!serverUrl.trim() || !apiKey.trim()) return;
    await saveConfig(serverUrl.trim(), apiKey.trim(), autoDelete);
    hasRun.current = false;
    setView('loading');
    await runSync(serverUrl.trim(), apiKey.trim(), autoDelete);
  }

  // ── Render ───────────────────────────────────────────────────────────
  return (
    <div className="app">
      {/* Header */}
      <div className="header">
        <img src="/icon.svg" alt="" className="header-icon" />
        <h1>DRONE<span>OPS</span> SYNC</h1>
        {view !== 'setup' && view !== 'settings' && (
          <button className="header-settings" onClick={() => setView('settings')}>
            SETTINGS
          </button>
        )}
      </div>

      <div className="content">
        {/* ── LOADING ────────────────────────────────────────────── */}
        {view === 'loading' && (
          <div className="card status-box">
            <div className="spinner" />
            <div className="status-title">INITIALIZING</div>
            <div className="status-detail">Loading configuration...</div>
          </div>
        )}

        {/* ── SETUP (first run) ─────────────────────────────────── */}
        {(view === 'setup' || view === 'settings') && (
          <>
            <div className="card">
              <div className="card-title">
                {view === 'setup' ? 'FIRST TIME SETUP' : 'CONNECTION SETTINGS'}
              </div>

              <div className="input-group">
                <label>Server URL</label>
                <input
                  type="url"
                  placeholder="https://droneops.example.com"
                  value={serverUrl}
                  onChange={(e) => { setServerUrl(e.target.value); setTestStatus('idle'); }}
                />
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

              {testStatus === 'ok' && (
                <div style={{ marginBottom: 12 }}>
                  <span className="badge badge-ok">{testMsg}</span>
                </div>
              )}
              {testStatus === 'fail' && (
                <div style={{ marginBottom: 12 }}>
                  <span className="badge badge-err">{testMsg}</span>
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
              <button
                className="btn btn-outline"
                onClick={() => {
                  setView('done');
                }}
              >
                BACK
              </button>
            )}
          </>
        )}

        {/* ── SYNCING ───────────────────────────────────────────── */}
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

        {/* ── DONE ──────────────────────────────────────────────── */}
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

            {/* Errors */}
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
                <div className="file-item" style={{ borderBottom: 'none' }}>
                  <span className="name">Device</span>
                  <span className="badge badge-ok">{healthResult.device_label}</span>
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

        {/* ── ERROR ─────────────────────────────────────────────── */}
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
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="footer">
        DRONEOPSSYNC v1.0.0 — BARNARD HQ
      </div>
    </div>
  );
}

function delay(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}
