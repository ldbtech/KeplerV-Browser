from __future__ import annotations

import html
import json
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1920, 1080


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def txt(x: int, y: int, value: str, size: int = 24, color: str = "#f4f2ff", weight: int = 400, opacity: float = 1.0, letter: float = 0) -> str:
    return f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{weight}" opacity="{opacity}" letter-spacing="{letter}">{esc(value)}</text>'


def multiline(x: int, y: int, lines: list[str], size: int = 22, color: str = "#c0bfd1", weight: int = 400, leading: int | None = None) -> str:
    leading = leading or int(size * 1.45)
    spans = "".join(f'<tspan x="{x}" dy="{0 if i == 0 else leading}">{esc(line)}</tspan>' for i, line in enumerate(lines))
    return f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{weight}">{spans}</text>'


def rect(x: int, y: int, w: int, h: int, fill: str = "#171923", radius: int = 18, stroke: str = "#2c2e3c", opacity: float = 1.0) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" opacity="{opacity}"/>'


def pill(x: int, y: int, label: str, bg: str = "#28253d", fg: str = "#c0adff", width: int | None = None) -> str:
    width = width or max(88, 22 + len(label) * 11)
    return rect(x, y, width, 34, bg, 17, bg) + txt(x + 16, y + 23, label, 14, fg, 700, letter=.4)


def rule(x: int, y: int, w: int, color: str = "#303240") -> str:
    return f'<path d="M{x} {y} H{x+w}" stroke="{color}" stroke-width="1"/>'


def button(x: int, y: int, label: str, width: int = 210, primary: bool = False) -> str:
    bg, fg = ("url(#accent)", "#11111a") if primary else ("#212330", "#e8e6f6")
    return rect(x, y, width, 52, bg, 15, bg if primary else "#393b4b") + txt(x + 20, y + 33, label, 17, fg, 700)


def card(x: int, y: int, w: int, h: int, title: str, body: list[str], tag: str | None = None, accent: str = "#9f8bff") -> str:
    out = [rect(x, y, w, h, "#181a25", 19, "#303342")]
    out.append(f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2" fill="{accent}"/>')
    if tag:
        out.append(pill(x + 22, y + 18, tag, "#29253d", accent))
        title_y = y + 86
    else:
        title_y = y + 42
    out.append(txt(x + 24, title_y, title, 21, "#f4f2ff", 700))
    out.append(multiline(x + 24, title_y + 35, body, 16, "#a8a9bb", 400, 25))
    return "".join(out)


def shell(selected: str, chapter: str, scene_no: int) -> list[str]:
    s = [
        '<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b0c12"/><stop offset=".58" stop-color="#11131c"/><stop offset="1" stop-color="#151324"/></linearGradient><linearGradient id="accent" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#b2a2ff"/><stop offset="1" stop-color="#856cff"/></linearGradient><radialGradient id="glow"><stop stop-color="#7055dc" stop-opacity=".22"/><stop offset="1" stop-color="#7055dc" stop-opacity="0"/></radialGradient></defs>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>',
        '<circle cx="1540" cy="190" r="520" fill="url(#glow)"/>',
        '<circle cx="190" cy="890" r="430" fill="url(#glow)"/>',
        rect(44, 32, 1832, 1010, "#10121a", 27, "#30323d"),
        '<path d="M44 111 H1876" stroke="#2d2f3a"/>',
        '<g transform="translate(83 58)" fill="none" stroke="#a38fff" stroke-width="3"><ellipse cx="19" cy="17" rx="19" ry="8"/><ellipse cx="19" cy="17" rx="19" ry="8" transform="rotate(60 19 17)"/><ellipse cx="19" cy="17" rx="19" ry="8" transform="rotate(120 19 17)"/><circle cx="19" cy="17" r="3.5" fill="#a38fff"/></g>',
        txt(127, 78, "keplerv", 25, "#f5f3ff", 700),
        txt(783, 77, "PRESENT", 14, "#aaa9bc", 600, letter=1.8),
        pill(1486, 51, f"ILLUSTRATED TOUR · {scene_no:02d}", "#28253d", "#c4b6ff", 318),
        rect(66, 132, 248, 878, "#12141d", 22, "#272a36"),
        txt(94, 176, "YOUR SPACE", 12, "#8c8fa2", 700, letter=2),
    ]
    nav = ["My goals", "Helpers", "Recent activity", "Safety", "LLM providers"]
    for i, item in enumerate(nav):
        yy = 208 + i * 62
        active = item == selected
        if active:
            s.append(rect(80, yy - 27, 218, 48, "#29263d", 13, "#38324f"))
        dot = "#b1a0ff" if active else "#717488"
        s.append(f'<circle cx="103" cy="{yy-3}" r="6" fill="{dot}"/>')
        s.append(txt(123, yy + 3, item, 16, "#eceafb" if active else "#a3a5b7", 600 if active else 400))
    s.append(rule(87, 555, 204))
    s.append(button(85, 886, "+ New goal", 206, True))
    s.append(txt(92, 965, "Accessibility & settings", 13, "#9698aa", 500))
    s.append(txt(363, 160, f"WORKSPACE  /  {chapter.upper()}", 13, "#9295a8", 700, letter=1.4))
    return s


def scene_svg(index: int, chapter: str, selected: str, draw_content) -> str:
    parts = shell(selected, chapter, index)
    parts.extend(draw_content())
    parts.append(rect(350, 1016, 1490, 1, "#333440", 0, "#333440"))
    parts.append(txt(363, 1038, "KEPLERV  ·  INTENT-FIRST BROWSING WITH USER-CONTROLLED AGENTS", 11, "#77798d", 600, letter=.7))
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}"><style>text{{dominant-baseline:auto}}</style>{"".join(parts)}</svg>'


