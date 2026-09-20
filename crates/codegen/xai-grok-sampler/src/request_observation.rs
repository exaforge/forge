//! Metadata-only observers for actor requests. Direct SamplingClient users are
//! deliberately outside this scope. Existing UI latency metrics stay unchanged.

use std::sync::{Arc, Mutex};
use std::time::Instant;

use serde_json::{Value, json};
use xai_grok_sampling_types::{
    ChatCompletionChunk, ConversationRequest, SamplingError, messages, rs,
};

use crate::config::SamplerConfig;
use crate::events::{SamplingChannel, SamplingErrorInfo, SamplingEvent};
use crate::observation;
use crate::types::RequestId;

pub(crate) struct RequestObservation {
    metadata: Value,
    start: Instant,
    attempts_started: u32,
    finished: bool,
}

impl RequestObservation {
    pub(crate) fn start(
        id: &RequestId,
        request: &ConversationRequest,
        config: &SamplerConfig,
    ) -> Option<Self> {
        if !observation::enabled() {
            return None;
        }
        let mut metadata = json!({
            "scope": "sampler_actor_request",
            "request_id": id,
            "session_id": request.x_grok_session_id,
            "prompt_id": request.x_grok_req_id,
            "model": safe_model(request.model.as_deref().unwrap_or(&config.model)),
            "backend": config.api_backend,
        });
        let start = Instant::now();
        metadata["event"] = json!("sampler_request_started");
        metadata["requested_config"] = json!({
            "max_output_tokens": request.max_output_tokens.or(config.max_completion_tokens),
            "temperature": request.temperature.or(config.temperature),
            "top_p": request.top_p.or(config.top_p),
            "reasoning_effort": request.reasoning_effort.or(config.reasoning_effort).map(|v| v.as_str()),
            "fast_mode": config.fast_mode,
            "force_http1": config.force_http1,
            "max_retries": config.max_retries,
            "idle_timeout_secs": config.idle_timeout_secs,
            "prompt_cache_key_present": request.prompt_cache_key.is_some(),
            "prompt_cache_key_forwarded_by_backend": config.api_backend.forwards_prompt_cache_key(),
            "input_item_count": request.items.len(),
            "tool_count": request.tools.len(),
            "hosted_tool_count": request.hosted_tools.len(),
            "json_schema_present": request.json_schema.is_some(),
        });
        observation::record(metadata.clone());
        metadata.as_object_mut().unwrap().remove("requested_config");
        Some(Self {
            metadata,
            start,
            attempts_started: 0,
            finished: false,
        })
    }

    pub(crate) fn attempt(&mut self) -> AttemptObservation {
        self.attempts_started += 1;
        let mut metadata = self.metadata.clone();
        metadata["attempt"] = json!(self.attempts_started);
        metadata["event"] = json!("sampler_attempt_started");
        let data = AttemptData::new(metadata);
        observation::record(data.metadata.clone());
        AttemptObservation {
            data: Some(Arc::new(Mutex::new(data))),
            finished: false,
        }
    }

    pub(crate) fn finish(&mut self, outcome: &'static str) {
        if self.finished {
            return;
        }
        self.finished = true;
        let mut event = self.metadata.clone();
        event["event"] = json!("sampler_request_finished");
        event["outcome"] = json!(outcome);
        event["duration_ms"] = json!(self.start.elapsed().as_secs_f64() * 1000.0);
        event["attempts_started"] = json!(self.attempts_started);
        event["retries_started"] = json!(self.attempts_started.saturating_sub(1));
        observation::record(event);
    }
}

impl Drop for RequestObservation {
    fn drop(&mut self) {
        self.finish("interrupted");
    }
}

// Model names are configuration metadata; do not accidentally emit a URL or
// credentials if a caller misconfigures this field.
fn safe_model(model: &str) -> &str {
    if model.len() <= 256
        && !model.starts_with('/')
        && !model.contains("://")
        && !model.contains('@')
        && model
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "-_/.:".contains(c))
    {
        model
    } else {
        "redacted_invalid_model_identifier"
    }
}

