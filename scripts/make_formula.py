"""Generate the tap formula from a release archive and its embedded metadata."""
import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import tarfile

parser = argparse.ArgumentParser()
parser.add_argument("archive", type=Path)
parser.add_argument("--url", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
with tarfile.open(args.archive) as archive:
    member = next(item for item in archive.getmembers() if item.name.count("/") == 1 and item.name.endswith("/PKG-INFO"))
    metadata = Parser().parsestr(archive.extractfile(member).read().decode())
version = metadata["Version"]
digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
url = json.dumps(args.url).replace("#{", "\\#{")
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text('''class Linkget < Formula
  desc "Save post photos and videos to macOS Photos or a folder"
  homepage "https://github.com/Popcornnnnnnnn/linkget"
  url %s
  version "%s"
  sha256 "%s"
  license "MIT"

  depends_on "ffmpeg"
  depends_on "gallery-dl"
  depends_on :macos
  depends_on "python@3.14"
  depends_on "yt-dlp"

  def install
    libexec.install "src/linkget"
    (bin/"linkget").write <<~SH
      #!/bin/sh
      exec "#{formula_opt_bin("python@3.14")}/python3.14" "#{libexec}/linkget/cli.py" "$@"
    SH
  end

  def caveats
    <<~EOS
      Run linkget and paste a post link. Use --folder to save to the current folder.
      Photos imports retain originals in ~/Library/Application Support/linkget/originals.
      Uninstalling does not remove your downloads or saved account data.
    EOS
  end

  test do
    assert_match "%s", shell_output("#{bin}/linkget --version")
    assert_match "Instagram", shell_output("#{bin}/linkget sites")
    assert_match "--folder [FOLDER]", shell_output("#{bin}/linkget help")
    assert_match "Not connected", shell_output("LINKGET_HOME=#{testpath}/data #{bin}/linkget auth")
  end
end
''' % (url, version, digest, version))
print(args.output)
