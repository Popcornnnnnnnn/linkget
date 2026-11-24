import {
  Action,
  ActionPanel,
  Clipboard,
  Form,
  getPreferenceValues,
  Icon,
  launchCommand,
  LaunchProps,
  LaunchType,
  openExtensionPreferences,
  showToast,
  Toast,
  useNavigation,
  Keyboard,
} from "@raycast/api";
import { useEffect, useRef, useState } from "react";
import { ResultDetail } from "./components/ResultDetail";
import { formatBytes, formatDuration } from "./lib/articles";
import { getFrontmostBrowserUrl } from "./lib/browser";
import {
  downloadLink,
  ensureFolderExists,
  getConfig,
  inspectUrl,
  isArticleFolderRequiredError,
  isArticleInspectTarget,
  normalizeLinkgetError,
  resolveDefaultDownloadTarget,
  resolveEffectiveArticleFolder,
  resolveInputTarget,
  resolveReadOutputFolder,
  startBackgroundDownload,
  submitReadTask,
} from "./lib/cli";
import type {
  AudioFormat,
  BrowserOption,
  ConfigResponse,
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
  const [folderError, setFolderError] = useState<string | undefined>();
  const [downloadAssets, setDownloadAssets] = useState<boolean>(true);
  const [audioFormat, setAudioFormat] = useState<AudioFormat>("original");
  const [clipRange, setClipRange] = useState<string>("");
  const [maxDuration, setMaxDuration] = useState<string>("");
  const [maxSize, setMaxSize] = useState<string>("");
  const [extractFrames, setExtractFrames] = useState<boolean>(false);
  const [frameOcr, setFrameOcr] = useState<boolean>(false);
  const [livePhotos, setLivePhotos] = useState<boolean>(false);
  const [showAdvanced, setShowAdvanced] = useState<boolean>(false);
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

  type PreparedSave =
    | {
        kind: "read";
        target: string;
        chosenFolder?: string;
      }
    | {
        kind: "download";
        target: string;
        effectiveMode: Exclude<SaveMode, "read">;
        effectiveDest: DestinationChoice;
        downloadDest: DestinationChoice;
        chosenFolder?: string;
        cachedConfig: ConfigResponse | null;
      };

  const prepareSaveTarget = async (
    toast: Toast,
    signal?: AbortSignal,
  ): Promise<PreparedSave | null> => {
    const failValidation = (message: string, title = "Check Inputs"): null => {
      toast.style = Toast.Style.Failure;
      toast.title = title;
      toast.message = message;
      return null;
    };

    const target = resolveInputTarget(urlInput);
    if (!target) {
      const msg =
        "Paste a valid post/article URL, share text, or local file path.";
      setUrlError(msg);
      return failValidation(msg, "Invalid Link or Path");
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
      const msg = "Choose a destination folder.";
      setFolderError(msg);
      return failValidation(msg, "Choose a Destination Folder");
    }

    if (
      effectiveMode !== "article" &&
      effectiveMode !== "read" &&
      livePhotos &&
      clipRange.trim()
    ) {
      return failValidation(
        "Clip Range cannot be combined with Live Photos.",
        "Incompatible Options",
      );
    }

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
        { binaryPath: prefs.linkgetPath, signal },
      ).catch((err) => {
        if (err instanceof Error && err.name === "AbortError") {
          throw err;
        }
        return null;
      });
    }

    if (signal?.aborted) {
      const abortErr = new Error("Aborted");
      abortErr.name = "AbortError";
      throw abortErr;
    }

    let chosenFolder: string | undefined;
    let downloadDest: DestinationChoice = effectiveDest;
    let cachedConfig: ConfigResponse | null = null;

    if (effectiveMode === "read") {
      if (effectiveDest === "folder") {
        chosenFolder = resolveReadOutputFolder(folderPaths[0]);
      }
      return { kind: "read", target, chosenFolder };
    }

    if (effectiveDest === "folder") {
      chosenFolder = ensureFolderExists(folderPaths[0]);
    } else if (effectiveDest === "default") {
      cachedConfig = await getConfig({
        binaryPath: prefs.linkgetPath,
        signal,
      }).catch((err) => {
        if (err instanceof Error && err.name === "AbortError") {
          throw err;
        }
        return null;
      });
      if (signal?.aborted) {
        const abortErr = new Error("Aborted");
        abortErr.name = "AbortError";
        throw abortErr;
      }
      const resolved = resolveDefaultDownloadTarget(
        effectiveMode,
        prefs,
        cachedConfig,
        currentInspect,
      );
      downloadDest = resolved.destination;
      if (resolved.folder) {
        chosenFolder = ensureFolderExists(resolved.folder);
      }
    }

    return {
      kind: "download",
      target,
      effectiveMode,
      effectiveDest,
      downloadDest,
      chosenFolder,
      cachedConfig,
    };
  };

  const resetUrlForNextEntry = () => {
    modeManualRef.current = false;
    inspectedTargetRef.current = null;
    setInspectResult(null);
    setMode("auto");
    setUrlError(undefined);
    setFolderError(undefined);
    setUrlInput("");
  };

  const openStatusBoardCommand = () =>
    void launchCommand({
      name: "status-board",
      type: LaunchType.UserInitiated,
    });

  const handleBackgroundSave = async () => {
    if (isSubmitting) {
      return;
    }
    setIsSubmitting(true);
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Starting Download in Background…",
    });
    try {
      const prepared = await prepareSaveTarget(toast);
      if (!prepared) {
        return;
      }

      if (prepared.kind === "read") {
        const readRes = await submitReadTask(
          prepared.target,
          {
            folder: prepared.chosenFolder,
            frames: extractFrames,
            frameOcr,
            noAssets: !downloadAssets,
            browser,
          },
          { binaryPath: prefs.linkgetPath },
        );
        if (!readRes.ok) {
          const err = normalizeLinkgetError(
            readRes.error,
            "Read Submission Failed",
          );
          toast.style = Toast.Style.Failure;
          toast.title = err.title;
          toast.message = err.message;
          return;
        }
        toast.style = Toast.Style.Success;
        toast.title = "Read Task Queued in Background";
        toast.message = readRes.task_id || "Check Status Board for progress";
        toast.primaryAction = {
          title: "Open Status Board",
          onAction: openStatusBoardCommand,
        };
        resetUrlForNextEntry();
        return;
      }

      await startBackgroundDownload(
        prepared.target,
        {
          mode: prepared.effectiveMode,
          destination: prepared.downloadDest,
          folder: prepared.chosenFolder,
          noAssets: !downloadAssets,
          audioFormat,
          clip: clipRange,
          maxDuration,
          maxSize,
          livePhotos,
          browser,
        },
        { binaryPath: prefs.linkgetPath },
      );

      toast.style = Toast.Style.Success;
      toast.title = "Download Started in Background";
      toast.message = "Track progress and outputs in Status Board";
      toast.primaryAction = {
        title: "Open Status Board",
        onAction: openStatusBoardCommand,
      };
      resetUrlForNextEntry();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Background Start Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSubmit = async () => {
    if (isSubmitting) {
      return;
    }
    const runController = new AbortController();
    activeRunAbortRef.current?.abort();
    activeRunAbortRef.current = runController;

    setIsSubmitting(true);
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Preparing Linkget Task…",
    });

    try {
      const prepared = await prepareSaveTarget(toast, runController.signal);
      if (!prepared) {
        return;
      }

      if (prepared.kind === "read") {
        toast.title = "Submitting Read Task…";
        const readRes = await submitReadTask(
          prepared.target,
          {
            folder: prepared.chosenFolder,
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

      toast.title =
        prepared.effectiveMode === "article"
          ? "Archiving Article Offline…"
          : "Downloading via Linkget…";
      toast.message =
        "Safe to close Raycast — download continues in background";

      let res = await downloadLink(
        prepared.target,
        {
          mode: prepared.effectiveMode,
          destination: prepared.downloadDest,
          folder: prepared.chosenFolder,
          noAssets: !downloadAssets,
          audioFormat,
          clip: clipRange,
          maxDuration,
          maxSize,
          livePhotos,
          browser,
        },
        {
          binaryPath: prefs.linkgetPath,
          signal: runController.signal,
          detachOnAbort: true,
        },
      );

      if (
        !res.ok &&
        prepared.effectiveMode === "auto" &&
        prepared.effectiveDest === "default" &&
        isArticleFolderRequiredError(res.error)
      ) {
        const fallbackArticleFolder = ensureFolderExists(
          resolveEffectiveArticleFolder(prefs, prepared.cachedConfig),
        );
        res = await downloadLink(
          prepared.target,
          {
            mode: "article",
            destination: "default",
            folder: fallbackArticleFolder,
            noAssets: !downloadAssets,
            livePhotos: false,
            browser,
          },
          {
            binaryPath: prefs.linkgetPath,
            signal: runController.signal,
            detachOnAbort: true,
          },
        );
      }

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
            title="Save in Background"
            icon={Icon.Cloud}
            shortcut={{ modifiers: ["cmd"], key: "b" }}
            onAction={() => void handleBackgroundSave()}
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
            title="Open Status Board"
            icon={Icon.List}
            shortcut={Keyboard.Shortcut.Common.Duplicate}
            onAction={openStatusBoardCommand}
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
        onChange={(next) => {
          setFolderError(undefined);
          setDestination(next as DestinationChoice);
        }}
      >
        <Form.Dropdown.Item
          value="default"
          title="Default (Photos for Media · ~/Downloads for Articles)"
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
          error={folderError}
          onChange={(paths) => {
            setFolderError(undefined);
            setFolderPaths(paths);
          }}
        />
      )}

      {(mode === "auto" || mode === "media") &&
        destination !== "folder" &&
        !isArticleInspectTarget(inspectResult) &&
        !inspectResult?.effective_audio_mode &&
        !inspectResult?.audio_only &&
        (destination === "photos" || !prefs.defaultMediaFolder?.trim()) && (
          <Form.Checkbox
            id="livePhotos"
            title="Live Photos"
            label="Import paired Live Photos into Apple Photos"
            value={livePhotos}
            onChange={setLivePhotos}
          />
        )}

      {(mode === "article" ||
        mode === "read" ||
        (mode === "auto" &&
          (isArticleInspectTarget(inspectResult) || showAdvanced))) && (
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

      <Form.Separator />

      <Form.Checkbox
        id="showAdvanced"
        title="Advanced"
        label="Show clip range, limits, and browser session"
        value={showAdvanced}
        onChange={setShowAdvanced}
      />

      {showAdvanced && (
        <>
          {(mode === "auto" || mode === "media" || mode === "audio") && (
            <>
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

          <Form.Dropdown
            id="browser"
            title="Browser Session"
            value={browser}
            onChange={(next) => setBrowser(next as BrowserOption)}
          >
            {BROWSER_OPTIONS.map((b) => (
              <Form.Dropdown.Item
                key={b.value}
                value={b.value}
                title={b.title}
              />
            ))}
          </Form.Dropdown>
        </>
      )}
    </Form>
  );
}
