use reqwest::blocking::Client;
use serde_json::{json, Value};
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::io::Read;
use std::io;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager, State, WebviewWindow};

struct ManagedRuntime {
    child: Child,
    base_url: String,
    token: String,
    poll_stop: Arc<AtomicBool>,
    poller: Option<JoinHandle<()>>,
    model_configured: bool,
}

const KEYRING_SERVICE: &str = "com.keplerv.browser";

#[derive(Clone, Serialize, Deserialize)]
struct ModelConfig { provider: String, endpoint: String, model: String }

fn read_model_config(app: &AppHandle) -> Option<ModelConfig> {
    let path = app.path().app_local_data_dir().ok()?.join("provider-config.json");
    serde_json::from_slice(&fs::read(path).ok()?).ok()
}

fn write_model_config(app: &AppHandle, config: &ModelConfig) -> Result<(), String> {
    let dir = app.path().app_local_data_dir().map_err(|_| "Could not access KeplerV settings.".to_string())?;
    fs::create_dir_all(&dir).map_err(|_| "Could not prepare provider settings.".to_string())?;
    let temp = dir.join("provider-config.json.tmp");
    fs::write(&temp, serde_json::to_vec(config).map_err(|_| "Could not save provider settings.".to_string())?).map_err(|_| "Could not save provider settings.".to_string())?;
    fs::rename(temp, dir.join("provider-config.json")).map_err(|_| "Could not save provider settings.".to_string())
}

fn write_provider_config(app: &AppHandle, config: &ModelConfig) -> Result<(), String> {
    let dir = app.path().app_local_data_dir().map_err(|_| "Could not access KeplerV settings.".to_string())?.join("providers");
    fs::create_dir_all(&dir).map_err(|_| "Could not prepare provider settings.".to_string())?;
    let path = dir.join(format!("{}.json", config.provider));
    let temp = dir.join(format!("{}.json.tmp", config.provider));
    fs::write(&temp, serde_json::to_vec(config).map_err(|_| "Could not save provider settings.".to_string())?).map_err(|_| "Could not save provider settings.".to_string())?;
    fs::rename(temp, path).map_err(|_| "Could not save provider settings.".to_string())
}

fn read_provider_config(app: &AppHandle, provider: &str) -> Option<ModelConfig> {
    let dir = app.path().app_local_data_dir().ok()?.join("providers");
    serde_json::from_slice(&fs::read(dir.join(format!("{provider}.json"))).ok()?).ok()
}

fn model_id(item: &Value) -> Option<String> {
    item.get("id").or_else(|| item.get("slug")).and_then(Value::as_str)
        .or_else(|| item.get("name").and_then(Value::as_str).map(|name| name.rsplit('/').next().unwrap_or(name)))
        .map(str::to_string)
}

fn keyring_entry(provider: &str) -> Result<keyring::Entry, String> {
    if !matches!(provider, "meta" | "openai" | "anthropic" | "google" | "deepseek" | "kimi" | "openrouter" | "groq" | "mistral" | "chatgpt" | "openai-compatible" | "local-ollama" | "local-lmstudio" | "local-openai-compatible") {
        return Err("That provider cannot store an API credential yet.".into());
    }
    keyring::Entry::new(KEYRING_SERVICE, provider).map_err(|_| "Could not access the system credential store.".into())
}

/// Keychain entry for a connected service OAuth account. The `oauth:` prefix
/// keeps service tokens in a namespace separate from model API credentials
/// (including the keychain `MODEL_API_KEY`), while sharing the app service.
fn oauth_keyring_entry(provider: &str, account: &str) -> Result<keyring::Entry, String> {
    if !crate::oauth::is_supported_provider(provider) {
        return Err("Unknown service provider.".into());
    }
    let account = crate::oauth::sanitize_account_id(account)?;
    let user = format!("oauth:{provider}:{account}");
    keyring::Entry::new(KEYRING_SERVICE, &user)
        .map_err(|_| "Could not access the system credential store.".into())
}

/// Non-secret metadata directory for connected service accounts. Tokens live
/// only in the keychain; this directory holds status/scopes/expiry metadata.
fn oauth_metadata_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_local_data_dir().map_err(|_| "Could not access KeplerV settings.".to_string())?.join("oauth-accounts");
    fs::create_dir_all(&dir).map_err(|_| "Could not prepare service account settings.".to_string())?;
    Ok(dir)
}

fn oauth_metadata_path(app: &AppHandle, provider: &str, account: &str) -> Result<PathBuf, String> {
    let account = crate::oauth::sanitize_account_id(account)?;
    let safe: String = account.chars().map(|c| if c == '@' { "_at_".to_string() } else { c.to_string() }).collect();
    Ok(oauth_metadata_dir(app)?.join(format!("{provider}__{safe}.json")))
}

fn write_oauth_metadata(app: &AppHandle, meta: &crate::oauth::ConnectedAccountMeta) -> Result<(), String> {
    let path = oauth_metadata_path(app, &meta.provider, &meta.email)?;
    let temp = path.with_extension("json.tmp");
    fs::write(&temp, serde_json::to_vec(meta).map_err(|_| "Could not save the service account.".to_string())?)
        .map_err(|_| "Could not save the service account.".to_string())?;
    fs::rename(temp, path).map_err(|_| "Could not save the service account.".to_string())
}

fn read_oauth_accounts(app: &AppHandle) -> Vec<crate::oauth::ConnectedAccountMeta> {
    let Ok(dir) = oauth_metadata_dir(app) else { return Vec::new() };
    let Ok(entries) = fs::read_dir(dir) else { return Vec::new() };
    let mut accounts = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|ext| ext.to_str()) != Some("json") {
            continue;
        }
        let Ok(raw) = fs::read(&path) else { continue };
        let Ok(mut meta) = serde_json::from_slice::<crate::oauth::ConnectedAccountMeta>(&raw) else { continue };
        // Derive liveness from expiry; stored "revoked" always wins.
        if meta.status != "revoked" && meta.expires_at <= crate::oauth::current_time() {
            meta.status = "expired".to_string();
        }
        accounts.push(meta);
    }
    accounts.sort_by(|a, b| (&a.provider, &a.email).cmp(&(&b.provider, &b.email)));
    accounts
}

