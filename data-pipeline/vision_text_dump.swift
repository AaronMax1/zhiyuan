import Foundation
import Vision
import AppKit

if CommandLine.arguments.count < 2 {
    fputs("usage: vision_text_dump.swift <image>\n", stderr)
    exit(2)
}

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("failed to read image: \(path)\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest { request, error in
    if let error = error {
        fputs("vision error: \(error)\n", stderr)
        exit(1)
    }
    let observations = (request.results as? [VNRecognizedTextObservation]) ?? []
    let lines = observations.compactMap { obs -> (CGFloat, CGFloat, String)? in
        guard let text = obs.topCandidates(1).first?.string else { return nil }
        return (obs.boundingBox.midY, obs.boundingBox.minX, text)
    }
    let asTSV = ProcessInfo.processInfo.environment["VISION_TSV"] == "1"
    for item in lines.sorted(by: { lhs, rhs in
        if abs(lhs.0 - rhs.0) > 0.006 { return lhs.0 > rhs.0 }
        return lhs.1 < rhs.1
    }) {
        if asTSV {
            print(String(format: "%.6f\t%.6f\t%@", Double(item.0), Double(item.1), item.2))
        } else {
            print(item.2)
        }
    }
}

request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["zh-Hans", "en-US"]

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])
