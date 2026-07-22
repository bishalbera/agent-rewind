"""Span/metric/log attribute names used across Rewind.

Two namespaces live here:

* ``GenAI*`` — OpenTelemetry GenAI semantic conventions. These are pinned
  deliberately rather than typed inline, because the GenAI conventions are still
  in Development status and rename between versions. Source of truth:
  https://github.com/open-telemetry/semantic-conventions-genai
  (the older opentelemetry.io/docs/specs/semconv/gen-ai/ page now just points here).

* ``Rewind*`` — our own ``rewind.*`` attributes. Nothing in OTel describes
  replay, so these are ours to define. Keeping them under one prefix means a
  SigNoz query can always separate "what the agent did" from "what Rewind did
  about it".

Verified against the semconv repo on 2026-07-21. Two notes from that check:

1. ``gen_ai.system`` is DEPRECATED in favour of ``gen_ai.provider.name``. Most
   instrumentation in the wild still emits the old name, so
   :func:`provider_attributes` writes both — new-name-first for correctness,
   old-name for backends that only index the legacy key.
2. Prompt/completion content is opt-in by spec: instrumentations "SHOULD NOT
   capture them by default". Rewind captures it unconditionally, because a
   replay is impossible without it. That is a deliberate divergence from the
   spec, not an oversight — see the privacy note in README's limitations.
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------
# OTel GenAI semantic conventions
# --------------------------------------------------------------------------

#: Operation being performed, e.g. "chat". Required.
GEN_AI_OPERATION_NAME: Final = "gen_ai.operation.name"

#: Provider identity, e.g. "anthropic". Required. Replaces ``gen_ai.system``.
GEN_AI_PROVIDER_NAME: Final = "gen_ai.provider.name"

#: Deprecated predecessor of GEN_AI_PROVIDER_NAME. Emitted alongside it for
#: backend compatibility only; never read this back during replay.
GEN_AI_SYSTEM_DEPRECATED: Final = "gen_ai.system"

GEN_AI_REQUEST_MODEL: Final = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE: Final = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS: Final = "gen_ai.request.max_tokens"

GEN_AI_RESPONSE_MODEL: Final = "gen_ai.response.model"
GEN_AI_RESPONSE_ID: Final = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"

GEN_AI_USAGE_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"

#: Opt-in content attributes. Rewind always sets these; see module docstring.
GEN_AI_SYSTEM_INSTRUCTIONS: Final = "gen_ai.system_instructions"
GEN_AI_INPUT_MESSAGES: Final = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES: Final = "gen_ai.output.messages"

#: Tool-call attributes.
GEN_AI_TOOL_NAME: Final = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID: Final = "gen_ai.tool.call.id"

# Values for GEN_AI_OPERATION_NAME.
OP_CHAT: Final = "chat"
OP_EXECUTE_TOOL: Final = "execute_tool"

PROVIDER_ANTHROPIC: Final = "anthropic"


def provider_attributes(provider: str = PROVIDER_ANTHROPIC) -> dict[str, str]:
    """Provider identity under both the current and deprecated attribute names.

    SigNoz (and most backends) index whichever key the instrumentation happens
    to send. Emitting both means dashboards keep working regardless of which
    name a given SigNoz version knows about.
    """
    return {
        GEN_AI_PROVIDER_NAME: provider,
        GEN_AI_SYSTEM_DEPRECATED: provider,
    }


# --------------------------------------------------------------------------
# Rewind attributes
# --------------------------------------------------------------------------

#: Groups every span produced by one agent run. Survives into the replay trace
#: so the original and its replays can be correlated without a join on trace id.
REWIND_SESSION_ID: Final = "rewind.session_id"

#: Monotonic counter over every LLM and tool span within a session, assigned in
#: the order the agent loop produced them. This is the ordering key replay
#: reconstructs from — span timestamps are not reliable enough, because
#: concurrent tool calls can interleave and clock skew is real.
REWIND_STEP_INDEX: Final = "rewind.step_index"

#: Distinguishes the two kinds of steps without parsing the span name.
REWIND_STEP_KIND: Final = "rewind.step_kind"
STEP_KIND_LLM: Final = "llm"
STEP_KIND_TOOL: Final = "tool"

#: Tool arguments and response, JSON-encoded. The response is what ToolMocker
#: serves on replay, which is the whole point of the recorder.
REWIND_TOOL_ARGS: Final = "rewind.tool.args"
REWIND_TOOL_RESPONSE: Final = "rewind.tool.response"
REWIND_TOOL_ERROR: Final = "rewind.tool.error"

#: Set on a replay root span, carrying the trace id being replayed.
REWIND_REPLAY_OF: Final = "rewind.replay_of"
#: Nth replay of that original trace, so repeated replays stay distinguishable.
REWIND_REPLAY_RUN: Final = "rewind.replay_run"

#: True when the replayed agent asked for something that does not match what was
#: recorded. Two flavours, distinguished by REWIND_DIVERGENCE_KIND:
REWIND_DIVERGENCE: Final = "rewind.divergence"
REWIND_DIVERGENCE_KIND: Final = "rewind.divergence_kind"
#: Right tool, different arguments — recorded response still served.
DIVERGENCE_ARGS: Final = "args_mismatch"
#: A tool call with no recorded counterpart — synthetic response served.
DIVERGENCE_UNMATCHED: Final = "unmatched_call"

#: Which chaos scenario was injected, if any. Lets a dashboard slice traces by
#: failure mode instead of eyeballing them.
REWIND_CHAOS_SCENARIO: Final = "rewind.chaos.scenario"

# --------------------------------------------------------------------------
# Metric + log names
# --------------------------------------------------------------------------

METRIC_COST_USD: Final = "rewind.tokens.cost_usd"
METRIC_LLM_DURATION: Final = "rewind.llm.duration"
METRIC_TOOL_DURATION: Final = "rewind.tool.duration"
METRIC_TOOL_ERRORS: Final = "rewind.tool.errors"

#: Instrumentation scope name — also the service.name default.
INSTRUMENTATION_NAME: Final = "rewind"
