//! Opt-in, metadata-only local observations. Disabled unless
//! `FORGE_OBSERVATION_FILE` names an absolute JSONL path at first use.
//!
//! Callers must construct metadata explicitly: never pass serialized requests,
//! responses, tool arguments/results, error messages, URLs, or credentials.
//! This is separate from tracing and never enables existing payload logging.
//! Records are best effort. A bounded queue and size limit keep collection from
//! growing without limit; [`flush`] writes a cumulative health footer. Missing
//! footers or nonzero loss counters mean consumers must treat coverage as partial.

use std::fs::OpenOptions;
use std::io::{self, BufWriter, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, OnceLock, mpsc};
use std::time::{Duration, Instant};

use serde_json::{Value, json};

const QUEUE_CAPACITY: usize = 2048;
const MAX_RECORD_BYTES: usize = 16 * 1024;
const FLUSH_TIMEOUT: Duration = Duration::from_secs(2);

static SINK: OnceLock<Option<Sink>> = OnceLock::new();

#[derive(Default)]
struct Health {
    dropped_records: AtomicU64,
    write_failures: AtomicU64,
    flush_failures: AtomicU64,
    records_written: AtomicU64,
}

enum Message {
    Record(Vec<u8>),
    Flush(mpsc::Sender<()>),
}

struct Sink {
    tx: mpsc::SyncSender<Message>,
    health: Arc<Health>,
    start: Instant,
    run_id: Option<String>,
}

impl Sink {
    fn open(path: &Path, run_id: Option<String>) -> io::Result<Self> {
        if !path.is_absolute() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "absolute observation path required",
            ));
        }
        let mut options = OpenOptions::new();
        options.create(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(path)?;
        Self::with_writer(file, run_id)
    }

    fn with_writer(
        writer: impl Write + Send + 'static,
        run_id: Option<String>,
    ) -> io::Result<Self> {
        let (tx, rx) = mpsc::sync_channel(QUEUE_CAPACITY);
        let health = Arc::new(Health::default());
        let worker_health = Arc::clone(&health);
        let start = Instant::now();
        let worker_run_id = run_id.clone();
        std::thread::Builder::new()
            .name("forge-observations".into())
            .spawn(move || {
                let mut writer = BufWriter::with_capacity(64 * 1024, writer);
                while let Ok(message) = rx.recv() {
                    match message {
                        Message::Record(bytes) => {
                            if writer.write_all(&bytes).is_ok() {
                                worker_health.records_written.fetch_add(1, Ordering::Relaxed);
                            } else {
                                worker_health.write_failures.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                        Message::Flush(done) => {
                            // Flush preceding records first so the footer reflects errors
                            // found while draining the buffer, not only write_all errors.
                            if writer.flush().is_err() {
                                worker_health.write_failures.fetch_add(1, Ordering::Relaxed);
                            }
                            let mut footer = json!({
                                "event": "observation_health",
                                "dropped_records": worker_health.dropped_records.load(Ordering::Relaxed),
                                "write_failures": worker_health.write_failures.load(Ordering::Relaxed),
                                "flush_failures": worker_health.flush_failures.load(Ordering::Relaxed),
                                "records_written": worker_health.records_written.load(Ordering::Relaxed),
                                "counter_scope": "cumulative_process",
                            });
                            envelope(&mut footer, start, worker_run_id.as_deref());
                            if write_json_line(&mut writer, &footer).is_err() || writer.flush().is_err() {
                                worker_health.write_failures.fetch_add(1, Ordering::Relaxed);
                                tracing::warn!("observation sink could not flush metadata; observations may be incomplete");
                            }
                            let _ = done.send(());
                        }
                    }
                }
                let _ = writer.flush();
            })?;
        Ok(Self {
            tx,
            health,
            start,
            run_id,
        })
    }

    fn record(&self, mut event: Value) {
        if !event.is_object() {
            self.health.dropped_records.fetch_add(1, Ordering::Relaxed);
            return;
        }
        envelope(&mut event, self.start, self.run_id.as_deref());
        let mut bytes = Vec::new();
        if write_json_line(&mut bytes, &event).is_err() || bytes.len() > MAX_RECORD_BYTES {
            self.health.dropped_records.fetch_add(1, Ordering::Relaxed);
            return;
        }
        if self.tx.try_send(Message::Record(bytes)).is_err() {
            self.health.dropped_records.fetch_add(1, Ordering::Relaxed);
        }
    }

    fn flush(&self) {
        let deadline = Instant::now() + FLUSH_TIMEOUT;
        let (tx, rx) = mpsc::channel();
        let mut message = Message::Flush(tx);
        loop {
            match self.tx.try_send(message) {
                Ok(()) => break,
                Err(mpsc::TrySendError::Full(returned)) if Instant::now() < deadline => {
                    message = returned;
                    std::thread::sleep(Duration::from_millis(1));
                }
                Err(_) => {
                    self.health.flush_failures.fetch_add(1, Ordering::Relaxed);
                    tracing::warn!(
                        "observation flush could not reach the writer; observations may be incomplete"
                    );
                    return;
                }
            }
        }
        if rx
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .is_err()
        {
            self.health.flush_failures.fetch_add(1, Ordering::Relaxed);
            tracing::warn!("observation flush timed out; observations may be incomplete");
        }
    }
}

fn write_json_line(writer: &mut impl Write, value: &Value) -> io::Result<()> {
    serde_json::to_writer(&mut *writer, value).map_err(io::Error::other)?;
    writer.write_all(b"\n")
}

fn envelope(event: &mut Value, start: Instant, run_id: Option<&str>) {
    event["schema_version"] = json!(1);
    event["run_id"] = json!(run_id);
    event["process_id"] = json!(std::process::id());
    event["timestamp_unix_ms"] = json!(chrono::Utc::now().timestamp_millis());
    event["elapsed_ms"] = json!(start.elapsed().as_secs_f64() * 1000.0);
}

fn sink() -> Option<&'static Sink> {
    SINK.get_or_init(|| {
        let path = std::env::var_os("FORGE_OBSERVATION_FILE")?;
        let run_id = std::env::var("FORGE_RUN_ID").ok();
        match Sink::open(Path::new(&path), run_id) {
            Ok(sink) => Some(sink),
            Err(_) => {
                // Do not log the path or OS error: either may contain sensitive data.
                tracing::warn!("observation sink unavailable; metadata collection disabled");
                None
            }
        }
    })
    .as_ref()
}