def content_1():
    s = [txt(369, 226, "A browser built around what you want to do.", 48, "#f4f2ff", 700),
         multiline(371, 277, ["Turn a goal into focused research, useful media and a plan you can return to."], 21, "#aaaabd"),
         rect(368, 330, 1435, 534, "#151722", 25, "#303240")]
    s += [pill(408, 370, "INTENT SPACE", "#29253d", "#c0adff", 164),
          txt(413, 462, "Understand coastal resilience", 29, "#f4f2ff", 700),
          txt(413, 502, "Compare trusted sources and collect a study library.", 18, "#a6a7ba"),
          f'<circle cx="1079" cy="604" r="118" fill="#25213b" stroke="#7160b6" stroke-width="2"/>',
          f'<circle cx="1079" cy="604" r="82" fill="#30285a" stroke="#a18aff" stroke-width="2"/>',
          f'<circle cx="1079" cy="604" r="22" fill="url(#accent)"/>',
          '<path d="M962 600 C845 560 748 573 655 608" fill="none" stroke="#9280e4" stroke-width="2" stroke-dasharray="6 8"/>',
          '<path d="M1192 604 C1292 594 1365 575 1452 548" fill="none" stroke="#9280e4" stroke-width="2"/>',
          f'<circle cx="646" cy="608" r="11" fill="#aa9aff"/>', txt(670, 599, "Your goal", 18, "#f1efff", 700),
          f'<circle cx="1460" cy="545" r="11" fill="#8f7aed"/>', txt(1483, 539, "Researcher", 18, "#f1efff", 700),
          pill(413, 787, "GOAL", "#222331", "#aaa9bb", 90), pill(518, 787, "RESEARCH", "#29253d", "#c2b5ff", 136), pill(670, 787, "REVIEW", "#222331", "#aaa9bb", 104), pill(791, 787, "LEARN", "#222331", "#aaa9bb", 99),
          txt(371, 932, "A guided product tour  ·  The illustrated screens track the current UI and verified capability notes.", 17, "#9293a5")]
    return s


