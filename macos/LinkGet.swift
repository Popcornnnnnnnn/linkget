import AppKit
import SwiftUI
import Darwin

final class Session: ObservableObject {
    @Published var source = ""
    @Published var mode = 0
    @Published var running = false
    @Published var message = ""
    @Published var log = ""
    @Published var output: URL?
    @Published var chinese = UserDefaults.standard.bool(forKey: "chinese") { didSet { UserDefaults.standard.set(chinese, forKey: "chinese"); localizeMenu() } }
    @Published var theme = UserDefaults.standard.integer(forKey: "theme") { didSet { UserDefaults.standard.set(theme, forKey: "theme"); appearance() } }
    var process: Process?
    func t(_ en: String, _ zh: String) -> String { chinese ? zh : en }
    func localizeMenu() {
        guard let menu = NSApp.mainMenu else { return }
        menu.item(withTag: 100)?.submenu?.items.first?.title = t("Quit LinkGet", "退出 LinkGet")
        let titles = chinese ? ["剪切", "复制", "粘贴", "全选"] : ["Cut", "Copy", "Paste", "Select All"]
        menu.item(withTag: 200)?.title = t("Edit", "编辑")
        menu.item(withTag: 200)?.submenu?.title = t("Edit", "编辑")
        for (index, title) in titles.enumerated() { menu.item(withTag: 200)?.submenu?.item(withTag: index + 1)?.title = title }
    }
    func appearance() { NSApp.appearance = theme == 2 ? nil : NSAppearance(named: theme == 1 ? .darkAqua : .aqua) }
    func executable() -> URL? {
        ["/opt/homebrew/bin/linkget", "/usr/local/bin/linkget"].first(where: { FileManager.default.isExecutableFile(atPath: $0) }).map { URL(fileURLWithPath: $0) }
    }
    func start() {
        guard !running else { return }
        let input = source.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { message = t("Enter a link or local file.", "请输入链接或本地文件。"); return }
        guard let cli = executable() else { message = t("Install the linkget command first.", "请先安装 linkget 命令。"); return }
        let parent = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0].appendingPathComponent("linkget-" + (mode == 0 ? "read" : "downloads"))
        let destination = parent.appendingPathComponent(ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-") + "-" + UUID().uuidString.prefix(8))
        let job = Process(), pipe = Pipe()
        job.executableURL = cli
        job.arguments = mode == 0 ? ["read", input, "--folder", destination.path] : [input, "--folder", destination.path]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["LINKGET_GUI_JOB"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        job.environment = env
        job.standardOutput = pipe; job.standardError = pipe; job.standardInput = FileHandle.nullDevice
        log = ""; output = nil; running = true; message = t("Processing…", "正在处理…")
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            let text = String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async { self?.log.append(text); if let self, self.log.count > 50000 { self.log = String(self.log.suffix(50000)) } }
        }
        job.terminationHandler = { [weak self] task in
            DispatchQueue.main.async {
                guard let self else { return }
                self.running = false; self.process = nil
                self.message = task.terminationStatus == 0 ? self.t("Completed", "已完成") : task.terminationStatus == 130 || task.terminationReason == .uncaughtSignal ? self.t("Cancelled · read checkpoints retained", "已取消 · 读取断点已保留") : self.t("Stopped · see details", "已停止 · 请查看详情")
                if FileManager.default.fileExists(atPath: destination.path) { self.output = destination }
            }
        }
        do { try job.run(); process = job } catch { running = false; message = error.localizedDescription }
    }
    func cancel() {
        guard let task = process, task.isRunning else { return }
        // The CLI creates its own session before work, so cancellation includes media tools.
        if kill(-task.processIdentifier, SIGINT) != 0 { task.interrupt() }
    }
    func choose() {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url { source = url.path; mode = 0 }
    }
}

