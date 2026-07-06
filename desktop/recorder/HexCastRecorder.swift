// HexCast native screen recorder (ScreenCaptureKit).
//
// Two modes, driven by argv so Python can spawn it like it did ffmpeg:
//   hexcast-recorder list
//       -> prints JSON {"screens":[{index,name}], "windows":[{index,name}], "mics":[...]}
//          ("index" is an opaque token the record mode understands)
//   hexcast-recorder record --target <token> --out <path.mp4> [--mic <token>] [--fps 30]
//       -> captures the chosen screen/window (+ optional mic) until SIGINT, then
//          finalizes the mp4 (moov atom) and exits 0.
//
// ScreenCaptureKit gives per-window capture and excludes HexCast's own window,
// which avfoundation could not do. Needs macOS 12.3+ (we target 14).

import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreMedia
import CoreGraphics
import AppKit

// MARK: - helpers

func die(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(2)
}

func jsonString(_ s: String) -> String {
    var out = "\""
    for c in s.unicodeScalars {
        switch c {
        case "\"": out += "\\\""
        case "\\": out += "\\\\"
        case "\n": out += "\\n"
        case "\t": out += "\\t"
        case "\r": out += "\\r"
        default:
            if c.value < 0x20 { out += String(format: "\\u%04x", c.value) }
            else { out.unicodeScalars.append(c) }
        }
    }
    return out + "\""
}

// The HexCast app + this helper are excluded from the window list.
let SELF_BUNDLE_PREFIXES = ["ai.eventhex.hexcast", "com.apple.dock", "com.apple.WindowManager",
                           "com.apple.controlcenter", "com.apple.notificationcenterui"]

// MARK: - shareable content (sync bridge over the async API)

func fetchContent() -> SCShareableContent {
    let sem = DispatchSemaphore(value: 0)
    var result: SCShareableContent?
    var failure: Error?
    SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true) { content, err in
        result = content; failure = err; sem.signal()
    }
    sem.wait()
    if let e = failure { die("could not read shareable content — grant Screen Recording permission. \(e.localizedDescription)") }
    guard let c = result else { die("no shareable content") }
    return c
}

// MARK: - list

func runList() {
    let content = fetchContent()
    // screens: token "screen:<displayID>"
    var screens: [String] = []
    for (i, d) in content.displays.enumerated() {
        let name = "Screen \(i + 1) — \(d.width)×\(d.height)"
        screens.append("{\"index\":\"screen:\(d.displayID)\",\"name\":\(jsonString(name))}")
    }
    // windows: token "window:<windowID>", labelled "App — Title"
    var windows: [String] = []
    for w in content.windows {
        guard let app = w.owningApplication else { continue }
        if SELF_BUNDLE_PREFIXES.contains(where: { app.bundleIdentifier.hasPrefix($0) }) { continue }
        let title = (w.title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if w.frame.width < 80 || w.frame.height < 80 { continue }   // skip tiny/util windows
        let label = title.isEmpty ? app.applicationName : "\(app.applicationName) — \(title)"
        windows.append("{\"index\":\"window:\(w.windowID)\",\"name\":\(jsonString(label))}")
    }
    // mics: token "mic:<uniqueID>"
    var mics: [String] = []
    let discovery = AVCaptureDevice.DiscoverySession(
        deviceTypes: [.microphone], mediaType: .audio, position: .unspecified)
    for dev in discovery.devices {
        mics.append("{\"index\":\(jsonString("mic:" + dev.uniqueID)),\"name\":\(jsonString(dev.localizedName))}")
    }
    print("{\"screens\":[\(screens.joined(separator: ","))],"
        + "\"windows\":[\(windows.joined(separator: ","))],"
        + "\"mics\":[\(mics.joined(separator: ","))]}")
}

// MARK: - record

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate, AVCaptureAudioDataOutputSampleBufferDelegate {
    let writer: AVAssetWriter
    let videoInput: AVAssetWriterInput
    var audioInput: AVAssetWriterInput?
    var stream: SCStream?
    var session: AVCaptureSession?
    let queue = DispatchQueue(label: "hexcast.recorder")
    var started = false
    var finished = false

    init(outURL: URL, width: Int, height: Int, wantMic: Bool) {
        do { writer = try AVAssetWriter(outputURL: outURL, fileType: .mp4) }
        catch { die("cannot open output: \(error.localizedDescription)") }

        let vsettings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: max(width * height * 5, 2_000_000)],
        ]
        videoInput = AVAssetWriterInput(mediaType: .video, outputSettings: vsettings)
        videoInput.expectsMediaDataInRealTime = true
        if writer.canAdd(videoInput) { writer.add(videoInput) }

        super.init()

        if wantMic {
            let asettings: [String: Any] = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVNumberOfChannelsKey: 1,
                AVSampleRateKey: 44100,
                AVEncoderBitRateKey: 128000,
            ]
            let ai = AVAssetWriterInput(mediaType: .audio, outputSettings: asettings)
            ai.expectsMediaDataInRealTime = true
            if writer.canAdd(ai) { writer.add(ai); audioInput = ai }
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, CMSampleBufferIsValid(sampleBuffer),
              CMSampleBufferGetNumSamples(sampleBuffer) > 0 else { return }
        // SCK marks each frame's status; only append "complete" frames.
        guard let attach = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let statusRaw = attach.first?[.status] as? Int,
              let status = SCFrameStatus(rawValue: statusRaw), status == .complete else { return }
        // already on `queue` (our sampleHandlerQueue) — no sync, that would deadlock
        if finished { return }
        if !started {
            let ok = writer.startWriting()
            if !ok || writer.status != .writing {
                FileHandle.standardError.write("startWriting failed ok=\(ok) status=\(writer.status.rawValue) err=\(String(describing: writer.error))\n".data(using: .utf8)!)
                return
            }
            writer.startSession(atSourceTime: CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
            started = true
        }
        if writer.status == .writing && videoInput.isReadyForMoreMediaData {
            videoInput.append(sampleBuffer)
        }
    }

    // mic — delivered on the same `queue`, so no sync
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard started, !finished, let ai = audioInput, ai.isReadyForMoreMediaData else { return }
        ai.append(sampleBuffer)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write("stream stopped: \(error.localizedDescription)\n".data(using: .utf8)!)
    }

    func finish(_ done: @escaping () -> Void) {
        var already = false
        queue.sync {
            if finished { already = true } else { finished = true }
        }
        if already { return }
        session?.stopRunning()
        stream?.stopCapture { [self] _ in
            // finalize on our serial queue so it can't race the sample handler
            queue.async { [self] in
                guard started, writer.status == .writing else {
                    let msg = "no writable video (status=\(writer.status.rawValue) err=\(String(describing: writer.error)))"
                    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
                    done(); return
                }
                videoInput.markAsFinished()
                audioInput?.markAsFinished()
                writer.finishWriting { done() }
            }
        }
    }
}

