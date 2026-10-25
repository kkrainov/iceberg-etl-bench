// Licensed to the Apache Software Foundation (ASF) under one or more contributor
// license agreements. Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License. You may obtain a
// copy of the License at http://www.apache.org/licenses/LICENSE-2.0
// Unless required by applicable law or agreed to in writing, software distributed
// under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.
//
// Derived from datafusion-contrib/datafusion-distributed@ee3eacd93af66e4ef2e2e72a253842c723f92ca5:benchmarks/remote-worker/src/main.rs
// (axum HTTP query endpoint, shared SessionContext, Iceberg integration, abort-on-drop
// query task, mimalloc). Changes: Iceberg REST catalog, memory pool with high-water
// mark, disabled disk manager, Arrow IPC results, per-query stats, cancel, structured
// errors. The Flight worker server and worker resolution return with `node` mode (step 5).

//! dfrunner: DataFusion query server for the iceberg-etl-bench harness.
//!
//! Usage: `dfrunner [single]` (serve; `node` arrives in step 5) or `dfrunner healthcheck`.
//! Configuration: a properties file (`DFRUNNER_CONFIG`, default
//! /etc/dfrunner/dfrunner.properties) plus S3 credentials from AWS_* variables.

mod catalog;
mod pool;
mod query;

use std::collections::{BTreeMap, HashMap, VecDeque};
use std::error::Error;
use std::io::{Read, Write};
use std::num::NonZeroUsize;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use datafusion::execution::SessionStateBuilder;
use datafusion::execution::disk_manager::{DiskManagerBuilder, DiskManagerMode};
use datafusion::execution::memory_pool::{GreedyMemoryPool, MemoryPool, TrackConsumersPool};
use datafusion::execution::runtime_env::RuntimeEnvBuilder;
use datafusion::prelude::{SessionConfig, SessionContext};
use datafusion_distributed_iceberg::{IcebergExt, IcebergIntegrationOptions};
use log::{error, info};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::pool::PeakPool;

#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

const DEFAULT_CONFIG: &str = "/etc/dfrunner/dfrunner.properties";
const HISTORY: usize = 512;

/// Parsed properties file. Keys: `dfrunner.*` (server), `catalog.*` (REST catalog),
/// `s3.*` (FileIO), `datafusion.*` / `iceberg.*` (session options, applied verbatim).
struct Config {
    props: BTreeMap<String, String>,
}

impl Config {
    fn load() -> Result<Self, Box<dyn Error>> {
        let path = std::env::var("DFRUNNER_CONFIG").unwrap_or_else(|_| DEFAULT_CONFIG.into());
        let text = std::fs::read_to_string(&path).map_err(|e| format!("config {path}: {e}"))?;
        let mut props = BTreeMap::new();
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let (k, v) = line.split_once('=').ok_or(format!("bad config line: {line}"))?;
            props.insert(k.trim().to_string(), v.trim().to_string());
        }
        Ok(Self { props })
    }

    fn get(&self, key: &str) -> Option<&str> {
        self.props.get(key).map(String::as_str)
    }

    fn require(&self, key: &str) -> Result<&str, String> {
        self.get(key).ok_or(format!("config key {key} is required"))
    }
}