struct Action: View {
    var title: String; var symbol: String; var enabled = true; var perform: () -> Void
    @State private var hover = false
    var body: some View {
        Button(action: perform) { Label(title, systemImage: symbol).padding(.vertical, 3).padding(.horizontal, 5) }
            .buttonStyle(.bordered).disabled(!enabled)
            .background(RoundedRectangle(cornerRadius: 7).fill(hover && enabled ? Color.accentColor.opacity(0.18) : .clear))
            .onHover { hover = $0 }
    }
}
struct Panel: View {
    @ObservedObject var session: Session
    @State var details = false
    @State var hoverLanguage = false
    @State var hoverTheme = false
    @State var hoverMode = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("LinkGet").font(.title2.weight(.semibold))
                Spacer()
                Picker(session.t("Language", "语言"), selection: $session.chinese) { Text("English").tag(false); Text("中文").tag(true) }
                    .labelsHidden().frame(width: 104).background(hoverLanguage ? Color.accentColor.opacity(0.12) : .clear).cornerRadius(6).onHover { hoverLanguage = $0 }
                Picker(session.t("Appearance", "外观"), selection: $session.theme) {
                    Text(session.t("Light", "浅色")).tag(0); Text(session.t("Dark", "深色")).tag(1); Text(session.t("System", "系统")).tag(2)
                }.labelsHidden().frame(width: 100).background(hoverTheme ? Color.accentColor.opacity(0.12) : .clear).cornerRadius(6).onHover { hoverTheme = $0 }
            }
            TextField(session.t("Link or local media file", "链接或本地媒体文件"), text: $session.source)
                .textFieldStyle(.roundedBorder).disabled(session.running).onSubmit { session.start() }
            HStack {
                Picker(session.t("Action", "操作"), selection: $session.mode) {
                    Text(session.t("Read", "读取文字")).tag(0); Text(session.t("Download", "下载媒体")).tag(1)
                }.pickerStyle(.segmented).frame(width: 230).disabled(session.running)
                    .background(hoverMode && !session.running ? Color.accentColor.opacity(0.14) : .clear).cornerRadius(6).onHover { hoverMode = $0 }
                Spacer()
                Action(title: session.t("Choose file", "选择文件"), symbol: "folder", enabled: !session.running) { session.choose() }
            }
            HStack {
                Text(session.message).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                Spacer()
                if session.running { ProgressView().controlSize(.small); Action(title: session.t("Cancel", "取消"), symbol: "xmark") { session.cancel() } }
                else { Action(title: session.t("Start", "开始"), symbol: "play.fill") { session.start() }.keyboardShortcut(.return, modifiers: .command) }
            }
            HStack {
                Action(title: session.t("Details", "详情"), symbol: details ? "chevron.up" : "chevron.down") { details.toggle() }
                Spacer()
                Action(title: session.t("Open result", "打开结果"), symbol: "arrow.up.forward", enabled: session.output != nil) { if let url = session.output { NSWorkspace.shared.open(url) } }
            }
            if details {
                ScrollView { Text(session.log).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(10) }
                    .frame(height: 170).background(Color.secondary.opacity(0.07)).cornerRadius(8)
            }
        }.padding(22).frame(width: 560).fixedSize(horizontal: true, vertical: true)
    }
}
final class Delegate: NSObject, NSApplicationDelegate {
    let session = Session()
    var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        session.appearance()
        let menu = NSMenu(), appItem = NSMenuItem(), appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit LinkGet", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.tag = 100; appItem.submenu = appMenu; menu.addItem(appItem)
        let editItem = NSMenuItem(), edit = NSMenu(title: "Edit")
        for (title, action, key) in [("Cut", #selector(NSText.cut(_:)), "x"), ("Copy", #selector(NSText.copy(_:)), "c"), ("Paste", #selector(NSText.paste(_:)), "v"), ("Select All", #selector(NSText.selectAll(_:)), "a")] { let item = edit.addItem(withTitle: title, action: action, keyEquivalent: key); item.tag = edit.items.count }
        editItem.tag = 200; editItem.submenu = edit; menu.addItem(editItem); NSApp.mainMenu = menu; session.localizeMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 604, height: 300), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "LinkGet"; window.contentView = NSHostingView(rootView: Panel(session: session)); window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.servicesProvider = self; NSUpdateDynamicServices(); NSApp.activate(ignoringOtherApps: true)
    }
    func application(_ app: NSApplication, open urls: [URL]) {
        guard let url = urls.first else { return }
        if url.scheme == "linkget", let components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            let items = components.queryItems ?? []
            if let value = items.first(where: { $0.name == "url" })?.value { session.source = URL(string: value)?.isFileURL == true ? ((NSURL(string: value)?.filePathURL) ?? URL(string: value)!).path : value }
            session.mode = items.first(where: { $0.name == "mode" })?.value == "download" ? 1 : 0
        } else { session.source = url.isFileURL ? url.path : url.absoluteString; session.mode = 0 }
        window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }
    @objc func readSelection(_ pasteboard: NSPasteboard, userData: String?, error: AutoreleasingUnsafeMutablePointer<NSString>) {
        session.source = pasteboard.string(forType: .URL) ?? pasteboard.string(forType: .string) ?? ""
        session.mode = userData == "download" ? 1 : 0
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { session.cancel(); return .terminateNow }
    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows: Bool) -> Bool { window.makeKeyAndOrderFront(nil); return true }
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
