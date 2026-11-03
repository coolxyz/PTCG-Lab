import Foundation
import UIKit
import UniformTypeIdentifiers

private typealias Callback = @convention(c) (UnsafeMutableRawPointer?, Int32, UnsafePointer<UInt8>?, Int) -> Void
@_silgen_name("ptcg_mobile_protected_data_changed")
private func protectedDataChanged(_ active: Int32)

private func mainValue<T>(_ body: () -> T) -> T {
    if Thread.isMainThread { return body() }
    return DispatchQueue.main.sync(execute: body)
}
@_cdecl("ptcg_mobile_data_available")
public func dataAvailable() -> Int32 {
    mainValue { UIApplication.shared.isProtectedDataAvailable ? 1 : 0 }
}
@_cdecl("ptcg_mobile_protect_file")
public func protectFile(_ raw: UnsafePointer<CChar>) -> Int32 {
    do { try FileManager.default.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: String(cString: raw)); return 1 }
    catch { return -1 }
}

private var observations: [NSObjectProtocol] = []
@_cdecl("ptcg_mobile_protect")
public func protect(_ raw: UnsafePointer<CChar>) -> Int32 {
    let root = URL(fileURLWithPath: String(cString: raw), isDirectory: true)
    do {
        let manager = FileManager.default
        // Remove only this bridge's UUID-named, ephemeral exports after a prior
        // process termination. Persistent application data is never targeted.
        for url in (try? manager.contentsOfDirectory(at: manager.temporaryDirectory, includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey])) ?? [] {
            let name = url.lastPathComponent
            if name.hasPrefix("ptcg-share-"), UUID(uuidString: String(name.dropFirst("ptcg-share-".count))) != nil {
                try? manager.removeItem(at: url)
            }
        }
        try manager.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: root.path)
        // Existing SQLite/WAL and journal files receive the same protection;
        // newly created children inherit the protected directory class.
        if let items = manager.enumerator(at: root, includingPropertiesForKeys: [.isSymbolicLinkKey]) {
            var count = 0
            for case let url as URL in items {
                count += 1
                let info = try url.resourceValues(forKeys: [.isSymbolicLinkKey])
                if count > 5000 || info.isSymbolicLink == true { return -1 }
                try manager.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: url.path)
            }
        }
        var online = root.appendingPathComponent("online", isDirectory: true)
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        try online.setResourceValues(values)
        mainValue {
            if observations.isEmpty {
                let center = NotificationCenter.default
                observations.append(center.addObserver(forName: UIApplication.protectedDataWillBecomeUnavailableNotification, object: nil, queue: .main) { _ in protectedDataChanged(0) })
                observations.append(center.addObserver(forName: UIApplication.protectedDataDidBecomeAvailableNotification, object: nil, queue: .main) { _ in
                    protectedDataChanged(UIApplication.shared.applicationState == .active ? 1 : 0)
                })
            }
        }
        return 1
    } catch { return -1 }
}

