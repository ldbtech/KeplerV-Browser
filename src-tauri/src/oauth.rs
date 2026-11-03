//! Generic OAuth 2.0 Authorization Code + PKCE support for connected services.
//!
//! This module is provider-agnostic: endpoints and scopes come from
//! [`provider_config`], and only a public `client_id` is ever configured — no
//! client secrets are committed or accepted. Tokens are returned to the caller
//! in [`OAuthTokenSet`] so [`crate::runtime_bridge`] can persist them in the
//! system keychain under `oauth:{provider}:{account}` entries, separate from
//! the model `MODEL_API_KEY` credential path.
//!
//! Security notes:
//! - The loopback callback listener binds `127.0.0.1` only.
//! - `state` is generated per attempt and validated before any code exchange.
//! - Error strings never interpolate tokens, codes, or verifiers.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use rand::{distributions::Alphanumeric, Rng};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use url::Url;

/// Providers with an OAuth descriptor. `fake-mail` is a clearly-labeled local
/// test fixture: it performs no network I/O and mints `FAKE-` tokens only.
pub const SUPPORTED_PROVIDERS: &[&str] = &["gmail", "outlook", "fake-mail"];

pub const CALLBACK_PATH: &str = "/oauth/callback";
pub const CALLBACK_TIMEOUT_SECS: u64 = 300;

/// Non-secret provider descriptor. Mirrors `runtime/services/registry.py`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OAuthProviderConfig {
    pub provider: String,
    pub label: String,
    pub authorize_url: String,
    pub token_url: String,
    pub revoke_url: Option<String>,
    pub userinfo_url: Option<String>,
    pub scopes: Vec<String>,
    /// Capabilities this provider unlocks. Declared here so policy layers can
    /// check them without touching tokens.
    pub capabilities: Vec<String>,
}

/// Secret token set. Persist in the keychain; never log or send to the model.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OAuthTokenSet {
    pub provider: String,
    pub account_email: String,
    pub access_token: String,
    pub refresh_token: Option<String>,
    pub scopes: Vec<String>,
    pub expires_at: u64,
}

/// Non-secret account metadata. Safe to surface in UI and runtime metadata.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConnectedAccountMeta {
    pub provider: String,
    pub email: String,
    pub scopes: Vec<String>,
    pub expires_at: u64,
    pub status: String,
}

#[derive(Debug, Clone)]
pub struct Pkce {
    pub verifier: String,
    pub challenge: String,
    pub state: String,
    pub nonce: String,
}

#[derive(Deserialize)]
struct TokenReply {
    access_token: String,
    refresh_token: Option<String>,
    #[serde(default = "default_token_lifetime")]
    expires_in: u64,
    scope: Option<String>,
}

fn default_token_lifetime() -> u64 {
    3600
}

pub fn provider_config(provider: &str) -> Option<OAuthProviderConfig> {
    match provider {
        "gmail" => Some(OAuthProviderConfig {
            provider: "gmail".into(),
            label: "Gmail (read-only)".into(),
            authorize_url: "https://accounts.google.com/o/oauth2/v2/auth".into(),
            token_url: "https://oauth2.googleapis.com/token".into(),
            revoke_url: Some("https://oauth2.googleapis.com/revoke".into()),
            userinfo_url: Some("https://openidconnect.googleapis.com/v1/userinfo".into()),
            scopes: vec!["openid".into(), "email".into(), "https://www.googleapis.com/auth/gmail.readonly".into()],
            capabilities: vec!["mail.read".into(), "mail.search".into(), "mail.get".into()],
        }),
        "outlook" => Some(OAuthProviderConfig {
            provider: "outlook".into(),
            label: "Outlook (read-only)".into(),
            authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize".into(),
            token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token".into(),
            revoke_url: None,
            userinfo_url: Some("https://graph.microsoft.com/oidc/userinfo".into()),
            scopes: vec!["openid".into(), "email".into(), "Mail.Read".into()],
            capabilities: vec!["mail.read".into(), "mail.search".into(), "mail.get".into()],
        }),
        "fake-mail" => Some(OAuthProviderConfig {
            provider: "fake-mail".into(),
            label: "Fake Mail (clearly-labeled test fixture — no live mail)".into(),
            authorize_url: "http://127.0.0.1/fake-authorize".into(),
            token_url: "http://127.0.0.1/fake-token".into(),
            revoke_url: None,
            userinfo_url: None,
            scopes: vec!["fake-mail.read".into()],
            capabilities: vec!["mail.read".into(), "mail.search".into(), "mail.get".into()],
        }),
        _ => None,
    }
}