fn saved_secret(provider: &str) -> Option<String> {
    keyring_entry(provider).ok()?.get_password().ok()
}

fn model_key(provider: &str) -> Option<String> {
    if provider == "meta" {
        env::var("MODEL_API_KEY").ok().filter(|key| !key.is_empty())
            .or_else(|| saved_secret(provider).filter(|key| !key.is_empty()))
    } else {
        saved_secret(provider).filter(|key| !key.is_empty())
    }
}

fn chatgpt_profile() -> Option<crate::chatgpt_auth::ChatGptProfile> {
    let raw = keyring_entry("chatgpt").ok()?.get_password().ok()?;
    serde_json::from_str(&raw).ok()
}

fn stop_runtime(slot: &mut Option<ManagedRuntime>) {
    if let Some(mut old) = slot.take() {
        old.poll_stop.store(true, Ordering::SeqCst);
        let _ = old.child.kill();
        if let Some(poller) = old.poller.take() { let _ = poller.join(); }
        let _ = old.child.wait();
    }
}

#[derive(Default)]
pub struct RuntimeBridge {
    runtime: Mutex<Option<ManagedRuntime>>,
}

impl RuntimeBridge {
    pub fn shutdown(&self) {
        let Ok(mut slot) = self.runtime.lock() else {
            return;
        };
        if let Some(mut runtime) = slot.take() {
            runtime.poll_stop.store(true, Ordering::SeqCst);
            let _ = runtime.child.kill();
            if let Some(poller) = runtime.poller.take() {
                let _ = poller.join();
            }
            let _ = runtime.child.wait();
        }
    }
}

impl Drop for RuntimeBridge {
    fn drop(&mut self) {
        if let Ok(slot) = self.runtime.get_mut() {
            if let Some(mut runtime) = slot.take() {
                runtime.poll_stop.store(true, Ordering::SeqCst);
                let _ = runtime.child.kill();
                if let Some(poller) = runtime.poller.take() {
                    let _ = poller.join();
                }
                let _ = runtime.child.wait();
            }
        }
    }
}

fn ensure_main_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("The local agent runtime is available only to KeplerV's main window.".into());
    }
    Ok(())
}

fn application_root(app: &AppHandle) -> Result<PathBuf, String> {
    let cwd = env::current_dir().map_err(|e| e.to_string())?;
    if cwd.join("runtime").join("main.py").is_file() {
        return Ok(cwd);
    }
    let resources = app.path().resource_dir().map_err(|e| e.to_string())?;
    if resources.join("runtime").join("main.py").is_file() {
        return Ok(resources);
    }
    Err("KeplerV's local runtime files were not found. Reinstall the application or run it from the project folder.".into())
}

fn python_executable(root: &Path) -> String {
    if let Ok(path) = env::var("KEPLERV_PYTHON") {
        return path;
    }
    // In development, Tauri resolves bundled runtime files from target/debug,
    // while the project's virtual environment lives several directories above.
    let mut directory = Some(root);
    while let Some(current) = directory {
        for candidate in [current.join(".venv/bin/python"), current.join(".venv/bin/python3")] {
            if candidate.is_file() {
                return candidate.to_string_lossy().into_owned();
            }
        }
        directory = current.parent();
    }
    let mac_python = "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12";
    if Path::new(mac_python).is_file() {
        return mac_python.into();
    }
    "python3".into()
}

fn random_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    fs::File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut bytes))
        .map_err(|e| format!("Could not create runtime authentication token: {e}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn available_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .map_err(|e| format!("Could not reserve a local runtime port: {e}"))
}

fn authorized_client() -> Result<Client, String> {
    Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| format!("Could not initialize the local runtime connection: {e}"))
}

fn authenticated_get(
    client: &Client,
    base_url: &str,
    token: &str,
    path: &str,
) -> Result<Value, String> {
    let response = client
        .get(format!("{base_url}{path}"))
        .bearer_auth(token)
        .send()
        .map_err(|_| "Could not reach KeplerV's local runtime.".to_string())?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!(
            "KeplerV runtime returned HTTP {}.",
            status.as_u16()
        ));
    }
    response
        .json()
        .map_err(|_| "KeplerV runtime returned an unreadable response.".into())
}

fn authenticated_post_bytes(
    client: &Client,
    base_url: &str,
    token: &str,
    path: &str,
    content_type: &str,
    body: Vec<u8>,
) -> Result<Value, String> {
    let response = client
        .post(format!("{base_url}{path}"))
        .bearer_auth(token)
        .header("Content-Type", content_type)
        .body(body)
        .send()
        .map_err(|_| "Could not reach KeplerV's local runtime.".to_string())?;
    let status = response.status();
    let payload: Value = response.json().unwrap_or_else(|_| json!({}));
    if !status.is_success() {
        let detail = payload
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or("Runtime request failed.");
        return Err(format!("{}", detail.chars().take(500).collect::<String>()));
    }
    Ok(payload)
}

fn authenticated_post(
    client: &Client,
    base_url: &str,
    token: &str,
    path: &str,
    body: Value,
) -> Result<Value, String> {
    let response = client
        .post(format!("{base_url}{path}"))
        .bearer_auth(token)
        .json(&body)
        .send()
        .map_err(|_| "Could not reach KeplerV's local runtime.".to_string())?;
    let status = response.status();
    let payload: Value = response.json().unwrap_or_else(|_| json!({}));
    if !status.is_success() {
        let detail = payload
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or("Runtime request failed.");
        return Err(format!("{}", detail.chars().take(500).collect::<String>()));
    }
    Ok(payload)
}

