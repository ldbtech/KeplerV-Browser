import { describe, expect, it } from "vitest";
import type { Evidence } from "../types";
import { sourceHostname, summarizeSourceCoverage } from "./researchSummary";

const evidence = (sourceUrl: string): Evidence => ({ id: sourceUrl, goalId: "goal", agentId: "agent", runId: "run", finding: "Finding", sourceUrl, quote: "Quote", createdAt: "now" });

describe("research source coverage", () => {
  it("shows unique source hostnames without claiming publisher independence", () => {
    const result = summarizeSourceCoverage([evidence("https://www.example.org/a"), evidence("https://example.org/b"), evidence("https://docs.example.net/page")]);
    expect(result.hostnames).toEqual(["docs.example.net", "example.org"]);
    expect(result.label).toBe("2 source hostnames");
  });

  it("handles malformed URLs and empty evidence clearly", () => {
    expect(sourceHostname("not-a-url")).toBe("Web source");
    expect(summarizeSourceCoverage([]).label).toBe("No source links saved");
  });
});
