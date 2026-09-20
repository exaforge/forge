use std::collections::HashMap;

const MAX_LINES_READ_DEFAULT: usize = 1_000;

/// Opt-in defaults for local coding tools. Explicit tool/config limits win.
/// Kept in Resources only when a caller needs to pin the policy (e.g. tests);
/// normal calls resolve the process flag without mutating shared configuration.
#[derive(Debug, Clone, Copy, Default)]
pub(crate) struct ToolOutputBudget(pub(crate) bool);

impl ToolOutputBudget {
    pub(crate) fn from_env() -> Self {
        Self::from_value(std::env::var("FORGE_TOOL_OUTPUT_BUDGET").ok().as_deref())
    }

    fn from_value(value: Option<&str>) -> Self {
        Self(value == Some("1"))
    }

    pub(crate) fn read_limit(self, config: &TruncationConfig, requested: Option<usize>) -> usize {
        if self.0 && requested.is_none() && config.max_lines_read.is_none() {
            300
        } else {
            requested.unwrap_or(usize::MAX).min(config.max_lines_read())
        }
    }

    pub(crate) fn terminal_limit(
        self,
        config: &TruncationConfig,
        configured: Option<usize>,
    ) -> usize {
        let fallback = configured.unwrap_or(if self.0 {
            8_000
        } else {
            crate::DEFAULT_TOOL_OUTPUT_CHARS
        });
        config.max_output_bytes_for("run_terminal_cmd", fallback)
    }
}

/// Client-configurable truncation settings.
/// All fields are optional — `None` means "use the tool's built-in default".
///
/// There is deliberately no per-line cap: clipping long lines silently
/// corrupts single-line files (minified JSON, data dumps) with no way for
/// the model to recover the clipped bytes. Non-skill reads are bounded by
/// the whole-read `MAX_NUM_TOKENS` cap instead (skill files are exempt from
/// all read limits by design). Other agent CLIs likewise apply no
/// per-line cap. The wire field (`TruncationConfig.max_chars_per_line` in
/// grok-tools.proto) is deprecated and ignored.
#[derive(Debug, Clone, Default)]
pub struct TruncationConfig {
    /// Max total output bytes for any tool. Default: 40KB.
    pub default_max_output_bytes: Option<usize>,
    /// Per-tool overrides keyed by canonical tool name.
    pub per_tool_max_output_bytes: HashMap<String, usize>,
    /// Max lines to read (read_file). Default: 1000.
    pub max_lines_read: Option<usize>,
    /// Inline cap for MCP tool results only (bytes). Consulted by the MCP
    /// truncation path (`mcp_max_output_bytes_for`) between the per-tool map
    /// and `default_max_output_bytes`. Deliberately separate from
    /// `default_max_output_bytes` so an MCP-specific override (e.g. a repo's
    /// `[mcp] max_output_bytes`) never changes non-MCP readers like the
    /// opencode bash cap.
    pub mcp_max_output_bytes: Option<usize>,
}

impl TruncationConfig {
    /// Resolved max lines per `read_file` window.
    pub fn max_lines_read(&self) -> usize {
        self.max_lines_read.unwrap_or(MAX_LINES_READ_DEFAULT)
    }

    /// Resolve the max output bytes for a specific tool.
    ///
    /// Precedence: per-tool override > default override > built-in fallback.
    pub fn max_output_bytes_for(&self, tool_name: &str, builtin_default: usize) -> usize {
        if let Some(&per_tool) = self.per_tool_max_output_bytes.get(tool_name) {
            return per_tool;
        }
        self.default_max_output_bytes.unwrap_or(builtin_default)
    }

    /// Resolve the max output bytes for an **MCP** payload.
    ///
    /// Precedence: per-tool override > MCP-specific override
    /// (`mcp_max_output_bytes`) > default override > built-in fallback.
    ///
    /// Only the MCP truncation path (`util::mcp_truncate`) should call this;
    /// non-MCP tools keep using [`Self::max_output_bytes_for`] so that an
    /// MCP-specific override never bleeds into their caps.
    pub fn mcp_max_output_bytes_for(&self, tool_name: &str, builtin_default: usize) -> usize {
        if let Some(&per_tool) = self.per_tool_max_output_bytes.get(tool_name) {
            return per_tool;
        }
        self.mcp_max_output_bytes
            .or(self.default_max_output_bytes)
            .unwrap_or(builtin_default)
    }

