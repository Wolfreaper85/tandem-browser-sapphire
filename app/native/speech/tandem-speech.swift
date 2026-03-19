import Foundation
import Speech

// tandem-speech — Apple Speech Framework CLI for Tandem Browser
// Usage: tandem-speech <audio-file-path> [language]
// Output: transcribed text on stdout, errors on stderr
// Exit: 0 on success, 1 on failure

let args = CommandLine.arguments
guard args.count >= 2 else {
    fputs("Usage: tandem-speech <audio-file> [language]\n", stderr)
    exit(1)
}

let filePath = args[1]
let language = args.count >= 3 ? args[2] : "nl-NL"
let fileURL = URL(fileURLWithPath: filePath)

guard FileManager.default.fileExists(atPath: filePath) else {
    fputs("File not found: \(filePath)\n", stderr)
    exit(1)
}

// Check authorization status — don't request, just check
// (authorization was granted to the parent Electron app)
let authStatus = SFSpeechRecognizer.authorizationStatus()
if authStatus == .denied || authStatus == .restricted {
    fputs("Speech recognition not authorized (status: \(authStatus.rawValue))\n", stderr)
    exit(1)
}

guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: language)), recognizer.isAvailable else {
    fputs("Speech recognizer not available for: \(language)\n", stderr)
    exit(1)
}

let request = SFSpeechURLRecognitionRequest(url: fileURL)
request.shouldReportPartialResults = false

var done = false
var exitCode: Int32 = 1

// Use RunLoop instead of DispatchSemaphore for better CLI compatibility
recognizer.recognitionTask(with: request) { result, error in
    defer {
        done = true
    }
    if let error = error {
        fputs("Recognition error: \(error.localizedDescription)\n", stderr)
        exitCode = 1
        return
    }
    guard let result = result, result.isFinal else { return }
    let text = result.bestTranscription.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
    if text.isEmpty {
        fputs("Empty transcription\n", stderr)
        exitCode = 1
    } else {
        print(text)
        exitCode = 0
    }
}

// Run until done or timeout (30s)
let deadline = Date().addingTimeInterval(30)
while !done && Date() < deadline {
    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
}

if !done {
    fputs("Timeout waiting for transcription\n", stderr)
    exitCode = 1
}

exit(exitCode)
