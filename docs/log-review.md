# Local log review, September 2026

The September 10 review inspected 4,190 plugin history entries spanning July 22
through September 10, 2026. Raw histories, paths, selections, and object names
remain local. Counts below describe that snapshot, not ongoing telemetry.

## Findings addressed

- Twelve failed `spectrum` operations supplied numeric scale bounds. The parser
  included those extra arguments in the selection, and the dispatcher did not
  forward bounds. Both now support optional minimum and maximum arguments.
- Eight failed `label` operations supplied literal text as an unquoted expression.
  `label_text` now accepts literal text and handles quoting through the existing
  guarded label dispatcher. Computed labels remain available through `label`.
- Code inspection found a `refresh` probe on every cached-connection lookup.
  Reuse now sends the requested operation directly, saving one socket round trip
  per cached lookup. This does not remove discovery when the instance is omitted.
  Transport failures disconnect and surface to the caller without automatically
  retrying an operation that may already have executed.
- Test execution polluted personal history: the snapshot contained 69 refresh
  failures reporting a missing PyMOL module and 77 synthetic selection failures,
  plus successful stub outputs. Tests now disable personal history before plugin
  imports; dedicated history tests opt into temporary directories. Existing
  history has not been rewritten or deleted.

## Follow-up changes

All seven failed `alter_state` calls attempted simple axis translations. The
`translate` tool now accepts a finite three-number vector in model coordinates,
with state 0 for all states or a positive state index. It uses PyMOL's direct
translation API without expression evaluation and reports the selected atom count.

Two setting failures involved vector values being split at commas. `set_setting`
now accepts scalar or three-number vector values with explicit global, object,
or atom scope. Effective values are returned through `SettingReport`. Vector
readback also now preserves all three components instead of only the first.
Translations and setting writes have a fresh-process replay integration test.

## Remaining opportunities

There were 408 adjacent `set`/`set` pairs and 382 `color`/`color` pairs within
sessions. These suggest examining styling workflows, but do not establish that
`execute_batch` was missing: plugin history does not retain MCP call boundaries.
Likewise, repeated renders may be necessary visual checks rather than waste.

Only five adjacent pairs had identical command names and arguments, including
three refresh pairs in the test-contaminated history. This snapshot does not
support indiscriminately deduplicating operations. A repeated command can also
be intentional after an unlogged GUI change.

Plugin history omits parser rejections, tool discovery, implicit health checks,
and MCP timing. Any further performance study should collect opt-in aggregate
server metrics for tool counts, batch sizes, parser rejection categories, and
socket round trips. Avoid recording raw arguments by default. Compare those
metrics on the same synthetic task before adding broad new tools or caching
scene-dependent inspection results.

# Local log review, October 2026

The October 12 review inspected the 3,381 entries recorded after the September
review, through October 12, 2026. Five failed. No test traffic reached personal
history after the September isolation change.

## Findings addressed

- One `set label_position, (3,3,0), <selection>` failed because the parser split
  the bracketed vector at its first comma. The `set` pattern now reads a
  bracketed value as one argument, and the plugin converts it to three finite
  numbers instead of passing text on. `set_setting` remains the typed route.
- One `inspect_movie` reached a PyMOL started before that command existed and
  was reported as `Unknown command`, which the server classified as a syntax
  error. The server now says the running plugin is older and how to fix it.
  Session scripts also name the plugin build that wrote them.
- Three `render_movie` calls wrote 303 per-frame `png` records, about 12% of the
  history bytes in the window, and no record of the movie. A movie now leaves
  one record with its outcome and output path.
- One `inspect_setting` on a misspelled name returned a bare `unknown setting`.
  The plugin now names the nearest settings.
- A failed command with replay syntax was marked `replayable`. The replay
  script was already correct; the flag now requires success.

## History additions

The September review could not see parser rejections, MCP call boundaries, or
timing. Records now carry `duration_ms` and the `call_id` and `tool` of the MCP
call that sent them, and the server asks the plugin to record input it rejected
itself. These are local, like the rest of the history, and rejected input goes
through the same path redaction as arguments on export.

Two readings of this snapshot were wrong until the code was checked, and both
would have been answered by `call_id`: 50 adjacent `set`/`inspect_setting`
pairs were `set_setting` reading back its own write, and a 300-step
`frame`/`png` loop was `render_movie`.

## Limits

Rejections are recorded only while a PyMOL is reachable, and arguments that
fail schema validation in the MCP framework never reach the tool, so neither
is counted. Typed tool results are still not stored.
