import { describe, expect, it } from "vitest";
import { buildLearningLibrary, vimeoEmbedFromUrl, youtubeEmbedFromUrl } from "./learningLibrary";

describe("agent learning library", () => {
  it("turns ordinary video links into playable items without asking for provider IDs", () => {
    expect(youtubeEmbedFromUrl("https://youtu.be/abcdefghijk")).toContain("/embed/abcdefghijk");
    expect(youtubeEmbedFromUrl("https://www.youtube.com/watch?v=abcdefghijk&t=4")).toContain("/embed/abcdefghijk");
    expect(vimeoEmbedFromUrl("https://vimeo.com/12345678")).toBe("https://player.vimeo.com/video/12345678");
    expect(youtubeEmbedFromUrl("https://notyoutube.com/watch?v=abcdefghijk")).toBeNull();
    expect(vimeoEmbedFromUrl("https://vimeo.com.attacker.invalid/12345678")).toBeNull();
  });

  it("groups agent evidence with saved links and recognizes PDF and direct video files", () => {
    const library = buildLearningLibrary("goal-1", [
      { id: "pdf", goalId: "goal-1", agentId: "agent-1", label: "Regression notes", url: "https://example.org/notes.PDF?download=1" },
      { id: "clip", goalId: "goal-1", agentId: "agent-1", label: "Lecture", url: "https://cdn.example.org/lesson.webm" },
      { id: "other", goalId: "goal-2", agentId: "agent-1", label: "Other goal", url: "https://youtube.com/watch?v=abcdefghijk" },
    ], [{ id: "finding", goalId: "goal-1", agentId: "agent-1", runId: "run", finding: "Linear regression lecture", sourceUrl: "https://youtu.be/abcdefghijk", quote: "A useful explanation", createdAt: "now" }]);
    expect(library.map((item) => item.kind)).toEqual(["video", "video", "document"]);
    expect(library.find((item) => item.url === "https://youtu.be/abcdefghijk")?.title).toBe("Linear regression lecture");
    expect(library.find((item) => item.url.includes(".PDF"))?.player).toBe("pdf");
    expect(library.find((item) => item.url.endsWith(".webm"))?.player).toBe("video-file");
    expect(library).toHaveLength(3);
  });

  it("opens the learning library from voice without requiring a media id", () => {
    const library = buildLearningLibrary("goal-empty", [], []);
    expect(library).toEqual([]);
  });
});