/// Cheap after the first call. Configuration is fixed for the process lifetime.
pub fn enabled() -> bool {
    #[cfg(test)]
    if test_capture::active() {
        return true;
    }
    sink().is_some()
}

/// Record explicitly constructed metadata without blocking on disk I/O.
/// The schema/run/time envelope is assigned centrally and cannot be overridden.
pub fn record(event: Value) {
    #[cfg(test)]
    if test_capture::push(event.clone()) {
        return;
    }
    if let Some(sink) = sink() {
        sink.record(event);
    }
}

/// Thread-local capture avoids changing process environment or the production
/// OnceLock while async current-thread tests exercise the real request actor.
#[cfg(test)]
pub(crate) mod test_capture {
    use super::*;
    use std::cell::RefCell;

    thread_local! {
        static RECORDS: RefCell<Option<(Instant, Vec<Value>)>> = const { RefCell::new(None) };
    }

    pub(crate) struct Capture;
    impl Capture {
        pub(crate) fn start() -> Self {
            RECORDS.with(|records| *records.borrow_mut() = Some((Instant::now(), Vec::new())));
            Self
        }
        pub(crate) fn take(&self) -> Vec<Value> {
            RECORDS.with(|records| std::mem::take(&mut records.borrow_mut().as_mut().unwrap().1))
        }
    }
    impl Drop for Capture {
        fn drop(&mut self) {
            RECORDS.with(|records| *records.borrow_mut() = None);
        }
    }
    pub(super) fn active() -> bool {
        RECORDS.with(|records| records.borrow().is_some())
    }
    pub(super) fn push(mut event: Value) -> bool {
        RECORDS.with(|records| {
            if let Some((start, values)) = records.borrow_mut().as_mut() {
                envelope(&mut event, *start, Some("test-run"));
                values.push(event);
                true
            } else {
                false
            }
        })
    }
}