pub(crate) type AttemptHandle = Option<Arc<Mutex<AttemptData>>>;

#[derive(Default)]
pub(crate) struct AttemptObservation {
    data: AttemptHandle,
    finished: bool,
}

impl AttemptObservation {
    pub(crate) fn handle(&self) -> AttemptHandle {
        self.data.clone()
    }

    pub(crate) fn finish(&mut self, outcome: &'static str, error: Option<&SamplingError>) {
        if self.finished {
            return;
        }
        self.finished = true;
        if let Some(data) = &self.data
            && let Ok(data) = data.lock()
        {
            observation::record(data.finish_event(
                outcome,
                error,
                data.start.elapsed().as_secs_f64() * 1000.0,
            ));
        }
    }
}

impl Drop for AttemptObservation {
    fn drop(&mut self) {
        self.finish("interrupted", None);
    }
}

pub(crate) struct AttemptData {
    metadata: Value,
    start: Instant,
    first_response_event_ms: Option<f64>,
    first_text_ms: Option<f64>,
    first_reasoning_ms: Option<f64>,
    first_tool_delta_ms: Option<f64>,
    visible_text_chunk_count: u64,
    generated_content_event_count: u64,
    raw_response_event_count: u64,
    raw_stream_error_count: u64,
    provider_usage: Option<Value>,
    usage_is_final: bool,
    chat_stop_seen: bool,
    messages_uncached_input: Option<u64>,
    messages_cache_read: Option<u64>,
    messages_cache_write: Option<u64>,
    messages_output: Option<u64>,
}

impl AttemptData {
    fn new(metadata: Value) -> Self {
        Self {
            metadata,
            start: Instant::now(),
            first_response_event_ms: None,
            first_text_ms: None,
            first_reasoning_ms: None,
            first_tool_delta_ms: None,
            visible_text_chunk_count: 0,
            generated_content_event_count: 0,
            raw_response_event_count: 0,
            raw_stream_error_count: 0,
            provider_usage: None,
            usage_is_final: false,
            chat_stop_seen: false,
            messages_uncached_input: None,
            messages_cache_read: None,
            messages_cache_write: None,
            messages_output: None,
        }
    }

    fn elapsed_ms(&self) -> f64 {
        self.start.elapsed().as_secs_f64() * 1000.0
    }

    fn observe_event_at(&mut self, event: &SamplingEvent, elapsed: f64) {
        match event {
            SamplingEvent::ChannelToken {
                channel: SamplingChannel::Text,
                text,
                ..
            } if !text.is_empty() => {
                self.first_text_ms.get_or_insert(elapsed);
                self.visible_text_chunk_count += 1;
                self.generated_content_event_count += 1;
            }
            SamplingEvent::ChannelToken {
                channel: SamplingChannel::Reasoning,
                text,
                ..
            } if !text.is_empty() => {
                self.first_reasoning_ms.get_or_insert(elapsed);
                self.generated_content_event_count += 1;
            }
            SamplingEvent::ToolCallDelta {
                id,
                name,
                arguments_delta,
                ..
            } if [id, name, arguments_delta]
                .into_iter()
                .any(|v| v.as_ref().is_some_and(|v| !v.is_empty())) =>
            {
                self.first_tool_delta_ms.get_or_insert(elapsed);
                self.generated_content_event_count += 1;
            }
            // StreamStarted and FirstToken are synthetic L2 events, not receipt
            // of actual provider data and not necessarily visible text.
            _ => {}
        }
    }

    fn update_messages_usage(&mut self) {
        let input = self.messages_uncached_input.map(|uncached| {
            uncached
                + self.messages_cache_read.unwrap_or(0)
                + self.messages_cache_write.unwrap_or(0)
        });
        self.provider_usage = Some(json!({
            "input_tokens": input,
            "output_tokens": self.messages_output,
            "total_tokens": null,
            "reasoning_tokens": null,
            "cached_input_tokens": self.messages_cache_read,
            "cache_creation_input_tokens": self.messages_cache_write,
            "detail_availability": "messages_cache_fields_may_default_missing_to_zero;reasoning_and_total_unreported",
        }));
    }