fn spawn_event_poller(
    app: AppHandle,
    base_url: String,
    token: String,
    stop: Arc<AtomicBool>,
    client: Client,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut sequence = authenticated_get(&client, &base_url, &token, "/snapshot?after=0")
            .ok()
            .and_then(|snapshot| snapshot.get("last_sequence").and_then(Value::as_u64))
            .unwrap_or(0);
        while !stop.load(Ordering::SeqCst) {
            if let Ok(snapshot) = authenticated_get(
                &client,
                &base_url,
                &token,
                &format!("/snapshot?after={sequence}"),
            ) {
                if let Some(events) = snapshot.get("events").and_then(Value::as_array) {
                    for event in events {
                        if let Some(next) = event.get("sequence").and_then(Value::as_u64) {
                            sequence = sequence.max(next);
                        }
                        let _ = app.emit_to("main", "runtime:event", event.clone());
                    }
                }
            }
            thread::sleep(Duration::from_millis(500));
        }
    })
}

fn start_locked(app: &AppHandle, slot: &mut Option<ManagedRuntime>) -> Result<Value, String> {
    if let Some(runtime) = slot.as_mut() {
        if runtime
            .child
            .try_wait()
            .map_err(|e| e.to_string())?
            .is_none()
        {
            return Ok(
                json!({"status":"ready","model_configured":runtime.model_configured}),
            );
        }
        if let Some(mut old) = slot.take() {
            old.poll_stop.store(true, Ordering::SeqCst);
            if let Some(poller) = old.poller.take() {
                let _ = poller.join();
            }
            let _ = old.child.wait();
        }
    }
    let root = application_root(app)?;
    let data_dir = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&data_dir)
        .map_err(|e| format!("Could not prepare KeplerV runtime data: {e}"))?;
    let token = random_token()?;
    let port = available_port()?;
    let base_url = format!("http://127.0.0.1:{port}");
    // Uvicorn reports bind and import failures on stdout; capture both streams until health is confirmed.
    let mut command = Command::new(python_executable(&root));
    command
        .args(["-m", "runtime.main"])
        .current_dir(&root)
        .env_clear()
        .env("PATH", env::var("PATH").unwrap_or_default())
        .env("HOME", env::var("HOME").unwrap_or_default())
        .env("TMPDIR", env::var("TMPDIR").unwrap_or_default())
        .env("KEPLERV_RUNTIME_TOKEN", &token)
        .env("KEPLERV_RUNTIME_PORT", port.to_string())
        .env("KEPLERV_DATA_DIR", &data_dir)
        .env("PYTHONPATH", &root)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let saved_config = read_model_config(app).unwrap_or_else(|| ModelConfig { provider: "meta".into(), endpoint: "https://api.meta.ai/v1".into(), model: "muse-spark-1.3".into() });
    let provider = env::var("KEPLERV_MODEL_PROVIDER").unwrap_or(saved_config.provider);
    let model = env::var("KEPLERV_MODEL_NAME").unwrap_or(saved_config.model);
    let endpoint = env::var("KEPLERV_MODEL_BASE_URL").unwrap_or(saved_config.endpoint);
    let profile = if provider == "chatgpt" {
        let previous = chatgpt_profile().ok_or("Sign in with ChatGPT before selecting it for research.")?;
        let refreshed = crate::chatgpt_auth::refresh_if_needed(previous)?;
        let serialized = serde_json::to_string(&refreshed).map_err(|_| "Could not secure the ChatGPT connection.".to_string())?;
        keyring_entry("chatgpt")?.set_password(&serialized).map_err(|_| "Could not update ChatGPT credentials in the system credential store.".to_string())?;
        Some(refreshed)
    } else { chatgpt_profile() };
    let configured_key = if provider == "chatgpt" { None } else { model_key(&provider) };
    if let Some(key) = configured_key.as_deref() {
        command.env("MODEL_API_KEY", key);
    }
    let chatgpt_active = provider == "chatgpt" && profile.as_ref().is_some_and(|p| p.scopes.iter().any(|s| s == "chatgpt.tokens.use.direct"));
    if chatgpt_active {
        if let Some(profile) = profile.as_ref() {
        if profile.scopes.iter().any(|scope| scope == "chatgpt.tokens.use.direct") {
            command.env("KEPLERV_OPENAI_ACCESS_TOKEN", &profile.access_token);
        }
        }
    }
    command.env("KEPLERV_MODEL_PROVIDER", &provider).env("KEPLERV_MODEL_NAME", &model).env("KEPLERV_MODEL_BASE_URL", &endpoint);
    if env::var("KEPLERV_DEMO_MODE").as_deref() == Ok("1") {
        command.env("KEPLERV_DEMO_MODE", "1");
    }
    let mut child = command.spawn().map_err(|_| "Could not start Python 3.12 runtime. Install runtime/requirements.txt and select Python with KEPLERV_PYTHON if needed.".to_string())?;
    let client = authorized_client()?;
    let mut ready = false;
    for _ in 0..60 {
        if authenticated_get(&client, &base_url, &token, "/healthz").is_ok() {
            ready = true;
            break;
        }
        thread::sleep(Duration::from_millis(200));
    }
    if !ready {
        let mut child = child;
        let _ = child.kill();
        let _ = child.wait();
        let mut startup_error = String::new();
        if let Some(mut stdout) = child.stdout.take() {
            let _ = stdout.read_to_string(&mut startup_error);
        }
        if let Some(mut stderr) = child.stderr.take() {
            if !startup_error.is_empty() {
                startup_error.push('\n');
            }
            let _ = stderr.read_to_string(&mut startup_error);
        }
        let detail = startup_error.trim().chars().take(500).collect::<String>();
        let message = if detail.is_empty() {
            "Python runtime did not become ready. Check that FastAPI, Uvicorn, and runtime dependencies are installed.".to_string()
        } else {
            format!("Python runtime failed during startup: {detail}")
        };
        return Err(message);
    }
    // Drain both pipes after startup so later runtime logs cannot block the Python child.
    if let Some(mut output) = child.stdout.take() {
        thread::spawn(move || {
            let _ = io::copy(&mut output, &mut io::sink());
        });
    }
    if let Some(mut output) = child.stderr.take() {
        thread::spawn(move || {
            let _ = io::copy(&mut output, &mut io::sink());
        });
    }
    let stop = Arc::new(AtomicBool::new(false));
    let poller = spawn_event_poller(
        app.clone(),
        base_url.clone(),
        token.clone(),
        stop.clone(),
        client,
    );
    *slot = Some(ManagedRuntime {
        child,
        base_url,
        token,
        poll_stop: stop,
        poller: Some(poller),
        model_configured: configured_key.is_some() || chatgpt_active || provider.starts_with("local-") || provider == "meta" && saved_secret("meta").is_some(),
    });
    Ok(
        json!({"status":"ready","model_configured":configured_key.is_some() || chatgpt_active || provider.starts_with("local-")}),
    )
}

