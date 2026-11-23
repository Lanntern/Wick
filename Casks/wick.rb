# Homebrew cask for Wick, in this repository's own tap:
#   brew tap lanntern/wick https://github.com/Lanntern/Wick
#   brew install --cask wick
# Version and checksum are updated by scripts/update_cask.py when a release is published.
cask "wick" do
  version "0.2.0"
  sha256 :no_check

  url "https://github.com/Lanntern/Wick/releases/download/v#{version}/Wick-macos-arm64.dmg"
  name "Wick"
  desc "Get space back from photos, videos, music and apps: smaller formats, every result checked, undoable"
  homepage "https://github.com/Lanntern/Wick"

  depends_on arch: :arm64
  depends_on macos: ">= :monterey"

  app "Wick.app"

  zap trash: [
    "~/Library/Application Support/Wick",
    "~/Library/LaunchAgents/com.lanntern.wick.plist",
    "~/Library/Preferences/com.wick.Wick.plist",
    "~/Library/Services/Compress with Wick.workflow",
    "~/Library/Services/Convert with Wick….workflow",
  ]

  caveats <<~EOS
    Wick isn't signed by Apple yet, so the first time macOS says it can't check the developer.
    Open it with right-click > Open > Open, or run:
      xattr -dr com.apple.quarantine #{appdir}/Wick.app
  EOS
end