    fn finish_event(
        &self,
        outcome: &str,
        error: Option<&SamplingError>,
        duration_ms: f64,
    ) -> Value {
        let mut event = self.metadata.clone();
        event["event"] = json!("sampler_attempt_finished");
        event["outcome"] = json!(outcome);
        event["duration_ms"] = json!(duration_ms);
        event["first_response_event_ms"] = json!(self.first_response_event_ms);
        event["first_text_ms"] = json!(self.first_text_ms);
        event["first_reasoning_ms"] = json!(self.first_reasoning_ms);
        event["first_tool_delta_ms"] = json!(self.first_tool_delta_ms);
        event["visible_text_chunk_count"] = json!(self.visible_text_chunk_count);
        event["generated_content_event_count"] = json!(self.generated_content_event_count);
        event["raw_response_event_count"] = json!(self.raw_response_event_count);
        event["raw_stream_error_count"] = json!(self.raw_stream_error_count);
        event["timing_scope"] = json!(
            "client_observed_from_before_http_initialization;first_response_is_first_parsed_provider_event"
        );
        event["provider_usage"] = json!(self.provider_usage);
        event["usage_is_final"] = json!(self.usage_is_final);
        event["usage_scope"] = json!(if self.usage_is_final {
            "final_response_for_this_attempt"
        } else {
            "latest_reported_usage_for_this_attempt"
        });
        event["usage_semantics"] = json!(
            "input_includes_cache_reads_and_writes;output_includes_reasoning_where_reported;detail_counts_are_subsets;total_may_be_live_context_not_billable_total"
        );
        let output = self
            .provider_usage
            .as_ref()
            .and_then(|u| u["output_tokens"].as_u64());
        let first_generation = [
            self.first_text_ms,
            self.first_reasoning_ms,
            self.first_tool_delta_ms,
        ]
        .into_iter()
        .flatten()
        .reduce(f64::min);
        event["first_generation_event_ms"] = json!(first_generation);
        event["end_to_end_output_tokens_per_second"] = json!(
            output
                .filter(|n| *n > 0 && duration_ms > 0.0 && self.usage_is_final)
                .map(|n| n as f64 * 1000.0 / duration_ms)
        );
        let reasoning = self
            .provider_usage
            .as_ref()
            .and_then(|usage| usage["reasoning_tokens"].as_u64());
        // Output usage includes reasoning. Starting the denominator at the
        // first visible text/tool event would omit hidden reasoning work and
        // can produce an enormous fictitious generation rate. Missing reasoning
        // usage also leaves the numerator's scope unknown, so be conservative.
        let throughput_unavailable_reason = if !self.usage_is_final {
            Some("usage_not_final")
        } else if output.is_none() {
            Some("output_tokens_unreported")
        } else if output == Some(0) {
            Some("no_reported_output_tokens")
        } else if reasoning.is_none() {
            Some("reasoning_tokens_unreported")
        } else if reasoning.is_some_and(|tokens| tokens > 0) && self.first_reasoning_ms.is_none() {
            Some("reported_reasoning_not_streamed")
        } else if self.generated_content_event_count < 2 {
            Some("fewer_than_two_content_events")
        } else if first_generation.is_none() {
            Some("no_generation_event")
        } else if first_generation.is_some_and(|first| duration_ms <= first) {
            Some("nonpositive_generation_window")
        } else {
            None
        };
        event["throughput_unavailable_reason"] = json!(throughput_unavailable_reason);
        event["observed_output_tokens_per_second"] = json!(
            output
                .zip(first_generation)
                .filter(|_| throughput_unavailable_reason.is_none())
                .map(|(n, first)| n as f64 * 1000.0 / (duration_ms - first))
        );
        event["throughput_scope"] = json!(
            "client_observed_approximation;reported_output_including_reasoning_and_tools;includes_trailing_stream_work;reasoning_or_output_may_be_buffered;generation_window_requires_two_content_events_and_reported_reasoning_coverage;not_server_decode_speed_or_token_itl"
        );
        let (kind, status) = error.map(error_metadata).unwrap_or((None, None));
        event["error_kind"] = json!(kind);
        event["status_code"] = json!(status);
        event
    }
}

