# Ableton MCP Chain-Only Harness

Strict, deterministic MCP harness for Ableton Live focused only on device-chain building and inspection.

## Scope

In scope:
- Add one or more audio/MIDI devices to a target track (`action.build_device_chain`)
- Update parameters on existing devices (`action.update_device_parameters`)
- Apply deterministic parameter updates (absolute or display-verified)
- Apply text-targeted parameter updates for quantized controls (`target_display_text`)
- Inspect chain/device/parameter state (`action.inspect_track_chain`)

Out of scope:
- Session/arrangement editing
- Clip/note editing
- UI adapter actions
- Broad Ableton control surface tooling

## Runtime Processes

1. `ableton-mcp-server` (MCP transport + orchestration)
2. `ableton-bridge` (bridge daemon supervised by MCP server)

`Gateway_Remote/` contains the chain-only Ableton Remote Script surface.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Install Chain-Only Remote Script In Ableton

Replace the existing `Ableton_MCP_Gateway` Remote Script with this repo's
`Gateway_Remote` implementation, then select that control surface in Ableton.

```bash
mkdir -p "$HOME/Music/Ableton/User Library/Remote Scripts"
mv "$HOME/Music/Ableton/User Library/Remote Scripts/Ableton_MCP_Gateway" \
   "$HOME/Music/Ableton/User Library/Remote Scripts/Ableton_MCP_Gateway.bak.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
cp -R Gateway_Remote "$HOME/Music/Ableton/User Library/Remote Scripts/Ableton_MCP_Gateway"
```

After copying:
- In Ableton Preferences -> Link/Tempo/MIDI, select `Ableton_MCP_Gateway` as the control surface.
- Restart Ableton (or toggle the control surface off/on) so the new script loads.

## Start MCP Server

STDIO transport:

```bash
ableton-mcp-server --transport stdio
```

SSE transport:

```bash
ableton-mcp-server --transport sse --host 127.0.0.1 --port 8765
```

## Tool Surface

`tools/list` returns exactly:
- `action.build_device_chain`
- `action.update_device_parameters`
- `action.inspect_track_chain`
- `bridge.health_check`
- `bridge.capabilities`

## Strict Gateway Compatibility

Bridge startup/runtime preflight requires gateway support for:
- `build_device_chain`
- `update_device_parameters`
- `inspect_track_chain`
- `health_check` or `ping`

When missing, bridge returns:
- `error_code: GATEWAY_INCOMPATIBLE`
- deterministic envelope payload with compatibility details

Default behavior is strict and fail-fast.

## Optional Ollama Test Harness

This helper starts MCP server locally and lets you test natural-language prompts:

```bash
python scripts/llm_chain_harness.py --model gpt-oss:120b-cloud --think low
```

Optional:
- `--max-tool-rounds <int>` controls assistant/tool exchange rounds per user turn (minimum `3`, default `6`).

Commands:
- `/health`
- `/capabilities`
- `/reset`
- `/quit`

Notes:
- Harness validates model tool calls against MCP tool schema before execution.
- Harness canonicalizes common parameter aliases (for example `parameter`, `name`, `id`) to strict schema keys.
- EQ Eight supports common natural aliases (for example `Low Shelf Gain`) and maps them to canonical parameter names.
- Harness biases toward intent-first chain building; inspection is used only when context or verification is needed.
- MCP remains client-agnostic and can be used from any LLM/agent client.

## Example `action.build_device_chain` Payload

```json
{
  "target": {"use_selected_track": true},
  "steps": [
    {
      "device_name": "EQ Eight",
      "parameter_updates": [
        {"param_name": "1 Frequency A", "target_display_value": 500.0, "target_unit": "hz"}
      ]
    },
    {
      "device_name": "Auto Filter",
      "parameter_updates": [
        {"param_name": "Filter Type", "target_display_text": "high pass"}
      ]
    }
  ]
}
```

## Example `action.update_device_parameters` Payload

```json
{
  "target": {"use_selected_track": true},
  "updates": [
    {
      "device_name": "Auto Filter",
      "device_occurrence": 0,
      "parameter_updates": [
        {"param_name": "Frequency", "target_display_value": 100.0, "target_unit": "hz"},
        {"param_name": "Filter Type", "target_display_text": "high pass"}
      ]
    }
  ]
}
```

## Tests

```bash
python -m unittest discover -s tests -v
```
