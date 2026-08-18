import AppKit
import ApplicationServices
import AudioToolbox
import AudioUnit
import CoreAudio
import CoreBluetooth
import Darwin
import Foundation

private let serviceUUID = CBUUID(string: "A1C8E240-6F31-4B2A-9C11-0D8F1A7C0020")
private let snapshotUUID = CBUUID(string: "A1C8E240-6F31-4B2A-9C11-0D8F1A7C0021")
private let eventUUID = CBUUID(string: "A1C8E240-6F31-4B2A-9C11-0D8F1A7C0022")
private let audioUUID = CBUUID(string: "A1C8E240-6F31-4B2A-9C11-0D8F1A7C0023")
private let hidUUID = CBUUID(string: "1812")

private let stateDir = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".grok/command-watch")
private let rosterURL = stateDir.appendingPathComponent("roster.json")
private let deviceURL = stateDir.appendingPathComponent("device.json")
private let slotOrder = ["1L", "1R", "2L", "2R", "3L", "3R", "4L", "4R"]

private enum SlotState: Int {
    case empty = 0, idle = 1, running = 2, needsYou = 3, complete = 4, error = 5, loop = 6

    init(name: String) {
        switch name {
        case "idle": self = .idle
        case "running": self = .running
        case "needs_you": self = .needsYou
        case "complete": self = .complete
        case "error": self = .error
        case "loop": self = .loop
        default: self = .empty
        }
    }
}

private struct DeviceBind: Codable {
    var uuid: String
}

private final class UsbEvents {
    private var fd: Int32 = -1
    private var source: DispatchSourceRead?
    private var buffer = Data()
    private var lastOpenAttempt: TimeInterval = 0
    private var openedPath = ""
    var onLine: ((String) -> Void)?

    var isOpen: Bool { fd >= 0 }

    func tick() {
        let path = Self.portPath()
        if fd >= 0 {
            if path != openedPath || path == nil {
                Companion.log("usb port changed, reopen")
                close()
            } else {
                return
            }
        }
        let now = Date().timeIntervalSince1970
        if now - lastOpenAttempt < 1.5 { return }
        lastOpenAttempt = now
        openIfNeeded()
    }

    func close() {
        source?.cancel()
        source = nil
        if fd >= 0 {
            _ = Darwin.close(fd)
            fd = -1
        }
        openedPath = ""
    }

    func writeLine(_ line: String) {
        guard fd >= 0 else { return }
        var text = line
        if !text.hasSuffix("\n") { text.append("\n") }
        let bytes = Array(text.utf8)
        let n = bytes.withUnsafeBufferPointer { ptr in
            Darwin.write(fd, ptr.baseAddress, ptr.count)
        }
        if n < 0 {
            Companion.log("usb write fail errno=\(errno)")
            close()
        }
    }

    private static func portPath() -> String? {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: "/dev")) ?? []
        guard let name = names.filter({ $0.hasPrefix("cu.usbmodem") && !$0.contains("SN234567892") }).sorted().first else {
            return nil
        }
        return "/dev/\(name)"
    }

    private func openIfNeeded() {
        guard let path = Self.portPath() else { return }
        let opened = Darwin.open(path, O_RDWR | O_NOCTTY | O_NONBLOCK)
        guard opened >= 0 else { return }
        var term = termios()
        if tcgetattr(opened, &term) == 0 {
            cfmakeraw(&term)
            term.c_cflag |= tcflag_t(CLOCAL | CREAD)
            term.c_cflag &= ~tcflag_t(HUPCL)
            cfsetspeed(&term, speed_t(B115200))
            _ = tcsetattr(opened, TCSANOW, &term)
        }
        fd = opened
        openedPath = path
        let src = DispatchSource.makeReadSource(fileDescriptor: opened, queue: .main)
        src.setEventHandler { [weak self] in self?.drain() }
        src.setCancelHandler { [weak self] in
            if let self, self.fd >= 0 {
                _ = Darwin.close(self.fd)
                self.fd = -1
                self.openedPath = ""
            }
        }
        src.resume()
        source = src
        Companion.log("usb open \(path)")
    }

    private func drain() {
        guard fd >= 0 else { return }
        var tmp = [UInt8](repeating: 0, count: 1024)
        while true {
            let n = Darwin.read(fd, &tmp, tmp.count)
            if n < 0 {
                if errno == EAGAIN || errno == EWOULDBLOCK { break }
                Companion.log("usb read fail errno=\(errno)")
                close()
                return
            }
            if n == 0 { break }
            buffer.append(contentsOf: tmp.prefix(n))
        }
        while let nl = buffer.firstIndex(of: 0x0A) {
            let lineData = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            var line = String(data: lineData, encoding: .utf8) ?? ""
            if line.hasSuffix("\r") { line.removeLast() }
            if !line.isEmpty { onLine?(line) }
        }
        if buffer.count > 8192 { buffer.removeAll(keepingCapacity: true) }
    }
}

