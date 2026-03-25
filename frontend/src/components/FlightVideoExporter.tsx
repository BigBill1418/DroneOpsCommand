/**
 * FlightVideoExporter — Renders the flight replay to a downloadable WebM video.
 *
 * Pipeline:
 * 1. Fetch CartoDB dark map tiles for the flight bounding box
 * 2. Composite tiles into a static map background on canvas
 * 3. For each frame: draw map bg + altitude-colored trail + drone marker + telemetry HUD
 * 4. Capture canvas stream with MediaRecorder → WebM
 * 5. Offer download when complete
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import {
  Modal,
  Stack,
  Text,
  Button,
  Group,
  Progress,
  Badge,
  Select,
  Card,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconVideo, IconDownload, IconX } from '@tabler/icons-react';

const heading = { fontFamily: "'Bebas Neue', sans-serif" };
const mono = { fontFamily: "'Share Tech Mono', monospace" };

// ── Types ──────────────────────────────────────────────────────────
interface GpsPoint {
  lat: number;
  lng: number;
  alt?: number;
  speed?: number;
  timestamp?: string;
}

interface FlightInfo {
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

interface Props {
  opened: boolean;
  onClose: () => void;
  flight: FlightInfo;
  track: GpsPoint[];
  timeOffsets: number[];
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

// Altitude to color (matches FlightReplay)
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

// ── Resolution presets ─────────────────────────────────────────────
const RESOLUTIONS: Record<string, { w: number; h: number; label: string }> = {
  '1080p': { w: 1920, h: 1080, label: '1080p (1920×1080)' },
  '720p': { w: 1280, h: 720, label: '720p (1280×720)' },
};

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

// ── Main component ─────────────────────────────────────────────────
export default function FlightVideoExporter({ opened, onClose, flight, track, timeOffsets }: Props) {
  const [resolution, setResolution] = useState<string>('1080p');
  const [rendering, setRendering] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusMsg, setStatusMsg] = useState('');
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [videoBlob, setVideoBlob] = useState<Blob | null>(null);
  const cancelRef = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Clean up blob URL on unmount
  useEffect(() => {
    return () => { if (videoUrl) URL.revokeObjectURL(videoUrl); };
  }, [videoUrl]);

  const handleClose = useCallback(() => {
    cancelRef.current = true;
    setRendering(false);
    setProgress(0);
    setStatusMsg('');
    if (videoUrl) { URL.revokeObjectURL(videoUrl); setVideoUrl(null); }
    setVideoBlob(null);
    onClose();
  }, [onClose, videoUrl]);

  const startRender = useCallback(async () => {
    if (track.length < 2) return;
    cancelRef.current = false;
    setRendering(true);
    setProgress(0);
    setVideoUrl(null);
    setVideoBlob(null);

    const res = RESOLUTIONS[resolution];
    const W = res.w;
    const H = res.h;
    const FPS = 30;
    const SIDEBAR_W = Math.round(W * 0.22);
    const MAP_W = W - SIDEBAR_W;
    const MAP_H = H - 60; // bottom bar
    const BAR_H = 60;

    // Create canvas
    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    canvasRef.current = canvas;
    const ctx = canvas.getContext('2d')!;

    // ── 1. Calculate map bounds & zoom ──────────────────────────
    setStatusMsg('Calculating map bounds...');
    let minLat = Infinity, maxLat = -Infinity, minLng = Infinity, maxLng = -Infinity;
    for (const p of track) {
      if (p.lat < minLat) minLat = p.lat;
      if (p.lat > maxLat) maxLat = p.lat;
      if (p.lng < minLng) minLng = p.lng;
      if (p.lng > maxLng) maxLng = p.lng;
    }
    // Add 15% padding
    const latPad = (maxLat - minLat) * 0.15 || 0.002;
    const lngPad = (maxLng - minLng) * 0.15 || 0.002;
    minLat -= latPad; maxLat += latPad;
    minLng -= lngPad; maxLng += lngPad;

    // Find best zoom level to fit in MAP_W x MAP_H
    let zoom = 18;
    for (let z = 18; z >= 1; z--) {
      const pxW = lng2pixel(maxLng, z) - lng2pixel(minLng, z);
      const pxH = lat2pixel(minLat, z) - lat2pixel(maxLat, z); // minLat is south = larger pixel
      if (pxW <= MAP_W && pxH <= MAP_H) { zoom = z; break; }
    }

    // Pixel offsets for the viewport
    const centerLat = (minLat + maxLat) / 2;
    const centerLng = (minLng + maxLng) / 2;
    const centerPxX = lng2pixel(centerLng, zoom);
    const centerPxY = lat2pixel(centerLat, zoom);
    const vpLeft = centerPxX - MAP_W / 2;
    const vpTop = centerPxY - MAP_H / 2;

    // Convert GPS point to canvas coordinates
    const gpsToCanvas = (lat: number, lng: number): [number, number] => {
      return [
        lng2pixel(lng, zoom) - vpLeft,
        lat2pixel(lat, zoom) - vpTop,
      ];
    };

    // ── 2. Fetch map tiles ──────────────────────────────────────
    setStatusMsg('Fetching map tiles...');
    const tileMinX = lng2tile(minLng - lngPad, zoom);
    const tileMaxX = lng2tile(maxLng + lngPad, zoom);
    const tileMinY = lat2tile(maxLat + latPad, zoom); // north = smaller tile Y
    const tileMaxY = lat2tile(minLat - latPad, zoom);

    const tilePromises: Promise<{ img: HTMLImageElement; tx: number; ty: number } | null>[] = [];
    for (let tx = tileMinX; tx <= tileMaxX; tx++) {
      for (let ty = tileMinY; ty <= tileMaxY; ty++) {
        tilePromises.push(
          fetchTile(tx, ty, zoom)
            .then(img => ({ img, tx, ty }))
            .catch(() => null)
        );
      }
    }
    const tiles = (await Promise.all(tilePromises)).filter(Boolean) as { img: HTMLImageElement; tx: number; ty: number }[];
    if (cancelRef.current) return;

    // ── 3. Render map background to offscreen canvas ────────────
    setStatusMsg('Compositing map background...');
    const mapBg = document.createElement('canvas');
    mapBg.width = MAP_W;
    mapBg.height = MAP_H;
    const mapCtx = mapBg.getContext('2d')!;
    mapCtx.fillStyle = '#050608';
    mapCtx.fillRect(0, 0, MAP_W, MAP_H);

    for (const tile of tiles) {
      // @2x tiles are 512px, standard are 256px
      const tileSize = 512; // using @2x
      const scale = 256 / tileSize; // tiles cover 256 world-pixels but image is 512px
      const drawX = tile.tx * 256 - vpLeft;
      const drawY = tile.ty * 256 - vpTop;
      mapCtx.drawImage(tile.img, drawX, drawY, 256, 256);
    }

    // ── 4. Determine frame count ────────────────────────────────
    // Target: ~30s video at 30fps for a typical flight
    const totalTime = timeOffsets[timeOffsets.length - 1] || track.length;
    // Video duration: compress flight to ~20-40s
    const videoDurationSecs = Math.min(Math.max(totalTime / 10, 15), 60);
    const totalFrames = Math.ceil(videoDurationSecs * FPS);
    const pointsPerFrame = track.length / totalFrames;

    // ── 5. Set up MediaRecorder ─────────────────────────────────
    setStatusMsg('Initializing video encoder...');
    const stream = canvas.captureStream(FPS);
    let mimeType = 'video/webm;codecs=vp9';
    if (!MediaRecorder.isTypeSupported(mimeType)) {
      mimeType = 'video/webm;codecs=vp8';
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = 'video/webm';
      }
    }
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 8_000_000 });
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };

    const recorderDone = new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        const blob = new Blob(chunks, { type: 'video/webm' });
        resolve(blob);
      };
    });

    recorder.start();

    // ── 6. Render frames ────────────────────────────────────────
    setStatusMsg('Rendering frames...');

    // Pre-compute flight stats for HUD
    const maxAltFt = flight.max_altitude * 3.28084;
    const maxSpeedMph = flight.max_speed * 2.23694;
    const distanceMiles = flight.total_distance * 0.000621371;
    const flightDate = flight.start_time ? new Date(flight.start_time).toLocaleDateString() : '';
    const aircraftName = flight.drone_name || flight.drone_model || 'Unknown Aircraft';

    for (let frame = 0; frame < totalFrames; frame++) {
      if (cancelRef.current) { recorder.stop(); return; }

      const pointIdx = Math.min(Math.floor(frame * pointsPerFrame), track.length - 1);
      const currentPoint = track[pointIdx];
      const prevPoint = pointIdx > 0 ? track[pointIdx - 1] : currentPoint;
      const hdg = calcHeading(prevPoint, currentPoint);
      const currentTime = timeOffsets[pointIdx] || 0;
      const altFt = (currentPoint.alt ?? 0) * 3.28084;
      const speedMph = (currentPoint.speed ?? 0) * 2.23694;
      const pct = pointIdx / (track.length - 1);

      // Clear canvas
      ctx.fillStyle = '#050608';
      ctx.fillRect(0, 0, W, H);

      // ── Draw map background ──
      ctx.drawImage(mapBg, 0, 0);

      // ── Draw ghost trail (full path, dimmed) ──
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(26, 31, 46, 0.6)';
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      for (let i = 0; i < track.length; i++) {
        const [px, py] = gpsToCanvas(track[i].lat, track[i].lng);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.stroke();
      ctx.setLineDash([]);

      // ── Draw altitude-colored trail (up to current point) ──
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

      // ── Draw home point ──
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

      // ── Draw start marker ──
      const [sx, sy] = gpsToCanvas(track[0].lat, track[0].lng);
      ctx.beginPath();
      ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#2ecc40';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // ── Draw drone marker ──
      const [dx, dy] = gpsToCanvas(currentPoint.lat, currentPoint.lng);
      ctx.save();
      ctx.translate(dx, dy);
      ctx.rotate((hdg * Math.PI) / 180);
      // Draw drone icon (arrow/plane shape)
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
      // Glow
      ctx.shadowColor = '#00d4ff';
      ctx.shadowBlur = 12;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.restore();

      // ── Draw sidebar panel ──
      const sbX = MAP_W;
      ctx.fillStyle = '#0e1117';
      ctx.fillRect(sbX, 0, SIDEBAR_W, H - BAR_H);
      // Sidebar border
      ctx.strokeStyle = '#1a1f2e';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(sbX, 0);
      ctx.lineTo(sbX, H - BAR_H);
      ctx.stroke();

      let yPos = 24;
      const padX = sbX + 16;
      const valX = sbX + SIDEBAR_W - 16;

      // ── Sidebar: Title ──
      ctx.font = `bold 18px 'Bebas Neue', sans-serif`;
      ctx.fillStyle = '#e8edf2';
      ctx.textAlign = 'left';
      ctx.fillText('FLIGHT TELEMETRY', padX, yPos);
      yPos += 28;

      // Aircraft name
      ctx.font = `13px 'Share Tech Mono', monospace`;
      ctx.fillStyle = '#00d4ff';
      ctx.fillText(aircraftName, padX, yPos);
      yPos += 18;

      // Date
      if (flightDate) {
        ctx.fillStyle = '#5a6478';
        ctx.fillText(flightDate, padX, yPos);
        yPos += 24;
      } else {
        yPos += 6;
      }

      // Divider
      ctx.strokeStyle = '#1a1f2e';
      ctx.beginPath();
      ctx.moveTo(padX, yPos);
      ctx.lineTo(valX, yPos);
      ctx.stroke();
      yPos += 16;

      // ── Telemetry rows ──
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

      // ALTITUDE
      drawRow('ALTITUDE', `${altFt.toFixed(0)} ft`, '#e8edf2');
      drawBar(altFt, maxAltFt, altColor(currentPoint.alt ?? 0));

      // SPEED
      drawRow('SPEED', `${speedMph.toFixed(1)} mph`, '#e8edf2');
      drawBar(speedMph, maxSpeedMph, '#ffd43b');

      // HEADING
      drawRow('HEADING', `${hdg.toFixed(0)}°`, '#e8edf2');
      yPos += 4;

      // POSITION
      drawRow('LATITUDE', currentPoint.lat.toFixed(6), '#74c0fc');
      drawRow('LONGITUDE', currentPoint.lng.toFixed(6), '#74c0fc');
      yPos += 4;

      // ELAPSED
      drawRow('ELAPSED', formatDuration(currentTime), '#da77f2');
      drawRow('TOTAL', formatDuration(totalTime), '#5a6478');
      yPos += 8;

      // Divider
      ctx.strokeStyle = '#1a1f2e';
      ctx.beginPath();
      ctx.moveTo(padX, yPos);
      ctx.lineTo(valX, yPos);
      ctx.stroke();
      yPos += 16;

      // ── Flight Stats ──
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

      // ── Altitude legend ──
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
        { label: '100–200 ft', color: '#ffd43b' },
        { label: '200–400 ft', color: '#69db7c' },
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

      // Flight name on left
      ctx.font = `bold 16px 'Bebas Neue', sans-serif`;
      ctx.fillStyle = '#e8edf2';
      ctx.textAlign = 'left';
      const nameStr = flight.name.length > 60 ? flight.name.slice(0, 57) + '...' : flight.name;
      ctx.fillText(nameStr, 16, H - BAR_H + 24);

      // Time on right
      ctx.font = `13px 'Share Tech Mono', monospace`;
      ctx.fillStyle = '#5a6478';
      ctx.textAlign = 'right';
      ctx.fillText(
        `${formatDuration(currentTime)} / ${formatDuration(totalTime)}  (${Math.round(pct * 100)}%)`,
        W - 16,
        H - BAR_H + 22,
      );

      // Progress bar
      const barY = H - 16;
      const barFullW = W - 32;
      ctx.fillStyle = '#1a1f2e';
      ctx.fillRect(16, barY, barFullW, 6);
      // Gradient fill
      const grad = ctx.createLinearGradient(16, 0, 16 + barFullW, 0);
      grad.addColorStop(0, '#2ecc40');
      grad.addColorStop(0.5, '#00d4ff');
      grad.addColorStop(1, '#ff6b1a');
      ctx.fillStyle = grad;
      ctx.fillRect(16, barY, barFullW * pct, 6);

      // Update progress
      setProgress(Math.round((frame / totalFrames) * 100));

      // Yield to UI thread — render at ~30fps pace
      await new Promise(r => setTimeout(r, 1));
    }

    // ── 7. Finalize ─────────────────────────────────────────────
    setStatusMsg('Encoding video...');
    recorder.stop();
    const blob = await recorderDone;

    if (cancelRef.current) return;

    const url = URL.createObjectURL(blob);
    setVideoUrl(url);
    setVideoBlob(blob);
    setRendering(false);
    setProgress(100);
    setStatusMsg('');

    notifications.show({
      title: 'Video Ready',
      message: `${(blob.size / 1024 / 1024).toFixed(1)} MB — click Download`,
      color: 'green',
    });
  }, [track, timeOffsets, flight, resolution]);

  const handleDownload = useCallback(() => {
    if (!videoUrl || !videoBlob) return;
    const a = document.createElement('a');
    a.href = videoUrl;
    const safeName = flight.name.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 50);
    a.download = `flight_replay_${safeName}.webm`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [videoUrl, videoBlob, flight.name]);

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title={
        <Group gap="xs">
          <IconVideo size={20} color="#00d4ff" />
          <Text fw={700} size="lg" c="#e8edf2" style={{ ...heading, letterSpacing: '2px' }}>
            EXPORT FLIGHT VIDEO
          </Text>
        </Group>
      }
      size="md"
      centered
      styles={{
        header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' },
        body: { background: '#050608' },
        content: { background: '#050608', border: '1px solid #1a1f2e' },
      }}
    >
      <Stack gap="md" pt="sm">
        {!rendering && !videoUrl && (
          <>
            <Text c="#5a6478" size="xs" style={mono}>
              Render the full flight replay as a downloadable video with map, flight path, and telemetry data overlay.
              Ideal for after-action reports and customer deliverables.
            </Text>

            <Card padding="sm" radius="md" style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
              <Stack gap="xs">
                <Group justify="space-between">
                  <Text size="xs" c="#5a6478" style={mono}>Flight</Text>
                  <Text size="xs" c="#e8edf2" style={mono} lineClamp={1}>{flight.name}</Text>
                </Group>
                <Group justify="space-between">
                  <Text size="xs" c="#5a6478" style={mono}>Aircraft</Text>
                  <Text size="xs" c="#e8edf2" style={mono}>{flight.drone_name || flight.drone_model || '—'}</Text>
                </Group>
                <Group justify="space-between">
                  <Text size="xs" c="#5a6478" style={mono}>GPS Points</Text>
                  <Text size="xs" c="#e8edf2" style={mono}>{track.length}</Text>
                </Group>
                <Group justify="space-between">
                  <Text size="xs" c="#5a6478" style={mono}>Duration</Text>
                  <Text size="xs" c="#e8edf2" style={mono}>{formatDuration(flight.duration_secs)}</Text>
                </Group>
              </Stack>
            </Card>

            <Select
              label="Resolution"
              data={Object.entries(RESOLUTIONS).map(([k, v]) => ({ value: k, label: v.label }))}
              value={resolution}
              onChange={(v) => v && setResolution(v)}
              styles={{
                input: { background: '#0e1117', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', ...mono, fontSize: 11, letterSpacing: '1px' },
                dropdown: { background: '#0e1117', borderColor: '#1a1f2e' },
                option: { color: '#e8edf2', '&[data-selected]': { background: '#00d4ff' } },
              }}
            />

            <Text c="#5a6478" size="xs" style={mono}>
              Output: WebM video ({RESOLUTIONS[resolution].label}) at 30fps.
              Estimated ~15-60 seconds of video. Rendering takes about 30 seconds.
            </Text>

            <Button
              fullWidth
              color="cyan"
              leftSection={<IconVideo size={18} />}
              onClick={startRender}
              styles={{ root: { ...heading, letterSpacing: '2px', fontSize: 16 } }}
            >
              START RENDERING
            </Button>
          </>
        )}

        {rendering && (
          <>
            <Text c="#e8edf2" fw={600} style={heading} size="lg" ta="center">
              RENDERING VIDEO...
            </Text>
            <Progress
              value={progress}
              color="cyan"
              size="xl"
              animated
              styles={{ root: { background: '#1a1f2e' } }}
            />
            <Group justify="space-between">
              <Text size="xs" c="#5a6478" style={mono}>{statusMsg}</Text>
              <Badge color="cyan" variant="light" size="lg" style={mono}>{progress}%</Badge>
            </Group>
            <Text c="#5a6478" size="xs" ta="center" style={mono}>
              Do not close this window while rendering.
            </Text>
            <Button
              variant="subtle"
              color="red"
              fullWidth
              leftSection={<IconX size={16} />}
              onClick={() => { cancelRef.current = true; setRendering(false); setStatusMsg('Cancelled'); }}
              styles={{ root: { ...mono } }}
            >
              CANCEL
            </Button>
          </>
        )}

        {videoUrl && !rendering && (
          <>
            <Text c="#e8edf2" fw={600} style={heading} size="lg" ta="center">
              VIDEO READY
            </Text>

            {/* Preview */}
            <Card padding={0} radius="md" style={{ overflow: 'hidden', border: '1px solid #1a1f2e' }}>
              <video
                src={videoUrl}
                controls
                style={{ width: '100%', display: 'block', background: '#000' }}
              />
            </Card>

            <Group justify="space-between">
              <Text size="xs" c="#5a6478" style={mono}>
                {videoBlob ? `${(videoBlob.size / 1024 / 1024).toFixed(1)} MB` : ''}
              </Text>
              <Badge color="green" variant="light" size="sm" style={mono}>
                {RESOLUTIONS[resolution].label}
              </Badge>
            </Group>

            <Button
              fullWidth
              color="cyan"
              leftSection={<IconDownload size={18} />}
              onClick={handleDownload}
              styles={{ root: { ...heading, letterSpacing: '2px', fontSize: 16 } }}
            >
              DOWNLOAD VIDEO
            </Button>

            <Button
              fullWidth
              variant="subtle"
              color="gray"
              onClick={() => { setVideoUrl(null); setVideoBlob(null); setProgress(0); }}
              styles={{ root: { ...mono } }}
            >
              RENDER AGAIN
            </Button>
          </>
        )}
      </Stack>
    </Modal>
  );
}
