use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use jsonwebtoken::{decode, decode_header, DecodingKey, Validation, Algorithm};
use rand::{distributions::Alphanumeric, Rng};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use url::Url;

const ISSUER: &str = "https://auth.openai.com";
const AUTHORIZE: &str = "https://auth.openai.com/api/accounts/authorize";
const TOKEN: &str = "https://auth.openai.com/api/accounts/oauth/token";
const JWKS: &str = "https://auth.openai.com/.well-known/jwks.json";
const RESOURCE: &str = "https://api.openai.com/v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatGptProfile {
    pub client_id: String,
    pub host_id: String,
    pub id_token: String,
    pub access_token: String,
    pub refresh_token: String,
    pub email: Option<String>,
    pub subject: String,
    pub scopes: Vec<String>,
    pub expires_at: u64,
}

#[derive(Deserialize)]
struct TokenReply {
    access_token: String,
    refresh_token: Option<String>,
    id_token: Option<String>,
    #[serde(default = "default_token_lifetime")]
    expires_in: u64,
    scope: Option<String>,
}

fn default_token_lifetime() -> u64 { 3600 }

#[derive(Deserialize)]
struct Claims {
    iss: String,
    sub: String,
    aud: serde_json::Value,
    exp: u64,
    iat: u64,
    nonce: String,
    email: Option<String>,
}

fn random_value(length: usize) -> String {
    rand::thread_rng().sample_iter(&Alphanumeric).take(length).map(char::from).collect()
}

fn current_time() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