private enum Keys {
    private static let sockPath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".grok/command-watch/keys.sock").path

    @discardableResult
    static func send(_ line: String) -> Bool {
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            Companion.log("keys socket create fail")
            return false
        }
        defer { _ = Darwin.close(fd) }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = sockPath.utf8CString
        if pathBytes.count > MemoryLayout.size(ofValue: addr.sun_path) {
            Companion.log("keys socket path too long")
            return false
        }
        withUnsafeMutableBytes(of: &addr.sun_path) { buf in
            pathBytes.withUnsafeBytes { src in
                buf.copyMemory(from: src)
            }
        }
        let ok = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sap in
                Darwin.connect(fd, sap, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard ok == 0 else {
            Companion.log("keys socket connect fail errno=\(errno)")
            return false
        }
        var text = line
        if !text.hasSuffix("\n") { text.append("\n") }
        let bytes = Array(text.utf8)
        let n = bytes.withUnsafeBufferPointer { Darwin.write(fd, $0.baseAddress, $0.count) }
        return n > 0
    }

    static func tab(_ slot: Int) {
        let file = loadPanesFile()
        let index = file.page * 10 + slot
        _ = send("focus \(index)")
        Companion.log("focus pane=\(index) page=\(file.page)")
    }

    static func page(_ delta: Int) {
        _ = send("page \(delta)")
        Companion.log("page delta=\(delta)")
    }

    private static var lastGoodPanes: (page: Int, pids: [Int], total: Int) = (0, [], 0)

    static func loadPanesFile() -> (page: Int, pids: [Int], total: Int) {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".grok/command-watch/panes.json")
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return lastGoodPanes
        }
        let page = (obj["page"] as? NSNumber)?.intValue ?? (obj["page"] as? Int) ?? 0
        let raw = obj["panes"] as? [[String: Any]] ?? []
        let pids = raw.compactMap { item -> Int? in
            if let n = item["pid"] as? Int { return n }
            return (item["pid"] as? NSNumber)?.intValue
        }
        lastGoodPanes = (max(0, page), pids, pids.count)
        return lastGoodPanes
    }

    static func shandianshuo() {
        _ = send("shandianshuo")
        Companion.log("shandianshuo")
    }

    static func enter() {
        _ = send("enter")
        Companion.log("send enter")
    }

    static func paste(_ text: String) {
        _ = send("paste \(text)")
        Companion.log("paste chars=\(text.count)")
    }
}

/// Pipes watch PCM into a virtual input so 闪电说 can hear the watch.
/// Keyboard RCmd is untouched and still uses DJI via system default.
private final class WatchVoiceSink {
    private var unit: AudioUnit?
    private var keyboardInput: AudioDeviceID = 0
    private var sinkID: AudioDeviceID = 0
    private var sinkUID = ""
    private var running = false
    private var startedAt: TimeInterval = 0
    private var pcm = Data()
    private var pushed = 0
    private let lock = NSLock()
    private var rate: Double = 48_000
    var ready: Bool { sinkID != 0 }
    var sessionAge: TimeInterval {
        running ? Date().timeIntervalSince1970 - startedAt : 999
    }

    init() {
        if let found = Self.findDevice(["BlackHole", "Loopback", "VB-Cable", "CABLE Input"]) {
            sinkID = found.id
            sinkUID = found.uid
            rate = Self.nominalRate(found.id) ?? 48_000
            Companion.log("watch-voice sink=\(found.name) rate=\(Int(rate))")
        } else {
            Companion.log("watch-voice sink missing — 表说话进不了闪电说，需安装 BlackHole 2ch")
        }
        let current = Self.defaultInput()
        if let dji = Self.findDevice(["Wireless Mic", "Lark", "DJI"]) {
            keyboardInput = dji.id
            Companion.log("watch-voice keyboard-in=\(dji.name)")
        } else if current != sinkID {
            keyboardInput = current
        }
    }

