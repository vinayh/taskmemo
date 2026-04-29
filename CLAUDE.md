# taskmemo

Daily task printout for a Phomemo M02 Pro thermal printer. `uv` project
(`pyproject.toml`, Python 3.14). Single source file: `print_day.py`. Runs via
`uv run print_day.py`.

The script is task-system-agnostic. It calls one MCP tool to fetch tasks,
then asks Claude to curate them per a user-supplied system prompt. There's
no built-in default for either the tool name or the prompt; both must be
provided explicitly (env var or repo-level file). This is on purpose.

## Architecture

`print_day.py` is the whole project. It does four things in sequence:

1. **Curate**. Spawn `claude -p` with `--allowedTools <MCP_TOOL>` and the
   contents of `prompt.md` (or `$TASKMEMO_PROMPT_FILE`) as the system
   prompt. Claude calls the configured tool, then emits a JSON brief
   matching the script's schema. Output shape is enforced by `--json-schema`.
   Result fields: top 3 to 5 picks (priority + one-line rationale) plus
   the full remaining task list.
2. **Render**. Pillow draws to a 560-px-wide grayscale canvas using bundled
   Inter Regular + Bold TTFs, then thresholds to 1-bit. Layout has a date
   header, a numbered TOP block, and a checkbox-prefixed ALL TASKS block.
3. **Encode**. Convert the 1-bit image to ESC/POS raster bytes, matching
   `vivier/phomemo-tools`'s reference. Header is `ESC @` then `ESC a 0x01`
   (center align) then the Phomemo proprietary prefix.
4. **Print**. Connect via Bleak BLE GATT to characteristic `0xff02` and
   write the encoded bytes in chunks with inter-chunk pacing.

## Critical knowledge for any change to print_day.py

### Width is 560 pixels, not 384

The M02 **Pro** is 300 DPI on 53 mm paper, so the printable area is 560 dots
wide (70 bytes per line). The vivier reference uses 384 because that's what
the original M02 (203 DPI) does, and that's likely what they actually tested
against; their listed M02 Pro support is presumed untested. Don't flip back
to 384 even if reading vivier's code.

### Always use writeWithoutResponse over BLE

The M02 Pro firmware only triggers a print on `writeWithoutResponse`-arrived
data. A `writeWithResponse` write gets ACK'd at the link layer (transmission
succeeds), but the buffer is silently ignored.

### Pacing matters; tail of the stream gets clipped if you go too fast

Default is `mtu-3` chunk size (~197 bytes) with 40 ms inter-chunk delay,
giving ~6 KB/s feed rate. The printer's nominal print rate is ~12 KB/s but
content-density-dependent; for dense rasters it drops below 10. Faster feed
configurations (e.g. 100 B / 10 ms ≈ 10 KB/s) sometimes drop the last
printed line. If you change defaults, verify on full-content prints, not on
test patterns.

### "Run failed" almost always means stale printer state

The Phomemo's BLE buffer accumulates state from failed prints. If a run fails
or cuts off, power-cycle the printer before iterating on config. Otherwise
you'll waste hours debugging "why did 40 ms work yesterday and not today";
it's printer state, not code.

### macOS Bluetooth permission gotchas

- The terminal needs explicit Bluetooth permission under
  System Settings then Privacy & Security then Bluetooth.
- If the printer was previously paired in a way macOS forgot, you'll see
  `CBErrorDomain Code=14 "Peer removed pairing information"`. Fix: forget
  the device in System Settings, power-cycle the printer, re-run.
- macOS's native `socket.AF_BLUETOOTH` does not support BTPROTO_RFCOMM, so
  the classic-Bluetooth path used by `vivier/phomemo-tools` and
  `hkeward/phomemo_printer` doesn't work here. We have to use BLE GATT.

### What was tried and rejected

- **Bun + @stoprocent/noble**: doesn't fragment writeWithoutResponse on
  macOS. Large writes silently drop.
- **Bun + @abandonware/noble**: ships non-NAPI prebuilds, won't load in Bun.
- **SimplePyBLE write_command**: silently drops large buffers on macOS
  (returns 0 ms for any size). write_request transmits but printer ignores.
- **Bluetooth Classic SPP via /dev/cu.***: macOS doesn't surface the M02 Pro
  as a serial device even though it has a classic BT MAC. Probably BLE-only
  in firmware for data transport.
- **Bleak with response=False, single buffer write**: works at the BLE level
  but printer drops most bytes (per-write buffer overflow).

The current Bleak + chunked + paced approach is the only one that has
produced full clean prints.

## Configuration boundaries

The repo is intentionally agnostic about which task system or MCP server you
plug in. Specifically:

- **`MCP_TOOL` has no default**, set via `TASKMEMO_MCP_TOOL` or the constant
  near the top of `print_day.py`. Script errors if not set.
- **System prompt lives in `prompt.md`** (gitignored), seeded from the
  checked-in `prompt.example.md`. Override path via `TASKMEMO_PROMPT_FILE`.
  Script errors if the prompt file doesn't exist.
- **No bundled `.mcp.json`** in the repo; gitignored via `.mcp.json` and
  `*-mcp.json`. Users register their MCP server with Claude Code through
  whatever mechanism they prefer.
- **No bundled credentials or auth flow.** The script doesn't know about
  1Password / OAuth / API keys; that's between the user and their MCP server.
- **`TASKMEMO_MCP_CONFIG`** env var (path or inline JSON) is the recommended
  way to point Claude at a single MCP server. When set, the script adds
  `--strict-mcp-config --mcp-config $TASKMEMO_MCP_CONFIG` to the `claude -p`
  call so only that one server spawns.

If adding new functionality: don't reintroduce specific-task-source
assumptions in code or docs (i.e. don't reference any particular MCP
server's tool names, auth flow, or env vars).

## Dependencies

Managed in `pyproject.toml`, locked in `uv.lock`. Both checked in alongside
`.python-version` (currently 3.14) for reproducibility.

- `pillow` for rendering
- `bleak` for BLE GATT (pulls in pyobjc-* on macOS)

External tools assumed on `PATH`:

- `uv` (run the script)
- `claude` (Claude Code CLI; uses your existing login)

## Run modes

```sh
uv run --env-file .env print_day.py             # full pipeline including print
uv run --env-file .env print_day.py --dry-run   # render and save PNG, skip print
uv run --env-file .env print_day.py --debug     # full pipeline with verbose BLE logging
```

The `.env` file is the canonical config home (gitignored, seeded from
`.env.example`). Users can also `export` env vars manually and drop the
`--env-file .env`. `/tmp/print-day.png` is always written so you can
verify output post-hoc.

## Don't add

- A persistent BLE client / connection-pool. The script is run once a day;
  reconnecting per run is fine and avoids accumulated-state bugs.
- A bundled `.mcp.json` (or `*-mcp.json`) or any MCP-server-specific auth
  code. The pattern is gitignored on purpose so users can drop their own
  configs in.
- A bundled `prompt.md`. Gitignored. Customizing it is part of setup.
- Default values for `MCP_TOOL` or the prompt that point at a specific
  task system. The script must remain task-source-agnostic.
- Async retry/backoff loops around `claude -p`. The retry that exists (3x
  on empty output) is enough; the failure mode is almost always the MCP
  server not being reachable, which retries can't fix.