def content_2():
    s = [txt(363, 228, "Your work, at a glance.", 43, "#f5f3ff", 700), txt(365, 267, "Choose a goal to see its progress, tasks and helpers.", 18, "#a7a8ba"), button(1607, 192, "+ New goal", 196, True),
         rect(363, 304, 1440, 579, "#151721", 24, "#2c2e3b"),
         txt(400, 350, "CURRENT GOAL", 14, "#9e9eb2", 700, letter=1.4),
         txt(400, 402, "Compare coastal adaptation plans", 28, "#f6f4ff", 700),
         txt(400, 439, "Public sources  ·  0% complete  ·  3 related tasks", 16, "#999bae"),
         '<ellipse cx="1080" cy="612" rx="490" ry="165" fill="none" stroke="#343143" stroke-width="2"/>',
         '<ellipse cx="1080" cy="612" rx="334" ry="118" fill="none" stroke="#3b3750" stroke-width="2"/>',
         '<ellipse cx="1080" cy="612" rx="185" ry="66" fill="none" stroke="#574c7d" stroke-width="2"/>',
         '<circle cx="1080" cy="612" r="36" fill="url(#accent)"/>',
         '<circle cx="727" cy="610" r="13" fill="#ab9aff"/>',
         '<path d="M748 610 C850 610 904 612 1028 612" stroke="#9380e7" fill="none" stroke-width="2"/>',
         '<circle cx="1425" cy="613" r="12" fill="#8d79e4"/>',
         '<path d="M1116 612 C1252 612 1316 613 1412 613" stroke="#9380e7" fill="none" stroke-width="2"/>',
         txt(620, 582, "GOAL", 13, "#aaa3d1", 700, letter=1), txt(622, 644, "Research plan", 18, "#f3f1ff", 700),
         txt(1450, 586, "HELPER", 13, "#aaa3d1", 700, letter=1), txt(1450, 646, "Researcher", 17, "#f3f1ff", 700),
         pill(400, 798, "RELATED TASKS", "#252633", "#aaaabd", 154),
         txt(578, 821, "01  Clarify the question", 16, "#d7d5e7"), txt(867, 821, "02  Gather evidence", 16, "#d7d5e7"), txt(1132, 821, "03  Review the outcome", 16, "#d7d5e7"),
         txt(364, 931, "Type or speak a goal. KeplerV keeps tasks and helpers attached to the same workspace.", 17, "#999bae")]
    return s


def content_3():
    s = [txt(363, 228, "Research with a clear boundary.", 42, "#f5f3ff", 700),
         txt(365, 268, "A goal can collect source-linked findings after you approve its research context.", 18, "#a7a8ba"),
         rect(364, 310, 888, 572, "#151721", 22, "#2d2f3d"),
         pill(397, 342, "ACTIVE GOAL", "#29253d", "#c0b3ff", 140),
         txt(399, 423, "Understand coastal resilience", 25, "#f4f2ff", 700),
         multiline(399, 460, ["Compare public research and identify practical", "adaptation strategies for the North Shore."], 18, "#a9aabc"),
         card(398, 538, 370, 250, "Related tasks", ["Clarify the next step", "Collect useful evidence", "Review the outcome"], "3 STEPS"),
         card(790, 538, 423, 250, "Assigned helper", ["Researcher", "Runs on this device"], "LOCAL"),
         rect(1290, 310, 514, 572, "#1a1b28", 22, "#4b4268"),
         txt(1323, 360, "Research with Muse", 24, "#f4f2ff", 700),
         multiline(1323, 399, ["Search across public sites, compare", "evidence, then save quotes and links."], 16, "#a8a9bb"),
         txt(1323, 493, "MAXIMUM TOKEN BUDGET", 12, "#aeb0c1", 700, letter=1),
         rect(1323, 511, 443, 54, "#11131c", 12, "#3b3d4d"), txt(1340, 546, "5,000 tokens per run", 17, "#f1efff", 600),
         txt(1323, 605, "PROVIDER CONTEXT CONSENT", 12, "#aeb0c1", 700, letter=1),
         rect(1323, 624, 21, 21, "#151721", 5, "#89849e"), txt(1355, 641, "Off until you approve", 16, "#c8c5d6", 600),
         multiline(1323, 690, ["The goal, starting link and selected", "public excerpts go to the chosen", "provider only after consent."], 14, "#9698aa", 400, 21),
         button(1323, 788, "Research with Muse", 443, False),
         txt(365, 932, "This example uses real public links selected for the tour; no live research run or provider call occurred.", 16, "#999bae")]
    return s


