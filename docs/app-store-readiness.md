# Mac App Store readiness

The direct-download DMG and the Mac App Store are separate distribution tracks. The DMG build is self-contained, but the current build is not an App Store submission build.

## Current findings

- KeplerV requests microphone access with a purpose string in `src-tauri/Info.plist`. Voice capture sends the recording to Meta Muse for transcription; the purpose string now says so.
- The desktop app launches a bundled Python runtime that opens an authenticated loopback HTTP listener. It also stores provider credentials in Keychain and app data in the app data directory.
- The current Tauri app has no App Sandbox entitlement. Mac App Store distribution requires App Sandbox. A runtime permission prompt cannot replace that entitlement.
- The bundled runtime and Chromium executables need an App Store signing and sandbox review as embedded helper code. Apple’s helper-tool guidance requires a helper to inherit the containing app’s sandbox. The current runtime packaging has only been exercised in the unsandboxed direct-download build.
- No in-app privacy policy or public privacy-policy URL was found. App Store submissions need a privacy policy, privacy disclosures, and clear consent before personal data is shared with third-party AI services.
- The app connects to configured model providers and sends user prompts, goals, and selected context for processing. Research now requires an explicit per-goal consent tied to the active provider and model; voice capture has a separate, withdrawable consent before recording and sending audio to Meta for transcription. Provider retention and cost terms remain provider-specific and are not inferred by KeplerV.
- Settings support bookmark HTML import/export and a manual workspace JSON backup for goals, saved links, citations, activity, and appearance. This is not cloud sync; passwords, cookies, ordinary browsing history, Keychain credentials, Guard records, and agent runtime history are not part of the portable backup.
- The current desktop app uses Tauri’s WebKit webview for visible UI and bundles headless Playwright Chromium for agent research. If the product is submitted as a general-purpose browser or requests default-browser status, confirm the applicable Apple browser entitlement and review requirements before submission.

## Work required before App Store submission

1. Add an App Store-only Tauri configuration with the App Sandbox entitlement, application identifier, team identifier, required network client/server access, and the App Store provisioning profile. Keep this separate from the Developer ID DMG build.
2. Sign and verify the app, the Python helper, Chromium, and nested libraries with the correct identities and helper entitlements. Exercise the installed app with App Sandbox enabled, including local runtime startup, provider requests, OAuth loopback callbacks, voice capture, Keychain, and app-data writes.
3. Publish a privacy policy and provide a direct in-app policy link. Validate the consent copy against each provider’s actual retention and billing terms, and add a separate, reviewed disclosure for any future use of private pages or files. The existing per-goal and voice controls do not establish or override a provider’s retention terms.
4. Publish a privacy policy and complete App Store privacy disclosures using the actual retention and processing terms of every configured provider. Avoid claims about external provider retention that KeplerV cannot verify.
5. Set the encryption export-compliance value accurately, choose the app category and age rating, provide App Review notes and a working demo mode, and create the App Store Connect listing.
6. Decide whether the App Store product is a general-purpose browser. If so, confirm and implement Apple’s applicable browser entitlement and review requirements. Produce the App Store `.pkg` with the Mac App Store signing certificates and provisioning profile, validate it, then upload through App Store Connect. The Developer ID DMG is not the App Store upload format.

## Continuity limits

- Manual workspace backup is available for moving goals and citations between devices. Provider credentials stay in that Mac’s Keychain and must be connected again on another Mac.
- KeplerV does not currently sync profiles, bookmarks, runtime history, or open tabs across devices. Bookmark import/export is a portable HTML file, not background synchronization.
- Deleting the saved workspace and runtime history clears local goals, citations, research traces, Guard evidence, and external-agent conversations. Provider and OAuth credentials remain in Keychain until disconnected separately.

## Permission rule of thumb

Ask macOS for a protected resource only when the user starts the feature that needs it, explain the purpose in the system purpose string, and keep an alternative path when practical. Use explicit in-app consent when data leaves the Mac for an AI or other provider. Do not ask users to grant broad permissions to compensate for missing sandbox entitlements.

Useful references:

- [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Apple App Sandbox](https://developer.apple.com/documentation/security/app-sandbox)
- [Apple helper tools in sandboxed apps](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app)
- [Tauri App Store distribution](https://v2.tauri.app/distribute/app-store/)