#[tauri::command]
pub fn runtime_start(
    app: AppHandle,
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let mut slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    start_locked(&app, &mut slot)
}

#[tauri::command]
pub fn runtime_status(
    app: AppHandle,
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let mut slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let Some(runtime) = slot.as_mut() else {
        let config = read_model_config(&app).unwrap_or(ModelConfig { provider: "meta".into(), endpoint: "".into(), model: "".into() });
        let ready = config.provider.starts_with("local-") || config.provider == "chatgpt" && chatgpt_profile().is_some_and(|p| p.scopes.iter().any(|s| s == "chatgpt.tokens.use.direct")) || model_key(&config.provider).is_some();
        return Ok(json!({"status":"stopped","model_configured":ready}));
    };
    if runtime
        .child
        .try_wait()
        .map_err(|e| e.to_string())?
        .is_some()
    {
        return Ok(json!({"status":"failed","error":"The Python runtime process exited."}));
    }
    Ok(
        json!({"status":"ready","model_configured":runtime.model_configured}),
    )
}

#[tauri::command]
pub fn credential_status(app: AppHandle, window: WebviewWindow) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let providers = ["meta", "openai", "anthropic", "google", "deepseek", "kimi", "openrouter", "groq", "mistral", "chatgpt", "local-ollama", "local-lmstudio", "local-openai-compatible", "openai-compatible"];
    let connected: Vec<String> = providers.iter().copied().filter(|provider| saved_secret(provider).is_some() || provider.starts_with("local-") && read_provider_config(&app, provider).is_some()).map(str::to_string).collect();
    let profile = chatgpt_profile();
    let config = read_model_config(&app);
    let sharing = profile.as_ref().is_some_and(|p| p.scopes.iter().any(|s| s == "chatgpt.tokens.use.direct"));
    let active_provider = config.as_ref().map(|c| c.provider.as_str()).unwrap_or("meta");
    let active_configured = active_provider.starts_with("local-") || active_provider == "chatgpt" && sharing || model_key(active_provider).is_some();
    Ok(json!({"connected":connected, "active_provider":active_provider, "active_model":config.as_ref().map(|c| c.model.as_str()).unwrap_or("muse-spark-1.3"), "meta_configured":active_configured, "meta_credential_saved":saved_secret("meta").is_some(), "chatgpt_connected":profile.is_some(), "chatgpt_email":profile.and_then(|p| p.email), "chatgpt_sharing":sharing}))
}

#[tauri::command]
pub fn provider_connect(app: AppHandle, window: WebviewWindow, state: State<'_, RuntimeBridge>, provider: String, endpoint: String, model: String, secret: Option<String>) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let local = matches!(provider.as_str(), "local-ollama" | "local-lmstudio" | "local-openai-compatible");
    let compatible = matches!(provider.as_str(), "openai" | "deepseek" | "kimi" | "openrouter" | "groq" | "mistral" | "meta" | "anthropic" | "google" | "openai-compatible" | "local-ollama" | "local-lmstudio" | "local-openai-compatible");
    if !local && !compatible { return Err("This provider does not have a runtime adapter yet.".into()); }
    let parsed = url::Url::parse(&endpoint).map_err(|_| "Enter a valid provider endpoint.".to_string())?;
    let local_host = matches!(parsed.host_str(), Some("localhost" | "127.0.0.1" | "::1"));
    if !parsed.username().is_empty() || parsed.password().is_some() || parsed.query().is_some() || parsed.fragment().is_some()
        || !(parsed.scheme() == "https" || parsed.scheme() == "http" && local_host) {
        return Err("Provider endpoints must use HTTPS. HTTP is allowed only for a local model on this device.".into());
    }
    if local && !local_host { return Err("Local model endpoints must point to this device.".into()); }
    let requested_model = model.trim().to_string();
    if requested_model.len() > 160 { return Err("Model name is too long.".into()); }
    let supplied_secret = secret.unwrap_or_default().trim().to_string();
    let secret = if supplied_secret.is_empty() { saved_secret(&provider).unwrap_or_default() } else { supplied_secret.clone() };
    if !local && secret.is_empty() { return Err("Enter this provider’s API key.".into()); }
    let mut url = endpoint.trim_end_matches('/').to_string();
    if url.ends_with('/') { url.pop(); }
    let model_list_url = if provider == "anthropic" { format!("{url}/models") } else { format!("{url}/models") };
    let mut request = Client::builder().no_proxy().timeout(Duration::from_secs(15)).build().map_err(|_| "Could not initialize provider connection check.".to_string())?.get(model_list_url);
    if provider == "google" { request = request.header("x-goog-api-key", &secret); }
    else if provider == "anthropic" { request = request.header("x-api-key", &secret).header("anthropic-version", "2023-06-01"); }
    else if !secret.is_empty() { request = request.bearer_auth(&secret); }
    let response = request.send().map_err(|_| "Could not reach this provider. Check the endpoint and your connection.".to_string())?;
    if !response.status().is_success() { return Err(format!("Provider connection check returned HTTP {}. Verify the key, endpoint, and model access.", response.status().as_u16())); }
    let body: Value = response.json().map_err(|_| "Provider returned an unreadable model list.".to_string())?;
    let models = body.get("data").or_else(|| body.get("models")).and_then(Value::as_array).ok_or("Provider returned no model list.")?;
    let selected = if requested_model.is_empty() {
        models.iter().find_map(model_id).ok_or("Provider returned no available models.")?
    } else {
        models.iter().find(|item| model_id(item).is_some_and(|name| name == requested_model || name.ends_with(&format!("/{}", requested_model)))).and_then(model_id)
            .ok_or("The provider is reachable, but that model was not found in its model list.")?
    };
    if local {
        if keyring_entry(&provider).is_ok() { let _ = keyring_entry(&provider).and_then(|entry| entry.delete_credential().map_err(|_| "Could not clear saved key.".to_string())); }
    } else if !supplied_secret.is_empty() {
        keyring_entry(&provider)?.set_password(&secret).map_err(|_| "Could not save this key in the system credential store.".to_string())?;
    }
    let config = ModelConfig { provider: provider.clone(), endpoint: url, model: selected };
    write_provider_config(&app, &config)?;
    write_model_config(&app, &config)?;
    let mut slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
    stop_runtime(&mut slot);
    let runtime = start_locked(&app, &mut slot)?;
    Ok(json!({"connected":true,"provider":provider,"model":config.model,"runtime":runtime}))
}