def content_4():
    s = [txt(363, 228, "A learning library tied to the goal.", 42, "#f5f3ff", 700),
         txt(365, 267, "Videos, documents and web pages stay together for review.", 18, "#a7a8ba"),
         pill(365, 303, "VIDEOS  1", "#29253d", "#c0b2ff", 123), pill(500, 303, "DOCUMENTS  1", "#222331", "#afb0c1", 158), pill(670, 303, "WEB PAGES  2", "#222331", "#afb0c1", 146),
         rect(363, 356, 892, 500, "#171923", 22, "#303240"),
         rect(386, 380, 846, 253, "#25243a", 18, "#38344f"),
         '<circle cx="808" cy="506" r="44" fill="#8c70ec" opacity=".94"/>',
         '<path d="M798 482 L798 530 L833 506 Z" fill="#14131d"/>',
         pill(410, 399, "OFFICIAL VIDEO PLAYLIST", "#332c4a", "#d1c7ff", 226),
         txt(410, 682, "Warming Up to Adaptation", 24, "#f5f3ff", 700),
         txt(410, 717, "National Park Service · opens at YouTube", 16, "#a4a5b9"),
         button(410, 765, "Open video", 192, True),
         card(1285, 356, 519, 236, "NOAA five-step guide", ["PDF document", "Open in the library or at the source.", "Remote sites may restrict embedding."], "PDF", "#85a9ff"),
         card(1285, 618, 519, 238, "Coastal adaptation guide", ["Web resource", "Saved to this research goal.", "Open the source whenever you need it."], "ARTICLE", "#8de0c6"),
         txt(365, 932, "YouTube / Vimeo use provider-supported embeds. DRM services such as Netflix open externally; KeplerV does not bypass DRM.", 15, "#999bae")]
    return s


def content_5():
    s = [txt(363, 228, "Helpers and connected services.", 42, "#f5f3ff", 700),
         txt(365, 267, "Keep routine research separate from actions that need your approval.", 18, "#a7a8ba"),
         card(364, 314, 446, 196, "On-device Researcher", ["Organizes public-source work", "Can be assigned to a goal", "Runs in the local workspace"], "HELPER", "#a48eff"),
         card(364, 534, 446, 255, "Connected services", ["Gmail / Outlook OAuth", "Your client ID and account are required", "Scoped tokens stay out of frontend JS"], "PHASE 5", "#74d3c4"),
         rect(852, 314, 951, 475, "#161822", 22, "#323441"),
         pill(886, 344, "SENSITIVE ACTION · EMAIL SEND", "#39272d", "#ffbdc4", 298),
         txt(888, 426, "Review before anything is sent", 25, "#f4f2ff", 700),
         txt(888, 480, "TO", 12, "#9e9fb2", 700, letter=1), rect(888, 495, 857, 44, "#11131c", 10, "#383a48"), txt(906, 524, "recipient shown for your review", 15, "#d4d2e2"),
         txt(888, 564, "SUBJECT + MESSAGE PREVIEW", 12, "#9e9fb2", 700, letter=1), rect(888, 581, 857, 83, "#11131c", 10, "#383a48"), multiline(906, 610, ["Draft content appears here before sending.", "Provider and account scope are shown with the request."], 14, "#aaaabb", 400, 23),
         pill(888, 700, "AIP GRANT REQUIRED", "#28253d", "#c0b4ff", 205), pill(1105, 700, "HUMAN APPROVAL REQUIRED", "#39272d", "#ffc0c5", 253),
         button(888, 742, "Approve send", 220, False),
         txt(365, 932, "Live email requires OAuth setup, an AIP grant and your per-send review. Disconnected calls fail closed; no mail was sent in this tour.", 15, "#999bae")]
    return s


def content_6():
    s = [txt(363, 228, "Guard makes policy decisions reviewable.", 40, "#f5f3ff", 700),
         txt(365, 267, "A blocked action can create a case with decision evidence and status.", 18, "#a7a8ba"),
         rect(363, 314, 936, 509, "#171923", 22, "#303240"),
         pill(397, 344, "SAFETY WORKSPACE", "#29253d", "#c0b2ff", 190),
         txt(397, 420, "No active cases in this staging profile", 25, "#f4f2ff", 700),
         multiline(397, 457, ["When AIP blocks a routed agent action, Guard can retain the", "decision, reason, evidence and review status here."], 17, "#a4a5b8"),
         card(397, 537, 258, 189, "Decision", ["Allowed / blocked", "Policy reason"], "01", "#ff9b9b"),
         card(671, 537, 258, 189, "Evidence", ["Action summary", "Relevant runtime record"], "02", "#ffbd7d"),
         card(945, 537, 315, 189, "Response", ["Review or undo", "Pause-only default"], "03", "#93e0c5"),
         rect(1332, 314, 472, 509, "#1a1b28", 22, "#3a3c4b"),
         txt(1367, 370, "What Guard covers", 23, "#f5f3ff", 700),
         multiline(1367, 410, ["Policy-gated tools and", "actions routed through", "KeplerV's AIP layer."], 17, "#b1b2c2"),
         rule(1367, 503, 399),
         txt(1367, 546, "What Guard cannot promise", 20, "#f4f2ff", 700),
         multiline(1367, 585, ["It is not an all-seeing", "monitor and cannot guarantee", "detection of novel attacks."], 16, "#a3a5b6"),
         pill(1367, 699, "ILLUSTRATIVE FLOW · NO INCIDENT", "#302b25", "#ffd397", 288),
         txt(365, 932, "Guard records real policy events; this illustrated panel is not a real block or incident.", 16, "#999bae")]
    return s


