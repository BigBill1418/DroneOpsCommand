use axum::{
    extract::Multipart,
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::env;
use tower_http::cors::CorsLayer;
use tracing_subscriber;

mod dji;
mod litchi;
mod airdata;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ParsedFlight {
    pub name: String,
    pub drone_model: Option<String>,
    pub drone_serial: Option<String>,
    pub battery_serial: Option<String>,
    pub start_time: Option<String>,
    pub duration_secs: f64,
    pub total_distance: f64,
    pub max_altitude: f64,
    pub max_speed: f64,
    pub home_lat: Option<f64>,
    pub home_lon: Option<f64>,
    pub point_count: usize,
    pub gps_track: Vec<TrackPoint>,
    pub telemetry: Option<TelemetryData>,
    pub battery_data: Option<BatteryData>,
    pub source: String,
    pub file_hash: String,
    pub original_filename: String,
    pub raw_metadata: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct TrackPoint {
    pub lat: f64,
    pub lng: f64,
    pub alt: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timestamp: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub speed: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub heading: Option<f64>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct TelemetryData {
    pub timestamps: Vec<String>,
    pub altitude: Vec<f64>,
    pub speed: Vec<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub battery_pct: Option<Vec<f64>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub battery_voltage: Option<Vec<f64>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub battery_temp: Option<Vec<f64>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub satellites: Option<Vec<u32>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signal_strength: Option<Vec<f64>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub distance_from_home: Option<Vec<f64>>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct BatteryData {
    pub serial: Option<String>,
    pub start_voltage: Option<f64>,
    pub end_voltage: Option<f64>,
    pub min_voltage: Option<f64>,
    pub max_temp: Option<f64>,
    pub discharge_mah: Option<f64>,
}

#[derive(Serialize)]
struct ParseResponse {
    success: bool,
    flights: Vec<ParsedFlight>,
    errors: Vec<String>,
}

#[derive(Serialize)]
struct HealthResponse {
    status: String,
    version: String,
    formats: Vec<String>,
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "healthy".to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
        formats: vec![
            "dji_txt".to_string(),
            "litchi_csv".to_string(),
            "airdata_csv".to_string(),
        ],
    })
}

async fn formats() -> Json<Vec<&'static str>> {
    Json(vec!["dji_txt", "litchi_csv", "airdata_csv"])
}

async fn parse(mut multipart: Multipart) -> Result<Json<ParseResponse>, StatusCode> {
    let mut flights = Vec::new();
    let mut errors = Vec::new();
    let dji_api_key = env::var("DJI_API_KEY").ok();

    while let Ok(Some(field)) = multipart.next_field().await {
        let filename = field.file_name().unwrap_or("unknown").to_string();
        let data = match field.bytes().await {
            Ok(d) => d,
            Err(e) => {
                errors.push(format!("{}: failed to read file: {}", filename, e));
                continue;
            }
        };

        // Compute SHA-256 hash for dedup
        let mut hasher = Sha256::new();
        hasher.update(&data);
        let hash = hex::encode(hasher.finalize());

        let lower = filename.to_lowercase();
        let result = if lower.ends_with(".txt") {
            dji::parse_dji_log(&data, &filename, &hash, dji_api_key.as_deref())
        } else if lower.ends_with(".csv") {
            // Try Litchi first, then Airdata
            match litchi::parse_litchi_csv(&data, &filename, &hash) {
                Ok(f) => Ok(f),
                Err(_) => airdata::parse_airdata_csv(&data, &filename, &hash),
            }
        } else {
            Err(format!("{}: unsupported file format (expected .txt or .csv)", filename))
        };

        match result {
            Ok(flight) => flights.push(flight),
            Err(e) => errors.push(e),
        }
    }

    Ok(Json(ParseResponse {
        success: errors.is_empty(),
        flights,
        errors,
    }))
}

#[tokio::main]
async fn main() {
    tracing_subscriber::init();

    let port = env::var("PARSER_PORT").unwrap_or_else(|_| "8100".to_string());

    let app = Router::new()
        .route("/health", get(health))
        .route("/formats", get(formats))
        .route("/parse", post(parse))
        .layer(CorsLayer::permissive());

    let addr = format!("0.0.0.0:{}", port);
    tracing::info!("flight-parser listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