    func start() {
        guard sinkID != 0, !running else { return }
        lock.lock(); pcm.removeAll(keepingCapacity: true); lock.unlock()
        var desc = AudioComponentDescription(
            componentType: kAudioUnitType_Output,
            componentSubType: kAudioUnitSubType_HALOutput,
            componentManufacturer: kAudioUnitManufacturer_Apple,
            componentFlags: 0,
            componentFlagsMask: 0
        )
        guard let comp = AudioComponentFindNext(nil, &desc) else {
            Companion.log("watch-voice no HAL")
            return
        }
        var u: AudioUnit?
        guard AudioComponentInstanceNew(comp, &u) == noErr, let u else {
            Companion.log("watch-voice unit fail")
            return
        }
        var enable: UInt32 = 1
        var disable: UInt32 = 0
        AudioUnitSetProperty(u, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Output, 0, &enable, 4)
        AudioUnitSetProperty(u, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Input, 1, &disable, 4)
        var dev = sinkID
        let setDev = AudioUnitSetProperty(
            u, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &dev, 4
        )
        if setDev != noErr {
            Companion.log("watch-voice set device fail \(setDev)")
            AudioComponentInstanceDispose(u)
            return
        }
        var fmt = AudioStreamBasicDescription(
            mSampleRate: rate,
            mFormatID: kAudioFormatLinearPCM,
            mFormatFlags: kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked,
            mBytesPerPacket: 4,
            mFramesPerPacket: 1,
            mBytesPerFrame: 4,
            mChannelsPerFrame: 2,
            mBitsPerChannel: 16,
            mReserved: 0
        )
        let fmtSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        AudioUnitSetProperty(u, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &fmt, fmtSize)
        var cb = AURenderCallbackStruct(
            inputProc: { ref, _, _, _, frames, abl -> OSStatus in
                guard let abl else { return noErr }
                let sink = Unmanaged<WatchVoiceSink>.fromOpaque(ref).takeUnretainedValue()
                return sink.render(frames, abl)
            },
            inputProcRefCon: Unmanaged.passUnretained(self).toOpaque()
        )
        AudioUnitSetProperty(u, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cb, UInt32(MemoryLayout<AURenderCallbackStruct>.size))
        guard AudioUnitInitialize(u) == noErr else {
            Companion.log("watch-voice init fail")
            AudioComponentInstanceDispose(u)
            return
        }
        _ = Self.setDefaultInput(sinkID)
        guard AudioOutputUnitStart(u) == noErr else {
            Companion.log("watch-voice start fail")
            AudioComponentInstanceDispose(u)
            _ = Self.setDefaultInput(keyboardInput)
            return
        }
        unit = u
        running = true
        startedAt = Date().timeIntervalSince1970
        pushed = 0
        Companion.log("watch-voice HAL started")
    }

    func push(_ chunk: Data) {
        guard running, !chunk.isEmpty else { return }
        lock.lock()
        pcm.append(chunk)
        pushed += chunk.count
        if pcm.count > 320_000 { pcm.removeFirst(pcm.count - 240_000) }
        lock.unlock()
    }

    func stop() {
        let bytes = pushed
        running = false
        if let u = unit {
            AudioOutputUnitStop(u)
            AudioUnitUninitialize(u)
            AudioComponentInstanceDispose(u)
            unit = nil
        }
        lock.lock(); pcm.removeAll(); lock.unlock()
        if keyboardInput != 0 { _ = Self.setDefaultInput(keyboardInput) }
        Companion.log("watch-voice stopped bytes=\(bytes) input restored")
    }

    private func render(_ frames: UInt32, _ abl: UnsafeMutablePointer<AudioBufferList>) -> OSStatus {
        let n = Int(frames)
        lock.lock()
        let srcCount = pcm.count / 2
        var src = [Int16](repeating: 0, count: srcCount)
        if srcCount > 0 {
            src.withUnsafeMutableBufferPointer { dest in
                _ = pcm.copyBytes(to: dest)
            }
        }
        let consume = min(srcCount, Int(Double(n) * 16000.0 / rate) + 1)
        if consume > 0 { pcm.removeFirst(consume * 2) }
        lock.unlock()
        let buf = abl.pointee.mBuffers
        guard let raw = buf.mData else { return noErr }
        let out = raw.assumingMemoryBound(to: Int16.self)
        let stereo = buf.mNumberChannels >= 2
        for i in 0..<n {
            let idx = Int(Double(i) * 16000.0 / rate)
            let s: Int16 = idx < consume ? src[idx] : 0
            if stereo {
                out[i * 2] = s
                out[i * 2 + 1] = s
            } else {
                out[i] = s
            }
        }
        return noErr
    }

