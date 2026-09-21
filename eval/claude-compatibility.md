# Claude Code and GPT-5.6 Sol

Checked on 2026-09-20, before the compact-description experiment. There is no
documented, supported direct route from Claude Code to GPT-5.6 Sol using the
existing Codex subscription. This is an unsupported integration, not a claim of
technical impossibility or a contractual prohibition.

Anthropic's [gateway documentation](https://code.claude.com/docs/en/llm-gateway)
explicitly states that routing Claude Code to non-Claude models through a gateway
is unsupported. Setting a custom base URL or model name is insufficient.
The [compatibility guide](https://code.claude.com/docs/en/llm-gateway-protocol)
requires the Anthropic Messages protocol at `/v1/messages`, compatible streaming,
and optionally `/v1/messages/count_tokens`. Model selection can also affect
Claude-specific reasoning, tool, caching, and context-management fields.

OpenAI documents [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
with Responses and Chat Completions support, streaming, function calling, and
medium reasoning. A custom bridge could in principle translate the protocols,
but would need to preserve tool calls, reasoning settings, usage, errors, and
streaming semantics. No such bridge was built or validated for this experiment.

OpenAI's [authentication documentation](https://learn.chatgpt.com/docs/auth)
separates ChatGPT subscription sign-in from API-key access. An existing Codex
login is not a general OpenAI Platform API key, and API-key requests use separate
Platform billing rather than included ChatGPT credits. A subscription-backed
bridge would additionally need a validated Codex authentication/session route.
No credentials were extracted, copied, or supplied to an external gateway.

The installed Claude Code `2.1.267` reported `logged_in=false` and
`auth_method=none`; no ambient Anthropic gateway or credential variables were
present. Only status booleans and the version were retained, without account
identities or secret values. No Claude model call, login, installation, or global
configuration change was made.

For this round, the external reference is direct Codex with Sol and medium
reasoning. Claude Code with its own model would be a separate product-and-model
comparison; Claude Code through a bridge would additionally measure the bridge.
Neither isolates the compact-description change. The controlled comparison is
between the two Forge configurations using the same native executable.