pub fn is_supported_provider(provider: &str) -> bool {
    SUPPORTED_PROVIDERS.contains(&provider)
}

/// Filesystem/keychain-safe account slug for multi-account support.
pub fn sanitize_account_id(email: &str) -> Result<String, String> {
    let trimmed = email.trim().to_lowercase();
    if trimmed.is_empty() || trimmed.len() > 254 {
        return Err("Enter a valid account email.".into());
    }
    if !trimmed.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '@' | '.' | '_' | '-' | '+')) {
        return Err("Account email contains unsupported characters.".into());
    }
    if !trimmed.contains('@') {
        return Err("Enter a valid account email.".into());
    }
    Ok(trimmed)
}

pub fn current_time() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

/// S256 PKCE challenge for a verifier.
pub fn pkce_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

fn random_value(length: usize) -> String {
    rand::thread_rng().sample_iter(&Alphanumeric).take(length).map(char::from).collect()
}

pub fn new_pkce() -> Pkce {
    let verifier = random_value(64);
    let challenge = pkce_challenge(&verifier);
    Pkce { verifier, challenge, state: random_value(48), nonce: random_value(48) }
}

pub fn build_authorize_url(
    config: &OAuthProviderConfig,
    pkce: &Pkce,
    client_id: &str,
    redirect_uri: &str,
) -> Result<Url, String> {
    let mut url = Url::parse(&config.authorize_url).map_err(|_| "Provider authorization endpoint is invalid.".to_string())?;
    {
        let mut query = url.query_pairs_mut();
        query
            .append_pair("client_id", client_id)
            .append_pair("response_type", "code")
            .append_pair("redirect_uri", redirect_uri)
            .append_pair("scope", &config.scopes.join(" "))
            .append_pair("state", &pkce.state)
            .append_pair("nonce", &pkce.nonce)
            .append_pair("code_challenge_method", "S256")
            .append_pair("code_challenge", &pkce.challenge);
    }
    Ok(url)
}

/// Validate a loopback callback request path. Returns the authorization code.
/// Pure function so state validation is unit-testable without sockets.
pub fn parse_callback_path(path_and_query: &str, expected_state: &str) -> Result<String, String> {
    let callback_url = Url::parse(&format!("http://127.0.0.1{path_and_query}"))
        .map_err(|_| "Invalid sign-in callback URL.".to_string())?;
    if callback_url.path() != CALLBACK_PATH {
        return Err("The sign-in callback path did not match.".into());
    }
    let params: HashMap<String, String> = callback_url
        .query_pairs()
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();
    if params.get("state").map(String::as_str) != Some(expected_state) {
        return Err("This sign-in response could not be verified. Please try again.".into());
    }
    if let Some(error) = params.get("error") {
        return Err(if error == "access_denied" {
            "Sign-in was cancelled.".into()
        } else {
            "The provider could not authorize KeplerV. Please try again.".into()
        });
    }
    params.get("code").cloned().filter(|code| !code.is_empty()).ok_or_else(|| "The provider did not return an authorization code.".to_string())
}