fn callback(listener: &TcpListener, expected_state: &str, expected_client: &str) -> Result<(String, String), String> {
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    let deadline = Instant::now() + Duration::from_secs(300);
    let (mut stream, _) = loop {
        match listener.accept() {
            Ok(pair) => break pair,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock && Instant::now() < deadline => std::thread::sleep(Duration::from_millis(100)),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => return Err("ChatGPT sign-in timed out. Please try again.".into()),
            Err(_) => return Err("Could not receive the ChatGPT sign-in response.".into()),
        }
    };
    stream.set_read_timeout(Some(Duration::from_secs(5))).map_err(|_| "Could not read the ChatGPT sign-in response.".to_string())?;
    let mut bytes = [0_u8; 8192];
    let size = stream.read(&mut bytes).map_err(|_| "Could not read the sign-in response.".to_string())?;
    let request = String::from_utf8_lossy(&bytes[..size]);
    let path = request.lines().next().and_then(|line| line.split_whitespace().nth(1)).ok_or("Invalid sign-in callback.")?;
    let callback_url = Url::parse(&format!("http://127.0.0.1{path}")).map_err(|_| "Invalid sign-in callback URL.")?;
    if callback_url.path() != "/auth/callback" {
        let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\nNot found.");
        return Err("The sign-in callback path did not match.".into());
    }
    let params: std::collections::HashMap<String, String> = callback_url.query_pairs().map(|(k,v)| (k.into_owned(),v.into_owned())).collect();
    if params.get("state").map(String::as_str) != Some(expected_state) {
        let _ = stream.write_all(b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n<!doctype html><meta charset=utf-8><title>KeplerV</title><p>This sign-in response could not be verified. Return to KeplerV and try again.</p>");
        return Err("ChatGPT sign-in could not be verified. Please try again.".into());
    }
    if let Some(error) = params.get("error") {
        let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n<!doctype html><meta charset=utf-8><title>KeplerV</title><p>Sign-in was not completed. Return to KeplerV to try again.</p>");
        return Err(if error == "access_denied" { "ChatGPT sign-in was cancelled.".into() } else { "ChatGPT could not authorize KeplerV. Please try again.".into() });
    }
    let Some(code) = params.get("code").cloned() else {
        let _ = stream.write_all(b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n<!doctype html><meta charset=utf-8><title>KeplerV</title><p>ChatGPT did not return a sign-in code. Return to KeplerV and try again.</p>");
        return Err("ChatGPT did not return an authorization code.".into());
    };
    let client_id = params.get("client_id").cloned().unwrap_or_else(|| expected_client.to_string());
    let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n<!doctype html><meta charset=utf-8><title>KeplerV</title><p>Sign-in response received. KeplerV is verifying your connection now. Return to KeplerV to see the result.</p>");
    Ok((code, client_id))
}

fn post_form<T: for<'de> Deserialize<'de>>(url: &str, fields: &[(&str, &str)]) -> Result<T, String> {
    let body = url::form_urlencoded::Serializer::new(String::new()).extend_pairs(fields.iter().copied()).finish();
    let response = reqwest::blocking::Client::builder().timeout(Duration::from_secs(30)).build()
        .map_err(|_| "Could not initialize ChatGPT sign-in.".to_string())?
        .post(url).header("Content-Type", "application/x-www-form-urlencoded").body(body)
        .send()
        .map_err(|_| "KeplerV could not reach ChatGPT to finish sign-in. Check your connection and try again.".to_string())?;
    let status = response.status();
    let payload: serde_json::Value = response.json()
        .map_err(|_| format!("ChatGPT returned an unreadable token response (HTTP {}).", status.as_u16()))?;
    if !status.is_success() {
        let code = payload.get("error").and_then(serde_json::Value::as_str)
            .filter(|value| !value.is_empty() && value.len() <= 80 && value.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.')))
            .unwrap_or("unknown_error");
        return Err(format!("ChatGPT sign-in reached token exchange, but it was rejected (HTTP {}, {}).", status.as_u16(), code));
    }
    serde_json::from_value(payload).map_err(|_| "ChatGPT accepted sign-in but returned incomplete token details. Please reconnect.".to_string())
}

fn verify_id_token(id_token: &str, client_id: &str, nonce: &str) -> Result<Claims, String> {
    let header = decode_header(id_token).map_err(|_| "ChatGPT returned an invalid identity token.".to_string())?;
    let kid = header.kid.ok_or("ChatGPT identity token has no signing key.")?;
    let client = reqwest::blocking::Client::builder().timeout(Duration::from_secs(15)).build().map_err(|_| "Could not initialize ChatGPT sign-in.".to_string())?;
    let jwks: serde_json::Value = client.get(JWKS).send().and_then(|r| r.error_for_status()).and_then(|r| r.json()).map_err(|_| "Could not validate ChatGPT identity right now. Please try again.".to_string())?;
    let keys = jwks.get("keys").and_then(serde_json::Value::as_array).ok_or("ChatGPT identity keys are unavailable.")?;
    let key = keys.iter().find(|key| key.get("kid").and_then(serde_json::Value::as_str) == Some(kid.as_str())).ok_or("ChatGPT identity signing key was not found.")?;
    let n = key.get("n").and_then(serde_json::Value::as_str).ok_or("ChatGPT identity key is incomplete.")?;
    let e = key.get("e").and_then(serde_json::Value::as_str).ok_or("ChatGPT identity key is incomplete.")?;
    let decoding = DecodingKey::from_rsa_components(n, e).map_err(|_| "Could not validate ChatGPT identity.".to_string())?;
    let mut validation = Validation::new(Algorithm::RS256);
    validation.set_issuer(&[ISSUER]);
    validation.set_audience(&[client_id]);
    validation.required_spec_claims.insert("iat".to_string());
    let token = decode::<Claims>(id_token, &decoding, &validation).map_err(|_| "ChatGPT identity could not be verified. Please try signing in again.".to_string())?;
    let claims = token.claims;
    if claims.iss != ISSUER || claims.sub.is_empty() || claims.exp <= current_time() || claims.iat > current_time().saturating_add(60) || claims.nonce != nonce {
        return Err("ChatGPT identity could not be verified. Please try signing in again.".into());
    }
    let audience_matches = match &claims.aud {
        serde_json::Value::String(aud) => aud == client_id,
        serde_json::Value::Array(auds) => auds.iter().any(|aud| aud.as_str() == Some(client_id)),
        _ => false,
    };
    if !audience_matches { return Err("ChatGPT identity was issued for a different connection.".into()); }
    Ok(claims)
}

fn connect_attempt(app: &AppHandle, data_dir: &Path, existing: Option<ChatGptProfile>, attempt: u8) -> Result<ChatGptProfile, String> {
    fs::create_dir_all(data_dir).map_err(|_| "Could not prepare secure ChatGPT settings.".to_string())?;
    let host_file = data_dir.join("chatgpt-host-id");
    let host_id = if host_file.exists() {
        fs::read_to_string(&host_file).map_err(|_| "Could not read ChatGPT connection settings.".to_string())?
    } else {
        let id = format!("urn:uuid:{}", uuid::Uuid::new_v4());
        fs::write(&host_file, &id).map_err(|_| "Could not save ChatGPT connection settings.".to_string())?;
        id
    };
    let state = random_value(48);
    let nonce = random_value(48);
    let verifier = random_value(64);
    let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|_| "Could not open a secure sign-in callback on this device.".to_string())?;
    let port = listener.local_addr().map_err(|_| "Could not prepare ChatGPT sign-in.".to_string())?.port();
    let redirect_uri = format!("http://127.0.0.1:{port}/auth/callback");
    let mut url = Url::parse(AUTHORIZE).map_err(|_| "Could not prepare ChatGPT sign-in.".to_string())?;
    let saved_client_id_path = data_dir.join("chatgpt-client-id");
    let retained_client_id = existing.as_ref().map(|profile| profile.client_id.clone()).or_else(|| {
        fs::read_to_string(&saved_client_id_path).ok().map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty() && value != "dynamic_agent_client")
    });
    let first_registration = retained_client_id.is_none();
    let client_id = retained_client_id.as_deref().unwrap_or("dynamic_agent_client");
    {
        let mut query = url.query_pairs_mut();
        query.append_pair("client_id", client_id)
            .append_pair("response_type", "code")
            .append_pair("redirect_uri", &redirect_uri)
            .append_pair("scope", "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct")
            .append_pair("resource", RESOURCE)
            .append_pair("state", &state)
            .append_pair("nonce", &nonce)
            .append_pair("code_challenge_method", "S256")
            .append_pair("code_challenge", &challenge)
            .append_pair("ext_agent_host_id", host_id.trim());
        if first_registration { query.append_pair("agent_name_hint", "KeplerV"); }
        if let Some(profile) = existing.as_ref() { query.append_pair("id_token_hint", &profile.id_token); }
    }
    // Keep authorization inside a KeplerV-owned webview. Remote webviews have no
    // Tauri IPC capability (see the default capability's main-window restriction).
    if let Some(previous) = app.get_webview_window("chatgpt-auth") {
        previous.navigate(url.clone()).map_err(|_| "KeplerV could not refresh its sign-in window.".to_string())?;
    } else {
        WebviewWindowBuilder::new(app, "chatgpt-auth", WebviewUrl::External(url.clone()))
            .title("Sign in with ChatGPT · KeplerV")
            .inner_size(520.0, 760.0)
            .min_inner_size(420.0, 560.0)
            .center()
            .build()
            .map_err(|_| "KeplerV could not open its sign-in window.".to_string())?;
    }
    let expected_client = client_id;
    let (code, issued_client_id) = callback(&listener, &state, expected_client)?;
    if first_registration && issued_client_id == "dynamic_agent_client" {
        return Err("OpenAI did not finish registering KeplerV. Please retry sign-in.".into());
    }
    if !first_registration && issued_client_id != client_id {
        return Err("ChatGPT returned a different account connection than the one selected.".into());
    }
    // Keep the issued registration ID if exchange fails, so the next attempt can
    // reauthorize this exact account registration instead of starting over.
    if first_registration {
        fs::write(&saved_client_id_path, &issued_client_id).map_err(|_| "KeplerV could not save the ChatGPT connection ID. Please retry sign-in.".to_string())?;
    }
    let response: TokenReply = match post_form(TOKEN, &[
            ("grant_type", "authorization_code"), ("client_id", issued_client_id.as_str()),
            ("code", code.as_str()), ("code_verifier", verifier.as_str()),
            ("redirect_uri", redirect_uri.as_str()), ("resource", RESOURCE),
        ]) {
        Ok(response) => response,
        Err(error) if attempt == 0 && error.contains("invalid_grant") => {
            // Authorization codes are short-lived and single-use. OpenAI's recovery
            // guidance is to discard an invalid grant and authorize again with the
            // issued client ID, which was saved above before exchanging the code.
            return connect_attempt(app, data_dir, existing, attempt + 1);
        }
        Err(error) if error.contains("invalid_grant") => {
            return Err("ChatGPT rejected the refreshed authorization code twice (invalid_grant). KeplerV kept the issued connection ID; check your clock and try again.".into());
        }
        Err(error) => return Err(error),
    };
    let id_token = response.id_token.ok_or("ChatGPT did not return a verified account identity.")?;
    let claims = verify_id_token(&id_token, &issued_client_id, &nonce)?;
    if existing.as_ref().is_some_and(|p| p.subject != claims.sub) {
        return Err("The ChatGPT account changed during reconnect. Choose Add account to connect it separately.".into());
    }
    let scopes: Vec<String> = response.scope.unwrap_or_default().split_whitespace().map(str::to_string).collect();
    let refresh_token = response.refresh_token.or_else(|| existing.as_ref().map(|p| p.refresh_token.clone())).ok_or("ChatGPT did not grant offline access. Please reconnect and allow requested access.")?;
    Ok(ChatGptProfile { client_id: issued_client_id, host_id: host_id.trim().to_string(), id_token, access_token: response.access_token, refresh_token, email: claims.email, subject: claims.sub, scopes, expires_at: current_time().saturating_add(response.expires_in) })
}

pub fn connect(app: &AppHandle, data_dir: &Path, existing: Option<ChatGptProfile>) -> Result<ChatGptProfile, String> {
    connect_attempt(app, data_dir, existing, 0)
}

pub fn refresh_if_needed(profile: ChatGptProfile) -> Result<ChatGptProfile, String> {
    if profile.expires_at > current_time().saturating_add(300) { return Ok(profile); }
    let body = url::form_urlencoded::Serializer::new(String::new()).extend_pairs([
        ("grant_type", "refresh_token"),
        ("client_id", profile.client_id.as_str()),
        ("refresh_token", profile.refresh_token.as_str()),
        ("resource", RESOURCE),
    ]).finish();
    let response: serde_json::Value = reqwest::blocking::Client::builder().timeout(Duration::from_secs(30)).build()
        .map_err(|_| "Could not refresh the ChatGPT connection.".to_string())?
        .post(TOKEN).header("Content-Type", "application/x-www-form-urlencoded").body(body)
        .send().and_then(|r| r.error_for_status()).and_then(|r| r.json())
        .map_err(|_| "ChatGPT needs you to reconnect this account.".to_string())?;
    let mut updated = profile;
    updated.access_token = response.get("access_token").and_then(serde_json::Value::as_str).ok_or("ChatGPT returned an invalid refreshed connection.")?.to_string();
    if let Some(token) = response.get("refresh_token").and_then(serde_json::Value::as_str) { updated.refresh_token = token.to_string(); }
    if let Some(scope) = response.get("scope").and_then(serde_json::Value::as_str) { updated.scopes = scope.split_whitespace().map(str::to_string).collect(); }
    let expires_in = response.get("expires_in").and_then(serde_json::Value::as_u64).unwrap_or(3600);
    updated.expires_at = current_time().saturating_add(expires_in);
    Ok(updated)
}

pub fn revoke(profile: &ChatGptProfile) -> bool {
    let Ok(client) = reqwest::blocking::Client::builder().timeout(Duration::from_secs(8)).build() else { return false; };
    let Ok(discovery) = client.get("https://auth.openai.com/.well-known/openid-configuration").send().and_then(|r| r.error_for_status()).and_then(|r| r.json::<serde_json::Value>()) else { return false; };
    let Some(endpoint) = discovery.get("revocation_endpoint").and_then(serde_json::Value::as_str) else { return false; };
    let body = url::form_urlencoded::Serializer::new(String::new()).extend_pairs([
        ("token", profile.refresh_token.as_str()),
        ("token_type_hint", "refresh_token"),
        ("client_id", profile.client_id.as_str()),
    ]).finish();
    client.post(endpoint).header("Content-Type", "application/x-www-form-urlencoded").body(body).send().is_ok_and(|response| response.status().is_success())
}