/// "4GiB", "512MiB", "4294967296" -> bytes.
fn parse_bytes(s: &str) -> Result<usize, String> {
    let s = s.trim();
    let (num, mult) = [("TiB", 1usize << 40), ("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)]
        .iter()
        .find_map(|(suffix, m)| s.strip_suffix(suffix).map(|n| (n.trim(), *m)))
        .unwrap_or((s, 1));
    let n: f64 = num.parse().map_err(|_| format!("bad byte size {s:?}"))?;
    Ok((n * mult as f64) as usize)
}

#[derive(Serialize, Clone)]
struct Versions {
    dfrunner: &'static str,
    datafusion: &'static str,
    datafusion_distributed: &'static str,
    iceberg_rust: &'static str,
    datafusion_iceberg: &'static str,
}

const VERSIONS: Versions = Versions {
    dfrunner: env!("CARGO_PKG_VERSION"),
    datafusion: env!("DFRUNNER_DEP_DATAFUSION"),
    datafusion_distributed: env!("DFRUNNER_DEP_DATAFUSION_DISTRIBUTED"),
    iceberg_rust: env!("DFRUNNER_DEP_ICEBERG"),
    datafusion_iceberg: env!("DFRUNNER_DEP_ICEBERG_DATAFUSION"),
};

struct AppState {
    mode: &'static str,
    ctx: SessionContext,
    pool: Arc<PeakPool>,
    catalog: Arc<catalog::IcebergCatalogProvider>,
    settings: Value,
    started: Instant,
    running: Mutex<HashMap<String, tokio::task::AbortHandle>>,
    history: Mutex<VecDeque<(String, Arc<Value>)>>,
    seq: Mutex<u64>,
}

type Shared = Arc<AppState>;

#[derive(Deserialize)]
struct QueryRequest {
    sql: String,
    query_id: Option<String>,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    env_logger::builder()
        .filter_level(log::LevelFilter::Info)
        .parse_default_env()
        .init();
    let mode = std::env::args().nth(1).unwrap_or_else(|| "single".into());
    match mode.as_str() {
        "healthcheck" => healthcheck(),
        "single" => serve().await,
        other => Err(format!("unknown mode {other:?} (expected single or healthcheck)").into()),
    }
}

/// `dfrunner healthcheck`: GET /health on the local listener (no curl in the image).
fn healthcheck() -> Result<(), Box<dyn Error>> {
    let addr = std::env::var("DFRUNNER_HEALTH_ADDR").unwrap_or_else(|_| "127.0.0.1:9000".into());
    let mut s = std::net::TcpStream::connect_timeout(&addr.parse()?, Duration::from_secs(2))?;
    s.set_read_timeout(Some(Duration::from_secs(5)))?;
    s.write_all(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")?;
    let mut resp = String::new();
    s.read_to_string(&mut resp)?;
    if resp.starts_with("HTTP/1.0 200") || resp.starts_with("HTTP/1.1 200") {
        Ok(())
    } else {
        Err(format!("unhealthy: {}", resp.lines().next().unwrap_or("")).into())
    }
}

async fn serve() -> Result<(), Box<dyn Error>> {
    let cfg = Config::load()?;
    let listen = cfg.get("dfrunner.listen").unwrap_or("0.0.0.0:9000").to_string();

    // Memory: the pool DataFusion builds for a memory limit (TrackConsumersPool over
    // GreedyMemoryPool), wrapped to record its high-water mark.
    let limit = parse_bytes(cfg.require("dfrunner.memory_pool.limit")?)?;
    let inner: Arc<dyn MemoryPool> = Arc::new(TrackConsumersPool::new(
        GreedyMemoryPool::new(limit),
        NonZeroUsize::new(5).unwrap(),
    ));
    let pool = Arc::new(PeakPool::new(inner));
    let disk = match cfg.get("dfrunner.disk_manager").unwrap_or("disabled") {
        "disabled" => DiskManagerMode::Disabled,
        "os_tmp" => DiskManagerMode::OsTmpDirectory,
        other => return Err(format!("dfrunner.disk_manager: unknown value {other:?}").into()),
    };
    let disk_desc = format!("{disk:?}");
    let runtime = RuntimeEnvBuilder::new()
        .with_memory_pool(Arc::clone(&pool) as Arc<dyn MemoryPool>)
        .with_disk_manager_builder(DiskManagerBuilder::default().with_mode(disk))
        .build_arc()?;

    // Session options: every datafusion.* / iceberg.* key, applied verbatim.
    let mut config = SessionConfig::new();
    let mut applied = serde_json::Map::new();
    for (k, v) in &cfg.props {
        if k.starts_with("datafusion.") {
            config = config.set_str(k, v);
            applied.insert(k.clone(), Value::from(v.clone()));
        }
    }
    let mut builder = SessionStateBuilder::new()
        .with_default_features()
        .with_config(config)
        .with_runtime_env(runtime)
        .with_iceberg_integration(IcebergIntegrationOptions::default());
    for (k, v) in &cfg.props {
        if k.starts_with("iceberg.") {
            builder.config().as_mut().unwrap().options_mut().set(k, v)?;
            applied.insert(k.clone(), Value::from(v.clone()));
        }
    }
    let ctx = SessionContext::new_with_state(builder.build());

    // Iceberg REST catalog, registered as DataFusion catalog `<catalog.name>`.
    let catalog_name = cfg.get("catalog.name").unwrap_or("iceberg").to_string();
    let mut props: HashMap<String, String> = HashMap::new();
    props.insert("uri".into(), cfg.require("catalog.uri")?.into());
    props.insert("warehouse".into(), cfg.require("catalog.warehouse")?.into());
    for (k, v) in &cfg.props {
        if k.starts_with("s3.") {
            props.insert(k.clone(), v.clone());
        }
    }
    for (env, key) in [
        ("AWS_ACCESS_KEY_ID", "s3.access-key-id"),
        ("AWS_SECRET_ACCESS_KEY", "s3.secret-access-key"),
    ] {
        if let Ok(v) = std::env::var(env) {
            props.insert(key.into(), v);
        }
    }
    let rest = catalog::connect_rest(&catalog_name, props).await?;
    let wait = Duration::from_secs(cfg.get("catalog.startup_wait_s").unwrap_or("60").parse()?);
    let provider = catalog::IcebergCatalogProvider::new(rest, wait).await?;
    ctx.register_catalog(&catalog_name, Arc::clone(&provider) as _);

    let opts = ctx.state().config().options().clone();
    let settings = json!({
        "file": cfg.props.iter()
            .filter(|(k, _)| !k.contains("secret") && !k.contains("access-key"))
            .collect::<BTreeMap<_, _>>(),
        "applied_session_options": applied,
        "target_partitions": opts.execution.target_partitions.to_string().parse::<u64>().ok(),
        "batch_size": opts.execution.batch_size.to_string().parse::<u64>().ok(),
        "memory_pool": { "kind": format!("PeakPool({})", pool.inner_name()), "limit": limit },
        "disk_manager": disk_desc,
        "catalog": { "name": catalog_name, "uri": cfg.get("catalog.uri"),
                     "warehouse": cfg.get("catalog.warehouse") },
    });
    info!("dfrunner {} (single) settings: {settings}", VERSIONS.dfrunner);

    let state: Shared = Arc::new(AppState {
        mode: "single",
        ctx,
        pool,
        catalog: provider,
        settings,
        started: Instant::now(),
        running: Mutex::new(HashMap::new()),
        history: Mutex::new(VecDeque::new()),
        seq: Mutex::new(0),
    });
    let app = Router::new()
        .route("/health", get(|| async { Json(json!({"status": "ok"})) }))
        .route("/status", get(status))
        .route("/query", post(run_query))
        .route("/queries/{id}", get(query_stats).delete(cancel_query))
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(&listen).await?;
    info!("Started dfrunner HTTP listener on {listen}");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn status(State(st): State<Shared>) -> Json<Value> {
    let namespaces = match st.catalog.refresh().await {
        Ok(v) => json!(v),
        Err(e) => json!({ "error": e.to_string() }),
    };
    let running: Vec<String> = st.running.lock().unwrap().keys().cloned().collect();
    Json(json!({
        "mode": st.mode,
        "versions": VERSIONS,
        "settings": st.settings,
        "pool": { "limit": st.pool.limit(), "reserved": st.pool.reserved(), "peak": st.pool.peak() },
        "namespaces": namespaces,
        "running": running,
        "uptime_s": st.started.elapsed().as_secs_f64(),
    }))
}

async fn query_stats(State(st): State<Shared>, Path(id): Path<String>) -> Response {
    let found = st.history.lock().unwrap().iter().rev().find(|(q, _)| *q == id).map(|(_, v)| Arc::clone(v));
    match found {
        Some(v) => Json((*v).clone()).into_response(),
        None if st.running.lock().unwrap().contains_key(&id) => {
            (StatusCode::ACCEPTED, Json(json!({"query_id": id, "state": "running"}))).into_response()
        }
        None => (StatusCode::NOT_FOUND, Json(json!({"error": format!("unknown query {id}")}))).into_response(),
    }
}

async fn cancel_query(State(st): State<Shared>, Path(id): Path<String>) -> Response {
    match st.running.lock().unwrap().get(&id) {
        Some(handle) => {
            handle.abort();
            info!("Cancelled query {id}");
            Json(json!({"query_id": id, "cancelled": true})).into_response()
        }
        None => (StatusCode::NOT_FOUND, Json(json!({"query_id": id, "cancelled": false}))).into_response(),
    }
}

/// Aborts the query task when the request future is dropped (client went away) and
/// removes it from the running set.
struct RunningGuard {
    st: Shared,
    id: String,
    handle: tokio::task::AbortHandle,
}

impl Drop for RunningGuard {
    fn drop(&mut self) {
        self.handle.abort();
        self.st.running.lock().unwrap().remove(&self.id);
    }
}

fn unix_ms() -> u128 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0)
}

async fn run_query(State(st): State<Shared>, body: Bytes) -> Response {
    let req: QueryRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(json!({"error": format!("bad request: {e}")}))).into_response(),
    };
    let id = req.query_id.unwrap_or_else(|| {
        let mut seq = st.seq.lock().unwrap();
        *seq += 1;
        format!("q{}-{}", unix_ms(), *seq)
    });
    if st.running.lock().unwrap().contains_key(&id) {
        return (StatusCode::CONFLICT, Json(json!({"error": format!("query {id} is already running")}))).into_response();
    }

    // One query at a time is the benchmark protocol, so the pool high-water mark from
    // here to the end is this query's (concurrent queries would share it; step 5).
    let pool_at_start = st.pool.reset_peak();
    let cpu0 = query::process_cpu_s();
    let started_at = unix_ms();
    let t0 = Instant::now();
    let progress = Arc::new(query::Progress::default());
    let task = tokio::spawn(query::run(st.ctx.clone(), req.sql.clone(), Arc::clone(&progress)));
    let guard = RunningGuard { st: Arc::clone(&st), id: id.clone(), handle: task.abort_handle() };
    st.running.lock().unwrap().insert(id.clone(), task.abort_handle());
    info!("Query {id} started");

    let joined = task.await;
    let elapsed_ms = t0.elapsed().as_secs_f64() * 1000.0;
    let cpu_s = query::process_cpu_s() - cpu0;
    let peak = st.pool.peak();
    drop(guard);

    let (outcome, error) = match joined {
        Ok(Ok(out)) => (Ok(out), None),
        Ok(Err(e)) => {
            let info = query::error_info(&e);
            (Err(()), Some(info))
        }
        Err(join) if join.is_cancelled() => (Err(()), Some(query::cancelled_error())),
        Err(join) => (Err(()), Some(query::ErrorInfo {
            variant: "Panic".into(),
            contexts: vec![],
            root_message: join.to_string(),
            message: join.to_string(),
        })),
    };
    let planning_ms = *progress.planned_ms.lock().unwrap();
    let (rows, batches) = match &outcome {
        Ok(out) => (Some(out.batches.iter().map(|b| b.num_rows()).sum::<usize>()), out.batches.len()),
        Err(_) => (None, 0),
    };
    let plan = progress.plan.lock().unwrap().take();
    let stats = Arc::new(json!({
        "query_id": id,
        "state": if error.is_none() { "finished" } else { "failed" },
        "sql": req.sql.chars().take(4000).collect::<String>(),
        "started_at_ms": started_at as u64,
        "elapsed_ms": elapsed_ms,
        "planning_ms": planning_ms,
        "execution_ms": planning_ms.map(|p| elapsed_ms - p),
        "process_cpu_s": cpu_s,
        "rows": rows,
        "batches": batches,
        "pool": { "limit": st.pool.limit(), "at_start": pool_at_start, "peak": peak,
                  "at_end": st.pool.reserved() },
        "error": error,
        "metrics": plan.as_ref().map(query::metrics_summary),
    }));
    {
        let mut h = st.history.lock().unwrap();
        h.push_back((id.clone(), Arc::clone(&stats)));
        while h.len() > HISTORY {
            h.pop_front();
        }
    }

    match outcome {
        Ok(out) => match query::to_ipc(&out) {
            Ok(buf) => {
                info!("Query {id} finished: {} rows in {elapsed_ms:.1} ms, pool peak {peak}", rows.unwrap_or(0));
                let mut headers = HeaderMap::new();
                headers.insert(header::CONTENT_TYPE, HeaderValue::from_static("application/vnd.apache.arrow.stream"));
                let hv = |s: String| HeaderValue::from_str(&s).unwrap_or(HeaderValue::from_static(""));
                headers.insert("x-dfrunner-query-id", hv(id.clone()));
                headers.insert("x-dfrunner-elapsed-ms", hv(format!("{elapsed_ms:.3}")));
                headers.insert("x-dfrunner-pool-peak-bytes", hv(peak.to_string()));
                headers.insert("x-dfrunner-rows", hv(rows.unwrap_or(0).to_string()));
                (StatusCode::OK, headers, buf).into_response()
            }
            Err(e) => {
                error!("Query {id}: IPC encoding failed: {e}");
                let info = query::error_info(&e);
                (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"query_id": id, "error": info}))).into_response()
            }
        },
        Err(()) => {
            let info = stats["error"].clone();
            error!("Query {id} failed after {elapsed_ms:.1} ms: {info}");
            (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({"query_id": id, "error": info}))).into_response()
        }
    }
}
