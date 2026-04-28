import { renderBriefToPng } from "./render.tsx";

const fixture = {
  date: "2026-04-28",
  topPicks: [
    { title: "Ship the daily printout script", why: "Unblocks the rest of this week's automation work." },
    { title: "Reply to Anna re: Q2 OKR draft", why: "She's blocked on your input before tomorrow's review." },
    { title: "Test M02 Pro print path end-to-end", why: "First real BLE write; isolate failure modes early." },
  ],
  remainingTasks: [
    { title: "Renew domain registration", meta: "due 5/2" },
    { title: "Review pull request #312", meta: "med" },
    { title: "Pick up dry cleaning", meta: "" },
    { title: "Run morning workout", meta: "07:30" },
    { title: "Read paper on dithering", meta: "low" },
  ],
  habitsLine: "Habits: 2/5",
};

const png = await renderBriefToPng(fixture);
const out = process.argv[2] ?? "/tmp/print-day.png";
await Bun.write(out, png);
console.log(`Wrote ${png.length} bytes to ${out}`);
