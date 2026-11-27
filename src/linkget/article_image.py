"""Render one high-resolution PNG from a saved X Article."""

from pathlib import Path
import subprocess
import sys
import tempfile

if __package__:
    from . import ownership, runtime
else:
    import ownership, runtime


MAC_BROWSERS = {
    "neo": ("BrowserOS Neo.app", "BrowserOS neo"),
    "chrome": ("Google Chrome.app", "Google Chrome"),
}


def render(bundle, assets, node, browser, *, filename="article.png", ffmpeg=None):
    missing = [
        asset for asset in assets
        if asset.get("media_type") == "image" and asset.get("role") in {"cover", "inline"}
        and asset.get("status") != "downloaded"
    ]
    if missing:
        raise RuntimeError(f"{len(missing)} article photo(s) could not be downloaded.")
    if sys.platform != "darwin":
        raise RuntimeError("Saving article images to Apple Photos requires macOS.")
    if not node:
        raise RuntimeError("Install Node.js to render article images.")
    if browser not in MAC_BROWSERS:
        raise RuntimeError("Set the Linkget page reader to Neo or Chrome to render article images.")
    app, executable = MAC_BROWSERS[browser]
    suffix = Path(app) / "Contents/MacOS" / executable
    binary = next(
        (base / suffix for base in (Path("/Applications"), Path.home() / "Applications")
         if (base / suffix).is_file()),
        None,
    )
    if binary is None:
        raise RuntimeError(f"The configured page reader, {app}, could not be found.")

    with tempfile.TemporaryDirectory(prefix="linkget-article-png-") as temporary:
        rendered = Path(temporary) / "article.png"
        command = [node, str(Path(__file__).with_suffix(".js")), str(binary),
                   str(bundle / "article.html"), str(rendered)]
        if ffmpeg:
            command.append(ffmpeg)
        try:
            result = runtime.run_process(command, timeout=55, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            raise RuntimeError("The article image renderer timed out.") from None
        if result.returncode or not rendered.is_file():
            detail = result.stderr.strip() or "The browser did not produce a PNG."
            raise RuntimeError(detail)
        return ownership.copy(ownership.FileProof.observe(rendered), bundle / filename)