fn error_metadata(error: &SamplingError) -> (Option<&'static str>, Option<u16>) {
    let info = SamplingErrorInfo::from(error);
    (
        Some(info.kind.as_str()),
        match error {
            SamplingError::Http(error) => error.status().map(|s| s.as_u16()),
            _ => info.status_code,
        },
    )
}

pub(crate) fn observe_event(handle: &AttemptHandle, event: &SamplingEvent) {
    if let Some(handle) = handle
        && let Ok(mut data) = handle.lock()
    {
        let elapsed = data.elapsed_ms();
        data.observe_event_at(event, elapsed);
    }
}

pub(crate) fn observe_raw<T: RawObservation>(
    handle: &AttemptHandle,
    result: &Result<T, SamplingError>,
) {
    if let Some(handle) = handle
        && let Ok(mut data) = handle.lock()
    {
        match result {
            Ok(value) => {
                let elapsed = data.elapsed_ms();
                data.first_response_event_ms.get_or_insert(elapsed);
                data.raw_response_event_count += 1;
                value.observe(&mut data);
            }
            Err(_) => data.raw_stream_error_count += 1,
        }
    }
}

pub(crate) fn retry_scheduled(id: &RequestId, index: u32, max_retries: u32, error: &SamplingError) {
    if !observation::enabled() {
        return;
    }
    let (kind, status) = error_metadata(error);
    observation::record(json!({
        "event":"sampler_retry_scheduled", "scope":"sampler_actor_request", "request_id":id,
        "retry_index_within_budget":index, "max_retries":max_retries,
        "retry_budget":if matches!(error,SamplingError::DoomLoopDetected{..}) {"doom_loop"} else {"transport_or_response"},
        "error_kind":kind, "status_code":status,
    }));
}

pub(crate) trait RawObservation {
    fn observe(&self, data: &mut AttemptData);
}

impl RawObservation for ChatCompletionChunk {
    fn observe(&self, data: &mut AttemptData) {
        data.chat_stop_seen |= self
            .choices
            .iter()
            .any(|choice| choice.finish_reason.is_some());
        if let Some(usage) = &self.usage {
            data.provider_usage = Some(json!({
                "input_tokens":usage.prompt_tokens, "output_tokens":usage.completion_tokens,
                "total_tokens":usage.total_tokens,
                "reasoning_tokens":usage.completion_tokens_details.as_ref().map(|v|v.reasoning_tokens),
                "cached_input_tokens":usage.prompt_tokens_details.as_ref().map(|v|v.cached_tokens),
                "cache_creation_input_tokens":null,
                "detail_availability":"absent_detail_objects_are_null;individual_fields_may_default_missing_to_zero",
            }));
        }
        data.usage_is_final = data.chat_stop_seen && data.provider_usage.is_some();
    }
}

impl RawObservation for rs::ResponseStreamEvent {
    fn observe(&self, data: &mut AttemptData) {
        let response = match self {
            Self::ResponseCompleted(event) => &event.response,
            Self::ResponseIncomplete(event) => &event.response,
            Self::ResponseFailed(event) => &event.response,
            _ => return,
        };
        if let Some(usage) = &response.usage {
            data.provider_usage = Some(json!({
                "input_tokens":usage.input_tokens, "output_tokens":usage.output_tokens,
                "total_tokens":usage.total_tokens,
                "reasoning_tokens":usage.output_tokens_details.reasoning_tokens,
                "cached_input_tokens":usage.input_tokens_details.cached_tokens,
                "cache_creation_input_tokens":null,
                "detail_availability":"parsed_responses_usage;total_may_be_rewritten_to_live_context_by_decoder",
            }));
            data.usage_is_final = true;
        }
    }
}