fn wait_for_callback(listener: &TcpListener, expected_state: &str) -> Result<String, String> {
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    let deadline = Instant::now() + Duration::from_secs(CALLBACK_TIMEOUT_SECS);
    let (mut stream, _) = loop {
        match listener.accept() {
            Ok(pair) => break pair,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock && Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(100))
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                return Err("Sign-in timed out. Please try again.".into())
            }
            Err(_) => return Err("Could not receive the sign-in response.".into()),
        }
    };
    stream.set_read_timeout(Some(Duration::from_secs(5))).map_err(|_| "Could not read the sign-in response.".to_string())?;
    let mut bytes = [0_u8; 8192];
    let size = stream.read(&mut bytes).map_err(|_| "Could not read the sign-in response.".to_string())?;
    let request = String::from_utf8_lossy(&bytes[..size]);
    let path = request.lines().next().and_then(|line| line.split_whitespace().nth(1)).unwrap_or("/");
    match parse_callback_path(path, expected_state) {
        Ok(code) => {
            let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n<!doctype html><meta charset=utf-8><title>KeplerV</title><p>Sign-in response received. KeplerV is verifying your connection now. Return to KeplerV to see the result.</p>");
            Ok(code)
        }
        Err(error) => {
            let _ = stream.write_all(b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n<!doctype html><meta charset=utf-8><title>KeplerV</title><p>This sign-in response could not be verified. Return to KeplerV and try again.</p>");
            Err(error)
        }
    }
}

fn post_form<T: for<'de> Deserialize<'de>>(url: &str, fields: &[(&str, &str)], label: &str) -> Result<T, String> {
    let body = url::form_urlencoded::Serializer::new(String::new()).extend_pairs(fields.iter().copied()).finish();
    let response = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|_| format!("Could not initialize {label} sign-in."))?
        .post(url)
        .header("Content-Type", "application/x-www-form-urlencoded")
        .body(body)
        .send()
        .map_err(|_| format!("KeplerV could not reach {label} to finish sign-in. Check your connection and try again."))?;
    let status = response.status();
    let payload: serde_json::Value = response
        .json()
        .map_err(|_| format!("{label} returned an unreadable token response (HTTP {}).", status.as_u16()))?;
    if !status.is_success() {
        let code = payload
            .get("error")
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.is_empty() && value.len() <= 80 && value.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.')))
            .unwrap_or("unknown_error");
        return Err(format!("{label} sign-in reached token exchange, but it was rejected (HTTP {}, {}).", status.as_u16(), code));
    }
    serde_json::from_value(payload).map_err(|_| format!("{label} accepted sign-in but returned incomplete token details. Please reconnect."))
}

/// Public-client client_id lookup. Reads `KEPLERV_OAUTH_CLIENT_ID_{PROVIDER}`
/// (dashes become underscores) or `oauth-client-ids.json` in the app data dir.
/// Only a public client_id is supported — never a client secret.
pub fn resolve_client_id(provider: &str, data_dir: &std::path::Path) -> Result<String, String> {
    let env_name = format!("KEPLERV_OAUTH_CLIENT_ID_{}", provider.replace('-', "_").to_uppercase());
    if let Ok(value) = std::env::var(&env_name) {
        let trimmed = value.trim().to_string();
        if !trimmed.is_empty() {
            return Ok(trimmed);
        }
    }
    let path = data_dir.join("oauth-client-ids.json");
    if let Ok(raw) = std::fs::read(&path) {
        if let Ok(map) = serde_json::from_slice::<HashMap<String, String>>(&raw) {
            if let Some(value) = map.get(provider).map(|v| v.trim().to_string()).filter(|v| !v.is_empty()) {
                return Ok(value);
            }
        }
    }
    Err(format!(
        "No OAuth client ID is configured for {provider}. Set {env_name} or add it to oauth-client-ids.json in the KeplerV data directory. No client secrets are used."
    ))
}

fn fetch_userinfo_email(config: &OAuthProviderConfig, access_token: &str) -> Option<String> {
    let url = config.userinfo_url.as_deref()?;
    let client = reqwest::blocking::Client::builder().timeout(Duration::from_secs(15)).build().ok()?;
    let payload: serde_json::Value = client.get(url).bearer_auth(access_token).send().ok()?.error_for_status().ok()?.json().ok()?;
    payload.get("email").and_then(serde_json::Value::as_str).map(str::to_string)
}

