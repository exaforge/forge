//! Opt-in model-facing guidance; tool execution and permission policy are unchanged.
use crate::types::{definition::ToolDefinition, tool::ToolNamespace};

const BATCH_READ_GUIDANCE: &str = "\n\nWhen several file reads or searches are independent, request them together in one response. Wait for results before dependent edits or commands. Preserve read-before-edit requirements and run the relevant tests after changing code.";

pub(super) fn compact_description<'a>(
    configured: Option<&'a str>,
    namespace: ToolNamespace,
    id: &str,
    contract_version: Option<&str>,
    flag: Option<&str>,
) -> Option<&'a str> {
    if configured.is_some()
        || flag != Some("1")
        || namespace != ToolNamespace::GrokBuild
        || contract_version.is_some_and(|version| version != "current")
    {
        return configured;
    }
    // Only replace reviewed current read-only templates. Input schemas, tool
    // names, versions, reminders, and all edit/execute safety guidance stay intact.
    match id {
        "read_file" => Some(
            "Read ${{ params.read.target_file }} (workspace-relative or absolute); default up to {max_lines_read} lines. Anchors mark the first line and each tenth line as LINE_NUMBER→LINE_CONTENT (1-based); count intervening lines. Supports PDFs, PPTX, notebooks, and images; images are shown visually.",
        ),
        "grep" => Some(
            "Search contents with ripgrep regex. Pass raw ${{ params.search.pattern }} without surrounding quotes; escape literal metacharacters. Respects .gitignore unless overridden by broad globs. Filter with ${{ params.search.type }} or ${{ params.search.glob }} only when source type is known (import suffixes may differ). ':' marks matches, '-' context, grouped by file. Capped results give 'at least' counts.",
        ),
        "list_dir" => Some(
            "List ${{ params.list.target_directory }} (workspace-relative or absolute). Omits dot-files, dot-directories and .gitignore matches. Large directories show counts and extension summaries instead of every file.",
        ),
        _ => None,
    }
}

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
    fn compact_templates_preserve_parameter_remapping_and_contracts() {
        use crate::{
            implementations::grok_build::{
                grep::GrepTool, list_dir::ListDirTool, read_file::ReadFileTool,
            },
            types::{
                template_renderer::TemplateRenderer, tool::ToolKind, tool_metadata::ToolMetadata,
            },
        };
        use std::collections::HashMap;
        let renderer = TemplateRenderer::new(
            HashMap::new(),
            HashMap::from([
                (
                    ToolKind::Read,
                    HashMap::from([("target_file".into(), "renamed_path".into())]),
                ),
                (
                    ToolKind::Search,
                    HashMap::from([
                        ("pattern".into(), "pattern".into()),
                        ("type".into(), "type".into()),
                        ("glob".into(), "glob".into()),
                    ]),
                ),
                (
                    ToolKind::List,
                    HashMap::from([("target_directory".into(), "renamed_path".into())]),
                ),
            ]),
        );
        let schema = json!({"type":"object","properties":{"target_file":{"type":"string","description":"Keep argument constraints."}},"required":["target_file"],"additionalProperties":false});
        let param_map = HashMap::from([("target_file".to_string(), "renamed_path".to_string())]);
        let read = ReadFileTool;
        let grep = GrepTool;
        let list = ListDirTool;
        for (id, tool) in [
            ("read_file", &read as &dyn ToolMetadata),
            ("grep", &grep),
            ("list_dir", &list),
        ] {
            let original = tool.versioned_definition(
                Some("current"),
                "alias",
                None,
                &renderer,
                &param_map,
                &schema,
                &json!({}),
            );
            let short = compact_description(
                None,
                ToolNamespace::GrokBuild,
                id,
                Some("current"),
                Some("1"),
            );
            let compact = tool.versioned_definition(
                Some("current"),
                "alias",
                short,
                &renderer,
                &param_map,
                &schema,
                &json!({}),
            );
            let text = compact.function.description.as_ref().unwrap();
            assert!(!text.contains("${{"), "{id}: {text}");
            assert!(text.len() < original.function.description.as_ref().unwrap().len());
            let original = serde_json::to_value(original).unwrap();
            let mut compact = serde_json::to_value(compact).unwrap();
            compact["function"]["description"] = original["function"]["description"].clone();
            assert_eq!(original, compact);
        }
    }

    #[test]
    fn compact_templates_preserve_explicit_legacy_external_and_mutating_descriptions() {
        for flag in [None, Some("0"), Some("true")] {
            assert_eq!(
                compact_description(None, ToolNamespace::GrokBuild, "read_file", None, flag),
                None
            );
        }
        for (namespace, id, version) in [
            (ToolNamespace::MCP, "read_file", None),
            (ToolNamespace::GrokBuild, "read_file", Some("legacy-0.4.10")),
            (ToolNamespace::GrokBuild, "search_replace", None),
            (ToolNamespace::GrokBuild, "run_terminal_cmd", None),
        ] {
            assert_eq!(
                compact_description(None, namespace, id, version, Some("1")),
                None
            );
        }
        assert_eq!(
            compact_description(
                Some("custom policy"),
                ToolNamespace::GrokBuild,
                "read_file",
                None,
                Some("1")
            ),
            Some("custom policy")
        );
    }

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
