use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use url::Url;

mod runtime_bridge;
mod chatgpt_auth;
mod oauth;

static RESOURCE_WINDOW_ID: AtomicU64 = AtomicU64::new(1);

const ALLOWED_HOSTS: &[&str] = &[
    "www.nps.gov",
    "www.noaa.gov",
    "en.wikipedia.org",
    "www.wikipedia.org",
];

fn is_allowed_resource(url: &Url) -> bool {
    url.scheme() == "https"
        && url.port_or_known_default() == Some(443)
        && url.username().is_empty()
        && url.password().is_none()
        && url
            .host_str()
            .is_some_and(|host| ALLOWED_HOSTS.contains(&host))
}

/// Hosts allowed for the labeled system-browser fallback. These are
/// DRM-protected providers (e.g. Netflix) that cannot play inside a Tauri
/// webview, so they are never opened in an embedded window.
const SYSTEM_BROWSER_ALLOWED_HOSTS: &[&str] = &["netflix.com", "www.netflix.com"];

fn is_main_window_label(label: &str) -> bool {
    label == "main"
}

/// Strict validation for the system-browser fallback: http(s), standard port,
/// no embedded credentials, and an explicit host allowlist. The returned URL
/// is re-serialized from the parsed form so the opener receives exactly one
/// normalized address.
fn parse_system_browser_url(raw: &str) -> Result<Url, String> {
    let parsed = Url::parse(raw.trim()).map_err(|_| "Enter a valid http(s) URL.".to_string())?;
    let standard_port = match parsed.scheme() {
        "https" => Some(443),
        "http" => Some(80),
        _ => None,
    };
    if standard_port.is_none() || parsed.port_or_known_default() != standard_port {
        return Err(
            "Only http(s) URLs on the standard port can be opened in the system browser."
                .to_string(),
        );
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("URLs with embedded credentials cannot be opened.".to_string());
    }
    if !parsed
        .host_str()
        .is_some_and(|host| SYSTEM_BROWSER_ALLOWED_HOSTS.contains(&host))
    {
        return Err("This address is not in the system-browser allowlist.".to_string());
    }
    Ok(parsed)
}

#[tauri::command]
fn open_system_browser(window: WebviewWindow, url: String) -> Result<(), String> {
    if !is_main_window_label(window.label()) {
        return Err("The system-browser fallback is available only to KeplerV's main window."
            .to_string());
    }
    let parsed = parse_system_browser_url(&url)?;
    // No shell: the validated URL is passed as a single argv element to macOS `open`.
    Command::new("open")
        .arg(parsed.as_str())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Could not open the system browser: {error}"))
}

#[tauri::command]
fn open_public_resource(app: AppHandle, url: String, label: String) -> Result<(), String> {
    let parsed = Url::parse(&url).map_err(|_| "Enter a valid public URL.".to_string())?;
    if !is_allowed_resource(&parsed) {
        return Err(
            "Only allowlisted public HTTPS resources on the standard port are allowed.".to_string(),
        );
    }
    let safe_title = label.chars().take(70).collect::<String>();
    let window_label = format!(
        "resource-{}",
        RESOURCE_WINDOW_ID.fetch_add(1, Ordering::Relaxed)
    );
    WebviewWindowBuilder::new(&app, &window_label, WebviewUrl::External(parsed))
        .title(if safe_title.is_empty() {
            "KeplerV public resource"
        } else {
            &safe_title
        })
        .inner_size(1040.0, 760.0)
        .min_inner_size(480.0, 360.0)
        .on_navigation(is_allowed_resource)
        .build()
        .map(|_| ())
        .map_err(|error| format!("Could not open the native resource window: {error}"))
}

