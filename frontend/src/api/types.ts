export interface Customer {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  state: string | null;
  zip_code: string | null;
  company: string | null;
  notes: string | null;
  tos_signed: boolean;
  tos_signed_at: string | null;
  signature_data: string | null;
  intake_completed_at: string | null;
  intake_token: string | null;
  created_at: string;
  updated_at: string;
}

export interface Aircraft {
  id: string;
  model_name: string;
  manufacturer: string;
  serial_number: string | null;
  image_filename: string | null;
  specs: Record<string, string>;
  created_at: string;
}

export interface MissionFlight {
  id: string;
  opendronelog_flight_id: string;
  aircraft_id: string | null;
  aircraft: Aircraft | null;
  flight_data_cache: Record<string, any> | null;
  added_at: string;
}

export interface MissionImage {
  id: string;
  file_path: string;
  caption: string | null;
  sort_order: number;
}

export interface Mission {
  id: string;
  customer_id: string | null;
  title: string;
  mission_type: string;
  description: string | null;
  mission_date: string | null;
  location_name: string | null;
  area_coordinates: Record<string, any> | null;
  status: string;
  is_billable: boolean;
  unas_folder_path: string | null;
  download_link_url: string | null;
  download_link_expires_at: string | null;
  client_notes: string | null;
  created_at: string;
  updated_at: string;
  flights: MissionFlight[];
  images: MissionImage[];
}

export interface Report {
  id: string;
  mission_id: string;
  user_narrative: string | null;
  llm_generated_content: string | null;
  final_content: string | null;
  ground_covered_acres: number | null;
  flight_duration_total_seconds: number | null;
  map_image_path: string | null;
  pdf_path: string | null;
  include_download_link: boolean;
  generated_at: string | null;
  sent_at: string | null;
}

export interface LineItem {
  id: string;
  description: string;
  category: string;
  quantity: number;
  unit_price: number;
  total: number;
  sort_order: number;
}

export interface Invoice {
  id: string;
  mission_id: string;
  invoice_number: string | null;
  subtotal: number;
  tax_rate: number;
  tax_amount: number;
  total: number;
  paid_in_full: boolean;
  notes: string | null;
  created_at: string;
  line_items: LineItem[];
  // ADR-0009 — operator-side deposit fields. Defaults from
  // schemas/invoice.py:InvoiceResponse so older payloads (read from a
  // pre-v2.65.0 row) deserialize fine.
  deposit_required?: boolean;
  deposit_amount?: number;
  deposit_paid?: boolean;
  deposit_paid_at?: string | null;
  deposit_payment_method?: string | null;
}

export interface RateTemplate {
  id: string;
  name: string;
  description: string | null;
  category: string;
  default_quantity: number;
  default_unit: string | null;
  default_rate: number;
  is_active: boolean;
  sort_order: number;
  created_at: string;
}

export interface CoverageData {
  acres: number;
  square_yards: number | null;
  num_flights: number;
  total_points: number;
}

export interface NominatimResult {
  display_name: string;
  address?: {
    house_number?: string;
    road?: string;
    city?: string;
    town?: string;
    village?: string;
    state?: string;
    postcode?: string;
  };
}

export interface FlightRecord {
  id?: number | string;
  display_name?: string;
  displayName?: string;
  name?: string;
  title?: string;
  file_name?: string;
  fileName?: string;
  duration_secs?: number;
  durationSecs?: number;
  duration?: number;
  duration_seconds?: number;
  total_distance?: number;
  totalDistance?: number;
  distance?: number;
  distance_meters?: number;
  max_altitude?: number;
  maxAltitude?: number;
  max_alt?: number;
  max_speed?: number;
  maxSpeed?: number;
  drone_model?: string;
  drone_name?: string;
  droneModel?: string;
  droneName?: string;
  drone?: string;
  aircraft?: string;
  aircraft_obj?: Aircraft;
  model?: string;
  start_time?: string;
  startTime?: string;
  date?: string;
  created_at?: string;
  point_count?: number;
  pointCount?: number;
  notes?: string;
  drone_serial?: string;
  droneSerial?: string;
  // Native flight fields
  home_lat?: number;
  home_lon?: number;
  source?: string;
  original_filename?: string;
  battery_serial?: string;
  tags?: string[];
  gps_track?: TrackPoint[];
  telemetry?: TelemetryData;
  aircraft_id?: string;
  updated_at?: string;
}

export interface TrackPoint {
  lat: number;
  lng: number;
  alt: number;
  timestamp?: string;
  speed?: number;
  heading?: number;
}

export interface TelemetryData {
  timestamps: string[];
  altitude: number[];
  speed: number[];
  battery_pct?: number[];
  battery_voltage?: number[];
  battery_temp?: number[];
  satellites?: number[];
  signal_strength?: number[];
  distance_from_home?: number[];
}

export interface BatteryRecord {
  id: string;
  serial: string;
  name: string | null;
  model: string | null;
  purchase_date: string | null;
  cycle_count: number;
  last_voltage: number | null;
  health_pct: number | null;
  status: string;
  notes: string | null;
  aircraft_id: string | null;
  aircraft: Aircraft | null;
  created_at: string;
  updated_at: string;
}

export interface BatteryLogRecord {
  id: string;
  battery_id: string;
  flight_id: string | null;
  timestamp: string;
  start_voltage: number | null;
  end_voltage: number | null;
  min_voltage: number | null;
  max_temp: number | null;
  cycles_at_time: number | null;
  discharge_mah: number | null;
}

export interface MaintenanceRecordType {
  id: string;
  aircraft_id: string;
  maintenance_type: string;
  description: string | null;
  performed_at: string;
  flight_hours_at: number | null;
  next_due_hours: number | null;
  next_due_date: string | null;
  cost: number | null;
  notes: string | null;
  images: string[] | null;
  created_at: string;
}

export interface MaintenanceAlert {
  schedule_id?: string;
  record_id?: string;
  aircraft_id: string;
  maintenance_type: string;
  description: string | null;
  next_due_date: string | null;
  days_until: number;
  overdue: boolean;
}
