# taskmemo

Daily task printout: pulls today's tasks/habits from a local
[griply-mcp](https://github.com/vinayh/griply-mcp) checkout, has Claude pick the
few that matter and lay them out alongside the full list, renders to a 560-px
bitmap, and prints it over Bluetooth to a Phomemo M02 Pro.

## Setup

```bash
bun install
cp .env.example .env  # then fill in GRIPLY_* and GRIPLY_MCP_PATH
```

The curation step shells out to `claude -p`, so it uses your existing Claude
Code login (subscription or API key) — no `ANTHROPIC_API_KEY` env var needed.
Make sure `claude` is on your `PATH`.

The Phomemo printer must be **powered on but NOT paired in macOS Bluetooth
settings** — `@stoprocent/noble` cannot discover BLE peripherals that
`bluetoothd` has already claimed. If it's listed under System Settings →
Bluetooth, click the (i) and "Forget This Device" first. The terminal app
running this script also needs Bluetooth permission under
System Settings → Privacy & Security → Bluetooth.

## Commands

```bash
# end-to-end: fetch → curate → render → print
bun run print-day

# render only, save the PNG to /tmp/print-day.png
bun run print-day:dry

# render the fixture brief without calling Griply or Claude
bun run render-test [/tmp/out.png]

# fetch today's Griply summary as JSON, no curation/render/print
bun run griply-test
```

## Layout pipeline

| Step | Module | Output |
|---|---|---|
| Fetch tasks/habits | `src/griply.ts` | `TodaySummary` JSON via MCP stdio |
| Curate brief | `src/curate.ts` | `{ topPicks, remainingTasks, habitsLine }` from `claude -p --json-schema` |
| Render | `src/render.tsx` | 560-px-wide PNG (Satori → Sharp, 1-bit threshold) |
| Print | `src/print.ts` | ESC/POS over BLE via `@stoprocent/noble` |