def content_7():
    s = [txt(363, 228, "Outside agents are opt-in and approval-gated.", 38, "#f5f3ff", 700),
         txt(365, 267, "Phase Six connects to a public A2A endpoint only after you review each step.", 18, "#a7a8ba"),
         rect(363, 314, 930, 523, "#171923", 22, "#303240"),
         pill(397, 344, "PUBLIC A2A · PER GOAL", "#29253d", "#c0b2ff", 224),
         txt(397, 416, "Allow this goal to contact external agents", 22, "#f4f2ff", 700),
         rect(397, 443, 24, 24, "#a58eff", 6, "#a58eff"),
         '<path d="M403 455 L409 461 L418 449" fill="none" stroke="#14131d" stroke-width="2.5"/>',
         txt(435, 461, "Opt in for this goal only", 16, "#dfddec", 600),
         txt(397, 511, "AGENT CARD URL", 12, "#9e9fb2", 700, letter=1),
         rect(397, 528, 858, 55, "#11131c", 11, "#383a48"), txt(416, 563, "https://public-agent.example/.well-known/agent-card.json", 15, "#a7a8ba"),
         txt(397, 626, "DESTINATION + MESSAGE PREVIEW", 12, "#9e9fb2", 700, letter=1),
         rect(397, 643, 858, 102, "#11131c", 11, "#383a48"), multiline(416, 675, ["Exact endpoint and outbound text are shown here.", "You approve the session and each message separately."], 14, "#b4b5c5", 400, 24),
         button(397, 765, "Review request", 222, False),
         rect(1327, 314, 477, 523, "#1a1b28", 22, "#4b4268"),
         txt(1361, 371, "Trust Gateway", 23, "#f5f3ff", 700),
         multiline(1361, 411, ["HTTPS destination checks", "No private-network targets", "Rate and message budgets", "Transcript per session"], 16, "#aeb0c0", 400, 32),
         pill(1361, 573, "REPLY = UNTRUSTED INPUT", "#39272d", "#ffc0c5", 271),
         txt(1361, 648, "Public, unauthenticated A2A agents only", 15, "#cdcadb", 600),
         multiline(1361, 684, ["No universal directory. OAuth / API-key", "agent onboarding and file-part transport", "are not supported yet."], 14, "#9fa1b2", 400, 22),
         pill(1361, 779, "NO OUTBOUND CONTACT IN THIS VIDEO", "#2c2638", "#c7b8ff", 339),
         txt(365, 932, "An A2A endpoint is not proof of identity. Agent trust tiers are self-asserted unless independently established.", 15, "#999bae")]
    return s


