use std::sync::atomic::{AtomicU64, Ordering};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use url::Url;

mod runtime_bridge;
mod chatgpt_auth;

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
            runtime_bridge::runtime_start,
            runtime_bridge::runtime_snapshot,
            runtime_bridge::runtime_create_run,
            runtime_bridge::runtime_control,
            runtime_bridge::runtime_status,
            runtime_bridge::credential_status,
            runtime_bridge::credential_save,
            runtime_bridge::credential_delete,
            runtime_bridge::credential_chatgpt_connect,
            runtime_bridge::credential_chatgpt_disconnect,
            runtime_bridge::provider_connect,
            runtime_bridge::provider_configuration,
            runtime_bridge::provider_activate
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
}
