import {
  Action,
  ActionPanel,
  Clipboard,
  Form,
  Icon,
  LaunchProps,
  openExtensionPreferences,
  showToast,
  Toast,
  useNavigation,
} from "@raycast/api";
import { getPreferenceValues } from "@raycast/api";
import { useEffect, useRef, useState } from "react";
import { ResultDetail } from "./components/ResultDetail";
import { formatBytes, formatDuration } from "./lib/articles";
import { getFrontmostBrowserUrl } from "./lib/browser";
import {
  downloadLink,
  ensureFolderExists,
  getConfig,
  inspectUrl,
  isArticleInspectTarget,
  normalizeLinkgetError,
  resolveEffectiveArticleFolder,
  resolveEffectiveAudioFolder,
  resolveEffectiveMediaFallbackFolder,
  resolveInputTarget,
  resolveReadOutputFolder,
  submitReadTask,
} from "./lib/cli";
import type {
  AudioFormat,
  BrowserOption,
  DestinationChoice,
  ExtensionPreferences,
  InspectResponse,
  SaveMode,
} from "./lib/types";

interface SaveLinkLaunchContext {
  url?: string;
}

const MODE_LABELS: { value: SaveMode; title: string }[] = [
  { value: "auto", title: "Auto Detect (Article / Media / Audio)" },
  { value: "article", title: "Article Archive (Markdown, HTML, Assets)" },
  { value: "media", title: "Photos & Videos" },
  { value: "audio", title: "Audio Only" },
  { value: "read", title: "Background Read / Transcript / OCR" },
];

const AUDIO_FORMATS: { value: AudioFormat; title: string }[] = [
  { value: "original", title: "Original Best Audio" },
  { value: "mp3", title: "MP3" },
  { value: "m4a", title: "M4A" },
  { value: "flac", title: "FLAC" },
  { value: "wav", title: "WAV" },
  { value: "opus", title: "OPUS" },
];

const BROWSER_OPTIONS: { value: BrowserOption; title: string }[] = [
  { value: "auto", title: "Auto (Public First, Then Saved)" },
  { value: "none", title: "Public Only (No Browser)" },
  { value: "chrome", title: "Google Chrome" },
  { value: "safari", title: "Safari" },
  { value: "firefox", title: "Firefox" },
  { value: "edge", title: "Microsoft Edge" },
  { value: "brave", title: "Brave" },
];

function summarizeInspect(res: InspectResponse | null): string {
  if (!res) {
    return "Paste a URL or share text to detect site capabilities automatically.";
  }
  if (!res.ok) {
    const err = normalizeLinkgetError(res.error, "Unsupported target");
    return `Cannot inspect: ${err.message}`;
  }

  const tags: string[] = [];
  if (res.site) tags.push(res.site);
  if (res.engine) tags.push(`engine: ${res.engine}`);
  if (isArticleInspectTarget(res)) tags.push("article archive");
  else if (res.supports_article_download) tags.push("media or X article");
  else if (res.effective_audio_mode || res.audio_only) tags.push("audio");
  else if (res.kind === "local_file") tags.push("local file (read/OCR)");
  if (res.session_state && res.session_state !== "not_applicable") {
    tags.push(`session: ${res.session_state}`);
  }
  if (res.tools_missing && res.tools_missing.length > 0) {
    tags.push(`missing tools: ${res.tools_missing.join(", ")}`);
  }

  const parts = [tags.join(" · ")];
  const meta = res.metadata;
  if (meta) {
    const details: string[] = [];
    if (meta.title) details.push(String(meta.title));
    if (meta.author || meta.uploader || meta.artist) {
      details.push(`by ${String(meta.author || meta.uploader || meta.artist)}`);
    }
    if (typeof meta.duration === "number") {
      details.push(formatDuration(meta.duration));
    }
    const size = meta.filesize ?? meta.size_bytes;
    if (typeof size === "number") {
      details.push(formatBytes(size));
    }
    if (details.length > 0) {
      parts.push(details.join(" · "));
    }
  }
  return parts.join("\n");
}