def content_8():
    s = [txt(363, 228, "See the data path before a run.", 42, "#f5f3ff", 700),
         txt(365, 267, "Provider, endpoint, context and limits are visible for the selected goal.", 18, "#a7a8ba"),
         rect(363, 314, 885, 522, "#171923", 22, "#303240"),
         txt(399, 367, "Provider for this goal", 23, "#f4f2ff", 700),
         pill(399, 389, "CONFIGURED · CONSENT OFF", "#302b25", "#ffd397", 258),
         txt(399, 468, "Context that would be sent", 18, "#f0eefb", 600),
         "".join([f'<circle cx="410" cy="{y}" r="6" fill="#aa96ff"/>' + txt(429, y+6, label, 16, "#c7c5d5") for y, label in [(505,"Goal and related tasks"),(542,"Starting page, if supplied"),(579,"Selected public page excerpts")]]),
         rule(399, 620, 801),
         txt(399, 661, "Token ceiling", 16, "#e0deed", 600), txt(1010, 661, "1,000–50,000 tokens / run", 14, "#b0b2c2", 600),
         multiline(399, 711, ["A token cap is a usage limit, not a dollar estimate.", "The provider controls its own retention."], 15, "#a4a5b7"),
         rect(1292, 314, 512, 522, "#1a1b28", 22, "#3a3c4b"),
         txt(1327, 367, "Local privacy controls", 23, "#f5f3ff", 700),
         card(1327, 401, 440, 115, "Local endpoint", ["Data stays on this Mac only when" , "the configured endpoint is local."], None),
         card(1327, 534, 440, 115, "Keychain", ["OAuth secrets stay in macOS Keychain;", "tokens are bridged to the runtime."], None, "#70d7c4"),
         card(1327, 667, 440, 115, "History controls", ["Export a workspace backup or delete", "local runtime history from settings."], None, "#ffbf78"),
         txt(365, 932, "Consent is per goal and provider. Switching provider or endpoint requires fresh consent.", 16, "#999bae")]
    return s


def content_9():
    s = [txt(363, 228, "Switching in and out is supported.", 42, "#f5f3ff", 700),
         txt(365, 267, "Bring bookmarks in and keep a portable copy of your workspace.", 18, "#a7a8ba"),
         card(363, 326, 436, 249, "Import bookmarks", ["Browser bookmarks HTML", "Keep folder paths", "Merge without duplicate URLs"], "MOVE IN", "#86c3ff"),
         card(827, 326, 436, 249, "Export bookmarks", ["Standard HTML bookmark file", "Carry bookmarks to another browser", "Keep titles and folders"], "MOVE OUT", "#8de0c6"),
         card(1290, 326, 514, 249, "Workspace backup", ["Goals, saved resources, and evidence", "Portable JSON with schema validation", "Use it for manual device transfer"], "PORTABLE COPY", "#a48eff"),
         rect(363, 620, 1441, 195, "#171923", 21, "#303240"),
         txt(397, 674, "Today's continuity", 21, "#f4f2ff", 700),
         txt(397, 720, "Bookmarks + local workspace backup", 17, "#b7b5c8"),
         '<path d="M826 708 H1190" stroke="#51486e" stroke-width="2" stroke-dasharray="7 8"/>',
         '<circle cx="851" cy="708" r="11" fill="#a48eff"/>', '<circle cx="1172" cy="708" r="11" fill="#7f8194"/>',
         txt(1212, 702, "Cloud profile sync", 17, "#dad8e7", 700), pill(1212, 722, "NOT AVAILABLE IN THIS BUILD", "#302b25", "#ffd397", 259),
         txt(365, 932, "Bookmark import/export is the migration path today. Cross-device profile sync is not implemented.", 16, "#999bae")]
    return s


def content_10():
    s = [txt(363, 228, "One workspace for the whole learning loop.", 39, "#f5f3ff", 700),
         txt(365, 267, "KeplerV connects intent, research, media and action under visible user controls.", 18, "#a7a8ba"),
         rect(363, 314, 1441, 414, "#151721", 24, "#343342"),
         '<circle cx="1081" cy="523" r="146" fill="#211f32" stroke="#6a5b9a" stroke-width="2"/>',
         '<circle cx="1081" cy="523" r="90" fill="#302856" stroke="#9a84f0" stroke-width="2"/>',
         '<circle cx="1081" cy="523" r="26" fill="url(#accent)"/>',
         '<ellipse cx="1081" cy="523" rx="460" ry="142" fill="none" stroke="#403c51" stroke-width="2"/>',
         '<ellipse cx="1081" cy="523" rx="320" ry="100" fill="none" stroke="#51486e" stroke-width="2"/>',
         '<circle cx="620" cy="523" r="12" fill="#a795ff"/>', '<circle cx="1541" cy="523" r="12" fill="#7edbc9"/>',
         '<circle cx="1081" cy="380" r="12" fill="#87bbff"/>', '<circle cx="1081" cy="665" r="12" fill="#ffbf78"/>',
         txt(497, 516, "GOAL", 15, "#e2ddff", 700, letter=1), txt(1570, 516, "LEARN", 15, "#c7f4e8", 700, letter=1),
         txt(1038, 357, "RESEARCH", 14, "#cee0ff", 700, letter=1), txt(1027, 704, "REVIEW", 14, "#ffe4be", 700, letter=1),
         card(363, 773, 454, 114, "Live features need setup", ["Provider consent, OAuth sign-in or agent approval."], None),
         card(836, 773, 454, 114, "This film is illustrated", ["Curated source examples; no live agent or model call."], None, "#86c3ff"),
         card(1308, 773, 496, 114, "Continue the product", ["Configure, approve, review, and keep control."], None, "#8de0c6"),
         txt(363, 932, "KeplerV  ·  Make the goal the starting point.", 23, "#c7b8ff", 700)]
    return s