#[tauri::command]
pub fn provider_configuration(app: AppHandle, window: WebviewWindow, provider: String) -> Result<Value, String> {
    ensure_main_window(&window)?;
    if !matches!(provider.as_str(), "openai" | "anthropic" | "google" | "deepseek" | "kimi" | "openrouter" | "groq" | "mistral" | "openai-compatible" | "local-ollama" | "local-lmstudio" | "local-openai-compatible") {
        return Err("Unknown provider.".into());
    }
    let config = read_provider_config(&app, &provider);
    Ok(json!({"endpoint":config.as_ref().map(|c| c.endpoint.as_str()).unwrap_or(""),"model":config.as_ref().map(|c| c.model.as_str()).unwrap_or(""),"connected":saved_secret(&provider).is_some() || provider.starts_with("local-")}))
}

#[tauri::command]
pub fn provider_activate(app: AppHandle, window: WebviewWindow, state: State<'_, RuntimeBridge>, provider: String) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let config = read_provider_config(&app, &provider).ok_or("Connect this provider before selecting it.")?;
    if !provider.starts_with("local-") && provider != "chatgpt" && saved_secret(&provider).is_none() {
        return Err("This provider’s credential is missing. Connect it again.".into());
    }
    if provider == "chatgpt" && !chatgpt_profile().is_some_and(|p| p.scopes.iter().any(|s| s == "chatgpt.tokens.use.direct")) {
        return Err("Sign in with ChatGPT and enable plan usage before selecting it.".into());
    }
    write_model_config(&app, &config)?;
    let mut slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
    stop_runtime(&mut slot);
    let runtime = start_locked(&app, &mut slot)?;
    Ok(json!({"provider":provider,"model":config.model,"runtime":runtime}))
}

#[tauri::command]
pub async fn credential_chatgpt_connect(app: AppHandle, window: WebviewWindow, state: State<'_, RuntimeBridge>) -> Result<Value, String> {
    ensure_main_window(&window)?;
    // OAuth waits, token exchange, keychain writes, and model discovery all use
    // blocking APIs. Keep them off Tauri's async/UI executor so the main window stays
    // responsive while the user signs in.
    let auth_app = app.clone();
    let worker_result = tauri::async_runtime::spawn_blocking(move || {
      (|| {
        let data_dir = auth_app.path().app_local_data_dir().map_err(|_| "Could not prepare ChatGPT settings.".to_string())?;
        let profile = crate::chatgpt_auth::connect(&auth_app, &data_dir, chatgpt_profile())?;
        let email = profile.email.clone();
        let sharing = profile.scopes.iter().any(|scope| scope == "chatgpt.tokens.use.direct");
        let saved = serde_json::to_string(&profile).map_err(|_| "Could not secure the ChatGPT connection.".to_string())?;
        keyring_entry("chatgpt")?.set_password(&saved).map_err(|_| "Could not save the ChatGPT connection in the system credential store.".to_string())?;

        // Account sign-in is a successful connection on its own. Model discovery is
        // a separate step; keep the validated account and explain a discovery failure
        // instead of turning a successful OAuth callback into a false sign-in error.
        let mut model_error: Option<String> = None;
        let selected_model = if sharing {
            let models_result = Client::builder().no_proxy().timeout(Duration::from_secs(15)).build()
                .map_err(|_| "Could not initialize the model catalog request.".to_string())
                .and_then(|client| {
                    let response = client.get("https://api.openai.com/v1/models").bearer_auth(&profile.access_token).send()
                        .map_err(|_| "Could not reach the ChatGPT model catalog. Check your internet connection and try reconnecting.".to_string())?;
                    if !response.status().is_success() {
                        return Err(format!("ChatGPT connected, but its model catalog returned HTTP {}.", response.status().as_u16()));
                    }
                    response.json::<Value>().map_err(|_| "ChatGPT connected, but its model catalog response could not be read.".to_string())
                })
                .and_then(|response| {
                    let models = response.get("models").and_then(Value::as_array).ok_or("ChatGPT connected, but no model catalog was returned.".to_string())?;
                    models.iter().find(|m| m.get("visibility").and_then(Value::as_str) == Some("list"))
                        .and_then(|m| m.get("slug").and_then(Value::as_str))
                        .map(str::to_string)
                        .ok_or("ChatGPT connected, but no models are available for this account yet.".to_string())
                });
            match models_result {
                Ok(selected) => {
                    let config = ModelConfig { provider: "chatgpt".into(), endpoint: "https://api.openai.com/v1".into(), model: selected.clone() };
                    if let Err(error) = write_provider_config(&auth_app, &config).and_then(|_| write_model_config(&auth_app, &config).map(|_| ())) {
                        model_error = Some(format!("Account connected, but KeplerV could not save the model selection: {error}"));
                        None
                    } else { Some(selected) }
                }
                Err(error) => { model_error = Some(error); None }
            }
        } else { None };

        if !sharing && read_model_config(&auth_app).is_some_and(|config| config.provider == "chatgpt") {
            let fallback = read_provider_config(&auth_app, "meta").unwrap_or(ModelConfig { provider: "meta".into(), endpoint: "https://api.meta.ai/v1".into(), model: "muse-spark-1.3".into() });
            write_model_config(&auth_app, &fallback)?;
        }
        Ok::<Value, String>(json!({"connected":true,"email":email,"sharing":sharing,"model":selected_model,"model_error":model_error}))
      })()
    }).await;
    // Bring KeplerV forward after the in-app authentication window hands back.
    if let Some(auth_window) = app.get_webview_window("chatgpt-auth") { let _ = auth_window.close(); }
    let _ = window.set_focus();
    let mut result = worker_result.map_err(|_| "The ChatGPT sign-in worker stopped unexpectedly.".to_string())??;
    let runtime = match state.runtime.lock() {
        Ok(mut slot) => {
            stop_runtime(&mut slot);
            start_locked(&app, &mut slot).unwrap_or_else(|error| {
                let message = format!("Account connected, but KeplerV runtime could not restart: {error}");
                let object = result.as_object_mut();
                if let Some(object) = object {
                    if object.get("model_error").is_none_or(Value::is_null) { object.insert("model_error".into(), Value::String(message)); }
                }
                json!({"status":"unavailable","model_configured":false})
            })
        }
        Err(_) => {
            if let Some(object) = result.as_object_mut() {
                object.insert("model_error".into(), Value::String("Account connected, but KeplerV runtime state is unavailable.".into()));
            }
            json!({"status":"unavailable","model_configured":false})
        }
    };
    if let Some(object) = result.as_object_mut() { object.insert("runtime".into(), runtime); }
    Ok(result)
}

