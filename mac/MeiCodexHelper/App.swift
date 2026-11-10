import SwiftUI
import AppKit

// MARK: - 引擎桥接

enum Engine {
    static var path: String {
        (Bundle.main.resourcePath ?? "/Applications/MeiCodexHelper.app/Contents/Resources") + "/engine.sh"
    }
    @discardableResult
    static func run(_ args: [String]) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [path] + args
        p.environment = ProcessInfo.processInfo.environment
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        try? p.run()
        p.waitUntilExit()
        return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }
    static func runAsync(_ args: [String], done: @escaping (String) -> Void) {
        DispatchQueue.global().async {
            let out = run(args)
            DispatchQueue.main.async { done(out) }
        }
    }
}

// MARK: - 状态模型

struct SvcState: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let state: String   // ok / down
    var isOK: Bool { state == "ok" }
}

final class StatusStore: ObservableObject {
    @Published var services: [SvcState] = []
    @Published var busy = false
    @Published var doctorOutput: String?
    @Published var lastAction: String?

    var okCount: Int { services.filter(\.isOK).count }
    var allOK: Bool { !services.isEmpty && okCount == services.count }

    init() { refresh() }

    func refresh() {
        Engine.runAsync(["status-json"]) { out in
            let dec = JSONDecoder()
            if let data = out.data(using: .utf8),
               let list = try? dec.decode([SvcState].self, from: data) {
                self.services = list
            }
        }
    }

    func act(_ args: [String], label: String) {
        busy = true
        Engine.runAsync(args) { [weak self] out in
            self?.busy = false
            let last = out.split(separator: "\n").last.map(String.init) ?? ""
            self?.lastAction = "\(label): \(last)"
            self?.refresh()
        }
    }

    static func icon(for id: String) -> String {
        switch id {
        case "relay": return "arrow.triangle.2.circlepath.circle.fill"
        case "relayplain": return "bolt.horizontal.circle.fill"
        case "connect": return "network.circle.fill"
        case "proxy": return "globe.asia.southeast.fill"
        case "chatgpt": return "bubble.left.and.text.bubble.right.fill"
        case "daemon": return "desktopcomputer.and.arrow.down"
        default: return "circle.fill"
        }
    }
    static func restartTarget(for id: String) -> String? {
        switch id {
        case "relay", "relayplain": return "relay"
        case "connect": return "tlsproxy"
        case "daemon": return "daemon"
        default: return nil
        }
    }
}

// MARK: - 配置模型 (多配置)

struct Profile: Codable, Identifiable, Equatable {
    var id: String { name }
    var name: String
    var proxy_socks_host: String
    var proxy_socks_port: Int
    var relay_tls_port: Int
    var relay_plain_port: Int
    var connect_proxy_port: Int
    var impersonate: String
    var upstream_host: String
    var remote_control: Bool
    var watch_interval_sec: Int

    static func blank(name: String) -> Profile {
        Profile(name: name, proxy_socks_host: "127.0.0.1", proxy_socks_port: 7890,
                relay_tls_port: 8943, relay_plain_port: 8945, connect_proxy_port: 8944,
                impersonate: "safari17_0", upstream_host: "chatgpt.com",
                remote_control: true, watch_interval_sec: 60)
    }
    static func fromConfig(_ name: String) -> Profile {
        var p = Profile.blank(name: name)
        let json = (try? String(contentsOfFile: ProfileStore.configPath, encoding: .utf8)) ?? ""
        if let data = json.data(using: .utf8), let d = try? JSONDecoder().decode([String: JSONValue].self, from: data) {
            if let v = d["proxy_socks_host"]?.stringValue ?? d["clash_socks_host"]?.stringValue { p.proxy_socks_host = v }
            if let v = d["proxy_socks_port"]?.intValue ?? d["clash_socks_port"]?.intValue { p.proxy_socks_port = v }
            if let v = d["relay_tls_port"]?.intValue { p.relay_tls_port = v }
            if let v = d["relay_plain_port"]?.intValue { p.relay_plain_port = v }
            if let v = d["connect_proxy_port"]?.intValue { p.connect_proxy_port = v }
            if let v = d["impersonate"]?.stringValue { p.impersonate = v }
            if let v = d["upstream_host"]?.stringValue { p.upstream_host = v }
            if let v = d["remote_control"]?.boolValue { p.remote_control = v }
            if let v = d["watch_interval_sec"]?.intValue { p.watch_interval_sec = v }
        }
        return p
    }
}