    private struct Found { let id: AudioDeviceID; let uid: String; let name: String }

    private static func findDevice(_ needles: [String]) -> Found? {
        for id in allDevices() {
            let name = nameOf(id)
            let uid = uidOf(id)
            if needles.contains(where: { name.localizedCaseInsensitiveContains($0) }) {
                return Found(id: id, uid: uid, name: name)
            }
        }
        return nil
    }

    private static func allDevices() -> [AudioDeviceID] {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr else {
            return []
        }
        let n = Int(size) / MemoryLayout<AudioDeviceID>.size
        var ids = [AudioDeviceID](repeating: 0, count: n)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr else {
            return []
        }
        return ids
    }

    private static func nameOf(_ id: AudioDeviceID) -> String {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var cf: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &cf) == noErr, let cf else { return "" }
        return cf.takeUnretainedValue() as String
    }

    private static func uidOf(_ id: AudioDeviceID) -> String {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDeviceUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var cf: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &cf) == noErr, let cf else { return "" }
        return cf.takeUnretainedValue() as String
    }

    private static func nominalRate(_ id: AudioDeviceID) -> Double? {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyNominalSampleRate,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var rate = 0.0
        var size = UInt32(MemoryLayout<Double>.size)
        guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &rate) == noErr else { return nil }
        return rate
    }

    private static func defaultInput() -> AudioDeviceID {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var id: AudioDeviceID = 0
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &id)
        return id
    }

    private static func setDefaultInput(_ id: AudioDeviceID) -> Bool {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value = id
        let size = UInt32(MemoryLayout<AudioDeviceID>.size)
        return AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, size, &value) == noErr
    }
}

