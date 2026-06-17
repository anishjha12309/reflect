import type { Source } from "./types";

export interface ParsedReport {
  body: string; // markdown with [n] turned into links to #source-n
  sources: Source[];
}

const SOURCES_HEADING = /^##\s+Sources\s*$/m;
// "[1] Some Title — https://example.com"
const SOURCE_LINE = /^\[(\d+)\]\s+(.*?)\s+—\s+(https?:\/\/\S+)\s*$/;

/** Split the report into its body and its Sources table, and make [n] clickable. */
export function parseReport(report: string): ParsedReport {
  const match = SOURCES_HEADING.exec(report);
  const body = match ? report.slice(0, match.index) : report;
  const tail = match ? report.slice(match.index) : "";

  const sources: Source[] = [];
  for (const line of tail.split(/\r?\n/)) {
    const m = SOURCE_LINE.exec(line.trim());
    if (m) sources.push({ n: Number(m[1]), title: m[2], url: m[3] });
  }

  // Turn "[1]" into a markdown link "[\[1\]](#source-1)" so react-markdown renders
  // a clickable anchor that jumps to the sources panel.
  const linked = body.replace(/\[(\d+)\]/g, (_, n) => `[\\[${n}\\]](#source-${n})`);
  return { body: linked, sources };
}

export function downloadMarkdown(report: string, topic: string): void {
  const blob = new Blob([report], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug(topic)}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

function slug(topic: string): string {
  return topic.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "").slice(0, 60) || "report";
}
