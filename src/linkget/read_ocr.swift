import Foundation
import Vision
import AppKit

var results: [[String: Any]] = []
for argument in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: argument)
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en"]
    if #available(macOS 13, *) { request.automaticallyDetectsLanguage = true }
    do {
        try VNImageRequestHandler(url: url).perform([request])
        let observations = request.results ?? []
        let lines = observations.compactMap { $0.topCandidates(1).first }
        results.append(["file": url.lastPathComponent,
                        "text": lines.map { $0.string }.joined(separator: "\n"),
                        "recognition_verified": false,
                        "minimum_confidence": lines.map { $0.confidence }.min() ?? 0])
    } catch {
        fputs("Image OCR failed: \(url.lastPathComponent)\n", stderr)
        exit(1)
    }
}
let data = try JSONSerialization.data(withJSONObject: results)
FileHandle.standardOutput.write(data)