enum JSONValue: Codable {
    case string(String), int(Int), bool(Bool), other
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let v = try? c.decode(Bool.self) { self = .bool(v); return }
        if let v = try? c.decode(Int.self) { self = .int(v); return }
        if let v = try? c.decode(String.self) { self = .string(v); return }
        self = .other
    }
    func encode(to encoder: Encoder) throws {}
    var stringValue: String? { if case .string(let v) = self { return v }; return nil }
    var intValue: Int? { if case .int(let v) = self { return v }; return nil }
    var boolValue: Bool? { if case .bool(let v) = self { return v }; return nil }
}

final class ProfileStore: ObservableObject {
    static var dir: String {
        NSHomeDirectory() + "/Library/Application Support/MeiCodexHelper/profiles"
    }
    static var configPath: String {
        NSHomeDirectory() + "/Library/Application Support/MeiCodexHelper/config.json"
    }
    static var activePath: String {
        NSHomeDirectory() + "/Library/Application Support/MeiCodexHelper/active_profile"
    }

    @Published var profiles: [Profile] = []
    @Published var active: String = ""
    @Published var applying = false
    @Published var message: String?

    init() { reload() }

    func reload() {
        let fm = FileManager.default
        try? fm.createDirectory(atPath: Self.dir, withIntermediateDirectories: true)
        var list: [Profile] = []
        if let files = try? fm.contentsOfDirectory(atPath: Self.dir) {
            for f in files.filter({ $0.hasSuffix(".json") }).sorted() {
                if let data = fm.contents(atPath: Self.dir + "/" + f),
                   let p = try? JSONDecoder().decode(Profile.self, from: data) {
                    list.append(p)
                }
            }
        }
        if list.isEmpty {
            let seed = Profile.fromConfig("默认")
            list.append(seed)
            save(seed)
        }
        profiles = list
        active = (try? String(contentsOfFile: Self.activePath, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? list[0].name
    }

    private func file(for p: Profile) -> String { Self.dir + "/" + slug(p.name) + ".json" }

    func slug(_ s: String) -> String {
        s.replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: ":", with: "_")
    }

    func save(_ p: Profile) {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? enc.encode(p) {
            try? data.write(to: URL(fileURLWithPath: file(for: p)))
        }
    }

    func delete(_ p: Profile) {
        guard profiles.count > 1 else { return }
        try? FileManager.default.removeItem(atPath: file(for: p))
        if active == p.name { activate(profiles.first { $0.id != p.id }!, restart: false) }
        reload()
    }

    func isActive(_ p: Profile) -> Bool { p.name == active }

    /// 启用配置: 写入 config.json, (可选)重启服务
    func activate(_ p: Profile, restart: Bool = true) {
        applying = true
        message = nil
        DispatchQueue.global().async {
            let enc = JSONEncoder()
            enc.outputFormatting = [.prettyPrinted, .sortedKeys]
            if let data = try? enc.encode(p) {
                try? data.write(to: URL(fileURLWithPath: Self.configPath))
            }
            try? p.name.write(toFile: Self.activePath, atomically: true, encoding: .utf8)
            if restart { _ = Engine.run(["restart"]) }
            DispatchQueue.main.async {
                self.applying = false
                self.message = restart ? "已启用「\(p.name)」并重启服务" : "已启用「\(p.name)」"
                self.reload()
            }
        }
    }
}

// MARK: - 应用入口

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }
}

enum MainTab: String { case console, settings }

final class TabRouter: ObservableObject {
    @Published var tab: MainTab = .console
}

@main
struct MeiCodexHelperApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var dlg
    @StateObject private var status = StatusStore()
    @StateObject private var router = TabRouter()

    var body: some Scene {
        MenuBarExtra {
            MenuContent(status: status)
                .environmentObject(ProfileStore())
                .environmentObject(router)
        } label: {
            Image(systemName: status.allOK ? "checkmark.shield.fill" : "exclamationmark.shield.fill")
        }
        .menuBarExtraStyle(.window)

        Window("MeiCodexHelper", id: "main") {
            MainTabView().environmentObject(status).environmentObject(router)
        }
    }
}