private struct Request: Decodable {
    let op: String
    let kind: String
    let text: String?
    let filename: String?
}
private final class Coordinator: NSObject, UIDocumentPickerDelegate {
    static let shared = Coordinator()
    private var callback: Callback?
    private var context: UnsafeMutableRawPointer?
    private var kind = ""
    private var working = false
    private func complete(_ status: Int32, _ bytes: Data = Data()) {
        guard let reply = callback else { return }
        let state = context
        callback = nil; context = nil; working = false
        bytes.withUnsafeBytes { buffer in reply(state, status, buffer.bindMemory(to: UInt8.self).baseAddress, bytes.count) }
    }
    private func presenter() -> UIViewController? {
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first(where: { $0.activationState == .foregroundActive }),
              let root = scene.windows.first(where: { $0.isKeyWindow })?.rootViewController else { return nil }
        var current = root
        while let next = current.presentedViewController { current = next }
        return current
    }
    func begin(_ input: Data, _ state: UnsafeMutableRawPointer?, _ reply: @escaping Callback) {
        guard !working else { reply(state, 3, nil, 0); return }
        callback = reply; context = state; working = true
        guard UIApplication.shared.isProtectedDataAvailable, let request = try? JSONDecoder().decode(Request.self, from: input),
              ["deck", "collection", "catalog"].contains(request.kind), let host = presenter(), !host.isBeingDismissed else { complete(-1); return }
        kind = request.kind
        if request.op == "pick" {
            let types: [UTType] = kind == "catalog" ? [.json] : kind == "collection" ? [.commaSeparatedText, .plainText] : [.json, .commaSeparatedText, .plainText]
            let picker = UIDocumentPickerViewController(forOpeningContentTypes: types, asCopy: true)
            picker.allowsMultipleSelection = false; picker.delegate = self
            host.present(picker, animated: true)
        } else if request.op == "share", kind != "catalog", let text = request.text, text.utf8.count <= 16 * 1024 * 1024 {
            do {
                let manager = FileManager.default
                let folder = manager.temporaryDirectory.appendingPathComponent("ptcg-share-" + UUID().uuidString, isDirectory: true)
                try manager.createDirectory(at: folder, withIntermediateDirectories: false, attributes: [.protectionKey: FileProtectionType.complete, .posixPermissions: 0o700])
                var excluded = folder; var values = URLResourceValues(); values.isExcludedFromBackup = true; try excluded.setResourceValues(values)
                let filename = kind == "deck" ? (request.filename ?? "ptcg-deck.json") : "ptcg-collection.csv"
                guard ["ptcg-deck.json", "ptcg-deck.csv", "ptcg-deck.txt", "ptcg-collection.csv"].contains(filename) else {
                    try? manager.removeItem(at: folder); complete(-1); return
                }
                let file = folder.appendingPathComponent(filename)
                try Data(text.utf8).write(to: file, options: [.atomic, .completeFileProtection])
                let share = UIActivityViewController(activityItems: [file], applicationActivities: nil)
                share.popoverPresentationController?.sourceView = host.view
                share.popoverPresentationController?.sourceRect = CGRect(x: host.view.bounds.midX, y: host.view.bounds.midY, width: 1, height: 1)
                share.completionWithItemsHandler = { [weak self] _, completed, _, error in
                    try? manager.removeItem(at: folder)
                    self?.complete(error == nil ? (completed ? 1 : 0) : -1)
                }
                host.present(share, animated: true)
            } catch { complete(-1) }
        } else { complete(-1) }
    }
    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { complete(0) }
    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        guard urls.count == 1, let url = urls.first else { complete(-1); return }
        let limit = kind == "catalog" ? 80 * 1024 * 1024 : kind == "collection" ? 1024 * 1024 : 128 * 1024
        DispatchQueue.global(qos: .userInitiated).async {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            var result: Data?; var error: NSError?
            NSFileCoordinator().coordinate(readingItemAt: url, options: .withoutChanges, error: &error) { selected in
                do {
                    let info = try selected.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
                    guard info.isRegularFile == true, let size = info.fileSize, size <= limit else { return }
                    // Own the bytes while the coordinated/security-scoped read
                    // is active; do not retain a lazy mapping into a provider.
                    let bytes = try Data(contentsOf: selected, options: .uncached)
                    if bytes.count <= limit { result = bytes }
                } catch { }
            }
            DispatchQueue.main.async {
                guard error == nil, let bytes = result, UIApplication.shared.isProtectedDataAvailable else { self.complete(-1); return }
                self.complete(1, bytes)
            }
        }
    }
}

@_cdecl("ptcg_mobile_file_request")
public func fileRequest(_ input: UnsafePointer<CChar>, _ context: UnsafeMutableRawPointer?, _ callback: @escaping @convention(c) (UnsafeMutableRawPointer?, Int32, UnsafePointer<UInt8>?, Int) -> Void) {
    let bytes = Data(String(cString: input).utf8)
    DispatchQueue.main.async { Coordinator.shared.begin(bytes, context, callback) }
}
