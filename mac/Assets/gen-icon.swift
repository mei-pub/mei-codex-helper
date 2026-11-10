import AppKit

// 生成 MeiCodexHelper 图标: 蓝紫渐变圆角矩形 + 白色盾牌勾 + 闪电 (离屏渲染)
func render(_ s: CGFloat) -> NSBitmapImageRep? {
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(s), pixelsHigh: Int(s),
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return nil }
    rep.size = NSSize(width: s, height: s)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let rect = NSRect(x: 0, y: 0, width: s, height: s)
    if let gradient = NSGradient(colors: [NSColor(calibratedRed: 0.32, green: 0.20, blue: 0.96, alpha: 1),
                                           NSColor(calibratedRed: 0.08, green: 0.47, blue: 0.99, alpha: 1)]) {
        let path = NSBezierPath(roundedRect: rect, xRadius: s * 0.22, yRadius: s * 0.22)
        path.addClip()
        gradient.draw(in: rect, angle: -70)
    }
    if let shield = NSImage(systemSymbolName: "checkmark.shield.fill", accessibilityDescription: nil)?
        .withSymbolConfiguration(.init(pointSize: s * 0.52, weight: .bold)) {
        shield.draw(in: NSRect(x: (s - shield.size.width) / 2, y: (s - shield.size.height) / 2 + s * 0.02,
                               width: shield.size.width, height: shield.size.height))
    }
    if let bolt = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: nil)?
        .withSymbolConfiguration(.init(pointSize: s * 0.24, weight: .bold)) {
        bolt.draw(in: NSRect(x: s * 0.58, y: s * 0.10, width: bolt.size.width, height: bolt.size.height))
    }
    NSGraphicsContext.restoreGraphicsState()
    return rep
}

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."
let mapping: [(String, CGFloat)] = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]
for (name, s) in mapping {
    if let rep = render(s), let png = rep.representation(using: .png, properties: [:]) {
        try! png.write(to: URL(fileURLWithPath: outDir + "/" + name))
    }
}
print("icons done: \(mapping.count) files")