#[tauri::command]
pub fn credential_chatgpt_disconnect(app: AppHandle, window: WebviewWindow, state: State<'_, RuntimeBridge>) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let remote_revoked = chatgpt_profile().as_ref().is_some_and(crate::chatgpt_auth::revoke);
    let _ = keyring_entry("chatgpt")?.delete_credential();
    if read_model_config(&app).is_some_and(|config| config.provider == "chatgpt") {
        let fallback = read_provider_config(&app, "meta").unwrap_or(ModelConfig { provider: "meta".into(), endpoint: "https://api.meta.ai/v1".into(), model: "muse-spark-1.3".into() });
        write_model_config(&app, &fallback)?;
    }
    let mut slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
    stop_runtime(&mut slot);
    let status = start_locked(&app, &mut slot)?;
    Ok(json!({"connected":false,"remote_revoked":remote_revoked,"runtime":status}))
}

#[tauri::command]
pub fn credential_save(
    app: AppHandle,
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    provider: String,
    secret: String,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let secret = secret.trim().to_string();
    if secret.is_empty() || secret.len() > 8192 {
        return Err("Enter a valid credential (up to 8,192 characters).".into());
    }
    keyring_entry(&provider)?.set_password(&secret).map_err(|_| "Could not save this credential in the system credential store.".to_string())?;
    if provider == "meta" {
        let config = ModelConfig { provider: "meta".into(), endpoint: "https://api.meta.ai/v1".into(), model: "muse-spark-1.3".into() };
        write_provider_config(&app, &config)?;
        write_model_config(&app, &config)?;
        let mut slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
        if let Some(mut old) = slot.take() {
            old.poll_stop.store(true, Ordering::SeqCst);
            let _ = old.child.kill();
            if let Some(poller) = old.poller.take() { let _ = poller.join(); }
            let _ = old.child.wait();
        }
        return start_locked(&app, &mut slot);
    }
    Ok(json!({"saved":true}))
}

#[tauri::command]
pub fn credential_delete(app: AppHandle, window: WebviewWindow, state: State<'_, RuntimeBridge>, provider: String) -> Result<Value, String> {
    ensure_main_window(&window)?;
    if provider == "chatgpt" { return Err("Use the ChatGPT disconnect action to revoke this connection.".into()); }
    if provider.starts_with("local-") {
        let directory = app.path().app_local_data_dir().map_err(|_| "Could not access KeplerV settings.".to_string())?.join("providers");
        let _ = fs::remove_file(directory.join(format!("{provider}.json")));
    } else {
        keyring_entry(&provider)?.delete_credential().map_err(|_| "Could not remove this credential from the system credential store.".to_string())?;
    }
    let directory = app.path().app_local_data_dir().map_err(|_| "Could not access KeplerV settings.".to_string())?.join("providers");
    let _ = fs::remove_file(directory.join(format!("{provider}.json")));
    let active = read_model_config(&app).is_some_and(|config| config.provider == provider);
    if active {
        let fallback = read_provider_config(&app, "meta").unwrap_or(ModelConfig { provider: "meta".into(), endpoint: "https://api.meta.ai/v1".into(), model: "muse-spark-1.3".into() });
        write_model_config(&app, &fallback)?;
        let mut slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
        stop_runtime(&mut slot);
        let runtime = start_locked(&app, &mut slot)?;
        return Ok(json!({"deleted":true,"runtime":runtime}));
    }
    Ok(json!({"deleted":true}))
}

#[tauri::command]
pub fn runtime_snapshot(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    after: Option<u64>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let runtime = slot
        .as_ref()
        .ok_or_else(|| "Runtime is not running.".to_string())?;
    let client = authorized_client()?;
    authenticated_get(
        &client,
        &runtime.base_url,
        &runtime.token,
        &format!("/snapshot?after={}", after.unwrap_or(0)),
    )
}

