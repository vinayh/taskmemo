import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

export interface GriplyTask {
  id: string;
  name: string;
  description?: string;
  priority?: string;
  startDate?: string;
  startTime?: string;
  duration?: number;
  deadline?: string;
  goalId?: string;
  lifeAreaId?: string;
  parentTaskId?: string;
  isCompleted?: boolean;
  subtasks?: GriplyTask[];
}

export interface GriplyHabit {
  id: string;
  name: string;
  description?: string;
  targetPeriod?: string;
  targetCount?: number;
  schedulePeriod?: string;
  scheduleDays?: string[];
  startDate?: string;
  startTime?: string;
  duration?: number;
  priority?: string;
  icon?: string;
  goalId?: string;
  lifeAreaId?: string;
  isArchived?: boolean;
  completedToday?: boolean;
  todayCount?: number;
}

export interface TodaySummary {
  date: string;
  tasks: GriplyTask[];
  habits: GriplyHabit[];
  completedTaskCount: number;
  totalTaskCount: number;
  completedHabitCount: number;
  totalHabitCount: number;
}

export async function fetchTodaySummary(): Promise<TodaySummary> {
  const mcpPath = process.env.GRIPLY_MCP_PATH;
  if (!mcpPath) throw new Error("GRIPLY_MCP_PATH is not set");

  const env: Record<string, string> = {
    PATH: process.env.PATH ?? "",
    HOME: process.env.HOME ?? "",
  };
  for (const key of ["GRIPLY_EMAIL", "GRIPLY_PASSWORD", "GRIPLY_TIMEZONE"]) {
    const v = process.env[key];
    if (v) env[key] = v;
  }

  const transport = new StdioClientTransport({
    command: "bun",
    args: [`${mcpPath}/src/index.ts`, "--stdio"],
    env,
    stderr: "pipe",
  });

  const client = new Client(
    { name: "taskmemo-print-day", version: "0.1.0" },
    { capabilities: {} },
  );

  await client.connect(transport);
  try {
    const result = await client.callTool({
      name: "get_today_summary",
      arguments: process.env.GRIPLY_TIMEZONE
        ? { timezone: process.env.GRIPLY_TIMEZONE }
        : {},
    });

    const content = (result as { content: Array<{ type: string; text: string }> }).content;
    const textBlock = content?.find((c) => c.type === "text");
    if (!textBlock) throw new Error("get_today_summary: no text content in response");

    return JSON.parse(textBlock.text) as TodaySummary;
  } finally {
    await client.close();
  }
}