func arg(_ name: String) -> String? {
    let a = CommandLine.arguments
    if let i = a.firstIndex(of: name), i + 1 < a.count { return a[i + 1] }
    return nil
}

func runRecord() {
    guard let target = arg("--target"), let out = arg("--out") else {
        die("record needs --target <token> --out <path>")
    }
    let fps = Int(arg("--fps") ?? "30") ?? 30
    let micToken = arg("--mic")
    // connect to the WindowServer (CLI tools aren't GUI apps) — window capture
    // otherwise aborts with CGS_REQUIRE_INIT. Touching NSApplication.shared
    // initializes the GUI/CGS connection without running an event loop.
    _ = NSApplication.shared
    let content = fetchContent()

    // resolve the target token to an SCContentFilter + capture size
    var filter: SCContentFilter
    var width = 1920, height = 1080
    if target.hasPrefix("screen:") {
        let did = UInt32(target.dropFirst("screen:".count)) ?? 0
        guard let d = content.displays.first(where: { $0.displayID == did }) ?? content.displays.first else {
            die("no such screen")
        }
        filter = SCContentFilter(display: d, excludingWindows: [])
        width = d.width; height = d.height
    } else if target.hasPrefix("window:") {
        let wid = UInt32(target.dropFirst("window:".count)) ?? 0
        guard let w = content.windows.first(where: { $0.windowID == wid }) else { die("no such window") }
        filter = SCContentFilter(desktopIndependentWindow: w)
        width = Int(w.frame.width); height = Int(w.frame.height)
    } else {
        die("target must be screen:<id> or window:<id>")
    }
    // even dimensions + a sane cap for retina windows
    let scale = 2
    width = min(width * scale, 3840) & ~1
    height = min(height * scale, 2160) & ~1

    let cfg = SCStreamConfiguration()
    cfg.width = width
    cfg.height = height
    cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
    cfg.showsCursor = true
    cfg.pixelFormat = kCVPixelFormatType_32BGRA
    cfg.queueDepth = 6

    let outURL = URL(fileURLWithPath: out)
    try? FileManager.default.removeItem(at: outURL)
    let rec = Recorder(outURL: outURL, width: width, height: height, wantMic: micToken != nil)

    // mic via AVCaptureSession (SCK audio is system audio, not the mic)
    if let mt = micToken, mt.hasPrefix("mic:") {
        let uid = String(mt.dropFirst("mic:".count))
        if let dev = AVCaptureDevice(uniqueID: uid) ?? AVCaptureDevice.default(for: .audio),
           let input = try? AVCaptureDeviceInput(device: dev) {
            let s = AVCaptureSession()
            if s.canAddInput(input) { s.addInput(input) }
            let ao = AVCaptureAudioDataOutput()
            ao.setSampleBufferDelegate(rec, queue: rec.queue)
            if s.canAddOutput(ao) { s.addOutput(ao) }
            rec.session = s
            s.startRunning()
        }
    }

    let stream = SCStream(filter: filter, configuration: cfg, delegate: rec)
    do { try stream.addStreamOutput(rec, type: .screen, sampleHandlerQueue: rec.queue) }
    catch { die("addStreamOutput failed: \(error.localizedDescription)") }
    rec.stream = stream

    let startSem = DispatchSemaphore(value: 0)
    var startErr: Error?
    stream.startCapture { err in startErr = err; startSem.signal() }
    startSem.wait()
    if let e = startErr { die("could not start capture — grant Screen Recording permission to HexCast. \(e.localizedDescription)") }

    // stop cleanly on SIGINT/SIGTERM -> finalize the file, then exit
    signal(SIGINT, SIG_IGN); signal(SIGTERM, SIG_IGN)
    let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
    let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    let onStop: () -> Void = {
        rec.finish {
            print("{\"ok\":true}"); fflush(stdout)
            exit(0)
        }
    }
    sigint.setEventHandler(handler: onStop); sigterm.setEventHandler(handler: onStop)
    sigint.resume(); sigterm.resume()

    print("{\"recording\":true}"); fflush(stdout)   // tell the parent capture began
    dispatchMain()
}

// MARK: - entry

let mode = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
switch mode {
case "list": runList()
case "record": runRecord()
default: die("usage: hexcast-recorder list | record --target <token> --out <path> [--mic <token>] [--fps N]")
}
