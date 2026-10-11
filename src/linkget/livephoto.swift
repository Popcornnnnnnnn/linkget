// Offline Live Photo packaging. No library writes, permissions or UI activation.
import Foundation
import AppKit
import AVFoundation
import ImageIO
import Photos
import CoreMedia
import CoreGraphics

enum PairError: Error { case failed(String) }

func template(_ source: URL, _ target: URL, _ identifier: String) throws {
    guard let input = CGImageSourceCreateWithURL(source as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(input, 0, nil) as? [String: Any],
          let context = CGContext(data: nil, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue),
          let pixel = context.makeImage(),
          let output = CGImageDestinationCreateWithURL(target as CFURL, "public.jpeg" as CFString, 1, nil)
    else { throw PairError.failed("Cannot read the Live Photo image.") }
    var maker = properties[kCGImagePropertyMakerAppleDictionary as String] as? [String: Any] ?? [:]
    maker["17"] = identifier
    CGImageDestinationAddImage(output, pixel, [kCGImagePropertyMakerAppleDictionary as String: maker] as CFDictionary)
    guard CGImageDestinationFinalize(output) else { throw PairError.failed("Cannot create pairing metadata.") }
}

func movie(_ source: URL, _ target: URL, _ identifier: String) throws {
    let asset = AVURLAsset(url: source)
    let reader = try AVAssetReader(asset: asset)
    let writer = try AVAssetWriter(outputURL: target, fileType: .mov)
    let identity = AVMutableMetadataItem()
    identity.identifier = .quickTimeMetadataContentIdentifier
    identity.value = identifier as NSString
    identity.dataType = kCMMetadataBaseDataType_UTF8 as String
    writer.metadata = asset.metadata.filter { $0.identifier != .quickTimeMetadataContentIdentifier } + [identity]
    var channels: [(AVAssetReaderTrackOutput, AVAssetWriterInput)] = []
    for track in asset.tracks where track.mediaType == .video || track.mediaType == .audio {
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
        let format = track.formatDescriptions.first as! CMFormatDescription
        let input = AVAssetWriterInput(mediaType: track.mediaType, outputSettings: nil, sourceFormatHint: format)
        input.transform = track.preferredTransform
        guard reader.canAdd(output), writer.canAdd(input) else { throw PairError.failed("The motion codec cannot be copied without encoding.") }
        reader.add(output)
        writer.add(input)
        channels.append((output, input))
    }
    guard channels.contains(where: { $0.1.mediaType == .video }) else { throw PairError.failed("No motion video track.") }
    var format: CMFormatDescription?
    let spec: [String: Any] = [kCMMetadataFormatDescriptionMetadataSpecificationKey_Identifier as String: "mdta/com.apple.quicktime.still-image-time",
                               kCMMetadataFormatDescriptionMetadataSpecificationKey_DataType as String: kCMMetadataBaseDataType_SInt8 as String]
    guard CMMetadataFormatDescriptionCreateWithMetadataSpecifications(allocator: kCFAllocatorDefault,
              metadataType: kCMMetadataFormatType_Boxed, metadataSpecifications: [spec] as CFArray,
              formatDescriptionOut: &format) == noErr else { throw PairError.failed("Cannot describe the still-frame marker.") }
    let metadataInput = AVAssetWriterInput(mediaType: .metadata, outputSettings: nil, sourceFormatHint: format)
    let adaptor = AVAssetWriterInputMetadataAdaptor(assetWriterInput: metadataInput)
    writer.add(metadataInput)
    guard writer.startWriting(), reader.startReading() else { throw PairError.failed("Cannot start motion stream copy.") }
    writer.startSession(atSourceTime: .zero)
    let marker = AVMutableMetadataItem()
    marker.keySpace = .quickTimeMetadata
    marker.key = "com.apple.quicktime.still-image-time" as NSString
    marker.value = NSNumber(value: 0)
    marker.dataType = kCMMetadataBaseDataType_SInt8 as String
    // Source key-frame timing is not guaranteed by social exports. Do not claim
    // that this marker recovers the camera's original capture timing.
    guard adaptor.append(AVTimedMetadataGroup(items: [marker], timeRange: CMTimeRange(start: .zero, duration: CMTime(value: 1, timescale: 30))))
    else { throw PairError.failed("Cannot append the still-frame marker.") }
    metadataInput.markAsFinished()
    // Audio and video must drain together: finishing one track first can block
    // the interleaved writer while it waits for samples from the other track.
    let copied = DispatchGroup()
    for (index, channel) in channels.enumerated() {
        let (output, input) = channel
        let queue = DispatchQueue(label: "linkget.livephoto.track.\(index)")
        var ended = false
        copied.enter()
        input.requestMediaDataWhenReady(on: queue) {
            guard !ended else { return }
            while input.isReadyForMoreMediaData {
                guard let sample = output.copyNextSampleBuffer() else {
                    ended = true
                    input.markAsFinished()
                    copied.leave()
                    return
                }
                if !input.append(sample) {
                    ended = true
                    reader.cancelReading()
                    input.markAsFinished()
                    copied.leave()
                    return
                }
            }
        }
    }
    guard copied.wait(timeout: .now() + 30) == .success, reader.status == .completed,
          writer.status == .writing else {
        reader.cancelReading()
        writer.cancelWriting()
        throw PairError.failed("Motion streams were incomplete or stalled.")
    }
    let finished = DispatchSemaphore(value: 0)
    writer.finishWriting { finished.signal() }
    guard finished.wait(timeout: .now() + 30) == .success, writer.status == .completed
    else { throw PairError.failed("Motion stream copy did not complete.") }
}

func validate(_ photo: URL, _ movie: URL) throws {
    var finished = false
    var valid = false
    PHLivePhoto.request(withResourceFileURLs: [photo, movie], placeholderImage: nil, targetSize: .zero, contentMode: .aspectFit) { live, info in
        if (info[PHLivePhotoInfoIsDegradedKey] as? Bool) != true {
            valid = live != nil && info[PHLivePhotoInfoErrorKey] == nil
            finished = true
        }
    }
    let deadline = Date().addingTimeInterval(30)
    while !finished && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
    guard valid else { throw PairError.failed("The system did not recognize this image/video pair as a Live Photo.") }
}

do {
    let args = CommandLine.arguments
    guard args.count >= 4 else { throw PairError.failed("Invalid Live Photo helper arguments.") }
    switch args[1] {
    case "template":
        guard args.count == 5 else { throw PairError.failed("Invalid metadata arguments.") }
        try template(URL(fileURLWithPath: args[2]), URL(fileURLWithPath: args[3]), args[4])
    case "movie":
        guard args.count == 5 else { throw PairError.failed("Invalid motion arguments.") }
        try movie(URL(fileURLWithPath: args[2]), URL(fileURLWithPath: args[3]), args[4])
    case "validate": try validate(URL(fileURLWithPath: args[2]), URL(fileURLWithPath: args[3]))
    default: throw PairError.failed("Unknown Live Photo helper action.")
    }
} catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8))
    exit(1)
}
