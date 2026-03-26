/**
 * renderFlightVideo — Renders the flight replay directly to a downloadable WebM video.
 *
 * No modal, no multi-step flow. Call this function → it renders → auto-downloads.
 *
 * Pipeline:
 * 1. Fetch CartoDB dark map tiles for the flight bounding box
 * 2. Composite tiles into a static map background on canvas
 * 3. For each frame: draw map bg + altitude-colored trail + drone marker + telemetry HUD
 * 4. Capture canvas stream with MediaRecorder → WebM
 * 5. Auto-download when complete
 */

import { notifications } from '@mantine/notifications';

// ── Types ──────────────────────────────────────────────────────────
export interface GpsPoint {
  lat: number;
  lng: number;
  alt?: number;
  speed?: number;
  timestamp?: string;
}

export interface FlightInfo {
  name: string;
  drone_model: string | null;
  drone_name: string | null;
  start_time: string | null;
  duration_secs: number;
  total_distance: number;
  max_altitude: number;
  max_speed: number;
  home_lat: number | null;
  home_lon: number | null;
}

// ── Mercator projection helpers ────────────────────────────────────
function lng2pixel(lng: number, zoom: number): number {
  return ((lng + 180) / 360) * Math.pow(2, zoom) * 256;
}

function lat2pixel(lat: number, zoom: number): number {
  const latRad = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * Math.pow(2, zoom) * 256;
}

function lng2tile(lng: number, zoom: number): number {
  return Math.floor(((lng + 180) / 360) * Math.pow(2, zoom));
}

function lat2tile(lat: number, zoom: number): number {
  const latRad = (lat * Math.PI) / 180;
  return Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * Math.pow(2, zoom));
}

function altColor(altMeters: number): string {
  const ft = altMeters * 3.28084;
  if (ft <= 0) return '#5a6478';
  if (ft < 100) return '#ff6b6b';
  if (ft < 200) return '#ffd43b';
  if (ft < 400) return '#69db7c';
  return '#00d4ff';
}

function calcHeading(p1: GpsPoint, p2: GpsPoint): number {
  const dLon = ((p2.lng - p1.lng) * Math.PI) / 180;
  const lat1 = (p1.lat * Math.PI) / 180;
  const lat2 = (p2.lat * Math.PI) / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function formatDuration(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

// ── Tile cache ─────────────────────────────────────────────────────
const tileCache = new Map<string, HTMLImageElement>();

async function fetchTile(x: number, y: number, z: number): Promise<HTMLImageElement> {
  const key = `${z}/${x}/${y}`;
  if (tileCache.has(key)) return tileCache.get(key)!;

  const subdomain = ['a', 'b', 'c'][Math.abs(x + y) % 3];
  const url = `https://${subdomain}.basemaps.cartocdn.com/dark_all/${z}/${x}/${y}@2x.png`;

  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => { tileCache.set(key, img); resolve(img); };
    img.onerror = () => reject(new Error(`Failed to fetch tile ${key}`));
    img.src = url;
  });
}

