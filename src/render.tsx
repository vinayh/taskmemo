import satori from "satori";
import sharp from "sharp";
import type { CuratedBrief } from "./curate.ts";

export const PRINT_WIDTH = 560;

const FONT_REGULAR_PATH = `${import.meta.dir}/../fonts/Inter-Regular.ttf`;
const FONT_BOLD_PATH = `${import.meta.dir}/../fonts/Inter-Bold.ttf`;

let cachedFonts: Awaited<ReturnType<typeof loadFonts>> | null = null;

async function loadFonts() {
  const [regular, bold] = await Promise.all([
    Bun.file(FONT_REGULAR_PATH).arrayBuffer(),
    Bun.file(FONT_BOLD_PATH).arrayBuffer(),
  ]);
  return [
    { name: "Inter", data: regular, weight: 400 as const, style: "normal" as const },
    { name: "Inter", data: bold, weight: 700 as const, style: "normal" as const },
  ];
}

function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  const date = new Date(Date.UTC(y, m - 1, d));
  const day = date.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
  const mon = date.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" });
  return `${day} ${mon} ${d}`;
}

interface BriefDoc extends CuratedBrief {
  date: string;
}

const FULL_WIDTH = { width: "100%", display: "flex" } as const;

function SectionHeader({ label, top = false }: { label: string; top?: boolean }) {
  return (
    <div
      style={{
        ...FULL_WIDTH,
        fontSize: 18,
        fontWeight: 700,
        letterSpacing: 2,
        marginTop: top ? 18 : 22,
        marginBottom: 6,
        ...(top
          ? {}
          : { paddingTop: 8, borderTop: "1px dashed black" }),
      }}
    >
      {label}
    </div>
  );
}

function TopPicksBlock({ picks }: { picks: CuratedBrief["topPicks"] }) {
  return (
    <div
      style={{
        ...FULL_WIDTH,
        flexDirection: "column",
      }}
    >
      {picks.map((pick, i) => (
        <div
          key={i}
          style={{
            ...FULL_WIDTH,
            flexDirection: "column",
            marginBottom: 10,
          }}
        >
          <div
            style={{
              ...FULL_WIDTH,
              flexDirection: "row",
            }}
          >
            <div
              style={{
                fontWeight: 700,
                fontSize: 24,
                lineHeight: 1.15,
                marginRight: 8,
              }}
            >
              {`${i + 1}.`}
            </div>
            <div
              style={{
                fontWeight: 700,
                fontSize: 24,
                lineHeight: 1.15,
                flexGrow: 1,
                flexShrink: 1,
              }}
            >
              {pick.title}
            </div>
          </div>
          {pick.why ? (
            <div
              style={{
                ...FULL_WIDTH,
                fontSize: 18,
                paddingLeft: 28,
                lineHeight: 1.25,
              }}
            >
              {pick.why}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function RemainingTasksBlock({ tasks }: { tasks: CuratedBrief["remainingTasks"] }) {
  return (
    <div
      style={{
        ...FULL_WIDTH,
        flexDirection: "column",
      }}
    >
      {tasks.map((t, i) => (
        <div
          key={i}
          style={{
            ...FULL_WIDTH,
            flexDirection: "row",
            alignItems: "flex-start",
            marginBottom: 4,
          }}
        >
          <div style={{ fontSize: 22, lineHeight: 1.25, marginRight: 8 }}>{"□"}</div>
          <div style={{ fontSize: 22, lineHeight: 1.25, flexGrow: 1, flexShrink: 1 }}>
            {t.title}
          </div>
          {t.meta ? (
            <div style={{ fontSize: 18, lineHeight: 1.4, marginLeft: 8 }}>{t.meta}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function Document({ doc }: { doc: BriefDoc }) {
  return (
    <div
      style={{
        width: PRINT_WIDTH,
        display: "flex",
        flexDirection: "column",
        backgroundColor: "white",
        color: "black",
        paddingTop: 24,
        paddingBottom: 24,
        paddingLeft: 20,
        paddingRight: 20,
        fontFamily: "Inter",
      }}
    >
      <div
        style={{
          ...FULL_WIDTH,
          fontSize: 36,
          fontWeight: 700,
          letterSpacing: -1,
          paddingBottom: 8,
          borderBottom: "3px solid black",
        }}
      >
        {formatDate(doc.date)}
      </div>

      {doc.topPicks.length > 0 ? (
        <SectionHeader label="TOP" top />
      ) : null}
      {doc.topPicks.length > 0 ? (
        <TopPicksBlock picks={doc.topPicks} />
      ) : null}

      {doc.remainingTasks.length > 0 ? (
        <SectionHeader label="ALL TASKS" />
      ) : null}
      {doc.remainingTasks.length > 0 ? (
        <RemainingTasksBlock tasks={doc.remainingTasks} />
      ) : null}

      <div
        style={{
          ...FULL_WIDTH,
          fontSize: 20,
          marginTop: 22,
          paddingTop: 8,
          borderTop: "1px dashed black",
        }}
      >
        {doc.habitsLine}
      </div>
    </div>
  );
}

export async function renderBriefToPng(doc: BriefDoc): Promise<Buffer> {
  if (!cachedFonts) cachedFonts = await loadFonts();

  const svg = await satori(<Document doc={doc} />, {
    width: PRINT_WIDTH,
    height: 4000,
    fonts: cachedFonts,
  });

  const raw = await sharp(Buffer.from(svg))
    .flatten({ background: "white" })
    .greyscale()
    .threshold(160)
    .png()
    .toBuffer();

  const trimmed = await sharp(raw)
    .trim({ background: "white", threshold: 10 })
    .toBuffer();
  const trimmedMeta = await sharp(trimmed).metadata();

  const horizontalPad = Math.max(0, PRINT_WIDTH - (trimmedMeta.width ?? PRINT_WIDTH));
  const padded = await sharp(trimmed)
    .extend({
      top: 16,
      bottom: 32,
      left: Math.floor(horizontalPad / 2),
      right: Math.ceil(horizontalPad / 2),
      background: "white",
    })
    .png()
    .toBuffer();

  if (process.env.RENDER_DEBUG) {
    console.error(
      `[render] trimmed: ${trimmedMeta.width}x${trimmedMeta.height} → padded ${PRINT_WIDTH}x${(trimmedMeta.height ?? 0) + 48}`,
    );
  }
  return padded;
}
