#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use ptcg_native_shell::{backend::Backend, exports::export_name, platform::instance};
use std::sync::{Arc, Mutex};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

fn main() {
    let acceptance = match ptcg_native_shell::acceptance::Session::load() {
        Ok(value) => value,
        Err(_) => {
            eprintln!("INVALID_ACCEPTANCE");
            std::process::exit(2);
        }
    };
    let acceptance_error = acceptance.clone();
    let owned: Arc<Mutex<Option<Backend>>> = Arc::new(Mutex::new(None));
    let startup = owned.clone();
    let singleton = Arc::new(Mutex::new(None));
    let singleton_setup = singleton.clone();
    let app = tauri::Builder::default()
        .setup(move |app| {
            #[cfg(not(any(all(target_os = "macos", target_arch = "aarch64"), all(target_os = "windows", target_arch = "x86_64"))))]
            return Err("Unsupported native development target".into());
            #[cfg(windows)]
            let default_state = app.path().app_local_data_dir()?;
            #[cfg(not(windows))]
            let default_state = app.path().app_data_dir()?;
            let state = acceptance.as_ref().map_or(default_state, |s| s.state.clone());
            let Some(mut instance) = instance::Instance::acquire(&state)? else {
                if let Some(test) = &acceptance { test.focused(); }
                std::process::exit(0);
            };
            if let Some(test) = &acceptance { test.mark()?; test.control(app.handle().clone()); }
            let focus_handle = app.handle().clone();
            instance.listen(move || {
                if let Some(window) = focus_handle.get_webview_window("main") {
                    let _ = window.show(); let _ = window.unminimize(); let _ = window.set_focus();
                }
            });
            *singleton_setup.lock().unwrap() = Some(instance);
            // Only explicit product exports can become native downloads. Acceptance
            // uses its own private directory, never the user's Downloads folder.
            let download_root = acceptance.as_ref().map(|s| s.state.join("exports"))
                .or_else(|| app.path().download_dir().ok());
            let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("PTCG Lab Preview").inner_size(1280.0, 850.0).min_inner_size(900.0, 600.0)
                .on_navigation({
                    app.manage(AllowedOrigin::default());
                    let handle = app.handle().clone();
                    move |url| {
                        navigation_allowed(url, handle.state::<AllowedOrigin>().0.lock().unwrap().as_deref())
                    }
                })
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .on_download(move |webview, event| {
                    match event {
                        tauri::webview::DownloadEvent::Requested { url, destination } => {
                            let allowed = webview.state::<AllowedOrigin>();
                            let origin = allowed.0.lock().unwrap();
                            let Some((stem, extension)) = export_name(&url, origin.as_deref()) else { return false; };
                            let Some(root) = &download_root else { return false; };
                            let mut random = [0u8; 8];
                            if getrandom::fill(&mut random).is_err() || std::fs::create_dir_all(root).is_err() { return false; }
                            let suffix: String = random.iter().map(|b| format!("{b:02x}")).collect();
                            let target = root.join(format!("{stem}-{suffix}.{extension}"));
                            if target.try_exists().unwrap_or(true) { return false; }
                            *destination = target;
                            true
                        }
                        tauri::webview::DownloadEvent::Finished { success, .. } => {
                            let _ = webview.eval(&format!("window.dispatchEvent(new CustomEvent('ptcg-export-complete',{{detail:{success}}}))"));
                            true
                        }
                        _ => false,
                    }
                });
            let builder = if let Some(test) = &acceptance {
                let loaded = test.clone(); let titled = test.clone();
                builder.incognito(true).title("PTCG Lab — 隔离验收")
                    .on_page_load(move |window, page| {
                        if page.event() == tauri::webview::PageLoadEvent::Finished { loaded.page(&window, page.url()); }
                    })
                    .on_document_title_changed(move |window, title| { titled.title(&window, &title); })
            } else { builder };
            #[cfg(windows)]
            let builder = {
                let directory = state.join("webview");
                drop(ptcg_native_shell::platform::security::PrivateDirectory::open(&directory)?);
                builder.data_directory(directory)
            };
            let window = builder.build()?;
            let root = app.path().resource_dir()?.join("backend");
            let handle = app.handle().clone();
            let backend_state = startup.clone();
            std::thread::spawn(move || {
                match Backend::start(&root, &state) {
                    Ok((child, ready)) => {
                        if let Some(test) = &acceptance { test.backend(child.pid(), &ready.origin); }
                        *backend_state.lock().unwrap() = Some(child);
                        let origin = ready.origin;
                        *handle.state::<AllowedOrigin>().0.lock().unwrap() = Some(origin.clone());
                        let cookie = ready.cookie;
                        let target = window.clone();
                        let failed = backend_state.clone();
                        let _ = handle.run_on_main_thread(move || {
                            let cookie = tauri::webview::Cookie::build(("ptcg-local", cookie))
                                .domain("127.0.0.1").path("/").http_only(true)
                                .same_site(tauri::webview::cookie::SameSite::Strict).build();
                            if target.set_cookie(cookie).is_err() || target.navigate(origin.parse().unwrap()).is_err() {
                                let _ = target.set_title("PTCG Lab — 本机授权失败，请关闭后重开");
                                startup_error(&target, "无法进入本机牌桌", "本机服务授权失败。请关闭窗口后重新打开；本机卡组和数据不会受影响。");
                                failed.lock().unwrap().take();
                            }
                        });
                        loop {
                            std::thread::sleep(std::time::Duration::from_secs(1));
                            let mut owner = backend_state.lock().unwrap();
                            let Some(child) = owner.as_mut() else { break; };
                            if child.exited() {
                                owner.take();
                                drop(owner);
                                let _ = window.set_title("PTCG Lab — 后台已退出，请关闭后重开");
                                startup_error(&window, "本机服务已退出", "请关闭窗口后重新打开；已保存的卡组仍保留在本机。");
                                break;
                            }
                        }
                    }
                    Err(code) => {
                        if let Some(test) = &acceptance { test.failed(); handle.exit(1); }
                        let _ = window.set_title(&format!("PTCG Lab — {code}"));
                        let message = match code {
                            "DESKTOP_RECOVERY_REQUIRED" => "上次安装或回退尚未完成。请关闭窗口，使用安装工具完成恢复后重新打开；现有数据和备份已保留。",
                            "DESKTOP_INSTALL_REQUIRED" => "此版本尚未迁移本机数据。请关闭窗口，通过安装工具备份并切换版本后重新打开。",
                            "DESKTOP_SCHEMA_UNSUPPORTED" => "此版本不支持本机数据格式。请使用匹配的安装版本，或显式恢复匹配的整套备份。",
                            _ => "本机后台启动失败。请关闭后重试；安装包校验失败时请重新安装。",
                        };
                        startup_error(&window, "暂时无法启动牌桌", message);
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) { window.app_handle().exit(0); }
        })
        .build(tauri::generate_context!());
    let app = match app {
        Ok(app) => app,
        Err(_) => {
            if let Some(test) = acceptance_error {
                test.failed();
            } else {
                ptcg_native_shell::platform::startup_error();
            }
            return;
        }
    };
    app.run(move |_app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            owned.lock().unwrap().take();
            singleton.lock().unwrap().take();
        }
    });
}

#[derive(Default)]
struct AllowedOrigin(Mutex<Option<String>>);

fn navigation_allowed(url: &tauri::Url, origin: Option<&str>) -> bool {
    if !url.username().is_empty() || url.password().is_some() {
        return false;
    }
    let bootstrap = ((url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost")))
        && url.port().is_none()
        && matches!(url.path(), "" | "/" | "/index.html")
        && url.query().is_none()
        && url.fragment().is_none();
    bootstrap || origin.is_some_and(|expected| url.origin().ascii_serialization() == expected)
}

#[cfg(test)]
mod tests {
    #[test]
    fn navigation_is_limited_to_bootstrap_and_verified_product_origin() {
        for url in [
            "tauri://localhost",
            "tauri://localhost/",
            "tauri://localhost/index.html",
            "http://tauri.localhost/",
            "http://127.0.0.1:12345/#online",
        ] {
            assert!(super::navigation_allowed(
                &url.parse().unwrap(),
                Some("http://127.0.0.1:12345")
            ));
        }
        for url in [
            "https://evil.example/",
            "http://127.0.0.1:12346/",
            "tauri://evil/index.html",
            "http://tauri.localhost:12345/",
            "http://user:pass@127.0.0.1:12345/",
            "file:///etc/passwd",
        ] {
            assert!(!super::navigation_allowed(
                &url.parse().unwrap(),
                Some("http://127.0.0.1:12345")
            ));
        }
        assert!(!super::navigation_allowed(
            &"http://127.0.0.1:12345/".parse().unwrap(),
            None
        ));
    }
}

// Keep the branded startup screen and render diagnostics as text only.
fn startup_error(window: &tauri::WebviewWindow, heading: &str, message: &str) {
    let heading = serde_json::to_string(heading).unwrap();
    let message = serde_json::to_string(message).unwrap();
    let _ = window.eval(&format!(r#"(() => {{
      document.body.classList.add('is-error');
      let title = document.getElementById('startup-title');
      let detail = document.getElementById('startup-message');
      if (!title || !detail) {{
        const panel = document.createElement('main'); panel.setAttribute('role','alert');
        title = document.createElement('h1'); detail = document.createElement('p');
        panel.append(title, detail); document.body.replaceChildren(panel);
      }}
      title.textContent = {heading}; detail.textContent = {message};
      const help = document.querySelector('.startup-help');
      if (help) help.textContent = '关闭窗口后可重试。若问题持续，请保留安装包版本与错误提示以便排查。';
    }})()"#));
}
