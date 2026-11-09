# Project file (.mcx)

YAML; written by MCX Studio, readable and editable by hand. `python -m mcx_core serve` reloads it
when it changes (the endpoint settings need a restart). Top-level keys:

```yaml
name: Plant adapter
site: Bangkok
timezone: Asia/Bangkok          # plant time: naive dates in arguments, times in answers
endpoint: {host: 127.0.0.1, port: 8811, path: /mcp, http: true, stdio: true, tls_cert: '', tls_key: '', public_url: ''}
auth:
  mode: api_key                 # api_key | none (loopback only) | oauth (planned)
  api_keys: [{id: qe-key, profile: qe, client: Copilot Studio, hash: <sha256>, last4: '...'}]
limits: {max_rows: 500, max_days: 31, timeout_s: 10, rate_per_min: 60, cache_s: 5}
licence: {edition: Standard, max_sources: 5, max_risk: R3, profiles: true, expires: '2027-11-30'}
audit: {enabled: true, syslog_host: '', syslog_port: 514}
profiles: [{id: qe, title: Quality team, max_risk: R3, scope: Bangkok}]
sources:
  - id: smc_demo
    type: sql                   # sql | opcua | modbus | mqtt | files | rest | sim
    settings: {dialect: sqlserver, host: 10.0.0.12, database: smc_demo, user: mcp_reader, password: ${SMC_PW}}
    isa95: Bangkok/Assembly
    objects:                    # discovered; exposed = allow-list, pii columns never returned
      - {name: dbo.oee_shift_summary, kind: view, exposed: true, risk: R1, columns: [{name: line, type: string, pii: false}]}
    items:                      # tag sources: address -> ISA-95 path / name / unit / limits
      - {address: 'ns=2;s=L2.Press1.Temp', path: Bangkok/Assembly/L2/Press1, name: temperature, unit: °C, lo: 20, hi: 95}
tools:
  - name: get_oee
    kind: curated               # generic tools (browse_model, list_assets, read_tag, query_history) always exist
    source: smc_demo
    object: dbo.oee_shift_summary
    risk: R1
    date_column: date           # checked against limits.max_days
    params:                     # filters: column op bound-value; group_by: fixed column lists
      - {name: line, type: string, column: line, op: '=', values: [L1, L2, L3, L4]}
      - {name: date_from, type: date, required: true, column: date, op: '>='}
      - {name: date_to, type: date, required: true, column: date, op: '<='}
      - {name: group_by, type: enum, default: shift, group: {shift: [shift], day: [date]}}
    measures: [{column: oee, agg: avg, unit: ratio}]
    profiles: [eng, qe, mgr]
    version: 3
    published: {...}            # the snapshot MCP clients see; edits stay a draft until Publish
    history: [{version: 3, time: ..., user: ..., note: ...}]
```

Audit log: `audit/YYYY-MM.jsonl` next to the project, one hash-chained record per call.
Verify with `python -m mcx_core audit PROJECT.mcx [--export audit.csv]`.
