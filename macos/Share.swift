import AppKit
import UniformTypeIdentifiers

class HoverButton: NSButton {
    var tracking: NSTrackingArea?
    override func updateTrackingAreas() {
        if let tracking { removeTrackingArea(tracking) }
        tracking = NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self)
        addTrackingArea(tracking!); super.updateTrackingAreas()
    }
    override func mouseEntered(with event: NSEvent) { if isEnabled { wantsLayer = true; layer?.cornerRadius = 6; layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.18).cgColor } }
    override func mouseExited(with event: NSEvent) { layer?.backgroundColor = NSColor.clear.cgColor }
}
@objc(LinkGetShareController)
final class LinkGetShareController: NSViewController {
    let source = NSTextField()
    let action = NSPopUpButton()
    let status = NSTextField(labelWithString: "")
    let sendButton = HoverButton()
    let cancelButton = HoverButton()
    let language = HoverButton()
    let appearance = NSPopUpButton()
    var chinese = UserDefaults.standard.bool(forKey: "chinese")
    override func loadView() {
        view = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 190))
        source.frame = NSRect(x: 18, y: 124, width: 384, height: 26)
        action.frame = NSRect(x: 18, y: 86, width: 210, height: 26)
        language.frame = NSRect(x: 235, y: 86, width: 67, height: 28); language.bezelStyle = .rounded
        language.target = self; language.action = #selector(toggleLanguage)
        appearance.frame = NSRect(x: 307, y: 86, width: 95, height: 28); appearance.target = self; appearance.action = #selector(changeAppearance)
        sendButton.bezelStyle = .rounded; sendButton.frame = NSRect(x: 250, y: 20, width: 152, height: 30)
        sendButton.target = self; sendButton.action = #selector(send)
        cancelButton.bezelStyle = .rounded; cancelButton.frame = NSRect(x: 18, y: 20, width: 80, height: 30)
        cancelButton.target = self; cancelButton.action = #selector(cancelShare)
        for child in [source, action, status, sendButton, cancelButton, language, appearance] { view.addSubview(child) }
        status.frame = NSRect(x: 18, y: 53, width: 384, height: 20)
        localize(); applyAppearance()
    }
    func localize() {
        source.placeholderString = chinese ? "分享的链接或文件" : "Shared link or file"
        let index = max(0, action.indexOfSelectedItem)
        action.removeAllItems(); action.addItems(withTitles: chinese ? ["读取文字", "下载媒体"] : ["Read", "Download"]); action.selectItem(at: index)
        sendButton.title = chinese ? "在 LinkGet 打开" : "Open in LinkGet"
        cancelButton.title = chinese ? "取消" : "Cancel"
        language.title = chinese ? "English" : "中文"
        appearance.removeAllItems(); appearance.addItems(withTitles: chinese ? ["浅色", "深色", "系统"] : ["Light", "Dark", "System"])
        appearance.selectItem(at: UserDefaults.standard.integer(forKey: "theme"))
    }
    @objc func toggleLanguage() { chinese.toggle(); UserDefaults.standard.set(chinese, forKey: "chinese"); localize() }
    func applyAppearance() { let theme = UserDefaults.standard.integer(forKey: "theme"); view.appearance = theme == 2 ? nil : NSAppearance(named: theme == 1 ? .darkAqua : .aqua) }
    @objc func changeAppearance() { UserDefaults.standard.set(appearance.indexOfSelectedItem, forKey: "theme"); applyAppearance() }
    override func viewDidAppear() {
        super.viewDidAppear()
        for item in extensionContext?.inputItems as? [NSExtensionItem] ?? [] {
            for provider in item.attachments ?? [] {
                for type in [UTType.url.identifier, UTType.plainText.identifier] where provider.hasItemConformingToTypeIdentifier(type) {
                    provider.loadItem(forTypeIdentifier: type, options: nil) { [weak self] item, _ in
                        let resolvedURL = (item as? NSURL)?.filePathURL ?? (item as? URL)
                        let text = resolvedURL?.absoluteString ?? (item as? String) ?? (item as? NSAttributedString)?.string ?? (item as? Data).flatMap { String(data: $0, encoding: .utf8) } ?? ""
                        if !text.isEmpty { DispatchQueue.main.async { if self?.source.stringValue.isEmpty == true { self?.source.stringValue = text } } }
                    }
                    return
                }
            }
        }
    }
    @objc func send() {
        let value = source.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { status.stringValue = chinese ? "链接为空" : "Empty link"; return }
        var components = URLComponents(); components.scheme = "linkget"; components.host = "share"
        components.queryItems = [URLQueryItem(name: "url", value: value), URLQueryItem(name: "mode", value: action.indexOfSelectedItem == 1 ? "download" : "read")]
        if let url = components.url, NSWorkspace.shared.open(url) { extensionContext?.completeRequest(returningItems: [], completionHandler: nil) }
        else { status.stringValue = chinese ? "打开失败" : "Could not open" }
    }
    @objc func cancelShare() { extensionContext?.cancelRequest(withError: NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError)) }
}
