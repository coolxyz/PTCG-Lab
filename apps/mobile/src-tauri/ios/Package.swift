// swift-tools-version:5.9
import PackageDescription
let package = Package(
    name: "MobileDocuments",
    platforms: [.iOS(.v16)],
    products: [.library(name: "MobileDocuments", type: .static, targets: ["MobileDocuments"])],
    targets: [.target(name: "MobileDocuments", path: "Sources")]
)
