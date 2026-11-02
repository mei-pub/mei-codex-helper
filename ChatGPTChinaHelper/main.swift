import AppKit

// ChatGPT China Helper — 菜单栏控制器
// 所有重活由 Resources/engine.sh 完成, 本程序只做菜单与状态刷新

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var timer: Timer?
    let menu = NSMenu()
    var enginePath: String {
        let r = Bundle.main.resourcePath ?? "/Applications/ChatGPT China Helper.app/Contents/Resources"
        return r + "/engine.sh"
    }

    // 状态行
    let stItems = [
        NSMenuItem(title: "Relay(HTTPS): …", action: nil, keyEquivalent: ""),
        NSMenuItem(title: "CONNECT代理: …", action: nil, keyEquivalent: ""),
        NSMenuItem(title: "Clash SOCKS: …", action: nil, keyEquivalent: ""),
        NSMenuItem(title: "ChatGPT: …", action: nil, keyEquivalent: ""),
        NSMenuItem(title: "远程控制Daemon: …", action: nil, keyEquivalent: ""),
    ]

    func run(_ args: [String], completion: ((String) -> Void)? = nil) {
        DispatchQueue.global().async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = [self.enginePath] + args
            p.environment = ProcessInfo.processInfo.environment
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            try? p.run()
            p.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let out = String(data: data, encoding: .utf8) ?? ""
            DispatchQueue.main.async { completion?(out) }
        }
    }

    func confirm(_ text: String, button: String) -> Bool {
        let a = NSAlert()
        a.messageText = text
        a.addButton(withTitle: button)
        a.addButton(withTitle: "取消")
        return a.runModal() == .alertFirstButtonReturn
    }

    @objc func doInstall() {
        guard confirm("将安装/修复增强组件:\n• 生成并信任本地证书(可能弹出钥匙串授权)\n• 配置 ~/.codex 的 base_url 与 .env(自动备份)\n• 部署并启动三个后台服务\n继续?", button: "安装") else { return }
        run(["install"]) { _ in DispatchQueue.main.async { self.refreshStatus(nil) } }
    }

    @objc func doUninstall() {
        guard confirm("卸载将:\n• 停止并移除后台服务\n• 还原 ~/.codex/.env(备份)\n• 移除 base_url 配置\n\n证书与日志会保留。继续?", button: "卸载") else { return }
        run(["uninstall", "--yes"]) { _ in DispatchQueue.main.async { self.refreshStatus(nil) } }
    }

    @objc func doLaunch() { run(["launch-chatgpt"]) }
    @objc func doStart() { run(["restart"]) { _ in DispatchQueue.main.async { self.refreshStatus(nil) } } }
    @objc func doStop() {
        guard confirm("停止全部增强服务? ChatGPT 将无法通过代理访问。", button: "停止") else { return }
        run(["stop"]) { _ in DispatchQueue.main.async { self.refreshStatus(nil) } }
    }
    @objc func doOpenConfig() { run(["open-config"]) }
    @objc func doOpenLogs() { run(["open-logs"]) }
    @objc func doDoctor() { run(["doctor"]) }

    @objc func refreshStatus(_: Any?) {
        run(["status"]) { out in
            let lines = out.split(separator: "\n").map(String.init)
            for (i, item) in self.stItems.enumerated() {
                if i < lines.count {
                    let l = lines[i]
                    item.title = l.contains("●") ? l.replacingOccurrences(of: "●", with: "🟢") : l.replacingOccurrences(of: "○", with: "⚪️")
                }
            }
        }
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = statusItem.button {
            b.image = NSImage(systemSymbolName: "shield.lefthalf.filled", accessibilityDescription: "CN Helper")
        }
        buildMenu()
        statusItem.menu = menu
        refreshStatus(nil)
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.refreshStatus(nil)
        }
    }

    func buildMenu() {
        let title = NSMenuItem(title: "ChatGPT 中国访问增强", action: nil, keyEquivalent: "")
        title.attributedTitle = NSAttributedString(string: "ChatGPT 中国访问增强", attributes: [.font: NSFont.boldSystemFont(ofSize: 12)])
        menu.addItem(title)
        for it in stItems { menu.addItem(it) }
        menu.addItem(.separator())
        menu.addItem(withTitle: "🚀 启动 ChatGPT(增强模式)", action: #selector(doLaunch), keyEquivalent: "l")
        menu.addItem(withTitle: "🔄 重启增强服务", action: #selector(doStart), keyEquivalent: "r")
        menu.addItem(withTitle: "⏸ 停止增强服务…", action: #selector(doStop), keyEquivalent: "s")
        menu.addItem(.separator())
        menu.addItem(withTitle: "🧰 安装 / 修复增强组件…", action: #selector(doInstall), keyEquivalent: "i")
        menu.addItem(withTitle: "🔧 诊断 (Doctor)", action: #selector(doDoctor), keyEquivalent: "d")
        menu.addItem(withTitle: "📝 打开配置文件", action: #selector(doOpenConfig), keyEquivalent: ",")
        menu.addItem(withTitle: "📄 打开日志", action: #selector(doOpenLogs), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "🗑 卸载…", action: #selector(doUninstall), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        for item in menu.items { item.target = item.action != nil ? self : nil }
    }
}

let app = NSApplication.shared
let dlg = AppDelegate()
app.delegate = dlg
app.setActivationPolicy(.accessory)
app.run()