SCENES = [
    ("A browser around your goals", "My goals", content_1,
     "Meet KeplerV: a desktop browser centered on goals. You describe the outcome, then keep research, sources and follow-up steps in one workspace."),
    ("Goals and helpers", "My goals", content_2,
     "The workspace connects a goal to tasks, helpers and saved links. Progress and recent activity stay beside the plan, so you can return to the work instead of rebuilding context. Type or speak a goal to get started."),
    ("Research with consent", "My goals", content_3,
     "For public research, Muse can search across sites and save source-linked findings. Each run uses your configured provider, a token ceiling, and the goal context you explicitly approve. The NOAA, NPS and USGS links in this tour are curated examples, not results generated live."),
    ("Videos, PDFs and pages", "My goals", content_4,
     "The Learning Library gathers goal-related web pages, video links and PDFs. Open supported YouTube or Vimeo resources from their cards. PDFs open in the document view. DRM services such as Netflix hand off to the system browser; KeplerV does not bypass playback protections."),
    ("Helpers and connected services", "Helpers", content_5,
     "Helpers divide the work. Connected services use your own OAuth setup and scoped access. A live email send needs the account, an AIP grant, and your review of recipient, subject, message and provider. Disconnected service calls fail closed. This demo sent no email."),
    ("Guard and AIP", "Safety", content_6,
     "Guard explains policy decisions that block an action and keeps investigation evidence with the runtime workspace. It can pause or contain through configured controls. Guard covers actions routed through KeplerV policy; it is not an all-seeing security guarantee. No incident occurred in this demo profile."),
    ("Public agents with approval", "My goals", content_7,
     "Phase Six adds optional public A2A agent sessions. You provide an Agent Card or allow public web discovery. KeplerV shows the agent, destination and exact message for your approval. Replies are untrusted input and the session transcript is kept. This walkthrough contacted no outside agent."),
    ("Privacy and usage controls", "LLM providers", content_8,
     "Privacy controls show which provider and endpoint would receive goal context. Consent is per goal and provider. Token caps set usage boundaries, not dollar estimates. Local-model data stays on this Mac only when the configured endpoint is actually local. OAuth secrets stay in Keychain."),
    ("Import and export", "My goals", content_9,
     "Switching is supported through bookmark import and export and a portable workspace backup. The backup gives you a manual way to move your work between devices. Cross-device cloud profile sync is not implemented in this build."),
    ("KeplerV product tour", "My goals", content_10,
     "KeplerV brings goals, research, learning media and safety review together. Live model, email and agent features need configuration and approval. This is an illustrated walkthrough of the product flow: the links are curated, no live model ran, no email was sent, and no external agent was contacted."),
]


def run(args: list[str]) -> str:
    completed = subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.strip()


def duration(path: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]))