struct MainTabView: View {
    @EnvironmentObject var router: TabRouter
    @EnvironmentObject var status: StatusStore

    var body: some View {
        TabView(selection: $router.tab) {
            ConsoleView().tabItem { Label("控制台", systemImage: "square.grid.2x2") }.tag(MainTab.console)
            SettingsView().tabItem { Label("设置", systemImage: "gearshape") }.tag(MainTab.settings)
        }
        .frame(minWidth: 760, minHeight: 560)
    }
}

func activateApp() {
    NSApp.activate(ignoringOtherApps: true)
}

// MARK: - 菜单栏弹出面板 (.window 样式, 完全自定义布局)

struct MenuContent: View {
    @ObservedObject var status: StatusStore
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject var router: TabRouter

    var body: some View {
        VStack(spacing: 10) {
            // 头部: 整体状态
            HStack(spacing: 8) {
                Image(systemName: status.allOK ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(status.allOK ? .green : .orange)
                    .font(.system(size: 15))
                VStack(alignment: .leading, spacing: 1) {
                    Text("MeiCodexHelper").font(.system(size: 12, weight: .bold))
                    Text(status.allOK ? "全部服务正常" : "异常 \(status.services.count - status.okCount) 项")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
                Spacer()
                if status.busy { ProgressView().controlSize(.mini) }
            }

            // 服务状态列表(紧凑两列: 图标+名称 | 状态点)
            VStack(spacing: 4) {
                ForEach(status.services) { s in
                    HStack(spacing: 8) {
                        Image(systemName: StatusStore.icon(for: s.id))
                            .font(.system(size: 12))
                            .foregroundStyle(s.isOK ? .primary : .secondary)
                            .frame(width: 16)
                        Text(s.name).font(.system(size: 11)).lineLimit(1)
                        Spacer()
                        Text(s.isOK ? "正常" : "停止")
                            .font(.system(size: 10))
                            .foregroundStyle(s.isOK ? .green : .red)
                        Circle().fill(s.isOK ? Color.green : Color.red).frame(width: 7, height: 7)
                    }
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
                }
            }

            Divider()

            // 快捷操作 (2×2 网格)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 6) {
                panelButton("启动 ChatGPT", "paperplane.fill", prominent: true) {
                    status.act(["launch-chatgpt"], label: "启动 ChatGPT")
                }
                panelButton("重启服务", "arrow.triangle.2.circlepath") {
                    status.act(["restart"], label: "重启服务")
                }
                panelButton("控制台", "square.grid.2x2") {
                    router.tab = .console; dismiss(); openWindow(id: "main"); activateApp()
                }
                panelButton("设置", "gearshape") {
                    router.tab = .settings; dismiss(); openWindow(id: "main"); activateApp()
                }
            }

            Divider()

            // 底部行
            HStack {
                panelButton("诊断", "stethoscope") {
                    status.act(["doctor"], label: "诊断")
                }
                panelButton("日志", "folder") {
                    Engine.run(["open-logs"])
                }
                Spacer()
                Button {
                    NSApp.terminate(nil)
                } label: {
                    Image(systemName: "power")
                        .font(.system(size: 12))
                        .padding(5)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                }
                .buttonStyle(.plain)
                .help("退出 MeiCodexHelper")
            }
        }
        .padding(12)
        .frame(width: 290)
        .onAppear { status.refresh() }
    }

    private func panelButton(_ title: String, _ icon: String, prominent: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: icon).font(.system(size: 11))
                Text(title).font(.system(size: 11, weight: .medium))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
            .background(prominent ? AnyShapeStyle(Color.accentColor.opacity(0.18)) : AnyShapeStyle(.quaternary),
                        in: RoundedRectangle(cornerRadius: 7))
        }
        .buttonStyle(.plain)
        .foregroundStyle(prominent ? Color.accentColor : .primary)
    }
}

// MARK: - 控制台