// ── Main export function ───────────────────────────────────────────
export async function renderFlightVideo(
  flight: FlightInfo,
  track: GpsPoint[],
  timeOffsets: number[],
  onProgress?: (pct: number, msg: string) => void,
): Promise<void> {
  if (track.length < 2) {
    notifications.show({ title: 'Export Failed', message: 'Not enough GPS points for video export.', color: 'red' });
    return;
  }

  // Check MediaRecorder support
  if (typeof MediaRecorder === 'undefined') {
    notifications.show({ title: 'Export Failed', message: 'Your browser does not support video recording. Try Chrome or Edge.', color: 'red' });
    return;
  }

  const notifId = 'flight-video-export';
  notifications.show({
    id: notifId,
    title: 'Rendering Video...',
    message: 'Fetching map tiles...',
    color: 'cyan',
    loading: true,
    autoClose: false,
    withCloseButton: false,
  });

  try {
    const W = 1920;
    const H = 1080;
    const FPS = 30;
    const SIDEBAR_W = Math.round(W * 0.22);
    const MAP_W = W - SIDEBAR_W;
    const MAP_H = H - 60;
    const BAR_H = 60;

    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d')!;

    // ── 1. Calculate map bounds & zoom ──
    let minLat = Infinity, maxLat = -Infinity, minLng = Infinity, maxLng = -Infinity;
    for (const p of track) {
      if (p.lat < minLat) minLat = p.lat;
      if (p.lat > maxLat) maxLat = p.lat;
      if (p.lng < minLng) minLng = p.lng;
      if (p.lng > maxLng) maxLng = p.lng;
    }
    const latPad = (maxLat - minLat) * 0.15 || 0.002;
    const lngPad = (maxLng - minLng) * 0.15 || 0.002;
    minLat -= latPad; maxLat += latPad;
    minLng -= lngPad; maxLng += lngPad;

    let zoom = 18;
    for (let z = 18; z >= 1; z--) {
      const pxW = lng2pixel(maxLng, z) - lng2pixel(minLng, z);
      const pxH = lat2pixel(minLat, z) - lat2pixel(maxLat, z);
      if (pxW <= MAP_W && pxH <= MAP_H) { zoom = z; break; }
    }

    const centerLng = (minLng + maxLng) / 2;
    const centerLat = (minLat + maxLat) / 2;
    const centerPxX = lng2pixel(centerLng, zoom);
    const centerPxY = lat2pixel(centerLat, zoom);
    const vpLeft = centerPxX - MAP_W / 2;
    const vpTop = centerPxY - MAP_H / 2;

    const gpsToCanvas = (lat: number, lng: number): [number, number] => [
      lng2pixel(lng, zoom) - vpLeft,
      lat2pixel(lat, zoom) - vpTop,
    ];

    // ── 2. Fetch map tiles ──
    const tileMinX = lng2tile(minLng - lngPad, zoom);
    const tileMaxX = lng2tile(maxLng + lngPad, zoom);
    const tileMinY = lat2tile(maxLat + latPad, zoom);
    const tileMaxY = lat2tile(minLat - latPad, zoom);

    const tilePromises: Promise<{ img: HTMLImageElement; tx: number; ty: number } | null>[] = [];
    for (let tx = tileMinX; tx <= tileMaxX; tx++) {
      for (let ty = tileMinY; ty <= tileMaxY; ty++) {
        tilePromises.push(
          fetchTile(tx, ty, zoom).then(img => ({ img, tx, ty })).catch(() => null)
        );
      }
    }
    const tiles = (await Promise.all(tilePromises)).filter(Boolean) as { img: HTMLImageElement; tx: number; ty: number }[];

    // ── 3. Render map background ──
    notifications.update({ id: notifId, message: 'Compositing map...', loading: true });
    const mapBg = document.createElement('canvas');
    mapBg.width = MAP_W;
    mapBg.height = MAP_H;
    const mapCtx = mapBg.getContext('2d')!;
    mapCtx.fillStyle = '#050608';
    mapCtx.fillRect(0, 0, MAP_W, MAP_H);

    for (const tile of tiles) {
      const drawX = tile.tx * 256 - vpLeft;
      const drawY = tile.ty * 256 - vpTop;
      mapCtx.drawImage(tile.img, drawX, drawY, 256, 256);
    }

    // ── 4. Determine frame count ──
    const totalTime = timeOffsets[timeOffsets.length - 1] || track.length;
    const videoDurationSecs = Math.min(Math.max(totalTime / 10, 15), 60);
    const totalFrames = Math.ceil(videoDurationSecs * FPS);
    const pointsPerFrame = track.length / totalFrames;

    // ── 5. Set up MediaRecorder ──
    notifications.update({ id: notifId, message: 'Initializing encoder...', loading: true });
    const stream = canvas.captureStream(FPS);
    let mimeType = 'video/webm;codecs=vp9';
    if (!MediaRecorder.isTypeSupported(mimeType)) {
      mimeType = 'video/webm;codecs=vp8';
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = 'video/webm';
      }
    }

    if (!MediaRecorder.isTypeSupported(mimeType)) {
      notifications.update({ id: notifId, title: 'Export Failed', message: 'No supported video codec found in this browser.', color: 'red', loading: false, autoClose: 5000 });
      return;
    }

    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 8_000_000 });
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };

    const recorderDone = new Promise<Blob>((resolve) => {
      recorder.onstop = () => resolve(new Blob(chunks, { type: 'video/webm' }));
    });

    recorder.start();

    // ── 6. Pre-compute flight stats ──
    const maxAltFt = flight.max_altitude * 3.28084;
    const maxSpeedMph = flight.max_speed * 2.23694;
    const distanceMiles = flight.total_distance * 0.000621371;
    const flightDate = flight.start_time ? new Date(flight.start_time).toLocaleDateString() : '';
    const aircraftName = flight.drone_name || flight.drone_model || 'Unknown Aircraft';

    // ── 7. Render frames ──
    for (let frame = 0; frame < totalFrames; frame++) {
      const pointIdx = Math.min(Math.floor(frame * pointsPerFrame), track.length - 1);
      const currentPoint = track[pointIdx];
      const prevPoint = pointIdx > 0 ? track[pointIdx - 1] : currentPoint;
      const hdg = calcHeading(prevPoint, currentPoint);
      const currentTime = timeOffsets[pointIdx] || 0;
      const altFt = (currentPoint.alt ?? 0) * 3.28084;
      const speedMph = (currentPoint.speed ?? 0) * 2.23694;
      const pct = pointIdx / (track.length - 1);

      // Clear
      ctx.fillStyle = '#050608';
      ctx.fillRect(0, 0, W, H);

      // Map background
      ctx.drawImage(mapBg, 0, 0);

      // Ghost trail
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(26, 31, 46, 0.6)';
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      for (let i = 0; i < track.length; i++) {
        const [px, py] = gpsToCanvas(track[i].lat, track[i].lng);
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
      ctx.setLineDash([]);

      // Altitude-colored trail
      if (pointIdx > 0) {
        for (let i = 1; i <= pointIdx; i++) {
          const [x1, y1] = gpsToCanvas(track[i - 1].lat, track[i - 1].lng);
          const [x2, y2] = gpsToCanvas(track[i].lat, track[i].lng);
          ctx.beginPath();
          ctx.strokeStyle = altColor(track[i].alt ?? 0);
          ctx.lineWidth = 4;
          ctx.lineCap = 'round';
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
          ctx.stroke();
        }
      }

      // Home point
      if (flight.home_lat && flight.home_lon) {
        const [hx, hy] = gpsToCanvas(flight.home_lat, flight.home_lon);
        ctx.beginPath();
        ctx.arc(hx, hy, 6, 0, Math.PI * 2);
        ctx.fillStyle = '#ff6b6b';
        ctx.fill();
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Start marker
      const [sx, sy] = gpsToCanvas(track[0].lat, track[0].lng);
      ctx.beginPath();
      ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#2ecc40';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Drone marker
      const [dx, dy] = gpsToCanvas(currentPoint.lat, currentPoint.lng);
      ctx.save();
      ctx.translate(dx, dy);
      ctx.rotate((hdg * Math.PI) / 180);
      ctx.beginPath();
      ctx.moveTo(0, -14);
      ctx.lineTo(4, -2);
      ctx.lineTo(12, 2);
      ctx.lineTo(4, 4);
      ctx.lineTo(4, 12);
      ctx.lineTo(0, 8);
      ctx.lineTo(-4, 12);
      ctx.lineTo(-4, 4);
      ctx.lineTo(-12, 2);
      ctx.lineTo(-4, -2);
      ctx.closePath();
      ctx.fillStyle = '#00d4ff';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1;
      ctx.fill();
      ctx.stroke();
      ctx.shadowColor = '#00d4ff';
      ctx.shadowBlur = 12;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.restore();

      // ── Sidebar panel ──
      const sbX = MAP_W;
      ctx.fillStyle = '#0e1117';
      ctx.fillRect(sbX, 0, SIDEBAR_W, H - BAR_H);
      ctx.strokeStyle = '#1a1f2e';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(sbX, 0);
      ctx.lineTo(sbX, H - BAR_H);
      ctx.stroke();

      let yPos = 24;
      const padX = sbX + 16;
      const valX = sbX + SIDEBAR_W - 16;

      ctx.font = `bold 18px 'Bebas Neue', sans-serif`;
      ctx.fillStyle = '#e8edf2';
      ctx.textAlign = 'left';
      ctx.fillText('FLIGHT TELEMETRY', padX, yPos);
      yPos += 28;

      ctx.font = `13px 'Share Tech Mono', monospace`;
      ctx.fillStyle = '#00d4ff';
      ctx.fillText(aircraftName, padX, yPos);
      yPos += 18;

      if (flightDate) {
        ctx.fillStyle = '#5a6478';
        ctx.fillText(flightDate, padX, yPos);
        yPos += 24;
      } else {
        yPos += 6;
      }

      ctx.strokeStyle = '#1a1f2e';
      ctx.beginPath();
      ctx.moveTo(padX, yPos);
      ctx.lineTo(valX, yPos);
      ctx.stroke();
      yPos += 16;

      const drawRow = (label: string, value: string, color: string) => {
        ctx.font = `12px 'Share Tech Mono', monospace`;
        ctx.fillStyle = '#5a6478';
        ctx.textAlign = 'left';
        ctx.fillText(label, padX, yPos);
        ctx.fillStyle = color;
        ctx.textAlign = 'right';
        ctx.fillText(value, valX, yPos);
        yPos += 22;
      };

      const drawBar = (value: number, max: number, color: string) => {
        const barW = SIDEBAR_W - 32;
        const barH = 4;
        ctx.fillStyle = '#1a1f2e';
        ctx.fillRect(padX, yPos, barW, barH);
        const fillW = max > 0 ? (value / max) * barW : 0;
        ctx.fillStyle = color;
        ctx.fillRect(padX, yPos, Math.min(fillW, barW), barH);
        yPos += 14;
      };

      drawRow('ALTITUDE', `${altFt.toFixed(0)} ft`, '#e8edf2');
      drawBar(altFt, maxAltFt, altColor(currentPoint.alt ?? 0));
      drawRow('SPEED', `${speedMph.toFixed(1)} mph`, '#e8edf2');
      drawBar(speedMph, maxSpeedMph, '#ffd43b');
      drawRow('HEADING', `${hdg.toFixed(0)}\u00B0`, '#e8edf2');
      yPos += 4;
      drawRow('LATITUDE', currentPoint.lat.toFixed(6), '#74c0fc');
      drawRow('LONGITUDE', currentPoint.lng.toFixed(6), '#74c0fc');
      yPos += 4;
      drawRow('ELAPSED', formatDuration(currentTime), '#da77f2');
      drawRow('TOTAL', formatDuration(totalTime), '#5a6478');
      yPos += 8;

      ctx.strokeStyle = '#1a1f2e';
      ctx.beginPath();
      ctx.moveTo(padX, yPos);
      ctx.lineTo(valX, yPos);
      ctx.stroke();
      yPos += 16;

      ctx.font = `bold 16px 'Bebas Neue', sans-serif`;
      ctx.fillStyle = '#e8edf2';
      ctx.textAlign = 'left';
      ctx.fillText('FLIGHT STATS', padX, yPos);
      yPos += 22;

      drawRow('DURATION', formatDuration(flight.duration_secs), '#e8edf2');
      drawRow('DISTANCE', `${distanceMiles.toFixed(2)} mi`, '#e8edf2');
      drawRow('MAX ALT', `${maxAltFt.toFixed(0)} ft`, '#e8edf2');
      drawRow('MAX SPEED', `${maxSpeedMph.toFixed(1)} mph`, '#e8edf2');
      drawRow('GPS POINTS', `${track.length}`, '#e8edf2');
      yPos += 8;

      ctx.strokeStyle = '#1a1f2e';
      ctx.beginPath();
      ctx.moveTo(padX, yPos);
      ctx.lineTo(valX, yPos);
      ctx.stroke();
      yPos += 16;

      ctx.font = `bold 14px 'Bebas Neue', sans-serif`;
      ctx.fillStyle = '#e8edf2';
      ctx.textAlign = 'left';
      ctx.fillText('ALTITUDE SCALE', padX, yPos);
      yPos += 18;

      const legend = [
        { label: 'Ground', color: '#5a6478' },
        { label: '< 100 ft', color: '#ff6b6b' },
        { label: '100-200 ft', color: '#ffd43b' },
        { label: '200-400 ft', color: '#69db7c' },
        { label: '400+ ft', color: '#00d4ff' },
      ];
      for (const item of legend) {
        ctx.beginPath();
        ctx.arc(padX + 5, yPos - 4, 5, 0, Math.PI * 2);
        ctx.fillStyle = item.color;
        ctx.fill();
        ctx.font = `11px 'Share Tech Mono', monospace`;
        ctx.fillStyle = '#5a6478';
        ctx.textAlign = 'left';
        ctx.fillText(item.label, padX + 16, yPos);
        yPos += 16;
      }

      // ── Bottom progress bar ──
      ctx.fillStyle = '#0a0c10';
      ctx.fillRect(0, H - BAR_H, W, BAR_H);
      ctx.strokeStyle = '#1a1f2e';
      ctx.beginPath();
      ctx.moveTo(0, H - BAR_H);
      ctx.lineTo(W, H - BAR_H);
      ctx.stroke();

      ctx.font = `bold 16px 'Bebas Neue', sans-serif`;
      ctx.fillStyle = '#e8edf2';
      ctx.textAlign = 'left';
      const nameStr = flight.name.length > 60 ? flight.name.slice(0, 57) + '...' : flight.name;
      ctx.fillText(nameStr, 16, H - BAR_H + 24);

      ctx.font = `13px 'Share Tech Mono', monospace`;
      ctx.fillStyle = '#5a6478';
      ctx.textAlign = 'right';
      ctx.fillText(
        `${formatDuration(currentTime)} / ${formatDuration(totalTime)}  (${Math.round(pct * 100)}%)`,
        W - 16,
        H - BAR_H + 22,
      );

      const barY = H - 16;
      const barFullW = W - 32;
      ctx.fillStyle = '#1a1f2e';
      ctx.fillRect(16, barY, barFullW, 6);
      const grad = ctx.createLinearGradient(16, 0, 16 + barFullW, 0);
      grad.addColorStop(0, '#2ecc40');
      grad.addColorStop(0.5, '#00d4ff');
      grad.addColorStop(1, '#ff6b1a');
      ctx.fillStyle = grad;
      ctx.fillRect(16, barY, barFullW * pct, 6);

      // Update progress notification every 10%
      const progressPct = Math.round((frame / totalFrames) * 100);
      if (frame % Math.ceil(totalFrames / 20) === 0) {
        notifications.update({ id: notifId, message: `Rendering... ${progressPct}%`, loading: true });
        onProgress?.(progressPct, 'Rendering...');
      }

      // Yield to UI thread
      await new Promise(r => setTimeout(r, 1));
    }

    // ── 8. Finalize ──
    notifications.update({ id: notifId, message: 'Encoding video...', loading: true });
    recorder.stop();
    const blob = await recorderDone;

    // Auto-download
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const safeName = flight.name.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 50);
    a.download = `flight_replay_${safeName}.webm`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    // Clean up after a short delay (browser needs the URL alive for the download)
    setTimeout(() => URL.revokeObjectURL(url), 5000);

    const sizeMB = (blob.size / 1024 / 1024).toFixed(1);
    notifications.update({
      id: notifId,
      title: 'Video Downloaded',
      message: `${sizeMB} MB WebM file saved.`,
      color: 'green',
      loading: false,
      autoClose: 5000,
      withCloseButton: true,
    });
    onProgress?.(100, 'Done');

  } catch (err) {
    console.error('Flight video export failed:', err);
    notifications.update({
      id: notifId,
      title: 'Export Failed',
      message: err instanceof Error ? err.message : 'Unknown error during video rendering.',
      color: 'red',
      loading: false,
      autoClose: 5000,
      withCloseButton: true,
    });
  }
}