#[tauri::command]
pub fn runtime_mail_list(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    provider: Option<String>,
    email: Option<String>,
    query: Option<String>,
    limit: Option<u32>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let runtime = slot
        .as_ref()
        .ok_or_else(|| "Runtime is not running.".to_string())?;
    let client = authorized_client()?;
    let mut path = format!("/mail/messages?limit={}", limit.unwrap_or(10).clamp(1, 25));
    if let Some(provider) = provider.filter(|value| !value.trim().is_empty()) {
        path.push_str(&format!("&provider={}", urlencoding(provider.trim())));
    }
    if let Some(email) = email.filter(|value| !value.trim().is_empty()) {
        path.push_str(&format!("&email={}", urlencoding(email.trim())));
    }
    if let Some(query) = query.filter(|value| !value.trim().is_empty()) {
        path.push_str(&format!("&q={}", urlencoding(query.trim())));
    }
    authenticated_get(&client, &runtime.base_url, &runtime.token, &path)
}

fn urlencoding(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes()).collect()
}

#[tauri::command]
pub fn runtime_mail_get(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    message_id: String,
    provider: Option<String>,
    email: Option<String>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    if message_id.trim().is_empty() || message_id.len() > 256 {
        return Err("Mail message id was empty or too long.".into());
    }
    let slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let runtime = slot
        .as_ref()
        .ok_or_else(|| "Runtime is not running.".to_string())?;
    let client = authorized_client()?;
    let mut path = format!("/mail/messages/{}", urlencoding(message_id.trim()));
    let mut separator = "?";
    if let Some(provider) = provider.filter(|value| !value.trim().is_empty()) {
        path.push_str(&format!("{separator}provider={}", urlencoding(provider.trim())));
        separator = "&";
    }
    if let Some(email) = email.filter(|value| !value.trim().is_empty()) {
        path.push_str(&format!("{separator}email={}", urlencoding(email.trim())));
    }
    authenticated_get(&client, &runtime.base_url, &runtime.token, &path)
}

#[tauri::command]
pub fn runtime_info(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let runtime = slot
        .as_ref()
        .ok_or_else(|| "Runtime is not running.".to_string())?;
    Ok(json!({"base_url": runtime.base_url}))
}

#[tauri::command]
pub fn transcribe_audio(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    audio_b64: String,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    use base64::Engine;
    let audio = base64::engine::general_purpose::STANDARD
        .decode(audio_b64.as_bytes())
        .map_err(|_| "Voice recording could not be decoded.".to_string())?;
    if audio.is_empty() || audio.len() > 8 * 1024 * 1024 {
        return Err("Voice recording is too long; keep requests under a few minutes.".into());
    }
    let slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let runtime = slot
        .as_ref()
        .ok_or_else(|| "Runtime is not running.".to_string())?;
    let client = Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(100))
        .build()
        .map_err(|e| format!("Could not initialize the local runtime connection: {e}"))?;
    authenticated_post_bytes(
        &client,
        &runtime.base_url,
        &runtime.token,
        "/transcribe",
        "audio/wav",
        audio,
    )
}

#[tauri::command]
pub fn interpret_voice(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    transcript: String,
    context: Value,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    if transcript.trim().is_empty() || transcript.len() > 2000 {
        return Err("Voice transcript was empty or too long.".into());
    }
    let slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let runtime = slot
        .as_ref()
        .ok_or_else(|| "Runtime is not running.".to_string())?;
    let client = Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(75))
        .build()
        .map_err(|e| format!("Could not initialize the local runtime connection: {e}"))?;
    authenticated_post(
        &client,
        &runtime.base_url,
        &runtime.token,
        "/interpret",
        json!({"transcript":transcript,"context":context}),
    )
}

#[tauri::command]
pub fn runtime_create_run(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    goal_id: String,
    goal_title: String,
    goal_description: String,
    task: String,
    source_url: Option<String>,
    token_budget: u32,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let runtime = slot
        .as_ref()
        .ok_or_else(|| "Runtime is not running.".to_string())?;
    let client = authorized_client()?;
    authenticated_post(
        &client,
        &runtime.base_url,
        &runtime.token,
        "/runs",
        json!({
            "goal_id":goal_id,"goal_title":goal_title,"goal_description":goal_description,
            "task":task,"source_url":source_url,"step_budget":16,"token_budget":token_budget
        }),
    )
}

#[tauri::command]
pub fn runtime_control(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    run_id: String,
    action: String,
    call_id: Option<String>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let slot = state
        .runtime
        .lock()
        .map_err(|_| "Runtime state is unavailable.".to_string())?;
    let runtime = slot
        .as_ref()
        .ok_or_else(|| "Runtime is not running.".to_string())?;
    let client = authorized_client()?;
    authenticated_post(
        &client,
        &runtime.base_url,
        &runtime.token,
        &format!("/runs/{run_id}/control"),
        json!({"action":action,"call_id":call_id}),
    )
}

#[tauri::command]
pub fn runtime_guard_investigate(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    case_id: String,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let (base_url, token) = {
        let slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
        let runtime = slot.as_ref().ok_or_else(|| "Runtime is not running.".to_string())?;
        (runtime.base_url.clone(), runtime.token.clone())
    };
    let client = Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(150))
        .build()
        .map_err(|_| "Could not initialize the Guard connection.".to_string())?;
    authenticated_post(
        &client,
        &base_url,
        &token,
        &format!("/guard/cases/{case_id}/investigate"),
        json!({}),
    )
}

#[tauri::command]
pub fn runtime_guard_interview(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    case_id: String,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let (base_url, token) = {
        let slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
        let runtime = slot.as_ref().ok_or_else(|| "Runtime is not running.".to_string())?;
        (runtime.base_url.clone(), runtime.token.clone())
    };
    let client = Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(190))
        .build()
        .map_err(|_| "Could not initialize the Guard connection.".to_string())?;
    authenticated_post(
        &client,
        &base_url,
        &token,
        &format!("/guard/cases/{case_id}/interview"),
        json!({}),
    )
}