struct ConsoleView: View {
    @EnvironmentObject var status: StatusStore
    @EnvironmentObject var router: TabRouter
    @State private var logSource = "relay"
    @State private var logText = ""
    @State private var autoRefresh = true
    @State private var showStopConfirm = false
    @State private var showUninstallConfirm = false
    @State private var logTimer: Timer?

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            serviceGrid.padding(12)
            Divider()
            logSection
        }
        .frame(minWidth: 680, minHeight: 560)
        .onAppear { loadLog(); startTimer() }
        .onDisappear { logTimer?.invalidate() }
        .confirmationDialog("停止全部增强服务？ChatGPT 将无法经代理访问。", isPresented: $showStopConfirm, titleVisibility: .visible) {
            Button("停止服务", role: .destructive) { status.act(["stop"], label: "停止") }
        }
        .confirmationDialog("卸载将停止服务、还原 ~/.codex 配置备份。证书与日志保留。", isPresented: $showUninstallConfirm, titleVisibility: .visible) {
            Button("卸载", role: .destructive) { status.act(["uninstall", "--yes"], label: "卸载") }
        }
        .sheet(isPresented: Binding(get: { status.doctorOutput != nil }, set: { if !$0 { status.doctorOutput = nil } })) {
            doctorSheet
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.shield.fill")
                .font(.system(size: 26))
                .foregroundStyle(.linearGradient(colors: [.purple, .blue], startPoint: .topLeading, endPoint: .bottomTrailing))
            VStack(alignment: .leading, spacing: 2) {
                Text("MeiCodexHelper 控制台").font(.title3.bold())
                Text(status.allOK ? "所有组件运行正常" : "有组件异常, 可点击卡片上的重启按钮修复")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if status.busy { ProgressView().controlSize(.small) }
            Button { status.act(["launch-chatgpt"], label: "启动 ChatGPT") } label: {
                Label("启动 ChatGPT", systemImage: "rocket")
            }
            .buttonStyle(.borderedProminent).tint(.blue)
            Button { status.act(["restart"], label: "重启服务") } label: {
                Label("重启", systemImage: "arrow.triangle.2.circlepath")
            }.buttonStyle(.bordered)
            Button { router.tab = .settings } label: {
                Image(systemName: "gearshape")
            }.buttonStyle(.bordered).help("设置")
            Menu {
                Button(role: .destructive) { showStopConfirm = true } label: { Label("停止服务…", systemImage: "stop.circle") }
                Button { status.doctorOutput = nil; status.busy = true
                    Engine.runAsync(["doctor"]) { status.doctorOutput = $0; status.busy = false; status.refresh() }
                } label: { Label("运行诊断", systemImage: "stethoscope") }
                Button { Engine.run(["open-logs"]) } label: { Label("日志文件夹", systemImage: "folder") }
                Button(role: .destructive) { showUninstallConfirm = true } label: { Label("卸载…", systemImage: "trash") }
            } label: {
                Image(systemName: "ellipsis.circle")
            }.menuStyle(.borderlessButton).fixedSize()
        }
        .padding(12)
    }

    private var serviceGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
            ForEach(status.services) { s in
                ServiceCard(svc: s) { target in
                    status.act(["restart-service", target], label: "重启 \(s.name)")
                } onLaunch: {
                    status.act(["launch-chatgpt"], label: "启动 ChatGPT")
                }
            }
        }
    }

    private var logSection: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("", selection: $logSource) {
                    Text("中继日志").tag("relay")
                    Text("代理日志").tag("tlsproxy")
                }
                .pickerStyle(.segmented).frame(width: 200)
                Toggle("自动刷新", isOn: $autoRefresh).controlSize(.mini)
                Spacer()
                Button { loadLog() } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.borderless)
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
            ScrollViewReader { proxy in
                ScrollView {
                    Text(logText.isEmpty ? "暂无日志" : logText)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Color(red: 0.75, green: 0.92, blue: 0.78))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .id("logtail")
                }
                .background(Color(red: 0.08, green: 0.10, blue: 0.08))
                .onAppear { proxy.scrollTo("logtail", anchor: .bottom) }
            }
        }
        .frame(height: 220)
        .onChange(of: logSource) { _ in loadLog() }
    }

    private var doctorSheet: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("诊断报告", systemImage: "stethoscope").font(.headline)
                Spacer()
                Button("拷贝") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(status.doctorOutput ?? "", forType: .string) }
                Button("完成") { status.doctorOutput = nil }
            }
            ScrollView {
                Text(status.doctorOutput ?? "")
                    .font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(16).frame(width: 560, height: 420)
    }

    private func loadLog() {
        Engine.runAsync(["log", logSource, "300"]) { logText = $0 }
    }
    private func startTimer() {
        logTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in
            DispatchQueue.main.async {
                status.refresh()
                if autoRefresh { loadLog() }
            }
        }
    }
}