impl RawObservation for messages::MessageStreamEvent {
    fn observe(&self, data: &mut AttemptData) {
        match self {
            Self::MessageStart { message } => {
                data.messages_uncached_input = Some(u64::from(message.usage.input_tokens));
                data.messages_cache_read = Some(u64::from(message.usage.cache_read_input_tokens));
                data.messages_cache_write =
                    Some(u64::from(message.usage.cache_creation_input_tokens));
                data.messages_output = Some(u64::from(message.usage.output_tokens));
                data.update_messages_usage();
            }
            Self::MessageDelta { usage, .. } => {
                if let Some(input) = usage.input_tokens {
                    data.messages_uncached_input = Some(u64::from(input));
                }
                if let Some(read) = usage.cache_read_input_tokens {
                    data.messages_cache_read = Some(u64::from(read));
                }
                if let Some(write) = usage.cache_creation_input_tokens {
                    data.messages_cache_write = Some(u64::from(write));
                }
                data.messages_output = Some(u64::from(usage.output_tokens));
                data.update_messages_usage();
            }
            Self::MessageStop => data.usage_is_final = data.provider_usage.is_some(),
            _ => {}
        }
    }
}

#[cfg(test)]
impl RawObservation for u32 {
    fn observe(&self, _: &mut AttemptData) {}
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synthetic_start_is_not_first_response_or_text() {
        let mut data = AttemptData::new(json!({}));
        data.observe_event_at(
            &SamplingEvent::StreamStarted {
                request_id: "r".into(),
                timestamp_ms: 0,
            },
            1.0,
        );
        data.observe_event_at(
            &SamplingEvent::FirstToken {
                request_id: "r".into(),
            },
            2.0,
        );
        let event = data.finish_event("cancelled", None, 100.0);
        for key in [
            "first_response_event_ms",
            "first_text_ms",
            "first_reasoning_ms",
            "first_tool_delta_ms",
            "provider_usage",
            "observed_output_tokens_per_second",
        ] {
            assert!(event[key].is_null(), "{key}");
        }
        assert_eq!(event["visible_text_chunk_count"], 0);
    }

    #[test]
    fn reasoning_tool_text_are_independent_and_content_never_serialized() {
        let mut data = AttemptData::new(json!({"request_id":"r","attempt":2}));
        data.observe_event_at(
            &SamplingEvent::ChannelToken {
                request_id: "r".into(),
                channel: SamplingChannel::Reasoning,
                text: "secret reasoning".into(),
                chunk_index: 0,
            },
            15.0,
        );
        data.observe_event_at(
            &SamplingEvent::ToolCallDelta {
                request_id: "r".into(),
                tool_index: 0,
                id: Some("secret id".into()),
                name: Some("secret tool".into()),
                arguments_delta: Some("secret args".into()),
            },
            30.0,
        );
        data.observe_event_at(
            &SamplingEvent::ChannelToken {
                request_id: "r".into(),
                channel: SamplingChannel::Text,
                text: "secret output".into(),
                chunk_index: 1,
            },
            50.0,
        );
        data.observe_event_at(
            &SamplingEvent::ChannelToken {
                request_id: "r".into(),
                channel: SamplingChannel::Text,
                text: "".into(),
                chunk_index: 2,
            },
            60.0,
        );
        data.provider_usage = Some(json!({"output_tokens":20,"reasoning_tokens":5}));
        data.usage_is_final = true;
        let error = SamplingError::EventStreamError("https://secret-url?token=secret".into());
        let event = data.finish_event("stream_failed", Some(&error), 115.0);
        assert_eq!(event["first_reasoning_ms"], 15.0);
        assert_eq!(event["first_tool_delta_ms"], 30.0);
        assert_eq!(event["first_text_ms"], 50.0);
        assert_eq!(event["visible_text_chunk_count"], 1);
        assert_eq!(event["observed_output_tokens_per_second"], 200.0);
        assert_eq!(event["error_kind"], "http");
        assert!(!event.to_string().contains("secret"));
    }

    #[test]
    fn partial_usage_never_becomes_generation_throughput() {
        let mut data = AttemptData::new(json!({}));
        data.provider_usage = Some(json!({"output_tokens":20}));
        data.first_text_ms = Some(10.0);
        let event = data.finish_event("cancelled", None, 100.0);
        assert!(event["observed_output_tokens_per_second"].is_null());
        assert!(event["end_to_end_output_tokens_per_second"].is_null());
        assert_eq!(event["usage_is_final"], false);
    }

