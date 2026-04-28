import { fetchTodaySummary } from "./src/griply.ts";
import { curateBrief } from "./src/curate.ts";
import { renderBriefToPng } from "./src/render.tsx";
import { printPng } from "./src/print.ts";

interface Args {
  dryRun: boolean;
  savePng?: string;
  deviceName?: string;
}

function parseArgs(argv: string[]): Args {
  const args: Args = { dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dry-run") args.dryRun = true;
    else if (a === "--save-png") args.savePng = argv[++i];
    else if (a === "--device") args.deviceName = argv[++i];
    else if (a === "--help" || a === "-h") {
      console.log(
        `Usage: bun run print-day [--dry-run] [--save-png <path>] [--device <name>]\n\n` +
          `  --dry-run        skip BLE print, just render\n` +
          `  --save-png PATH  also save the rendered PNG to PATH\n` +
          `  --device NAME    override the BLE device name (default: $PHOMEMO_DEVICE_NAME or "M02 Pro")\n`,
      );
      process.exit(0);
    } else {
      console.error(`Unknown argument: ${a}`);
      process.exit(1);
    }
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  console.log("Fetching today's Griply summary…");
  const summary = await fetchTodaySummary();
  console.log(
    `  ${summary.tasks.length} pending task(s), ` +
      `${summary.completedTaskCount}/${summary.totalTaskCount} done, ` +
      `${summary.completedHabitCount}/${summary.totalHabitCount} habits done.`,
  );

  if (summary.tasks.length === 0 && summary.habits.length === 0) {
    console.log("Nothing to print today. Exiting.");
    return;
  }

  console.log("Curating brief with Claude…");
  const curated = await curateBrief(summary);
  console.log(`  ${curated.topPicks.length} top pick(s), ${curated.remainingTasks.length} more.`);

  console.log("Rendering to PNG…");
  const png = await renderBriefToPng({ ...curated, date: summary.date });
  console.log(`  ${png.length} bytes.`);

  if (args.savePng) {
    await Bun.write(args.savePng, png);
    console.log(`Saved PNG to ${args.savePng}`);
  }

  if (args.dryRun) {
    console.log("Dry run — skipping print.");
    return;
  }

  console.log("Connecting to printer…");
  await printPng(png, { deviceName: args.deviceName });
  console.log("Done.");
}

await main();
process.exit(0);