struct ServiceCard: View {
    let svc: SvcState
    let onRestart: (String) -> Void
    let onLaunch: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                Circle().fill(svc.isOK ? Color.green.opacity(0.15) : Color.red.opacity(0.15))
                    .frame(width: 38, height: 38)
                Image(systemName: StatusStore.icon(for: svc.id))
                    .font(.system(size: 17))
                    .foregroundStyle(svc.isOK ? .green : .red)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(svc.name).font(.system(size: 12, weight: .medium)).lineLimit(1)
                Text(svc.isOK ? "运行中" : "已停止")
                    .font(.caption2).foregroundStyle(svc.isOK ? .green : .red)
            }
            Spacer()
            action
        }
        .padding(10)
        .background(.background.secondary)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder
    private var action: some View {
        if let target = StatusStore.restartTarget(for: svc.id) {
            Button { onRestart(target) } label: {
                Image(systemName: "arrow.clockwise.circle")
            }.buttonStyle(.borderless).help("重启该服务")
        } else if svc.id == "proxy" {
            Image(systemName: "info.circle").foregroundStyle(.secondary).help("由你的代理环境提供 (Clash/Surge/任意 SOCKS 均可), 请确保其运行")
        } else if svc.id == "chatgpt" && !svc.isOK {
            Button { onLaunch() } label: { Image(systemName: "play.circle") }.buttonStyle(.borderless).help("带增强参数启动")
        }
    }
}

// MARK: - 设置

struct SettingsView: View {
    @EnvironmentObject private var status: StatusStore
    @EnvironmentObject var router: TabRouter
    @StateObject private var store = ProfileStore()
    @State private var selection: String?
    @State private var draft: Profile?
    @State private var newProfileName = ""
    @State private var showNew = false