export default function SaveLinkCommand(
  props: LaunchProps<{ launchContext?: SaveLinkLaunchContext }>,
) {
  const prefs = getPreferenceValues<ExtensionPreferences>();
  const { push } = useNavigation();

  const [urlInput, setUrlInput] = useState<string>(
    props.launchContext?.url || "",
  );
  const [urlError, setUrlError] = useState<string | undefined>();
  const [mode, setMode] = useState<SaveMode>("auto");
  const modeManualRef = useRef<boolean>(false);

  const [destination, setDestination] = useState<DestinationChoice>("default");
  const [folderPaths, setFolderPaths] = useState<string[]>([]);
  const [downloadAssets, setDownloadAssets] = useState<boolean>(true);
  const [audioFormat, setAudioFormat] = useState<AudioFormat>("original");
  const [clipRange, setClipRange] = useState<string>("");
  const [maxDuration, setMaxDuration] = useState<string>("");
  const [maxSize, setMaxSize] = useState<string>("");
  const [extractFrames, setExtractFrames] = useState<boolean>(false);
  const [frameOcr, setFrameOcr] = useState<boolean>(false);
  const [browser, setBrowser] = useState<BrowserOption>(
    prefs.defaultBrowser || "auto",
  );

  const [inspectResult, setInspectResult] = useState<InspectResponse | null>(
    null,
  );
  const [isInspecting, setIsInspecting] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const activeRunAbortRef = useRef<AbortController | null>(null);
  const inspectedTargetRef = useRef<string | null>(null);
  const inspectReqIdRef = useRef<number>(0);

  useEffect(() => {
    if (props.launchContext?.url || prefs.autoDetectClipboard === false) {
      return;
    }
    let cancelled = false;
    async function loadClipboard() {
      try {
        const text = await Clipboard.readText();
        const extracted = resolveInputTarget(text);
        if (
          !cancelled &&
          extracted &&
          (extracted.startsWith("http://") || extracted.startsWith("https://"))
        ) {
          setUrlInput((prev) => (prev ? prev : extracted));
        }
      } catch {
        // clipboard unavailable
      }
    }
    void loadClipboard();
    return () => {
      cancelled = true;
    };
  }, [props.launchContext?.url, prefs.autoDetectClipboard]);

  useEffect(() => {
    const target = resolveInputTarget(urlInput);
    if (!target) {
      inspectReqIdRef.current += 1;
      inspectedTargetRef.current = null;
      setInspectResult(null);
      setIsInspecting(false);
      return;
    }

    const reqId = ++inspectReqIdRef.current;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setIsInspecting(true);
      void inspectUrl(
        target,
        {
          noNetwork: true,
          article: mode === "article" ? true : undefined,
          audio: mode === "audio" ? true : undefined,
          browser,
        },
        { binaryPath: prefs.linkgetPath, signal: controller.signal },
      )
        .then((res) => {
          if (controller.signal.aborted || reqId !== inspectReqIdRef.current) {
            return;
          }
          inspectedTargetRef.current = target;
          setInspectResult(res);
          if (!modeManualRef.current && res.ok) {
            if (res.kind === "local_file") {
              setMode("read");
              setDestination((prev) => (prev === "photos" ? "default" : prev));
            } else if (isArticleInspectTarget(res)) {
              setMode("article");
              setDestination((prev) => (prev === "photos" ? "default" : prev));
            } else if (res.effective_audio_mode || res.audio_only) {
              setMode("audio");
              setDestination((prev) => (prev === "photos" ? "default" : prev));
            }
          }
        })
        .catch(() => {
          // ignore aborted or transient inspection failures during typing
        })
        .finally(() => {
          if (reqId === inspectReqIdRef.current) {
            setIsInspecting(false);
          }
        });
    }, 350);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [urlInput, mode, browser, prefs.linkgetPath]);

  useEffect(() => {
    return () => {
      activeRunAbortRef.current?.abort();
    };
  }, []);

  const handleUseBrowserUrl = async () => {
    const activeUrl = await getFrontmostBrowserUrl();
    if (!activeUrl) {
      await showToast({
        style: Toast.Style.Failure,
        title: "No Active Browser Tab Found",
        message:
          "Open a page in Safari, Chrome, Arc, Brave, Edge, or BrowserOS.",
      });
      return;
    }
    modeManualRef.current = false;
    inspectedTargetRef.current = null;
    setInspectResult(null);
    setMode("auto");
    setUrlError(undefined);
    setUrlInput(activeUrl);
    await showToast({
      style: Toast.Style.Success,
      title: "Loaded Active Browser URL",
    });
  };

  const handleProbeRemote = async () => {
    const target = resolveInputTarget(urlInput);
    if (!target) {
      setUrlError("Enter a valid http(s) URL or local file path first.");
      return;
    }
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Probing Remote Metadata…",
    });
    const reqId = ++inspectReqIdRef.current;
    setIsInspecting(true);
    try {
      const res = await inspectUrl(
        target,
        {
          noNetwork: false,
          article: mode === "article" ? true : undefined,
          audio: mode === "audio" ? true : undefined,
          browser,
        },
        { binaryPath: prefs.linkgetPath },
      );
      if (reqId === inspectReqIdRef.current) {
        inspectedTargetRef.current = target;
        setInspectResult(res);
      }
      if (res.ok) {
        if (!modeManualRef.current) {
          if (isArticleInspectTarget(res)) {
            setMode("article");
            setDestination((prev) => (prev === "photos" ? "default" : prev));
          } else if (res.effective_audio_mode || res.audio_only) {
            setMode("audio");
            setDestination((prev) => (prev === "photos" ? "default" : prev));
          }
        }
        toast.style = Toast.Style.Success;
        toast.title = res.metadata?.title || res.site || "Metadata Loaded";
      } else {
        const err = normalizeLinkgetError(res.error, "Probe Failed");
        toast.style = Toast.Style.Failure;
        toast.title = err.title;
        toast.message = err.message;
      }
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Probe Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    } finally {
      if (reqId === inspectReqIdRef.current) {
        setIsInspecting(false);
      }
    }
  };

  const handleSubmit = async () => {
    const target = resolveInputTarget(urlInput);
    if (!target) {
      setUrlError(
        "Paste a valid post/article URL, share text, or local file path.",
      );
      return;
    }

    const isLocalPath =
      !target.startsWith("http://") && !target.startsWith("https://");
    const effectiveMode: SaveMode = isLocalPath ? "read" : mode;
    const effectiveDest: DestinationChoice =
      (effectiveMode === "article" ||
        effectiveMode === "audio" ||
        effectiveMode === "read") &&
      destination === "photos"
        ? "default"
        : destination;

    if (effectiveDest === "folder" && !folderPaths[0]) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Choose a Destination Folder",
      });
      return;
    }

    activeRunAbortRef.current?.abort();
    const runController = new AbortController();
    activeRunAbortRef.current = runController;

    setIsSubmitting(true);
    const toast = await showToast({
      style: Toast.Style.Animated,
      title:
        effectiveMode === "read"
          ? "Submitting Read Task…"
          : effectiveMode === "article"
            ? "Archiving Article Offline…"
            : "Downloading via Linkget…",
    });

    try {
      let currentInspect =
        inspectResult && inspectedTargetRef.current === target
          ? inspectResult
          : null;
      if (
        !currentInspect &&
        effectiveMode === "auto" &&
        effectiveDest === "default"
      ) {
        currentInspect = await inspectUrl(
          target,
          { noNetwork: true, browser },
          { binaryPath: prefs.linkgetPath, signal: runController.signal },
        ).catch(() => null);
      }

      let chosenFolder: string | undefined;
      if (effectiveMode === "read") {
        if (effectiveDest === "folder") {
          chosenFolder = resolveReadOutputFolder(folderPaths[0]);
        }
      } else if (effectiveDest === "folder") {
        chosenFolder = ensureFolderExists(folderPaths[0]);
      } else if (effectiveDest === "default") {
        const config = await getConfig({
          binaryPath: prefs.linkgetPath,
          signal: runController.signal,
        }).catch(() => null);
        if (
          effectiveMode === "article" ||
          (effectiveMode === "auto" && isArticleInspectTarget(currentInspect))
        ) {
          chosenFolder = ensureFolderExists(
            resolveEffectiveArticleFolder(prefs, config),
          );
        } else if (
          effectiveMode === "audio" ||
          (effectiveMode === "auto" &&
            (currentInspect?.effective_audio_mode ||
              currentInspect?.audio_only))
        ) {
          chosenFolder = ensureFolderExists(
            resolveEffectiveAudioFolder(prefs, config),
          );
        } else {
          const fallback = resolveEffectiveMediaFallbackFolder(prefs, config);
          if (fallback) {
            chosenFolder = ensureFolderExists(fallback);
          }
        }
      }

      if (effectiveMode === "read") {
        const readRes = await submitReadTask(
          target,
          {
            folder: chosenFolder,
            frames: extractFrames,
            frameOcr,
            noAssets: !downloadAssets,
            browser,
          },
          { binaryPath: prefs.linkgetPath, signal: runController.signal },
        );
        if (!readRes.ok) {
          const err = normalizeLinkgetError(
            readRes.error,
            "Read Submission Failed",
          );
          toast.style = Toast.Style.Failure;
          toast.title = err.title;
          toast.message = err.message;
          push(
            <ResultDetail
              kind="error"
              errorResult={{ ...err, taskId: readRes.task_id }}
              binaryPath={prefs.linkgetPath}
            />,
          );
          return;
        }
        toast.style = Toast.Style.Success;
        toast.title = "Read Task Queued";
        toast.message = readRes.task_id;
        push(
          <ResultDetail
            kind="read"
            readResult={readRes}
            binaryPath={prefs.linkgetPath}
          />,
        );
        return;
      }

      const res = await downloadLink(
        target,
        {
          mode: effectiveMode,
          destination: effectiveDest,
          folder: chosenFolder,
          noAssets: !downloadAssets,
          audioFormat,
          clip: clipRange,
          maxDuration,
          maxSize,
          browser,
        },
        { binaryPath: prefs.linkgetPath, signal: runController.signal },
      );

      if (!res.ok) {
        const err = normalizeLinkgetError(res.error, "Download Failed");
        toast.style = Toast.Style.Failure;
        toast.title = err.title;
        toast.message = err.message;
        push(
          <ResultDetail
            kind="error"
            errorResult={{ ...err, taskId: res.task_id }}
            binaryPath={prefs.linkgetPath}
          />,
        );
        return;
      }

      toast.style = Toast.Style.Success;
      toast.title = res.title || "Saved Successfully";
      toast.message = `${res.saved_count ?? res.saved_files?.length ?? 0} file(s)`;
      push(
        <ResultDetail
          kind="download"
          downloadResult={res}
          binaryPath={prefs.linkgetPath}
        />,
      );
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      toast.style = Toast.Style.Failure;
      toast.title = "Linkget Error";
      toast.message = message;
      push(
        <ResultDetail
          kind="error"
          errorResult={{ title: "Linkget Error", message }}
          binaryPath={prefs.linkgetPath}
        />,
      );
    } finally {
      if (activeRunAbortRef.current === runController) {
        setIsSubmitting(false);
      }
    }
  };

  return (
    <Form
      isLoading={isInspecting || isSubmitting}
      navigationTitle="Linkget — Save Link"
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title="Start Save"
            icon={Icon.Download}
            onSubmit={() => void handleSubmit()}
          />
          <Action
            title="Use Frontmost Browser URL"
            icon={Icon.Globe}
            shortcut={{ modifiers: ["cmd", "shift"], key: "b" }}
            onAction={() => void handleUseBrowserUrl()}
          />
          <Action
            title="Probe Remote Metadata"
            icon={Icon.MagnifyingGlass}
            shortcut={{ modifiers: ["cmd"], key: "i" }}
            onAction={() => void handleProbeRemote()}
          />
          <Action
            title="Open Extension Preferences"
            icon={Icon.Gear}
            shortcut={{ modifiers: ["cmd", "shift"], key: "," }}
            onAction={() => void openExtensionPreferences()}
          />
        </ActionPanel>
      }
    >
      <Form.TextField
        id="url"
        title="URL or Share Text"
        placeholder="https://x.com/... or share text / local file path"
        value={urlInput}
        error={urlError}
        onChange={(value) => {
          setUrlError(undefined);
          modeManualRef.current = false;
          inspectedTargetRef.current = null;
          setInspectResult(null);
          setMode("auto");
          setUrlInput(value);
        }}
      />

      <Form.Description
        title="Detected"
        text={summarizeInspect(inspectResult)}
      />

      <Form.Dropdown
        id="mode"
        title="Mode"
        value={mode}
        onChange={(next) => {
          const nextMode = next as SaveMode;
          modeManualRef.current = true;
          setMode(nextMode);
          if (
            (nextMode === "article" ||
              nextMode === "audio" ||
              nextMode === "read") &&
            destination === "photos"
          ) {
            setDestination("default");
          }
        }}
      >
        {MODE_LABELS.map((item) => (
          <Form.Dropdown.Item
            key={item.value}
            value={item.value}
            title={item.title}
          />
        ))}
      </Form.Dropdown>

      <Form.Dropdown
        id="destination"
        title="Destination"
        value={destination}
        onChange={(next) => setDestination(next as DestinationChoice)}
      >
        <Form.Dropdown.Item
          value="default"
          title="Smart Default (Config / Preference Folder)"
        />
        {(mode === "auto" || mode === "media") && (
          <Form.Dropdown.Item value="photos" title="Apple Photos" />
        )}
        <Form.Dropdown.Item value="folder" title="Choose Specific Folder…" />
      </Form.Dropdown>

      {destination === "folder" && (
        <Form.FilePicker
          id="folderPicker"
          title="Save Folder"
          allowMultipleSelection={false}
          canChooseDirectories
          canChooseFiles={false}
          value={folderPaths}
          onChange={setFolderPaths}
        />
      )}

      {(mode === "article" || mode === "auto" || mode === "read") && (
        <Form.Checkbox
          id="downloadAssets"
          title="Article Assets"
          label="Download embedded images & videos locally"
          value={downloadAssets}
          onChange={setDownloadAssets}
        />
      )}

      {mode === "audio" && (
        <Form.Dropdown
          id="audioFormat"
          title="Audio Format"
          value={audioFormat}
          onChange={(next) => setAudioFormat(next as AudioFormat)}
        >
          {AUDIO_FORMATS.map((fmt) => (
            <Form.Dropdown.Item
              key={fmt.value}
              value={fmt.value}
              title={fmt.title}
            />
          ))}
        </Form.Dropdown>
      )}

      {mode === "read" && (
        <>
          <Form.Checkbox
            id="frames"
            title="Video Keyframes"
            label="Extract representative frames into frames/"
            value={extractFrames}
            onChange={(checked) => {
              setExtractFrames(checked);
              if (!checked) setFrameOcr(false);
            }}
          />
          <Form.Checkbox
            id="frameOcr"
            title="Keyframe OCR"
            label="Run macOS Vision OCR on extracted keyframes"
            value={frameOcr}
            onChange={(checked) => {
              setFrameOcr(checked);
              if (checked) setExtractFrames(true);
            }}
          />
        </>
      )}

      {(mode === "auto" || mode === "media" || mode === "audio") && (
        <>
          <Form.Separator />
          <Form.TextField
            id="clip"
            title="Clip Range"
            placeholder="Optional, e.g. 0:30-2:00 or 10-45"
            value={clipRange}
            onChange={setClipRange}
          />
          <Form.TextField
            id="maxDuration"
            title="Max Duration"
            placeholder="Optional cap, e.g. 10m or 90s"
            value={maxDuration}
            onChange={setMaxDuration}
          />
          <Form.TextField
            id="maxSize"
            title="Max Size"
            placeholder="Optional cap, e.g. 200MB or 1.5GB"
            value={maxSize}
            onChange={setMaxSize}
          />
        </>
      )}

      <Form.Separator />

      <Form.Dropdown
        id="browser"
        title="Browser Session"
        value={browser}
        onChange={(next) => setBrowser(next as BrowserOption)}
      >
        {BROWSER_OPTIONS.map((b) => (
          <Form.Dropdown.Item key={b.value} value={b.value} title={b.title} />
        ))}
      </Form.Dropdown>
    </Form>
  );
}
