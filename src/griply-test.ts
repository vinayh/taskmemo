import { fetchTodaySummary } from "./griply.ts";

const summary = await fetchTodaySummary();
console.log(JSON.stringify(summary, null, 2));
