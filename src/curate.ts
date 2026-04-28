import type { TodaySummary } from "./griply.ts";

export interface TopPick {
  title: string;
  why: string;
}

export interface RemainingTask {
  title: string;
  meta: string;
}

export interface CuratedBrief {
  topPicks: TopPick[];
  remainingTasks: RemainingTask[];
  habitsLine: string;
}

const SYSTEM_PROMPT = `You curate a one-page paper printout for the user's day.

You will be given today's tasks and habits as JSON. Produce a hybrid layout:

1. topPicks — pick 3 to 5 items (tasks, not habits) the user should focus on today, ordered by what to do first. For each, include:
   - title: the task name verbatim
   - why: one short sentence (≤14 words) on why it matters today (deadline pressure, blocks other work, time-bound, important goal). Specific, not generic.

2. remainingTasks — every other task not in topPicks. For each:
   - title: the task name verbatim
   - meta: a compact tag string with whatever is set: "HH:MM" if startTime, "due M/D" if deadline, "high"/"med"/"low" priority. Empty string if nothing notable. Keep under ~22 chars.

3. habitsLine — single line: "Habits: <completedHabitCount>/<totalHabitCount>".

Rules:
- Print-friendly: terse, no markdown, no emojis.
- Don't invent tasks. Only use what's in the input.
- If there are <3 tasks, put them all in topPicks and leave remainingTasks empty.
- If there are 0 tasks, return empty arrays and just the habits line.`;

const OUTPUT_SCHEMA = {
  type: "object",
  properties: {
    topPicks: {
      type: "array",
      items: {
        type: "object",
        properties: {
          title: { type: "string" },
          why: { type: "string" },
        },
        required: ["title", "why"],
      },
    },
    remainingTasks: {
      type: "array",
      items: {
        type: "object",
        properties: {
          title: { type: "string" },
          meta: { type: "string" },
        },
        required: ["title", "meta"],
      },
    },
    habitsLine: { type: "string" },
  },
  required: ["topPicks", "remainingTasks", "habitsLine"],
};

interface ClaudeJsonEnvelope {
  type: string;
  subtype?: string;
  is_error?: boolean;
  result?: string;
  error?: string;
  structured_output?: unknown;
}

export async function curateBrief(summary: TodaySummary): Promise<CuratedBrief> {
  const userInput = `Today's summary JSON:\n\n${JSON.stringify(summary, null, 2)}`;

  const proc = Bun.spawn(
    [
      "claude",
      "-p",
      userInput,
      "--output-format", "json",
      "--system-prompt", SYSTEM_PROMPT,
      "--json-schema", JSON.stringify(OUTPUT_SCHEMA),
      "--strict-mcp-config",
      "--mcp-config", '{"mcpServers":{}}',
      "--disallowedTools", "*",
      "--no-session-persistence",
      "--model", process.env.CURATE_MODEL ?? "sonnet",
    ],
    { stdout: "pipe", stderr: "pipe" },
  );

  const [stdoutText, stderrText, exitCode] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);

  if (exitCode !== 0) {
    throw new Error(
      `claude -p exited ${exitCode}\nstderr:\n${stderrText}\nstdout:\n${stdoutText.slice(0, 500)}`,
    );
  }

  let envelope: ClaudeJsonEnvelope;
  try {
    envelope = JSON.parse(stdoutText) as ClaudeJsonEnvelope;
  } catch (err) {
    throw new Error(
      `Could not parse claude envelope as JSON: ${(err as Error).message}\nraw stdout:\n${stdoutText.slice(0, 500)}`,
    );
  }

  if (envelope.is_error) {
    throw new Error(
      `claude returned error: ${envelope.error ?? envelope.subtype ?? "unknown"}\nraw:\n${stdoutText.slice(0, 500)}`,
    );
  }

  if (envelope.structured_output && typeof envelope.structured_output === "object") {
    return envelope.structured_output as CuratedBrief;
  }

  if (envelope.result) return parseSchemaOutput(envelope.result);

  throw new Error(`claude returned no structured_output and no result\nraw:\n${stdoutText.slice(0, 500)}`);
}

function parseSchemaOutput(text: string): CuratedBrief {
  const trimmed = text.trim();
  try {
    return JSON.parse(trimmed) as CuratedBrief;
  } catch {
    // fall through to fence extraction
  }
  const fence = trimmed.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
  if (fence?.[1]) return JSON.parse(fence[1]) as CuratedBrief;
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start >= 0 && end > start) {
    return JSON.parse(trimmed.slice(start, end + 1)) as CuratedBrief;
  }
  throw new Error(`Could not extract JSON from claude result:\n${trimmed.slice(0, 500)}`);
}