pub fn run() {
    tauri::Builder::default()
        .manage(runtime_bridge::RuntimeBridge::default())
        .invoke_handler(tauri::generate_handler![
            open_public_resource,
            open_system_browser,
            runtime_bridge::runtime_start,
            runtime_bridge::runtime_snapshot,
            runtime_bridge::runtime_mail_list,
            runtime_bridge::runtime_mail_get,
            runtime_bridge::runtime_info,
        runtime_bridge::transcribe_audio,
        runtime_bridge::interpret_voice,
        runtime_bridge::runtime_create_run,
            runtime_bridge::runtime_control,
            runtime_bridge::gateway_overview,
            runtime_bridge::gateway_transcript,
            runtime_bridge::gateway_verification,
            runtime_bridge::gateway_close_session,
            runtime_bridge::runtime_guard_investigate,
            runtime_bridge::runtime_guard_interview,
            runtime_bridge::runtime_guard_adjudicate,
            runtime_bridge::runtime_guard_containment,
            runtime_bridge::runtime_status,
            runtime_bridge::credential_status,
            runtime_bridge::credential_save,
            runtime_bridge::credential_delete,
            runtime_bridge::credential_chatgpt_connect,
            runtime_bridge::credential_chatgpt_disconnect,
            runtime_bridge::provider_connect,
            runtime_bridge::provider_configuration,
            runtime_bridge::provider_activate,
            runtime_bridge::oauth_connect,
            runtime_bridge::oauth_refresh,
            runtime_bridge::oauth_disconnect,
            runtime_bridge::oauth_client_ids,
            runtime_bridge::oauth_client_id_save,
            runtime_bridge::account_status
        ])
        .build(tauri::generate_context!())
        .expect("error while building KeplerV")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                if let Some(bridge) = app.try_state::<runtime_bridge::RuntimeBridge>() {
                    bridge.shutdown();
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_resource_policy_is_https_and_allowlisted() {
        let safe = Url::parse("https://www.nps.gov/subjects/climatechange/index.htm").unwrap();
        assert!(is_allowed_resource(&safe));

        let unsafe_host = Url::parse("https://example.com/").unwrap();
        assert!(!is_allowed_resource(&unsafe_host));
        let insecure = Url::parse("http://www.nps.gov/").unwrap();
        assert!(!is_allowed_resource(&insecure));
        let unusual_port = Url::parse("https://www.nps.gov:8443/").unwrap();
        assert!(!is_allowed_resource(&unusual_port));
        let credentials = Url::parse("https://user:secret@www.nps.gov/").unwrap();
        assert!(!is_allowed_resource(&credentials));
    }

    #[test]
    fn system_browser_policy_allows_only_allowlisted_http_hosts() {
        for raw in [
            "https://www.netflix.com/title/12345",
            "https://netflix.com/",
            "http://www.netflix.com/browse",
        ] {
            assert!(parse_system_browser_url(raw).is_ok(), "unexpected reject: {raw}");
        }
        // Host and scheme are normalized before the allowlist check.
        assert!(parse_system_browser_url("HTTPS://WWW.NETFLIX.COM/").is_ok());
        assert!(parse_system_browser_url("  https://www.netflix.com/  ").is_ok());
    }

    #[test]
    fn system_browser_policy_rejects_non_allowlisted_or_unsafe_urls() {
        for raw in [
            "https://example.com/",
            "https://netflix.com.evil.example/",
            "https://evil-netflix.com/",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/plain,hi",
            "https://www.netflix.com:8443/",
            "http://www.netflix.com:8080/",
            "https://user:secret@www.netflix.com/",
            "https://user@www.netflix.com/",
            "not a url",
            "",
        ] {
            assert!(parse_system_browser_url(raw).is_err(), "unexpected accept: {raw}");
        }
    }

    #[test]
    fn system_browser_shell_shaped_input_stays_a_single_url_argument() {
        // Command::new("open").arg(...) never spawns a shell, and validation
        // re-serializes from the parsed URL, so metacharacters cannot split argv.
        let parsed = parse_system_browser_url("https://www.netflix.com/title/123?x=`id`;echo")
            .expect("query metacharacters are legal URL content");
        assert_eq!(parsed.as_str(), "https://www.netflix.com/title/123?x=`id`;echo");
        assert!(parse_system_browser_url("https://www.netflix.com/\"; touch /tmp/pwned; echo \"").is_err());
    }

    #[test]
    fn system_browser_fallback_is_main_window_only() {
        assert!(is_main_window_label("main"));
        assert!(!is_main_window_label("resource-1"));
        assert!(!is_main_window_label("oauth-auth"));
        assert!(!is_main_window_label(""));
    }

    #[test]
    fn oauth_registry_covers_mail_providers_and_fake_fixture() {
        assert!(crate::oauth::is_supported_provider("gmail"));
        assert!(crate::oauth::is_supported_provider("outlook"));
        assert!(crate::oauth::is_supported_provider("fake-mail"));
        for provider in crate::oauth::SUPPORTED_PROVIDERS {
            let config = crate::oauth::provider_config(provider).expect("supported provider needs a descriptor");
            assert!(!config.scopes.is_empty());
            assert!(config.capabilities.contains(&"mail.read".to_string()));
        }
    }

    #[test]
    fn oauth_pkce_challenge_matches_rfc7636_vector() {
        assert_eq!(
            crate::oauth::pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn oauth_callback_state_mismatch_is_rejected() {
        let err = crate::oauth::parse_callback_path("/oauth/callback?code=abc&state=wrong", "expected").unwrap_err();
        assert!(err.contains("could not be verified"), "unexpected: {err}");
    }
}