/// Run the full Authorization Code + PKCE flow for a real provider.
/// Opens a KeplerV-owned webview and waits on a 127.0.0.1 loopback callback.
pub fn connect_account(
    app: &AppHandle,
    data_dir: &std::path::Path,
    provider: &str,
    email_hint: Option<&str>,
) -> Result<OAuthTokenSet, String> {
    let config = provider_config(provider).ok_or_else(|| "Unknown service provider.".to_string())?;
    if provider == "fake-mail" {
        return Err("Fake Mail connects locally without a browser sign-in.".into());
    }
    let client_id = resolve_client_id(provider, data_dir)?;
    let pkce = new_pkce();
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|_| "Could not open a secure sign-in callback on this device.".to_string())?;
    let port = listener.local_addr().map_err(|_| "Could not prepare sign-in.".to_string())?.port();
    let redirect_uri = format!("http://127.0.0.1:{port}{CALLBACK_PATH}");
    let url = build_authorize_url(&config, &pkce, &client_id, &redirect_uri)?;
    if let Some(previous) = app.get_webview_window("oauth-auth") {
        previous.navigate(url.clone()).map_err(|_| "KeplerV could not refresh its sign-in window.".to_string())?;
    } else {
        WebviewWindowBuilder::new(app, "oauth-auth", WebviewUrl::External(url))
            .title(format!("Connect {} · KeplerV", config.label))
            .inner_size(520.0, 760.0)
            .min_inner_size(420.0, 560.0)
            .center()
            .build()
            .map_err(|_| "KeplerV could not open its sign-in window.".to_string())?;
    }
    let code = wait_for_callback(&listener, &pkce.state)?;
    let reply: TokenReply = post_form(
        &config.token_url,
        &[
            ("grant_type", "authorization_code"),
            ("client_id", client_id.as_str()),
            ("code", code.as_str()),
            ("code_verifier", pkce.verifier.as_str()),
            ("redirect_uri", redirect_uri.as_str()),
        ],
        &config.label,
    )?;
    let email = fetch_userinfo_email(&config, &reply.access_token)
        .or_else(|| email_hint.map(str::to_string))
        .unwrap_or_else(|| "connected-account".to_string());
    let scopes = reply.scope.map(|s| s.split_whitespace().map(str::to_string).collect()).unwrap_or_else(|| config.scopes.clone());
    Ok(OAuthTokenSet {
        provider: provider.to_string(),
        account_email: email,
        access_token: reply.access_token,
        refresh_token: reply.refresh_token,
        scopes,
        expires_at: current_time().saturating_add(reply.expires_in),
    })
}

/// Clearly-labeled fake account for tests and demos. No network, no secrets.
pub fn connect_fake_account(email: &str) -> Result<OAuthTokenSet, String> {
    let account = sanitize_account_id(email)?;
    Ok(OAuthTokenSet {
        provider: "fake-mail".into(),
        account_email: account,
        access_token: format!("FAKE-ACCESS-{}", random_value(24)),
        refresh_token: Some(format!("FAKE-REFRESH-{}", random_value(24))),
        scopes: vec!["fake-mail.read".into()],
        expires_at: current_time().saturating_add(3600),
    })
}

pub fn refresh_token_set(token_set: &OAuthTokenSet, data_dir: &std::path::Path) -> Result<OAuthTokenSet, String> {
    if token_set.provider == "fake-mail" {
        let mut updated = token_set.clone();
        updated.expires_at = current_time().saturating_add(3600);
        return Ok(updated);
    }
    let config = provider_config(&token_set.provider).ok_or_else(|| "Unknown service provider.".to_string())?;
    let refresh_token = token_set.refresh_token.as_deref().filter(|t| !t.is_empty()).ok_or_else(|| "This connection has no refresh token. Reconnect the account.".to_string())?;
    let client_id = resolve_client_id(&token_set.provider, data_dir)?;
    let reply: TokenReply = post_form(
        &config.token_url,
        &[("grant_type", "refresh_token"), ("client_id", client_id.as_str()), ("refresh_token", refresh_token)],
        &config.label,
    )?;
    let mut updated = token_set.clone();
    updated.access_token = reply.access_token;
    if let Some(next) = reply.refresh_token {
        updated.refresh_token = Some(next);
    }
    if let Some(scope) = reply.scope {
        updated.scopes = scope.split_whitespace().map(str::to_string).collect();
    }
    updated.expires_at = current_time().saturating_add(reply.expires_in);
    Ok(updated)
}

