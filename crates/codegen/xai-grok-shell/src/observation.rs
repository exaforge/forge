//! Opt-in local evaluation observations. Never pass prompts, paths, arguments,
//! credentials, tool output, or error messages to this metadata-only interface.
//!
//! Intervals may overlap (especially parallel tools and nested phases). Their
//! sum is not wall time or Forge CPU time. A dropped guard denotes interrupted
//! work, including cancellation, unwinding, and early error returns.

use std::time::Instant;

use serde_json::{Value, json};
pub use xai_grok_sampler::observation::{enabled, flush, record};

/// A client-side interval with stable join keys and a monotonic duration.
pub struct Phase {
    started: Option<Instant>,
    fields: Value,
}

impl Phase {
    pub fn start(phase: &'static str, fields: impl FnOnce() -> Value) -> Self {
        if !enabled() {
            return Self {
                started: None,
                fields: Value::Null,
            };
        }
        let started = Instant::now();
        let mut fields = fields();
        if !fields.is_object() {
            fields = json!({});
        }
        fields["phase"] = json!(phase);
        fields["phase_id"] = json!(uuid::Uuid::new_v4().to_string());
        fields["event"] = json!("phase_start");
        record(fields.clone());
        Self {
            started: Some(started),
            fields,
        }
    }

    pub fn finish(mut self, status: &'static str) {
        self.end(status);
    }

    fn end(&mut self, status: &'static str) {
        if let Some(started) = self.started.take() {
            self.fields["event"] = json!("phase_end");
            self.fields["status"] = json!(status);
            self.fields["duration_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
            record(std::mem::take(&mut self.fields));
        }
    }
}

impl Drop for Phase {
    fn drop(&mut self) {
        self.end("interrupted");
    }
}