/// Flush preceding records and emit health metadata. Bounded to two seconds;
/// failures never change the sampling result. Call at headless/run shutdown.
pub fn flush() {
    if let Some(sink) = sink() {
        sink.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[derive(Clone, Default)]
    struct MemoryWriter(Arc<Mutex<Vec<u8>>>);

    impl Write for MemoryWriter {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn records_envelope_nulls_and_health_without_content() {
        let writer = MemoryWriter::default();
        let sink = Sink::with_writer(writer.clone(), Some("run-1".into())).unwrap();
        sink.record(json!({"event":"test", "first_text_ms":null, "schema_version":999}));
        sink.flush();
        let bytes = writer.0.lock().unwrap();
        let rows: Vec<Value> = std::str::from_utf8(&bytes)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["schema_version"], 1);
        assert_eq!(rows[0]["run_id"], "run-1");
        assert!(rows[0]["first_text_ms"].is_null());
        assert!(rows[0]["elapsed_ms"].as_f64().unwrap() >= 0.0);
        assert_eq!(rows[1]["event"], "observation_health");
        assert_eq!(rows[1]["dropped_records"], 0);
        assert_eq!(rows[1]["write_failures"], 0);
        assert_eq!(rows[1]["records_written"], 1);
    }

    #[test]
    fn relative_path_is_rejected_without_creating_a_file() {
        assert!(Sink::open(Path::new("relative.jsonl"), None).is_err());
    }

    #[test]
    fn invalid_and_oversize_records_are_reported_as_dropped() {
        let writer = MemoryWriter::default();
        let sink = Sink::with_writer(writer.clone(), None).unwrap();
        sink.record(json!([1, 2]));
        sink.record(json!({"event":"x", "too_large":"x".repeat(MAX_RECORD_BYTES)}));
        sink.flush();
        let bytes = writer.0.lock().unwrap();
        let footer: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(footer["dropped_records"], 2);
        assert_eq!(footer["records_written"], 0);
    }

    #[test]
    fn full_queue_drops_records_without_blocking() {
        let (tx, _rx) = mpsc::sync_channel(1);
        let sink = Sink {
            tx,
            health: Arc::new(Health::default()),
            start: Instant::now(),
            run_id: None,
        };
        sink.record(json!({"event":"one"}));
        sink.record(json!({"event":"two"}));
        assert_eq!(sink.health.dropped_records.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn writer_failure_is_nonfatal_and_counted() {
        struct FailingWriter;
        impl Write for FailingWriter {
            fn write(&mut self, _: &[u8]) -> io::Result<usize> {
                Err(io::Error::other("failure"))
            }
            fn flush(&mut self) -> io::Result<()> {
                Err(io::Error::other("failure"))
            }
        }
        let sink = Sink::with_writer(FailingWriter, None).unwrap();
        sink.record(json!({"event":"test"}));
        sink.flush();
        assert!(sink.health.write_failures.load(Ordering::Relaxed) > 0);
    }

    /// Run explicitly with `--ignored --nocapture`. This measures only the
    /// collector gate/serialization/queue path with an in-memory writer. It is
    /// not an HTTP, disk, whole-agent, or inference performance benchmark.
    #[test]
    #[ignore = "manual collector microbenchmark; not inference performance"]
    fn instrumentation_overhead_microbenchmark() {
        use std::hint::black_box;
        const RECORDS: u64 = 10_000;
        let writer = MemoryWriter::default();
        let sink = Sink::with_writer(writer, Some("benchmark".into())).unwrap();
        let event = json!({
            "event":"sampler_attempt_finished", "scope":"sampler_actor_request",
            "request_id":"benchmark-request", "attempt":1, "model":"benchmark-model",
            "backend":"chat_completions", "duration_ms":1000.0,
            "first_response_event_ms":100.0,"first_text_ms":200.0,"first_reasoning_ms":null,
            "first_tool_delta_ms":null,"visible_text_chunk_count":20,"outcome":"completed",
            "provider_usage":{"input_tokens":100,"output_tokens":20,"reasoning_tokens":null},
            "usage_is_final":true,"usage_scope":"final_response_for_this_attempt"
        });
        let disabled_start = Instant::now();
        for _ in 0..RECORDS {
            if let Some(sink) = black_box(None::<&Sink>) {
                sink.record(event.clone());
            }
        }
        let disabled_ns = disabled_start.elapsed().as_nanos() as f64 / RECORDS as f64;
        let enabled_start = Instant::now();
        for _ in 0..RECORDS {
            if let Some(sink) = black_box(Some(&sink)) {
                sink.record(event.clone());
            }
        }
        let enabled_ns = enabled_start.elapsed().as_nanos() as f64 / RECORDS as f64;
        let flush_start = Instant::now();
        sink.flush();
        let flush_ms = flush_start.elapsed().as_secs_f64() * 1000.0;
        println!(
            "{}",
            json!({
                "benchmark_scope":"collector_only_in_memory_writer_not_inference_or_disk",
                "records_requested":RECORDS,
                "disabled_gate_ns_per_record":disabled_ns,
                "enabled_record_ns_per_record":enabled_ns,
                "flush_ms":flush_ms,
                "records_written":sink.health.records_written.load(Ordering::Relaxed),
                "dropped_records":sink.health.dropped_records.load(Ordering::Relaxed),
                "write_failures":sink.health.write_failures.load(Ordering::Relaxed),
            })
        );
        assert_eq!(
            sink.health.records_written.load(Ordering::Relaxed)
                + sink.health.dropped_records.load(Ordering::Relaxed),
            RECORDS
        );
        assert_eq!(sink.health.write_failures.load(Ordering::Relaxed), 0);
    }
}