    /// Replace template placeholders in a tool description with current config values.
    ///
    /// Recognized placeholders:
    /// - `{max_lines_read}` — from `max_lines_read` (default 1000)
    /// - `{max_wait_ms}` — the blocking-wait ceiling, as `600000 (~10 min)`
    /// - `{max_output_bytes}` — resolved via `max_output_bytes_for(tool_name, builtin_default)`
    /// - `{max_chars_per_line}` — fixed display value for opencode-compat
    ///   descriptions only; the opencode `read` tool clips at its own
    ///   hardcoded `MAX_LINE_LENGTH` (2000), independent of this config.
    ///   grok_build `read_file` never clips lines.
    ///
    /// Returns the original string unchanged if no placeholders are present.
    pub fn interpolate_description(
        &self,
        description: &str,
        tool_name: &str,
        builtin_output_default: usize,
        max_wait_ms: u64,
    ) -> String {
        description
            .replace(
                "{max_lines_read}",
                &ToolOutputBudget::from_env()
                    .read_limit(self, None)
                    .to_string(),
            )
            .replace(
                "{max_wait_ms}",
                &xai_tool_types::format_wait_cap_ms(max_wait_ms),
            )
            .replace("{max_chars_per_line}", "2000")
            .replace(
                "{max_output_bytes}",
                &self
                    .max_output_bytes_for(tool_name, builtin_output_default)
                    .to_string(),
            )
    }