private final class Companion: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    private var manager: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var snapshotChar: CBCharacteristic?
    private var eventChar: CBCharacteristic?
    private var audioChar: CBCharacteristic?
    private let voiceSink = WatchVoiceSink()
    private var expected: UUID?
    private let demo: Bool
    private let verbose: Bool
    private var lastPayload = Data()
    private var selectedSlot = 0
    private var lastSnapStates: [Int] = []
    private var ttyByPid: [Int: String] = [:]
    private var lastTtyRefresh: TimeInterval = 0
    private var lastRosterTick: TimeInterval = 0
    private var lastWorkflowRefresh: TimeInterval = 0
    private var lastWatchTap: TimeInterval = 0
    private var connectDeadline: TimeInterval = 0
    private var discoverDeadline: TimeInterval = 0
    private var lastScanAt: TimeInterval = 0
    private var lastConnectAttempt: TimeInterval = 0
    private var failCount = 0
    private var lastStateLog: TimeInterval = 0
    private let usb = UsbEvents()
    private var lastHandledKey = ""
    private var lastHandledAt: TimeInterval = 0
    private var bleWanted = true

    init(expected: UUID?, demo: Bool, verbose: Bool) {
        self.expected = expected
        self.demo = demo
        self.verbose = verbose
        super.init()
        manager = CBCentralManager(delegate: self, queue: nil, options: [
            CBCentralManagerOptionShowPowerAlertKey: false,
        ])
        usb.onLine = { [weak self] line in self?.handleUsbLine(line) }
        Self.log("central manager created ax=\(AXIsProcessTrusted())")
    }

    static func log(_ line: String) {
        let url = stateDir.appendingPathComponent("companion.log")
        let text = "\(ISO8601DateFormatter().string(from: Date())) \(line)\n"
        guard let data = text.data(using: .utf8) else { return }
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: data)
        } else if let handle = try? FileHandle(forWritingTo: url) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
            try? handle.close()
        }
        fputs(text, stderr)
    }

    func tick() {
        let now = Date().timeIntervalSince1970
        if now - lastWorkflowRefresh >= 2.5 {
            lastWorkflowRefresh = now
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            proc.arguments = [stateDir.appendingPathComponent("roster.py").path, "--refresh"]
            proc.standardOutput = FileHandle.nullDevice
            proc.standardError = FileHandle.nullDevice
            try? proc.run()
        }
        if now - lastWatchTap > 1.2, let mac = Self.loadMacFocus() {
            if mac != selectedSlot {
                selectedSlot = mac
            }
        }
        if now - lastRosterTick >= 1 {
            lastRosterTick = now
            pushSnapshot()
        }
        usb.tick()
        recoverIfNeeded(now: now)
    }

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        let label: String
        switch central.state {
        case .poweredOn: label = "poweredOn"
        case .poweredOff: label = "poweredOff"
        case .unauthorized: label = "unauthorized — 给 Grok Command Watch 开蓝牙权限"
        case .unsupported: label = "unsupported"
        case .resetting: label = "resetting"
        case .unknown: label = "unknown"
        @unknown default: label = "other(\(central.state.rawValue))"
        }
        Self.log("bluetooth state=\(label)")
        guard central.state == .poweredOn else { return }
        hunt(reason: "state-on")
    }

    func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        let name = peripheral.name
            ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String)
            ?? ""
        let named = name.contains("Grok")
        if !named { return }
        Self.log("found \(name.isEmpty ? "-" : name) \(peripheral.identifier) RSSI=\(RSSI)")
        if demo && expected == nil {
            Self.log("COREBLUETOOTH_UUID=\(peripheral.identifier.uuidString)")
            try? saveDevice(peripheral.identifier)
        }
        use(peripheral, why: "scan")
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        Self.log("connected \(peripheral.identifier) name=\(peripheral.name ?? "-")")
        discoverDeadline = Date().timeIntervalSince1970 + 8
        peripheral.discoverServices(nil)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        Self.log("connect fail \(peripheral.identifier) \(error?.localizedDescription ?? "-")")
        failCount += 1
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
            self?.hunt(reason: "fail")
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didModifyServices invalidatedServices: [CBService]) {
        Self.log("services modified, rediscover")
        snapshotChar = nil
        eventChar = nil
        audioChar = nil
        peripheral.discoverServices(nil)
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        snapshotChar = nil
        eventChar = nil
        audioChar = nil
        Self.log("disconnected \(error?.localizedDescription ?? "clean")")
        guard bleWanted else { return }
        connectDeadline = Date().timeIntervalSince1970 + 8
        lastConnectAttempt = Date().timeIntervalSince1970
        central.connect(peripheral)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error {
            Self.log("discover services error: \(error.localizedDescription)")
            return
        }
        let ids = (peripheral.services ?? []).map(\.uuid.uuidString).joined(separator: ",")
        Self.log("services [\(ids)]")
        guard let service = peripheral.services?.first(where: { $0.uuid == serviceUUID }) else {
            Self.log("stale GATT cache, backoff then rescan")
            expected = nil
            lastConnectAttempt = Date().timeIntervalSince1970 + 8
            manager.cancelPeripheralConnection(peripheral)
            return
        }
        peripheral.discoverCharacteristics(nil, for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        for char in service.characteristics ?? [] {
            if char.uuid == snapshotUUID { snapshotChar = char }
            if char.uuid == eventUUID {
                eventChar = char
                peripheral.setNotifyValue(true, for: char)
            }
            if char.uuid == audioUUID {
                audioChar = char
                peripheral.setNotifyValue(true, for: char)
            }
        }
        let found = (service.characteristics ?? []).map(\.uuid.uuidString).joined(separator: ",")
        Self.log("chars [\(found)] snapshot=\(snapshotChar != nil) event=\(eventChar != nil) audio=\(audioChar != nil)")
        try? saveDevice(peripheral.identifier)
        pushSnapshot(force: true)
    }

    func peripheral(
        _ peripheral: CBPeripheral,
        didUpdateValueFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        if characteristic.uuid == audioUUID, let data = characteristic.value {
            voiceSink.push(data)
            return
        }
        guard characteristic.uuid == eventUUID, let data = characteristic.value,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let op = obj["op"] as? String else { return }
        handle(op: op, obj: obj)
    }

    private func hunt(reason: String) {
        guard bleWanted else { return }
        guard manager.state == .poweredOn else { return }
        Self.log("hunt \(reason) expected=\(expected?.uuidString ?? "-") fail=\(failCount)")
        if let live = firstLiveWatch() {
            use(live, why: "connected-\(reason)")
            return
        }
        if failCount == 0, let expected {
            let known = manager.retrievePeripherals(withIdentifiers: [expected])
            if let first = known.first {
                use(first, why: "uuid-\(reason)")
                return
            }
            Self.log("stored uuid not in cache, scanning")
        }
        startScan()
    }

    private func firstLiveWatch() -> CBPeripheral? {
        let gatt = manager.retrieveConnectedPeripherals(withServices: [serviceUUID])
        let hid = manager.retrieveConnectedPeripherals(withServices: [hidUUID])
        let battery = manager.retrieveConnectedPeripherals(withServices: [CBUUID(string: "180F")])
        let all = gatt + hid + battery
        if let named = all.first(where: { ($0.name ?? "").contains("Grok") }) {
            return named
        }
        return gatt.first
    }

    private func startScan() {
        let now = Date().timeIntervalSince1970
        if manager.isScanning, now - lastScanAt < 8 { return }
        lastScanAt = now
        manager.scanForPeripherals(withServices: nil, options: [
            CBCentralManagerScanOptionAllowDuplicatesKey: false,
        ])
        Self.log("scanning name=Grok Command")
    }

    private func recoverIfNeeded(now: TimeInterval) {
        if now - lastStateLog >= 4 {
            lastStateLog = now
            let state = peripheral.map { Self.stateName($0.state) } ?? "none"
            Self.log("health state=\(state) event=\(eventChar != nil) audio=\(audioChar != nil) usb=\(usb.isOpen) ble=\(bleWanted)")
        }
        if eventChar == nil, now - lastConnectAttempt > 12 {
            bleWanted = true
        }
        guard bleWanted else { return }
        let ready = eventChar != nil
        if ready { return }
        if let peripheral {
            if peripheral.state == .connecting, connectDeadline > 0, now > connectDeadline {
                Self.log("connect timeout, cancel")
                manager.cancelPeripheralConnection(peripheral)
                connectDeadline = 0
                failCount += 1
                if failCount >= 3 { expected = nil }
                startScan()
                return
            }
            if peripheral.state == .connected, discoverDeadline > 0, now > discoverDeadline {
                Self.log("discover timeout, rediscover")
                discoverDeadline = now + 8
                peripheral.discoverServices(nil)
                return
            }
            if peripheral.state == .disconnected, now - lastConnectAttempt >= 2.5 {
                hunt(reason: "tick-disconnected")
            }
        } else if !manager.isScanning, now - lastScanAt >= 2.5 {
            hunt(reason: "tick-empty")
        }
    }

    private func use(_ peripheral: CBPeripheral, why: String) {
        manager.stopScan()
        self.peripheral = peripheral
        peripheral.delegate = self
        Self.log("use \(why) id=\(peripheral.identifier) name=\(peripheral.name ?? "-") state=\(Self.stateName(peripheral.state))")
        if peripheral.state == .connected {
            discoverDeadline = Date().timeIntervalSince1970 + 8
            peripheral.discoverServices(nil)
            return
        }
        if peripheral.state == .connecting { return }
        let now = Date().timeIntervalSince1970
        if now - lastConnectAttempt < 2.5 { return }
        lastConnectAttempt = now
        connectDeadline = now + 8
        manager.connect(peripheral)
    }

    private static func stateName(_ state: CBPeripheralState) -> String {
        switch state {
        case .disconnected: return "disconnected"
        case .connecting: return "connecting"
        case .connected: return "connected"
        case .disconnecting: return "disconnecting"
        @unknown default: return "other"
        }
    }

    private func handle(op: String, obj: [String: Any]) {
        let slot = (obj["slot"] as? NSNumber)?.intValue ?? selectedSlot
        let n = (obj["n"] as? NSNumber)?.intValue ?? 0
        let key = "\(op):\(slot):\(n)"
        let now = Date().timeIntervalSince1970
        let gap = now - lastHandledAt
        if key == lastHandledKey && gap < 0.6 { return }
        if op == "focus", gap < 0.45 { return }
        lastHandledKey = key
        lastHandledAt = now
        switch op {
        case "select":
            selectedSlot = slot
            pushSnapshot(force: true)
        case "focus":
            selectedSlot = slot
            lastWatchTap = now
            Keys.tab(slot)
            pushSnapshot(force: true)
        case "page":
            Keys.page(n == 0 ? 1 : n)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
                self?.pushSnapshot(force: true)
            }
        case "voice_start":
            selectedSlot = slot
            lastWatchTap = now
            voiceSink.start()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                Keys.shandianshuo()
            }
        case "voice_stop":
            selectedSlot = slot
            lastWatchTap = now
            if voiceSink.sessionAge < 2.0 {
                Self.log("ignore short voice_stop")
                return
            }
            Keys.shandianshuo()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
                self?.voiceSink.stop()
            }
        case "voice", "jump_need", "ptt":
            selectedSlot = slot
            if voiceSink.ready {
                voiceSink.start()
            }
            Keys.shandianshuo()
        case "send":
            selectedSlot = slot
            Keys.enter()
        case "answer":
            selectedSlot = slot
            Keys.tab(slot)
        default:
            break
        }
    }

    private func handleUsbLine(_ line: String) {
        if line.hasPrefix("EVT ") {
            let json = String(line.dropFirst(4))
            guard let data = json.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let op = obj["op"] as? String else { return }
            Self.log("usb \(json)")
            handle(op: op, obj: obj)
            return
        }
        // Ignore "BLE event …" serial echoes; EVT json is the only USB source.
    }

    private func firstNeedsYou() -> Int? {
        let roster = loadRoster()
        let binds = loadBinds()
        for (index, name) in slotOrder.enumerated() {
            if SlotState(name: slotStateName(roster, binds, name)) == .needsYou {
                return index
            }
        }
        return nil
    }

    private func pushSnapshot(force: Bool = false) {
        let payload = buildSnapshot()
        if !force && payload == lastPayload { return }
        lastPayload = payload
        if let json = String(data: payload, encoding: .utf8) {
            usb.writeLine("SNAP \(json)")
        }
        // Docked USB already carries SNAP. Extra BLE writes with-response
        // time out the link every few seconds and the face goes blank.
        if usb.isOpen { return }
        guard let peripheral, let snapshotChar else { return }
        peripheral.writeValue(payload, for: snapshotChar, type: .withoutResponse)
    }

    private func buildSnapshot() -> Data {
        let roster = loadRoster()
        let binds = loadBinds()
        let cells = watchCellNames(roster, binds)
        let panes = Keys.loadPanesFile()
        let n = min(cells.count, 10)
        var states: [Int] = []
        var titles: [String] = []
        for index in 0..<n {
            let name = cells[index]
            let state = SlotState(name: slotStateName(roster, binds, name))
            states.append(state.rawValue)
            var title = slotTitle(roster, name)
            if panes.total > 10 {
                let shown = panes.page * 10 + index + 1
                title = "\(shown)/\(panes.total) \(title)"
            }
            titles.append(String(title.prefix(24)))
        }
        if selectedSlot >= n { selectedSlot = max(0, n - 1) }
        if states != lastSnapStates {
            lastSnapStates = states
            Self.log("snap \(cells) s=\(states) n=\(n) page=\(panes.page) total=\(panes.total)")
        }
        let body: [String: Any] = [
            "v": 1,
            "sel": selectedSlot,
            "fg": selectedSlot,
            "link": 2,
            "n": n,
            "s": states,
            "t": titles,
        ]
        return (try? JSONSerialization.data(withJSONObject: body)) ?? Data("{}".utf8)
    }

    private func loadRoster() -> [String: Any] {
        guard let data = try? Data(contentsOf: rosterURL),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return [:]
        }
        return obj
    }

    private func slotMap(_ roster: [String: Any]) -> [String: Any] {
        roster["slots"] as? [String: Any] ?? [:]
    }

    private static func loadMacFocus() -> Int? {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".grok/command-watch/focus.json")
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        let slot: Int
        if let n = obj["slot"] as? Int {
            slot = n
        } else if let n = obj["slot"] as? NSNumber {
            slot = n.intValue
        } else {
            return nil
        }
        guard (0..<10).contains(slot) else { return nil }
        return slot
    }

    private func loadBinds() -> [String: Any] {
        let url = stateDir.appendingPathComponent("slots.json")
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return [:]
        }
        return obj["slots"] as? [String: Any] ?? [:]
    }

    private func pidAlive(_ pid: Int) -> Bool {
        pid > 1 && kill(Int32(pid), 0) == 0
    }

    /// Pad 1–4 = current page of auto-detected grok processes.
    private func watchCellNames(_ roster: [String: Any], _ binds: [String: Any]) -> [String] {
        let file = Keys.loadPanesFile()
        let slice = Array(file.pids.dropFirst(file.page * 10).prefix(10))
        return slice.map { slotName(forPid: $0, roster: roster, binds: binds) }
    }

    private func slotName(forPid pid: Int, roster: [String: Any], binds: [String: Any]) -> String {
        for (name, raw) in slotMap(roster) {
            let slot = raw as? [String: Any]
            let rosterPid = slot?["pid"] as? Int
                ?? (slot?["pid"] as? NSNumber)?.intValue
                ?? 0
            if rosterPid == pid { return name }
        }
        return "p\(pid)"
    }

    private func refreshTtys(_ roster: [String: Any], _ binds: [String: Any]) {
        let now = Date().timeIntervalSince1970
        if now - lastTtyRefresh < 2 { return }
        lastTtyRefresh = now
        var next: [Int: String] = [:]
        for name in slotOrder {
            let pid = livePid(roster, binds, name)
            guard pid > 0 else { continue }
            if let cached = ttyByPid[pid], !cached.isEmpty {
                next[pid] = cached
            } else {
                next[pid] = Self.ttyOf(pid)
            }
        }
        ttyByPid = next
    }

    private static func ttyOf(_ pid: Int) -> String {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/ps")
        proc.arguments = ["-o", "tty=", "-p", String(pid)]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
            proc.waitUntilExit()
        } catch {
            return ""
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    private func livePid(_ roster: [String: Any], _ binds: [String: Any], _ name: String) -> Int {
        let rosterPid = (slotMap(roster)[name] as? [String: Any])?["pid"] as? Int
            ?? ((slotMap(roster)[name] as? [String: Any])?["pid"] as? NSNumber)?.intValue
            ?? 0
        if pidAlive(rosterPid) { return rosterPid }
        let bindPid = (binds[name] as? [String: Any])?["pid"] as? Int
            ?? ((binds[name] as? [String: Any])?["pid"] as? NSNumber)?.intValue
            ?? 0
        if pidAlive(bindPid) { return bindPid }
        if name.hasPrefix("p"), let pid = Int(name.dropFirst()), pidAlive(pid) {
            return pid
        }
        return 0
    }

    private func slotStateName(_ roster: [String: Any], _ binds: [String: Any], _ name: String) -> String {
        if livePid(roster, binds, name) == 0 { return "empty" }
        let slot = slotMap(roster)[name] as? [String: Any]
        if slot?["session_id"] as? String == "demo" { return "empty" }
        let raw = slot?["state"] as? String ?? ""
        if raw.isEmpty || raw == "empty" { return "idle" }
        return raw
    }

    private func slotTitle(_ roster: [String: Any], _ name: String) -> String {
        let slot = slotMap(roster)[name] as? [String: Any]
        let raw = slot?["title"] as? String ?? ""
        let ascii = raw.unicodeScalars.filter { $0.isASCII && $0.value >= 32 && $0.value < 127 }
        return String(String.UnicodeScalarView(ascii))
    }

    private func saveDevice(_ uuid: UUID) throws {
        try FileManager.default.createDirectory(at: stateDir, withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(DeviceBind(uuid: uuid.uuidString))
        try data.write(to: deviceURL, options: .atomic)
    }
}

private func loadExpectedUUID() -> UUID? {
    guard let data = try? Data(contentsOf: deviceURL),
          let bind = try? JSONDecoder().decode(DeviceBind.self, from: data) else {
        return nil
    }
    return UUID(uuidString: bind.uuid)
}

private func printUsage() -> Never {
    fputs("""
    command-watch-companion --watch [--verbose]
    command-watch-companion --demo [--verbose]
    """, stderr)
    exit(2)
}

var watch = false
var demo = false
var verbose = false
var args = Array(CommandLine.arguments.dropFirst())
while let arg = args.first {
    args.removeFirst()
    switch arg {
    case "--watch": watch = true
    case "--demo": demo = true
    case "--verbose": verbose = true
    default: printUsage()
    }
}
if !watch && !demo { watch = true }

try FileManager.default.createDirectory(at: stateDir, withIntermediateDirectories: true)

private func installLog() {
    let url = stateDir.appendingPathComponent("companion.log")
    FileManager.default.createFile(atPath: url.path, contents: nil)
    guard let handle = try? FileHandle(forWritingTo: url) else { return }
    _ = try? handle.seekToEnd()
    let fd = handle.fileDescriptor
    dup2(fd, STDOUT_FILENO)
    dup2(fd, STDERR_FILENO)
    setbuf(stdout, nil)
    setbuf(stderr, nil)
}

installLog()
_ = NSApplication.shared
NSApp.setActivationPolicy(.accessory)
NSApp.finishLaunching()
print("companion start watch=\(watch) demo=\(demo) \(Date())")

private let companion = Companion(expected: loadExpectedUUID(), demo: demo, verbose: verbose)
Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { _ in
    companion.tick()
}
NSApp.run()
