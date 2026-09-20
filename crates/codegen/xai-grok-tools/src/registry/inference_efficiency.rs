//! Opt-in model-facing guidance; tool execution and permission policy are unchanged.
use crate::types::{definition::ToolDefinition, tool::ToolNamespace};

const BATCH_READ_GUIDANCE: &str = "\n\nWhen several file reads or searches are independent, request them together in one response. Wait for results before dependent edits or commands. Preserve read-before-edit requirements and run the relevant tests after changing code.";

pub(super) fn append_batch_read_guidance(
    definition: &mut ToolDefinition,
    namespace: ToolNamespace,
    id: &str,
    has_description_override: bool,
    enabled: bool,
) {
    // Advertise once, through the ordinary reader. Do not rewrite custom or
    // external-tool instructions, or change ordering/execution of actual calls.
    if enabled
        && namespace == ToolNamespace::GrokBuild
        && id == "read_file"
        && !has_description_override
        && let Some(description) = definition.function.description.as_mut()
    {
        description.push_str(BATCH_READ_GUIDANCE);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn batching_preserves_schema_aliases_and_non_description_fields() {
        let mut definition = ToolDefinition::function(
            "renamed_read",
            Some("Read rules remain."),
            json!({"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}),
        );
        let original = serde_json::to_value(&definition).unwrap();
        append_batch_read_guidance(
            &mut definition,
            ToolNamespace::GrokBuild,
            "read_file",
            false,
            true,
        );
        assert!(
            definition
                .function
                .description
                .as_ref()
                .unwrap()
                .starts_with("Read rules remain.")
        );
        let mut actual = serde_json::to_value(&definition).unwrap();
        actual["function"]["description"] = original["function"]["description"].clone();
        assert_eq!(actual, original);
    }

    #[test]
    fn batching_respects_opt_in_custom_descriptions_and_tool_scope() {
        for (id, custom, enabled) in [
            ("read_file", false, false),
            ("read_file", true, true),
            ("run_terminal_cmd", false, true),
        ] {
            let mut definition = ToolDefinition::function("read_file", Some("original"), json!({}));
            append_batch_read_guidance(
                &mut definition,
                ToolNamespace::GrokBuild,
                id,
                custom,
                enabled,
            );
            assert_eq!(definition.function.description.as_deref(), Some("original"));
        }
    }
}
