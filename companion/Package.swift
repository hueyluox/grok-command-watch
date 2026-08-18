// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "CommandWatchCompanion",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "command-watch-companion", targets: ["CommandWatchCompanion"]),
    ],
    targets: [
        .executableTarget(name: "CommandWatchCompanion"),
    ]
)