#[tauri::command]
pub fn runtime_guard_adjudicate(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    case_id: String,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let (base_url, token) = {
        let slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
        let runtime = slot.as_ref().ok_or_else(|| "Runtime is not running.".to_string())?;
        (runtime.base_url.clone(), runtime.token.clone())
    };
    let client = Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(150))
        .build()
        .map_err(|_| "Could not initialize the Guard connection.".to_string())?;
    authenticated_post(
        &client,
        &base_url,
        &token,
        &format!("/guard/cases/{case_id}/adjudicate"),
        json!({}),
    )
}

#[tauri::command]
pub fn runtime_guard_containment(
    window: WebviewWindow,
    state: State<'_, RuntimeBridge>,
    case_id: String,
    action: String,
    reason: String,
    capability: Option<String>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let (base_url, token) = {
        let slot = state.runtime.lock().map_err(|_| "Runtime state is unavailable.".to_string())?;
        let runtime = slot.as_ref().ok_or_else(|| "Runtime is not running.".to_string())?;
        (runtime.base_url.clone(), runtime.token.clone())
    };
    let client = authorized_client()?;
    authenticated_post(
        &client,
        &base_url,
        &token,
        &format!("/guard/cases/{case_id}/containment"),
        json!({"action":action,"reason":reason,"capability":capability}),
    )
}

#[tauri::command]
pub async fn oauth_connect(
    app: AppHandle,
    window: WebviewWindow,
    provider: String,
    email: Option<String>,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    if !crate::oauth::is_supported_provider(&provider) {
        return Err("Unknown service provider.".into());
    }
    // OAuth waits, token exchange, and keychain writes are blocking. Keep them
    // off Tauri's async/UI executor so the main window stays responsive.
    let auth_app = app.clone();
    let is_fake = provider == "fake-mail";
    let worker_result = tauri::async_runtime::spawn_blocking(move || {
        (|| {
            let data_dir = auth_app.path().app_local_data_dir().map_err(|_| "Could not prepare service account settings.".to_string())?;
            let token_set = if provider == "fake-mail" {
                // Clearly-labeled local fixture: no browser, no network.
                crate::oauth::connect_fake_account(email.as_deref().unwrap_or("demo.person@example.test"))?
            } else {
                crate::oauth::connect_account(&auth_app, &data_dir, &provider, email.as_deref())?
            };
            let serialized = serde_json::to_string(&token_set).map_err(|_| "Could not secure the service connection.".to_string())?;
            oauth_keyring_entry(&token_set.provider, &token_set.account_email)?
                .set_password(&serialized)
                .map_err(|_| "Could not save the service connection in the system credential store.".to_string())?;
            let meta = crate::oauth::metadata_for(&token_set, "connected");
            write_oauth_metadata(&auth_app, &meta)?;
            // Non-secret metadata only; tokens stay in the keychain.
            Ok::<Value, String>(json!({"connected":true,"account":meta}))
        })()
    })
    .await;
    if !is_fake {
        if let Some(auth_window) = app.get_webview_window("oauth-auth") {
            let _ = auth_window.close();
        }
        let _ = window.set_focus();
    }
    worker_result.map_err(|_| "The service sign-in worker stopped unexpectedly.".to_string())?
}

#[tauri::command]
pub async fn oauth_refresh(
    app: AppHandle,
    window: WebviewWindow,
    provider: String,
    email: String,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let auth_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        (|| {
            let data_dir = auth_app.path().app_local_data_dir().map_err(|_| "Could not access service account settings.".to_string())?;
            let raw = oauth_keyring_entry(&provider, &email)?.get_password().map_err(|_| "This account is not connected. Connect it again.".to_string())?;
            let token_set: crate::oauth::OAuthTokenSet = serde_json::from_str(&raw).map_err(|_| "The stored connection could not be read. Reconnect the account.".to_string())?;
            let refreshed = crate::oauth::refresh_token_set(&token_set, &data_dir)?;
            let serialized = serde_json::to_string(&refreshed).map_err(|_| "Could not secure the refreshed connection.".to_string())?;
            oauth_keyring_entry(&refreshed.provider, &refreshed.account_email)?
                .set_password(&serialized)
                .map_err(|_| "Could not update the service connection in the system credential store.".to_string())?;
            let meta = crate::oauth::metadata_for(&refreshed, "connected");
            write_oauth_metadata(&auth_app, &meta)?;
            Ok::<Value, String>(json!({"refreshed":true,"account":meta}))
        })()
    })
    .await
    .map_err(|_| "The token refresh worker stopped unexpectedly.".to_string())?
}

#[tauri::command]
pub fn oauth_disconnect(
    app: AppHandle,
    window: WebviewWindow,
    provider: String,
    email: String,
) -> Result<Value, String> {
    ensure_main_window(&window)?;
    // Best-effort remote revocation first; local deletion happens regardless.
    let remote_revoked = oauth_keyring_entry(&provider, &email)
        .ok()
        .and_then(|entry| entry.get_password().ok())
        .and_then(|raw| serde_json::from_str::<crate::oauth::OAuthTokenSet>(&raw).ok())
        .is_some_and(|token_set| crate::oauth::revoke_token_set(&token_set));
    let _ = oauth_keyring_entry(&provider, &email)?.delete_credential();
    if let Ok(path) = oauth_metadata_path(&app, &provider, &email) {
        let _ = fs::remove_file(path);
    }
    Ok(json!({"disconnected":true,"remote_revoked":remote_revoked}))
}

#[tauri::command]
pub fn account_status(app: AppHandle, window: WebviewWindow) -> Result<Value, String> {
    ensure_main_window(&window)?;
    let accounts = read_oauth_accounts(&app);
    Ok(json!({"accounts":accounts}))
}