/// Best-effort remote revocation. Returns true when the provider confirmed;
/// local credential deletion must happen regardless.
pub fn revoke_token_set(token_set: &OAuthTokenSet) -> bool {
    if token_set.provider == "fake-mail" {
        return true;
    }
    let Some(config) = provider_config(&token_set.provider) else { return false };
    let Some(endpoint) = config.revoke_url.as_deref() else { return false };
    let token = token_set.refresh_token.as_deref().filter(|t| !t.is_empty()).unwrap_or(token_set.access_token.as_str());
    let Ok(client) = reqwest::blocking::Client::builder().timeout(Duration::from_secs(8)).build() else { return false };
    let body = url::form_urlencoded::Serializer::new(String::new()).extend_pairs([("token", token)]).finish();
    client.post(endpoint).header("Content-Type", "application/x-www-form-urlencoded").body(body).send().is_ok_and(|response| response.status().is_success())
}

pub fn metadata_for(token_set: &OAuthTokenSet, status: &str) -> ConnectedAccountMeta {
    ConnectedAccountMeta {
        provider: token_set.provider.clone(),
        email: token_set.account_email.clone(),
        scopes: token_set.scopes.clone(),
        expires_at: token_set.expires_at,
        status: status.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pkce_challenge_matches_rfc7636_vector() {
        // RFC 7636 Appendix B test vector.
        assert_eq!(pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
    }

    #[test]
    fn authorize_url_carries_pkce_and_state() {
        let config = provider_config("gmail").unwrap();
        let pkce = Pkce { verifier: "verifier".into(), challenge: "challenge".into(), state: "state123".into(), nonce: "nonce123".into() };
        let url = build_authorize_url(&config, &pkce, "client-id", "http://127.0.0.1:9/oauth/callback").unwrap();
        let params: HashMap<String, String> = url.query_pairs().map(|(k, v)| (k.into_owned(), v.into_owned())).collect();
        assert_eq!(params.get("code_challenge").map(String::as_str), Some("challenge"));
        assert_eq!(params.get("code_challenge_method").map(String::as_str), Some("S256"));
        assert_eq!(params.get("state").map(String::as_str), Some("state123"));
        assert_eq!(params.get("response_type").map(String::as_str), Some("code"));
        assert!(params.get("scope").is_some_and(|s| s.contains("gmail.readonly")));
    }

    #[test]
    fn callback_state_mismatch_is_rejected() {
        let err = parse_callback_path("/oauth/callback?code=abc&state=wrong", "expected").unwrap_err();
        assert!(err.contains("could not be verified"), "unexpected: {err}");
    }

    #[test]
    fn callback_wrong_path_is_rejected() {
        assert!(parse_callback_path("/other?code=abc&state=s", "s").is_err());
    }

    #[test]
    fn callback_cancellation_is_reported() {
        let err = parse_callback_path("/oauth/callback?error=access_denied&state=s", "s").unwrap_err();
        assert!(err.contains("cancelled"), "unexpected: {err}");
    }

    #[test]
    fn callback_valid_code_is_accepted() {
        assert_eq!(parse_callback_path("/oauth/callback?code=the-code&state=s", "s").unwrap(), "the-code");
    }

    #[test]
    fn fake_account_is_clearly_labeled() {
        let set = connect_fake_account("Demo.Person@Example.Test").unwrap();
        assert_eq!(set.provider, "fake-mail");
        assert_eq!(set.account_email, "demo.person@example.test");
        assert!(set.access_token.starts_with("FAKE-"));
        assert!(set.refresh_token.as_deref().is_some_and(|t| t.starts_with("FAKE-")));
        assert!(set.expires_at > current_time());
    }

    #[test]
    fn unsupported_provider_has_no_config() {
        assert!(provider_config("example-mail").is_none());
        assert!(!is_supported_provider("example-mail"));
    }

    #[test]
    fn bad_account_ids_are_rejected() {
        assert!(sanitize_account_id("").is_err());
        assert!(sanitize_account_id("no-at-sign").is_err());
        assert!(sanitize_account_id("a/b@c.com").is_err());
        assert_eq!(sanitize_account_id("Ada@Example.Test").unwrap(), "ada@example.test");
    }
}
