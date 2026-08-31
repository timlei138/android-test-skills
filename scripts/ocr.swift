import Vision
import AppKit
import Foundation

// 用法: swift ocr.swift <image_path>
// 用 macOS Vision 框架 OCR 截图，输出识别文字 + 归一化坐标（原点左下）

let args = CommandLine.arguments
guard args.count >= 2, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    print("ERROR: cannot load image: \(args.count >= 2 ? args[1] : "?")")
    exit(1)
}

let request = VNRecognizeTextRequest { req, err in
    guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
    for o in obs {
        if let top = o.topCandidates(1).first {
            let b = o.boundingBox
            print(String(format: "%.3f %.3f %.3f %.3f  %@",
                         b.origin.x, b.origin.y, b.size.width, b.size.height,
                         top.string))
        }
    }
}
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([request])
} catch {
    print("OCR error: \(error)")
    exit(1)
}