    /// Resolve placeholders in each schema property description, and pin the
    /// blocking-wait ceiling as a `maximum` on whichever property documents it.
    ///
    /// The tool description alone cannot carry the cap: `description_override`
    /// replaces that string outright under toolchain randomization, so on most
    /// draws the interpolated copy never reaches the model. Properties are only
    /// ever renamed, so a bound placed here survives every draw — and a
    /// `maximum` reaches a model that skips the prose.
    ///
    /// `{max_wait_ms}` in a property description is the marker for which
    /// property is the wait, so no tool or parameter name is hardcoded and a
    /// renamed parameter is handled for free (keys are remapped by the time
    /// this runs).
    pub fn apply_to_schema(
        &self,
        schema: &mut serde_json::Value,
        tool_name: &str,
        builtin_output_default: usize,
        max_wait_ms: u64,
    ) {
        let Some(properties) = schema.get_mut("properties").and_then(|p| p.as_object_mut()) else {
            return;
        };
        for property in properties.values_mut() {
            let Some(object) = property.as_object_mut() else {
                continue;
            };
            let Some(description) = object.get("description").and_then(|d| d.as_str()) else {
                continue;
            };
            let documents_wait = description.contains("{max_wait_ms}");
            let resolved = self.interpolate_description(
                description,
                tool_name,
                builtin_output_default,
                max_wait_ms,
            );
            object.insert("description".to_string(), serde_json::json!(resolved));
            if documents_wait {
                object.insert("maximum".to_string(), serde_json::json!(max_wait_ms));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_output_budget_defaults_and_explicit_limits() {
        let defaults = TruncationConfig::default();
        for value in [None, Some("0"), Some("true"), Some("")] {
            let budget = ToolOutputBudget::from_value(value);
            assert_eq!(budget.read_limit(&defaults, None), 1_000);
            assert_eq!(
                budget.terminal_limit(&defaults, None),
                crate::DEFAULT_TOOL_OUTPUT_CHARS
            );
        }
        let budget = ToolOutputBudget::from_value(Some("1"));
        assert_eq!(budget.read_limit(&defaults, None), 300);
        assert_eq!(budget.read_limit(&defaults, Some(700)), 700);
        assert_eq!(budget.read_limit(&defaults, Some(50)), 50);
        assert_eq!(budget.read_limit(&defaults, Some(2_000)), 1_000);
        assert_eq!(budget.terminal_limit(&defaults, None), 8_000);
        assert_eq!(budget.terminal_limit(&defaults, Some(25_000)), 25_000);
        let configured = TruncationConfig {
            max_lines_read: Some(800),
            default_max_output_bytes: Some(30_000),
            ..Default::default()
        };
        assert_eq!(budget.read_limit(&configured, None), 800);
        assert_eq!(budget.terminal_limit(&configured, None), 30_000);
        let per_tool = TruncationConfig {
            per_tool_max_output_bytes: HashMap::from([("run_terminal_cmd".to_string(), 12_000)]),
            ..configured
        };
        assert_eq!(budget.terminal_limit(&per_tool, Some(25_000)), 12_000);
    }

    #[test]
    fn max_lines_read_default_and_override() {
        assert_eq!(
            TruncationConfig::default().max_lines_read(),
            MAX_LINES_READ_DEFAULT
        );
        let cfg = TruncationConfig {
            max_lines_read: Some(50),
            ..Default::default()
        };
        assert_eq!(cfg.max_lines_read(), 50);
    }

    #[test]
    fn interpolate_description_resolves_max_wait_ms() {
        let cfg = TruncationConfig::default();
        let cap = 300_000;
        assert_eq!(
            cfg.interpolate_description("capped at {max_wait_ms}", "get_task_output", 40_000, cap),
            "capped at 300000 (~5 min)"
        );
        let default_cap =
            xai_tool_types::format_wait_cap_ms(xai_tool_types::MAX_WAIT_BLOCK_MS_DEFAULT);
        assert_eq!(
            TruncationConfig::default().interpolate_description(
                "capped at {max_wait_ms}",
                "get_task_output",
                40_000,
                xai_tool_types::MAX_WAIT_BLOCK_MS_DEFAULT,
            ),
            format!("capped at {default_cap}")
        );
    }

    #[test]
    fn apply_to_schema_resolves_and_pins_the_wait_property() {
        let cfg = TruncationConfig::default();
        let cap = 300_000;
        let mut schema = serde_json::json!({
            "properties": {
                "timeout_ms": {"type": "integer", "description": "Wait up to {max_wait_ms}."},
                "task_ids": {"type": "array", "description": "Task IDs."},
            }
        });
        cfg.apply_to_schema(&mut schema, "get_task_output", 40_000, cap);

        let timeout = &schema["properties"]["timeout_ms"];
        assert_eq!(timeout["description"], "Wait up to 300000 (~5 min).");
        assert_eq!(timeout["maximum"], serde_json::json!(300_000u64));
        // Only the property documenting the wait gets a ceiling.
        assert_eq!(schema["properties"]["task_ids"]["description"], "Task IDs.");
        assert!(schema["properties"]["task_ids"].get("maximum").is_none());
    }

    #[test]
    fn apply_to_schema_tracks_a_raised_ceiling_and_a_renamed_property() {
        // A 900s actor must not be handed the 300s default, and the marker —
        // not the property name — is what identifies the wait.
        let cfg = TruncationConfig::default();
        let cap = 900_000;
        let mut schema = serde_json::json!({
            "properties": {
                "max_wait": {"type": "integer", "description": "Up to {max_wait_ms}."},
            }
        });
        cfg.apply_to_schema(&mut schema, "get_task_output", 40_000, cap);

        assert_eq!(
            schema["properties"]["max_wait"]["description"],
            "Up to 900000 (~15 min)."
        );
        assert_eq!(
            schema["properties"]["max_wait"]["maximum"],
            serde_json::json!(900_000u64)
        );
    }

    /// The bound has to land somewhere that actually constrains the value.
    /// `Option<u64>` could plausibly be emitted as `anyOf: [integer, null]`, in
    /// which case a root `maximum` would be inert — so assert against the real
    /// generated schema rather than a hand-written one, and pin the shape it
    /// relies on. schemars puts `minimum` at the root for the same field, which
    /// is the precedent this follows.
    #[test]
    fn apply_to_schema_bounds_the_real_optional_u64_property() {
        let generated =
            serde_json::to_value(schemars::schema_for!(xai_tool_types::TaskOutputToolInput))
                .unwrap();
        let timeout = &generated["properties"]["timeout_ms"];
        assert!(
            timeout.get("anyOf").is_none(),
            "shape changed to anyOf — a root `maximum` no longer constrains the \
             integer arm, so apply_to_schema must walk the branches: {timeout}"
        );
        assert_eq!(timeout["type"], serde_json::json!(["integer", "null"]));

        let cfg = TruncationConfig::default();
        let cap = 300_000;
        let mut schema = generated.clone();
        cfg.apply_to_schema(&mut schema, "get_task_output", 40_000, cap);

        let bounded = &schema["properties"]["timeout_ms"];
        assert_eq!(bounded["maximum"], serde_json::json!(300_000u64));
        assert!(
            !bounded["description"].as_str().unwrap().contains("{max_"),
            "placeholder survived: {bounded}"
        );
    }

    #[test]
    fn apply_to_schema_tolerates_schemas_without_properties() {
        let mut schema = serde_json::json!({"type": "object"});
        TruncationConfig::default().apply_to_schema(
            &mut schema,
            "get_task_output",
            40_000,
            xai_tool_types::MAX_WAIT_BLOCK_MS_DEFAULT,
        );
        assert_eq!(schema, serde_json::json!({"type": "object"}));
    }

    #[test]
    fn mcp_max_output_bytes_for_lookup_order() {
        // per-tool > mcp-specific > default > builtin
        let cfg = TruncationConfig {
            default_max_output_bytes: Some(1_000),
            per_tool_max_output_bytes: HashMap::from([("use_tool".to_string(), 111)]),
            mcp_max_output_bytes: Some(500),
            ..Default::default()
        };
        assert_eq!(cfg.mcp_max_output_bytes_for("use_tool", 9_999), 111);
        assert_eq!(cfg.mcp_max_output_bytes_for("CallMcpTool", 9_999), 500);

        let no_mcp = TruncationConfig {
            default_max_output_bytes: Some(1_000),
            ..Default::default()
        };
        assert_eq!(no_mcp.mcp_max_output_bytes_for("use_tool", 9_999), 1_000);
        assert_eq!(
            TruncationConfig::default().mcp_max_output_bytes_for("use_tool", 9_999),
            9_999
        );
    }

    #[test]
    fn mcp_override_does_not_bleed_into_non_mcp_lookup() {
        // Regression: the MCP-specific cap must not change what non-MCP
        // readers (e.g. opencode bash via `max_output_bytes_for("bash", ..)`)
        // resolve.
        let cfg = TruncationConfig {
            mcp_max_output_bytes: Some(123),
            ..Default::default()
        };
        assert_eq!(cfg.max_output_bytes_for("bash", 20_000), 20_000);
        assert_eq!(cfg.mcp_max_output_bytes_for("use_tool", 20_000), 123);
    }
}