def main() -> None:
    frames_dir = ROOT / "frames"
    audio_dir = ROOT / "voice"
    segments_dir = ROOT / "segments"
    for folder in (frames_dir, audio_dir, segments_dir):
        folder.mkdir(exist_ok=True)

    narration = []
    for index, (chapter, selected, draw, voice) in enumerate(SCENES, start=1):
        svg = scene_svg(index, chapter, selected, draw)
        svg_path = frames_dir / f"scene-{index:02d}.svg"
        png_path = frames_dir / f"scene-{index:02d}.png"
        svg_path.write_text(svg, encoding="utf-8")
        run(["rsvg-convert", "-w", str(WIDTH), "-h", str(HEIGHT), "-o", str(png_path), str(svg_path)])
        narration.append(f"{index:02d}. {chapter}\n{voice}")
        voice_path = audio_dir / f"scene-{index:02d}.aiff"
        run(["say", "-v", "Samantha", "-r", "178", "-o", str(voice_path), voice])
        voice_length = duration(voice_path)
        segment_length = voice_length + 1.15
        segment_path = segments_dir / f"scene-{index:02d}.mp4"
        fade_out = max(voice_length + 0.5, segment_length - 0.35)
        frames = int(segment_length * 30)
        vf = f"zoompan=z='min(max(zoom,pzoom)+0.00003,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={WIDTH}x{HEIGHT}:fps=30,format=yuv420p,fade=t=in:st=0:d=0.35,fade=t=out:st={segment_length-0.35:.3f}:d=0.35"
        af = f"aresample=48000,apad=pad_dur=1.15,afade=t=in:st=0:d=0.08,afade=t=out:st={fade_out:.3f}:d=0.25"
        run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(png_path), "-i", str(voice_path), "-t", f"{segment_length:.3f}", "-vf", vf, "-af", af, "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(segment_path)])
        print(f"Rendered scene {index:02d}: {voice_length:.1f}s voice, {segment_length:.1f}s scene")

    (ROOT / "narration.txt").write_text("\n\n".join(narration) + "\n", encoding="utf-8")
    listing = ROOT / "segments.ffconcat"
    listing.write_text("ffconcat version 1.0\n" + "".join(f"file '{(segments_dir / f'scene-{i:02d}.mp4').as_posix()}'\n" for i in range(1, len(SCENES)+1)), encoding="utf-8")
    output = ROOT / "keplerv-judge-demo.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(output)])

    srt = []
    cursor = 0.0
    for i in range(1, len(SCENES)+1):
        voice_length = duration(audio_dir / f"scene-{i:02d}.aiff")
        end = cursor + voice_length + 0.8
        text = SCENES[i-1][3]
        lines = textwrap.wrap(text, width=78)
        start_stamp = f"{int(cursor//3600):02d}:{int(cursor%3600//60):02d}:{cursor%60:06.3f}".replace('.', ',')
        end_stamp = f"{int(end//3600):02d}:{int(end%3600//60):02d}:{end%60:06.3f}".replace('.', ',')
        srt.append(f"{i}\n{start_stamp} --> {end_stamp}\n" + "\n".join(lines))
        cursor += voice_length + 1.15
    (ROOT / "captions.srt").write_text("\n\n".join(srt) + "\n", encoding="utf-8")

    (ROOT / "README.md").write_text("""# KeplerV Judge Demo\n\n`keplerv-judge-demo.mp4` is a narrated, illustrated product tour prepared from the current KeplerV UI and the checked-in Phase 5 / Phase 6 capability notes. It is not a screen recording.\n\nThe tour is intentionally explicit about what was and was not exercised: its coastal-resilience links are curated public examples; no live Muse/model run, outbound email, or external-agent contact occurred. The product flows are presented as UI/capability explanations, not as a claim that those actions succeeded in this recording.\n\n## Files\n\n- `keplerv-judge-demo.mp4` — H.264 video with narration\n- `captions.srt` — captions for the narration\n- `narration.txt` — voiceover script\n- `frames/` — editable SVG storyboard frames and rendered PNGs\n- `voice/` and `segments/` — generated narration audio and intermediate scenes\n\n## Public source examples shown\n\n- NOAA five-step adaptation guide: https://coast.noaa.gov/digitalcoast/training/adaptation-5step-guide.html\n- NOAA guide PDF: https://coast.noaa.gov/data/digitalcoast/pdf/adaptation-5step-guide.pdf\n- National Park Service adaptation video page: https://www.nps.gov/subjects/climatechange/adaptationvideos.htm\n- National Park Service video playlist: https://www.youtube.com/playlist?list=PLr8uf42JALwefCJlBDfq05KX7TbOBO7-Z\n- USGS coastal hazards and resilience: https://www.usgs.gov/science/science-explorer/coasts/coastal-hazards-and-resilience\n\nThese are cited as resource examples only. The film does not represent them as findings from a live KeplerV run.\n""", encoding="utf-8")
    print(f"Created {output} ({duration(output):.1f}s)")


if __name__ == "__main__":
    main()
