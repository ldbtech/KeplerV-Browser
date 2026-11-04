# KeplerV privacy and data controls

This page describes the current desktop build. It does not make promises about a provider’s own retention, training, billing, or deletion policy.

## Before research runs

Each goal requires consent for the active model provider and model. KeplerV sends that provider the goal text, research task, starting URL, and selected page text needed to produce quoted findings. Changing the provider or model requires a new consent choice. Turn off the consent checkbox to withdraw it for future runs.

The optional token ceiling can be disabled with Unlimited. KeplerV shows observed token counts, not a dollar estimate; provider context windows, per-response limits, and charges still apply. Review pricing and retention with the selected provider. A model configured at a remote endpoint is remote even when its label says “local.”

## Voice

Voice input requires separate consent. KeplerV records only after the user starts capture, sends the resulting audio to Meta for transcription, and does not save the recording in the KeplerV workspace. The transcript is handled in the active app session. Meta’s own processing and retention terms apply. Microphone access is requested by macOS when the feature is started, and voice consent can be withdrawn in Settings.

## External agents

External-agent access is enabled per goal. KeplerV may search public web results for a public agent card or use a URL supplied by the user. It does not scan networks or invent endpoints. The user reviews the agent identity, destination, and exact message before each outbound session or message. Replies are untrusted. The destination operator’s retention terms are outside KeplerV’s control.

## What stays on this Mac

- Saved goals, tasks, bookmarks, activity, resources, and quoted findings live in the local workspace.
- Research run conversations, extracted page text, Guard evidence, and A2A session transcripts live in the local runtime database until deleted.
- Provider API keys and OAuth tokens are stored separately in Keychain. Workspace export does not include them.
- Browsing is performed in the app’s WebKit surface or isolated research browser. KeplerV does not currently provide a general browser history, password manager, cookie export, or cross-device profile sync.

## Export and deletion

- Bookmark HTML import/export uses the standard Netscape bookmark file format. Only HTTP and HTTPS bookmarks are accepted.
- Workspace JSON backup is a manual transfer for goals, tasks, bookmarks, resources, findings, activity, and appearance. It excludes runtime conversations and Guard evidence, credentials, cookies, passwords, and ordinary browser history. Treat the file as sensitive because goal text and quoted page excerpts can be included.
- **Delete workspace and local runtime history** removes saved goals, bookmarks, findings, research runs, Guard records, and external-agent conversations from this Mac. Provider and OAuth credentials remain in Keychain until separately disconnected. Downloaded backup files are not removed.
- **Delete goal** (goal inspector) stops that goal's runs, denies its pending approvals, and removes its runs, events, findings, Guard cases, gateway sessions, and screenshots; other goals are untouched.
- **Clear browsing data** (settings) clears cookies from live ephemeral browser contexts, removes saved screenshots, and resets cached page state. Research history, findings, and Guard records are kept, and live contexts stay open so runs keep working.

## Distribution

The direct-download Developer ID DMG and a Mac App Store build are separate release tracks. See [App Store readiness](app-store-readiness.md) for the remaining sandbox, helper-runtime, signing, privacy-policy, and review work.
