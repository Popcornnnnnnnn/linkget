"""Synthetic downloader fixtures are not real encoded images; test probes separately."""
def uninspected(files, variants=None):
    return [{"file": path.name, "source": (variants or {}).get(path.name, "engine_selected"),
             "width": None, "height": None, "codec": None, "probe_ok": None}
            for path in files]
