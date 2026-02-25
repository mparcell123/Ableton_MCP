# Operations Runbook

## Feature Flags

- `FF_BRIDGE_ENABLED` default `true`
- `FF_ENABLE_SSE_TRANSPORT` default `true`
- `FF_STRICT_GATEWAY_COMPAT` default `true`

## Rollback Controls

1. Disable bridge execution while keeping MCP up:

```bash
export FF_BRIDGE_ENABLED=false
```

2. Disable SSE transport:

```bash
export FF_ENABLE_SSE_TRANSPORT=false
```

3. Temporarily relax strict gateway compatibility (emergency only):

```bash
export FF_STRICT_GATEWAY_COMPAT=false
```

## Health and Compatibility Checks

- MCP tool: `bridge.health_check`
- MCP tool: `bridge.capabilities`
- JSON-RPC method: `server/status`

## Tool Contract Check

`tools/list` must return only:
- `action.build_device_chain`
- `action.update_device_parameters`
- `action.inspect_track_chain`
- `bridge.health_check`
- `bridge.capabilities`

Strict compatibility requires gateway support for:
- `build_device_chain`
- `update_device_parameters`
- `inspect_track_chain`
- `health_check` or `ping`

## Test Gate

```bash
python -m unittest discover -s tests -v
```
