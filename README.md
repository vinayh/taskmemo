# taskmemo

Daily task printout. Asks Claude (via `claude -p`) to fetch today's tasks
from an MCP tool of your choice, picks the few that matter, and prints the
curated brief to a Phomemo M02 Pro thermal printer over Bluetooth.

The script is invariant of which task system you use. It needs three things
to do its job:

1. The fully-qualified MCP tool name to call (set via `TASKMEMO_MCP_TOOL`).
2. A system prompt for Claude (path set via `TASKMEMO_PROMPT_FILE`,
   conventionally `prompt.md` at the repo root, copied from
   `prompt.example.md` and adapted for your tool).
3. The MCP server itself, registered with Claude Code somehow.

All three plus a few optional knobs are configured via `.env` (gitignored,
seeded from `.env.example`).

`uv` project (`pyproject.toml`); first `uv run` creates and caches the venv.

## Setup

You need:

- **`uv`** for the Python runtime and dependency management. Install via
  `brew install uv` or see [docs.astral.sh/uv](https://docs.astral.sh/uv/).
- **`claude`** (Claude Code CLI) on your `PATH`. Curation runs through
  `claude -p`, so it uses your existing Claude Code login.
- **An MCP server, registered with Claude Code, that returns today's tasks.**
  How you wire it up (project-level `.mcp.json`, user-level config, what
  credentials it needs) is your call; this script doesn't care.

```sh
uv sync                           # installs Pillow + bleak into .venv (one-time)
cp .env.example .env              # then fill in TASKMEMO_MCP_TOOL etc.
cp prompt.example.md prompt.md    # then edit prompt.md to reference your tool
```

Run everything with `uv run --env-file .env print_day.py [--dry-run|--debug]`
so the variables are loaded automatically. Or `export` them in your shell;
either works.

### Configure for your task system

In `.env`:

- **`TASKMEMO_MCP_TOOL`** (required): the fully-qualified MCP tool name in
  the form `mcp__<server>__<tool>`.
- **`TASKMEMO_MCP_CONFIG`** (recommended): path or inline JSON pointing at
  the MCP config that registers only your server. Without this, `claude -p`
  spawns every MCP server you have registered globally on every run, which
  adds several seconds. The script will pass
  `--strict-mcp-config --mcp-config $TASKMEMO_MCP_CONFIG` when it's set.

In your prompt file (`prompt.md` by default; override the path with
`TASKMEMO_PROMPT_FILE`):

- Contents are passed verbatim to Claude as the system prompt. The template
  in `prompt.example.md` assumes your tool returns a `date` field plus tasks
  with optional `priority` / `startTime` / `deadline`; adapt the prompt for
  your tool's actual fields.

The repo's `.gitignore` excludes `.env`, `*-mcp.json`, `.mcp.json`, and
`prompt.md`, so you can keep all your local config in the repo without
leaking credentials.

### Phomemo M02 Pro pairing

The M02 Pro pairs over BLE on macOS:

1. Power the printer on.
2. Open System Settings then Bluetooth and pair "M02 Pro" normally.
3. Grant your terminal Bluetooth permission under
   System Settings then Privacy & Security then Bluetooth (the prompt fires
   the first time the script tries to connect; or pre-add the terminal
   manually).

If a print fails or cuts off mid-stream, **power-cycle the printer before
retrying**. The firmware accumulates BLE buffer state from failed runs that a
reboot clears. Don't keep adjusting config knobs assuming the previous run
left it clean.

## Running

```sh
# end-to-end: curate, render, print
uv run --env-file .env print_day.py

# render only, save the PNG to /tmp/print-day.png; skips the print
uv run --env-file .env print_day.py --dry-run

# verbose BLE / print logging during the actual print step
uv run --env-file .env print_day.py --debug
```

(If you `export` your env vars manually, you can drop the `--env-file .env`.)

Every run writes the rendered PNG to `/tmp/print-day.png` so you can verify
what was just printed (override with `--save-png /elsewhere.png`).

## Pipeline

| Step | What it does |
|---|---|
| Curate | Spawns `claude -p` with `--allowedTools <MCP_TOOL>` and the contents of `prompt.md` as the system prompt. Claude calls the tool, then returns a JSON brief: top 3 to 5 picks (with priority and a one-line rationale) plus the full remaining task list. JSON shape is enforced via `--json-schema`. |
| Render | Pillow draws the brief to a 560-px-wide monochrome bitmap using bundled Inter Regular + Bold fonts. |
| Encode | Convert the 1-bit image to ESC/POS raster bytes, matching the [vivier/phomemo-tools](https://github.com/vivier/phomemo-tools) reference. |
| Print | Connect to "M02 Pro" via [Bleak](https://github.com/hbldh/bleak) BLE GATT, write to characteristic `0xff02` in MTU-sized chunks with 40 ms inter-chunk pacing (the M02 Pro's BLE buffer can't take faster). |

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `TASKMEMO_MCP_TOOL` | unset (required) | MCP tool to call. Override here or edit `MCP_TOOL` in `print_day.py`. |
| `TASKMEMO_PROMPT_FILE` | unset (required) | Path to the system prompt file. Relative paths resolve against the repo root. |
| `TASKMEMO_MCP_CONFIG` | unset | Path or inline JSON for an isolated MCP config (passed to `claude --strict-mcp-config --mcp-config`). Strongly recommended; see Setup above. |
| `CURATE_MODEL` | `opus` | Claude model alias for the curation step. |
| `PHOMEMO_DEVICE_NAME` | `M02 Pro` | BLE local name to scan for. |
| `PHOMEMO_CHUNK_SIZE` | `mtu - 3` | Bytes per BLE write. Smaller is gentler on the printer's per-write buffer. |
| `PHOMEMO_CHUNK_DELAY_MS` | `40` | Delay between chunks. Larger gives more buffer-drain headroom but slower transmission. |

## Troubleshooting

- **"BLE device 'M02 Pro' not found"**: printer off / out of range, or terminal lacks Bluetooth permission. Check System Settings then Privacy & Security then Bluetooth.
- **"Peer removed pairing information"**: stale pairing on macOS. System Settings then Bluetooth, click (i) next to M02 Pro, then Forget This Device, and re-run. The script will trigger a fresh pair.
- **Print starts then cuts off**: printer buffer state. Power-cycle the printer and retry.
- **"TASKMEMO_PROMPT_FILE is not set"** or **"System prompt file not found"**: set `TASKMEMO_PROMPT_FILE` in your `.env` to the path of your prompt file. Copy `prompt.example.md` to `prompt.md` if you don't have one yet.
- **"MCP_TOOL is not configured"**: set `TASKMEMO_MCP_TOOL` or edit the constant in `print_day.py`.
- **Curate returns an empty brief / hangs for minutes**: the MCP server isn't responding. Most often this is a credential issue at the MCP server's command (e.g. if it's wrapped in `op run`, your 1Password session has expired; run `op signin` to refresh). Could also be that `TASKMEMO_MCP_CONFIG` isn't pointing where you think, or the server isn't registered with Claude Code at all.

## Layout

```
print_day.py        # everything: curate, render, encode, print
prompt.example.md   # checked-in template for the system prompt
prompt.md           # your customized prompt (gitignored)
.env.example        # checked-in template for env vars
.env                # your filled-in env vars (gitignored)
fonts/              # bundled Inter Regular + Bold (OFL)
pyproject.toml      # deps + Python version
uv.lock             # checked-in lockfile
.python-version     # checked-in (3.14)
```
