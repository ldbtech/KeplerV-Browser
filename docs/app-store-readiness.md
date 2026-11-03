# Mac App Store readiness

The direct-download DMG and the Mac App Store are separate distribution tracks. The DMG build is self-contained, but the current build is not an App Store submission build.

## Current findings

- KeplerV requests microphone access with a purpose string in `src-tauri/Info.plist`. Voice capture sends the recording to Meta Muse for transcription; the purpose string now says so.
- The desktop app launches a bundled Python runtime that opens an authenticated loopback HTTP listener. It also stores provider credentials in Keychain and app data in the app data directory.
- The current Tauri app has no App Sandbox entitlement. Mac App Store distribution requires App Sandbox. A runtime permission prompt cannot replace that entitlement.
- The bundled runtime and Chromium executables need an App Store signing and sandbox review as embedded helper code. Apple’s helper-tool guidance requires a helper to inherit the containing app’s sandbox. The current runtime packaging has only been exercised in the unsandboxed direct-download build.
- No in-app privacy policy or public privacy-policy URL was found. App Store submissions need a privacy policy, privacy disclosures, and clear consent before personal data is shared with third-party AI services.
- The app connects to configured model providers and sends user prompts, goals, and selected context for processing. This data flow needs a clear disclosure and a user choice before the first provider call. Connecting a provider alone should not be assumed to cover every future data-sharing purpose.
- The current desktop app uses Tauri’s WebKit webview for visible UI and bundles headless Playwright Chromium for agent research. If the product is submitted as a general-purpose browser or requests default-browser status, confirm the applicable Apple browser entitlement and review requirements before submission.

## Work required before App Store submission

1. Add an App Store-only Tauri configuration with the App Sandbox entitlement, application identifier, team identifier, required network client/server access, and the App Store provisioning profile. Keep this separate from the Developer ID DMG build.
2. Sign and verify the app, the Python helper, Chromium, and nested libraries with the correct identities and helper entitlements. Exercise the installed app with App Sandbox enabled, including local runtime startup, provider requests, OAuth loopback callbacks, voice capture, Keychain, and app-data writes.
3. Add an in-app privacy notice and explicit consent flow before voice audio or personal workspace context is sent to an external AI provider. Provide a way to withdraw consent and describe which provider receives what data.
4. Publish a privacy policy and complete App Store privacy disclosures using the actual retention and processing terms of every configured provider. Avoid claims about external provider retention that KeplerV cannot verify.
5. Set the encryption export-compliance value accurately, choose the app category and age rating, provide App Review notes and a working demo mode, and create the App Store Connect listing.
6. Produce the App Store `.pkg` with the Mac App Store signing certificates and provisioning profile, validate the package, then upload it through App Store Connect. The DMG is not the App Store upload format.

## Permission rule of thumb

Ask macOS for a protected resource only when the user starts the feature that needs it, explain the purpose in the system purpose string, and keep an alternative path when practical. Use explicit in-app consent when data leaves the Mac for an AI or other provider. Do not ask users to grant broad permissions to compensate for missing sandbox entitlements.

Useful references:

- [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Apple App Sandbox](https://developer.apple.com/documentation/security/app-sandbox)
- [Apple helper tools in sandboxed apps](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app)
- [Tauri App Store distribution](https://v2.tauri.app/distribute/app-store/)
