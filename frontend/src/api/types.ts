export interface Customer {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  address: string | null;
  company: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface Aircraft {
  id: string;
  model_name: string;
  manufacturer: string;
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
  notes: string | null;
  created_at: string;
  line_items: LineItem[];
}

export interface CoverageData {
  acres: number;
  square_yards: number | null;
  num_flights: number;
  total_points: number;
}