    var body: some View {
        NavigationSplitView {
            sidebar
        } detail: {
            if let p = bindingDraft {
                ProfileEditor(profile: p, store: store)
            } else {
                Text("选择或新建一个配置").foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 720, minHeight: 480)
        .onAppear { if selection == nil { selection = store.active } }
        .alert("新建配置", isPresented: $showNew) {
            TextField("名称", text: $newProfileName)
            Button("创建") {
                let name = newProfileName.trimmingCharacters(in: .whitespaces)
                guard !name.isEmpty else { return }
                let p = Profile.blank(name: name)
                store.save(p); store.reload(); selection = name; draft = p; newProfileName = ""
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("基于默认值创建, 可复制现有配置后修改")
        }
        .environmentObject(status)
    }

    private var bindingDraft: Binding<Profile>? {
        guard let sel = selection, let idx = store.profiles.firstIndex(where: { $0.name == sel }) else { return nil }
        return Binding(get: { draft ?? store.profiles[idx] }, set: { draft = $0 })
    }

    private var sidebar: some View {
        List(selection: $selection) {
            Section("配置方案") {
                ForEach(store.profiles) { p in
                    HStack {
                        if store.isActive(p) { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green) }
                        Text(p.name).bold(store.isActive(p))
                    }
                    .tag(p.name)
                    .contextMenu {
                        Button { store.activate(p) } label: { Label("启用此配置", systemImage: "checkmark.circle") }
                        Button {
                            let copy = p; var c = copy; c.name = "\(p.name) 副本"
                            store.save(c); store.reload(); selection = c.name
                        } label: { Label("复制", systemImage: "doc.on.doc") }
                        Button(role: .destructive) { store.delete(p) } label: { Label("删除", systemImage: "trash") }
                            .disabled(store.profiles.count <= 1)
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .frame(width: 210)
        .toolbar {
            ToolbarItemGroup {
                Button { showNew = true } label: { Image(systemName: "plus") }.help("新建配置")
                Button {
                    guard let sel = selection, let p = store.profiles.first(where: { $0.name == sel }) else { return }
                    var c = p; c.name = "\(p.name) 副本"
                    store.save(c); store.reload(); selection = c.name
                } label: { Image(systemName: "doc.on.doc") }.help("复制当前配置")
                Button(role: .destructive) {
                    guard let sel = selection, let p = store.profiles.first(where: { $0.name == sel }) else { return }
                    store.delete(p); selection = store.profiles.first?.name
                } label: { Image(systemName: "trash") }.help("删除配置")
                .disabled(store.profiles.count <= 1)
            }
        }
    }
}

struct ProfileEditor: View {
    @Binding var profile: Profile
    @ObservedObject var store: ProfileStore

    var body: some View {
        Form {
            Section("基本") {
                LabeledRow(label: "配置名称", icon: "tag") {
                    TextField("", text: $profile.name)
                }
            }
            Section("上游出口") {
                LabeledRow(label: "代理主机", icon: "server.rack") {
                    TextField("", text: $profile.proxy_socks_host)
                }
                LabeledRow(label: "上游 SOCKS 端口", icon: "globe") {
                    PortField(value: $profile.proxy_socks_port)
                }
            }
            Section("本地服务端口") {
                LabeledRow(label: "中继 HTTPS 端口", icon: "lock.shield") {
                    PortField(value: $profile.relay_tls_port)
                }
                LabeledRow(label: "中继明文端口", icon: "bolt") {
                    PortField(value: $profile.relay_plain_port)
                }
                LabeledRow(label: "CONNECT 代理端口", icon: "network") {
                    PortField(value: $profile.connect_proxy_port)
                }
            }
            Section("高级") {
                LabeledRow(label: "TLS 指纹", icon: "person.crop.square.filled.and.at.rectangle") {
                    Picker("", selection: $profile.impersonate) {
                        Text("Safari 17 (推荐)").tag("safari17_0")
                        Text("Safari 18").tag("safari18_0")
                        Text("Edge 101").tag("edge101")
                    }.pickerStyle(.menu)
                }
                LabeledRow(label: "上游域名", icon: "link") {
                    TextField("", text: $profile.upstream_host)
                }
                LabeledRow(label: "远程控制支持", icon: "desktopcomputer.and.arrow.down") {
                    Toggle("", isOn: $profile.remote_control)
                }
                LabeledRow(label: "看门狗周期(秒)", icon: "clock.arrow.circlepath") {
                    Stepper(value: $profile.watch_interval_sec, in: 15...600, step: 15) {
                        Text("\(profile.watch_interval_sec) s")
                    }
                }
            }
        }
        .formStyle(.grouped)
        .safeAreaInset(edge: .bottom) {
            HStack {
                if store.applying { ProgressView().controlSize(.small); Text("应用中…") }
                if let msg = store.message {
                    Label(msg, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                }
                Spacer()
                Button("保存") { store.save(profile); store.reload() }
                    .disabled(profile.name.isEmpty)
                Button("保存并启用") { store.save(profile); store.activate(profile) }
                    .buttonStyle(.borderedProminent)
                    .disabled(profile.name.isEmpty)
            }
            .padding(12)
            .background(.bar)
        }
    }
}

struct LabeledRow<Content: View>: View {
    let label: String
    let icon: String
    @ViewBuilder var content: Content
    var body: some View {
        HStack {
            Label(label, systemImage: icon).frame(width: 190, alignment: .leading)
            content
        }
    }
}

struct PortField: View {
    @Binding var value: Int
    @State private var text: String = ""
    var body: some View {
        TextField("", text: $text, onEditingChanged: { _ in
            if let n = Int(text), (1...65535).contains(n) { value = n }
        })
        .textFieldStyle(.roundedBorder)
        .frame(width: 110)
        .onAppear { text = String(value) }
    }
}