    #[test]
    fn single_content_event_has_only_end_to_end_rate() {
        let mut data = AttemptData::new(json!({}));
        data.provider_usage = Some(json!({"output_tokens":20}));
        data.usage_is_final = true;
        data.first_text_ms = Some(10.0);
        data.generated_content_event_count = 1;
        let event = data.finish_event("completed", None, 100.0);
        assert!(event["observed_output_tokens_per_second"].is_null());
        assert_eq!(event["end_to_end_output_tokens_per_second"], 200.0);
    }

    #[test]
    fn generation_rate_requires_reported_reasoning_coverage() {
        let mut data = AttemptData::new(json!({}));
        data.usage_is_final = true;
        data.first_text_ms = Some(90.0);
        data.generated_content_event_count = 2;
        for (reasoning, unavailable_reason) in [
            (None, "reasoning_tokens_unreported"),
            (Some(15), "reported_reasoning_not_streamed"),
        ] {
            data.provider_usage = Some(json!({"output_tokens":20,"reasoning_tokens":reasoning}));
            let event = data.finish_event("completed", None, 100.0);
            assert!(event["observed_output_tokens_per_second"].is_null());
            assert_eq!(event["throughput_unavailable_reason"], unavailable_reason);
            assert_eq!(event["end_to_end_output_tokens_per_second"], 200.0);
        }
        data.provider_usage = Some(json!({"output_tokens":20,"reasoning_tokens":0}));
        let event = data.finish_event("completed", None, 100.0);
        assert!(
            event["observed_output_tokens_per_second"]
                .as_f64()
                .is_some()
        );
        assert!(event["throughput_unavailable_reason"].is_null());
    }

    #[test]
    fn messages_eof_without_stop_keeps_usage_partial() {
        let mut data = AttemptData::new(json!({}));
        let delta: messages::MessageStreamEvent = serde_json::from_value(json!({
            "type":"message_delta", "delta":{"stop_reason":null},
            "usage":{"output_tokens":5}
        }))
        .unwrap();
        delta.observe(&mut data);
        let event = data.finish_event("empty_response", None, 100.0);
        assert_eq!(event["usage_is_final"], false);
        assert!(event["provider_usage"]["input_tokens"].is_null());
    }

    #[test]
    fn chat_usage_preserves_absent_optional_details() {
        let chunk: ChatCompletionChunk = serde_json::from_value(json!({
            "id":"secret", "object":"chat.completion.chunk", "created":0,"model":"m", "choices":[],
            "usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}
        }))
        .unwrap();
        let mut data = AttemptData::new(json!({}));
        chunk.observe(&mut data);
        let usage = data.provider_usage.unwrap();
        assert_eq!(usage["input_tokens"], 10);
        assert!(usage["reasoning_tokens"].is_null());
        assert!(usage["cached_input_tokens"].is_null());
    }

    #[test]
    fn messages_usage_is_cumulative_not_summed_over_deltas() {
        let mut data = AttemptData::new(json!({}));
        for output in [5, 10] {
            let event:messages::MessageStreamEvent = serde_json::from_value(json!({
                "type":"message_delta", "delta":{"stop_reason":null},
                "usage":{"input_tokens":20,"cache_read_input_tokens":30,"cache_creation_input_tokens":4,"output_tokens":output}
            })).unwrap();
            event.observe(&mut data);
        }
        messages::MessageStreamEvent::MessageStop.observe(&mut data);
        let usage = data.provider_usage.unwrap();
        assert_eq!(usage["input_tokens"], 54);
        assert_eq!(usage["output_tokens"], 10);
        assert!(usage["reasoning_tokens"].is_null());
        assert!(usage["total_tokens"].is_null());
        assert!(data.usage_is_final);
    }

    #[test]
    fn model_metadata_cannot_be_a_url() {
        assert_eq!(safe_model("vendor/model-1"), "vendor/model-1");
        assert_eq!(
            safe_model("https://secret.example/token"),
            "redacted_invalid_model_identifier"
        );
    }
}
