#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod instance;
use backend::Backend;
use std::sync::{Arc, Mutex};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

fn main() {
    let owned: Arc<Mutex<Option<Backend>>> = Arc::new(Mutex::new(None));
    let startup = owned.clone();
    let singleton = Arc::new(Mutex::new(None));
    let singleton_setup = singleton.clone();
    let app = tauri::Builder::default()
        .setup(move |app| {
            #[cfg(not(all(target_os = "macos", target_arch = "aarch64")))]
            return Err("This P8-A preview is validated only on macOS arm64".into());
            let state = app.path().app_data_dir()?;
            let Some(mut instance) = instance::Instance::acquire(&state)? else { std::process::exit(0); };
            let focus_handle = app.handle().clone();
            instance.listen(move || {
                if let Some(window) = focus_handle.get_webview_window("main") {
                    let _ = window.show(); let _ = window.unminimize(); let _ = window.set_focus();
                }
            });
            *singleton_setup.lock().unwrap() = Some(instance);
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("PTCG Lab Preview").inner_size(1280.0, 850.0).min_inner_size(900.0, 600.0)
                .on_navigation({
                    app.manage(AllowedOrigin::default());
                    let handle = app.handle().clone();
                    move |url| {
                        navigation_allowed(url, handle.state::<AllowedOrigin>().0.lock().unwrap().as_deref())
                    }
                })
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .build()?;
            let root = app.path().resource_dir()?.join("backend");
            let handle = app.handle().clone();
            let backend_state = startup.clone();
            std::thread::spawn(move || {
                match Backend::start(&root, &state) {
                    Ok((child, ready)) => {
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
                                let _ = window.eval("document.body.replaceChildren(Object.assign(document.createElement('p'),{textContent:'本机后台已退出。请关闭窗口后重新打开；已保存的卡组仍保留在本机。'}))");
                                break;
                            }
                        }
                    }
                    Err(code) => {
                        let _ = window.set_title(&format!("PTCG Lab — {code}"));
                        let _ = window.eval("document.body.replaceChildren(Object.assign(document.createElement('p'),{textContent:'本机后台启动失败。请关闭后重试；安装包校验失败时请重新安装。'}))");
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) { window.app_handle().exit(0); }
        })
        .build(tauri::generate_context!())
        .expect("Native shell initialization failed");
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
        for url in ["tauri://localhost", "tauri://localhost/", "tauri://localhost/index.html", "http://tauri.localhost/", "http://127.0.0.1:12345/#online"] {
            assert!(super::navigation_allowed(&url.parse().unwrap(), Some("http://127.0.0.1:12345")));
        }
        for url in ["https://evil.example/", "http://127.0.0.1:12346/", "tauri://evil/index.html", "http://tauri.localhost:12345/", "http://user:pass@127.0.0.1:12345/", "file:///etc/passwd"] {
            assert!(!super::navigation_allowed(&url.parse().unwrap(), Some("http://127.0.0.1:12345")));
        }
        assert!(!super::navigation_allowed(&"http://127.0.0.1:12345/".parse().unwrap(), None));
    }

    pub fn scratch(label: &str) -> std::path::PathBuf {
        use std::os::unix::fs::DirBuilderExt;
        let mut random = [0u8; 8];
        getrandom::fill(&mut random).unwrap();
        let suffix: String = random.iter().map(|b| format!("{b:02x}")).collect();
        let root = std::path::Path::new("/private/tmp").join(format!("ptcg-{label}-{suffix}"));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&root)
            .unwrap();
        root
    }
}
