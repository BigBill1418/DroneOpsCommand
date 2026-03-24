use axum::{
    extract::{DefaultBodyLimit, Multipart},
    http::{HeaderMap, StatusCode},
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
    dji_key_configured: bool,
}

#[derive(Serialize)]
struct DjiKeyValidation {
    status: String,
    key_source: String,
    key_present: bool,
    key_length: usize,
    dji_api_reachable: bool,
    dji_api_status: Option<u16>,
    message: String,
}

/// Resolve DJI API key: prefer X-DJI-Api-Key header, fall back to env var.
fn resolve_dji_key(headers: &HeaderMap) -> Option<String> {
    // 1. Check header from backend (source of truth from Settings UI / DB)
    if let Some(hdr) = headers.get("x-dji-api-key") {
        if let Ok(val) = hdr.to_str() {
            let trimmed = val.trim().to_string();
            if !trimmed.is_empty() {
                return Some(trimmed);
            }
        }
    }
    // 2. Fall back to environment variable (from docker-compose / .env)
    env::var("DJI_API_KEY").ok().filter(|k| !k.trim().is_empty())
}

async fn health() -> Json<HealthResponse> {
    let key_configured = env::var("DJI_API_KEY")
        .ok()
        .map_or(false, |k| !k.trim().is_empty());
    Json(HealthResponse {
        status: "healthy".to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
        formats: vec![
            "dji_txt".to_string(),
            "litchi_csv".to_string(),
            "airdata_csv".to_string(),
        ],
        dji_key_configured: key_configured,
    })
}

async fn formats() -> Json<Vec<&'static str>> {
    Json(vec!["dji_txt", "litchi_csv", "airdata_csv"])
}

async fn validate_dji_key(headers: HeaderMap) -> Json<DjiKeyValidation> {
    let api_key = resolve_dji_key(&headers);

    let (key_present, key_length, key_source) = match &api_key {
        Some(k) => {
            let source = if headers.get("x-dji-api-key").is_some() {
                "settings_db".to_string()
            } else {
                "environment".to_string()
            };
            (true, k.len(), source)
        }
        None => (false, 0, "none".to_string()),
    };

    if !key_present {
        return Json(DjiKeyValidation {
            status: "error".to_string(),
            key_source,
            key_present: false,
            key_length: 0,
            dji_api_reachable: false,
            dji_api_status: None,
            message: "No DJI API key configured. Set it in Settings > Flight Data or in .env".to_string(),
        });
    }

    let key = api_key.unwrap();

    // Test the key against DJI's API
    let client = match ureq::AgentBuilder::new()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .get("https://developer-api.dji.com/openapi/v1/manage/user/info")
        .set("Api-Key", &key)
        .call()
    {
        Ok(resp) => {
            let status = resp.status();
            return Json(DjiKeyValidation {
                status: "online".to_string(),
                key_source,
                key_present: true,
                key_length,
                dji_api_reachable: true,
                dji_api_status: Some(status),
                message: "DJI API key verified — authenticated successfully".to_string(),
            });
        }
        Err(ureq::Error::Status(status, _resp)) => {
            let (s, msg) = match status {
                401 => ("error", "DJI API key is invalid (401 Unauthorized)".to_string()),
                403 => ("online", "DJI API key accepted — flight log decryption enabled".to_string()),
                429 => ("online", "DJI API key accepted (rate limited — try again later)".to_string()),
                _ => ("online", format!("DJI API reachable (HTTP {})", status)),
            };
            return Json(DjiKeyValidation {
                status: s.to_string(),
                key_source,
                key_present: true,
                key_length,
                dji_api_reachable: true,
                dji_api_status: Some(status),
                message: msg,
            });
        }
        Err(e) => e,
    };

    // Network error — can't reach DJI (might be Docker networking)
    tracing::warn!("DJI API unreachable during key validation: {}", client);
    Json(DjiKeyValidation {
        status: "warning".to_string(),
        key_source,
        key_present: true,
        key_length,
        dji_api_reachable: false,
        dji_api_status: None,
        message: "DJI API key configured but could not reach DJI servers to verify. Key will be used for flight log decryption.".to_string(),
    })
}

async fn parse(headers: HeaderMap, mut multipart: Multipart) -> Result<Json<ParseResponse>, StatusCode> {
    let mut flights = Vec::new();
    let mut errors = Vec::new();
    let dji_api_key = resolve_dji_key(&headers);

    if dji_api_key.is_some() {
        tracing::info!("parse: DJI API key available (len={})", dji_api_key.as_ref().unwrap().len());
    } else {
        tracing::info!("parse: no DJI API key — encrypted logs will use header data only");
    }

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
    tracing_subscriber::fmt::init();

    let port = env::var("PARSER_PORT").unwrap_or_else(|_| "8100".to_string());
    let has_key = env::var("DJI_API_KEY").ok().map_or(false, |k| !k.trim().is_empty());

    let app = Router::new()
        .route("/health", get(health))
        .route("/formats", get(formats))
        .route("/parse", post(parse))
        .route("/validate-dji-key", post(validate_dji_key))
        .layer(DefaultBodyLimit::max(50 * 1024 * 1024)) // 50 MB — DJI logs can be 5-20 MB
        .layer(CorsLayer::permissive());

    let addr = format!("0.0.0.0:{}", port);
    tracing::info!(
        "flight-parser listening on {} (max body: 50 MB, dji_key_env: {})",
        addr, has_key
    );

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
