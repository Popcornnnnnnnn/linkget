import {
  Action,
  ActionPanel,
  Alert,
  Color,
  confirmAlert,
  getPreferenceValues,
  Icon,
  Keyboard,
  LaunchType,
  launchCommand,
  List,
  open,
  openExtensionPreferences,
  showToast,
  Toast,
  useNavigation,
} from "@raycast/api";
import fs from "node:fs";
import path from "node:path";
import { useCallback, useEffect, useState } from "react";
import { pathToFileURL } from "node:url";
import { ArticleReaderDetail } from "./components/ArticleReaderDetail";
import {
  detectArticleArchive,
  formatBytes,
  formatTaskDate,
  readArticleLocalMeta,
} from "./lib/articles";
import {
  cancelReadTask,
  checkUpgrade,
  clearArticleFolderConfig,
  getConfig,
  getDoctor,
  listTasks,
  normalizeLinkgetError,
  resolveEffectiveArticleFolder,
  resolveLinkgetBinary,
  retryTask,
  runClean,
  setArticleFolderConfig,
  setDefaultDestinationConfig,
} from "./lib/cli";
import type {
  ArticleLocalMeta,
  CleanResponse,
  ConfigResponse,
  DoctorResponse,
  DownloadTask,
  ExtensionPreferences,
  ReadTask,
  TaskEntry,
  UpgradeCheckResponse,
} from "./lib/types";

type BoardFilter =
  "all" | "articles" | "media" | "reads" | "attention" | "system";

interface EnrichedTask {
  task: TaskEntry;
  isArticle: boolean;
  articleMeta: ArticleLocalMeta | null;
  primaryPath: string | null;
  existsOnDisk: boolean;
  isPhotos: boolean;
  displayTitle: string;
  displaySubtitle: string;
  formattedDate: string | null;
}

function pathExists(targetPath: string): boolean {
  try {
    fs.lstatSync(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIdx = 0;

  async function worker() {
    while (nextIdx < items.length) {
      const current = nextIdx;
      nextIdx += 1;
      results[current] = await fn(items[current]);
    }
  }

  const workers = Array.from(
    { length: Math.min(concurrency, items.length) },
    () => worker(),
  );
  await Promise.all(workers);
  return results;
}

async function enrichTask(task: TaskEntry): Promise<EnrichedTask> {
  const formattedDate = formatTaskDate(task.id);

  if (task.kind === "read") {
    const outFolder = task.outputs[0] || null;
    const archive = outFolder ? detectArticleArchive([], outFolder) : null;
    const articleMeta = archive ? await readArticleLocalMeta(archive) : null;
    const existsOnDisk = Boolean(
      (articleMeta && articleMeta.exists) ||
      (outFolder && pathExists(outFolder)),
    );
    const displayTitle =
      articleMeta?.title ||
      (outFolder ? path.basename(outFolder) : `Read (${task.task_kind})`);
    return {
      task,
      isArticle: Boolean(archive),
      articleMeta,
      primaryPath: outFolder,
      existsOnDisk,
      isPhotos: false,
      displayTitle,
      displaySubtitle: formattedDate || task.phase || task.id,
      formattedDate,
    };
  }

  const firstItem = task.items[0];
  const savedFiles = task.items.flatMap((row) => row.saved_files || []);
  const itemDest = firstItem?.destination || task.destination;
  const isPhotos =
    task.items.length > 0 &&
    task.items.every(
      (row) => (row.destination || task.destination) === "Photos",
    );

  const archive = detectArticleArchive(savedFiles, isPhotos ? null : itemDest);
  const articleMeta = archive ? await readArticleLocalMeta(archive) : null;

  const existingSaved = savedFiles.find((f) => pathExists(f)) || null;
  const primaryPath =
    existingSaved || (isPhotos ? null : pathExists(itemDest) ? itemDest : null);
  const existsOnDisk = isPhotos
    ? task.status === "completed"
    : Boolean(articleMeta?.exists || primaryPath);

  let displayTitle = articleMeta?.title || "";
  if (!displayTitle) {
    if (savedFiles.length > 0) {
      displayTitle = path.basename(savedFiles[0]);
      if (savedFiles.length > 1) {
        displayTitle += ` (+${savedFiles.length - 1})`;
      }
    } else if (isPhotos) {
      displayTitle = task.audio ? "Audio to Photos" : "Apple Photos Import";
    } else {
      displayTitle = path.basename(itemDest) || task.id;
    }
  }

  const displaySubtitle =
    articleMeta?.author ||
    formattedDate ||
    (isPhotos ? "Apple Photos" : task.destination);

  return {
    task,
    isArticle: Boolean(archive),
    articleMeta,
    primaryPath,
    existsOnDisk,
    isPhotos,
    displayTitle,
    displaySubtitle,
    formattedDate,
  };
}

function statusVisual(entry: EnrichedTask): {
  icon: Icon;
  color: Color;
  label: string;
} {
  const { task, isArticle, isPhotos } = entry;
  if (task.status === "completed") {
    if (isArticle) {
      return { icon: Icon.Book, color: Color.Green, label: "Completed" };
    }
    if (task.kind === "download" && task.audio) {
      return { icon: Icon.Music, color: Color.Purple, label: "Completed" };
    }
    if (isPhotos) {
      return { icon: Icon.Image, color: Color.Green, label: "Completed" };
    }
    if (task.kind === "read") {
      return { icon: Icon.Document, color: Color.Green, label: "Completed" };
    }
    return { icon: Icon.CheckCircle, color: Color.Green, label: "Completed" };
  }
  if (task.status === "running" || task.status === "queued") {
    return { icon: Icon.Clock, color: Color.Blue, label: task.status };
  }
  if (task.status === "cancelled" || task.status === "interrupted") {
    return { icon: Icon.MinusCircle, color: Color.Orange, label: task.status };
  }
  return {
    icon: Icon.ExclamationMark,
    color: Color.Red,
    label: task.status.replace(/_/g, " "),
  };
}

function buildTaskDetailMarkdown(entry: EnrichedTask): string {
  const { task, articleMeta } = entry;

  if (articleMeta && articleMeta.exists) {
    const title = articleMeta.title || entry.displayTitle;
    const safeAlt = title.replace(/[[\]]/g, "");
    const parts: string[] = [];
    if (articleMeta.coverPath) {
      parts.push(`![${safeAlt}](${pathToFileURL(articleMeta.coverPath).href})`);
    }
    parts.push(`## ${title}`);
    if (articleMeta.excerpt) {
      parts.push(articleMeta.excerpt);
    } else {
      parts.push(
        "*Open `article.html` or press Read Full Article to view the saved content.*",
      );
    }
    return parts.join("\n\n");
  }

  if (task.kind === "download") {
    const dl: DownloadTask = task;
    const failedItem = dl.items.find(
      (row) => row.status !== "completed" && row.failure,
    );
    const parts = [`## ${entry.displayTitle}`];
    if (failedItem?.failure) {
      parts.push(
        `### Failure${dl.items.length > 1 ? ` (Item ${failedItem.item})` : ""}\n\n${failedItem.failure.message || "Unknown error"}`,
      );
      if (failedItem.failure.next_step) {
        parts.push(`**Next Step:** ${failedItem.failure.next_step}`);
      }
    }
    const files = dl.items.flatMap((row) => row.saved_files || []);
    if (files.length > 0) {
      parts.push(
        `### Saved Files (${files.length})\n\n${files
          .slice(0, 20)
          .map((f) => `- \`${path.basename(f)}\``)
          .join("\n")}`,
      );
    }
    const retained = dl.items.flatMap((row) => row.retained_paths || []);
    if (retained.length > 0) {
      parts.push(
        `### Retained Staging Paths\n\n${retained
          .slice(0, 10)
          .map((p) => `- \`${p}\``)
          .join("\n")}`,
      );
    }
    if (!entry.existsOnDisk && !entry.isPhotos) {
      parts.push(
        "> *Saved files are no longer present at their original folder on disk.*",
      );
    }
    return parts.join("\n\n");
  }

  const rd: ReadTask = task;
  const parts = [`## ${entry.displayTitle}`, `**Phase:** ${rd.phase}`];
  if (rd.failure) {
    parts.push(`### Failure\n\n${rd.failure}`);
  }
  if (rd.outputs.length > 0) {
    parts.push(
      `### Outputs\n\n${rd.outputs.map((o) => `- \`${o}\``).join("\n")}`,
    );
  }
  return parts.join("\n\n");
}

function TaskDetailMetadata({ entry }: { entry: EnrichedTask }) {
  const { task, articleMeta } = entry;
  const visual = statusVisual(entry);

  if (articleMeta && articleMeta.exists) {
    return (
      <List.Item.Detail.Metadata>
        <List.Item.Detail.Metadata.TagList title="Archive">
          <List.Item.Detail.Metadata.TagList.Item
            text={articleMeta.site || "Article"}
            color={Color.Green}
          />
          <List.Item.Detail.Metadata.TagList.Item
            text={`${articleMeta.assetsDownloaded ?? articleMeta.assetCount} asset(s)`}
            color={Color.Blue}
          />
        </List.Item.Detail.Metadata.TagList>
        {articleMeta.author && (
          <List.Item.Detail.Metadata.Label
            title="Author"
            text={articleMeta.author}
          />
        )}
        {articleMeta.publishedAt && (
          <List.Item.Detail.Metadata.Label
            title="Published"
            text={articleMeta.publishedAt}
          />
        )}
        {entry.formattedDate && (
          <List.Item.Detail.Metadata.Label
            title="Archived"
            text={entry.formattedDate}
          />
        )}
        {articleMeta.totalBytes > 0 && (
          <List.Item.Detail.Metadata.Label
            title="Core Size"
            text={formatBytes(articleMeta.totalBytes)}
          />
        )}
        {articleMeta.canonicalUrl && (
          <List.Item.Detail.Metadata.Link
            title="Source"
            target={articleMeta.canonicalUrl}
            text={articleMeta.reference || "Open Source URL"}
          />
        )}
        <List.Item.Detail.Metadata.Label
          title="Folder"
          text={articleMeta.folderPath}
        />
      </List.Item.Detail.Metadata>
    );
  }

  return (
    <List.Item.Detail.Metadata>
      <List.Item.Detail.Metadata.TagList title="Status">
        <List.Item.Detail.Metadata.TagList.Item
          text={visual.label}
          color={visual.color}
        />
        <List.Item.Detail.Metadata.TagList.Item
          text={
            task.kind === "read"
              ? `Read (${task.task_kind})`
              : task.audio
                ? "Audio"
                : "Download"
          }
          color={Color.SecondaryText}
        />
      </List.Item.Detail.Metadata.TagList>
      {entry.formattedDate && (
        <List.Item.Detail.Metadata.Label
          title="Created"
          text={entry.formattedDate}
        />
      )}
      {task.kind === "download" ? (
        <>
          <List.Item.Detail.Metadata.Label
            title="Items"
            text={`${task.completed_items}/${task.total_items}`}
          />
          <List.Item.Detail.Metadata.Label
            title="Destination"
            text={task.items[0]?.destination || task.destination}
          />
        </>
      ) : (
        <List.Item.Detail.Metadata.Label title="Phase" text={task.phase} />
      )}
      <List.Item.Detail.Metadata.Label title="Task ID" text={task.id} />
    </List.Item.Detail.Metadata>
  );
}

function formatDestinationSetting(
  setting?: { mode: string; path?: string } | null,
  fallbackText = "Not set",
): string {
  if (!setting) {
    return fallbackText;
  }
  if (setting.mode === "photos") {
    return "Apple Photos";
  }
  if (setting.mode === "cwd") {
    return "Current Folder (terminal cwd)";
  }
  return setting.path || "Folder";
}

export default function StatusBoardCommand() {
  const prefs = getPreferenceValues<ExtensionPreferences>();
  const { push } = useNavigation();

  const [filter, setFilter] = useState<BoardFilter>("all");
  const [entries, setEntries] = useState<EnrichedTask[]>([]);
  const [doctor, setDoctor] = useState<DoctorResponse | null>(null);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [cleanPreview, setCleanPreview] = useState<CleanResponse | null>(null);
  const [upgradeInfo, setUpgradeInfo] = useState<UpgradeCheckResponse | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tasksError, setTasksError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    setTasksError(null);
    try {
      const [tasksRes, docRes, cfgRes, cleanRes] = await Promise.allSettled([
        listTasks({ limit: 50 }, { binaryPath: prefs.linkgetPath }),
        getDoctor({ binaryPath: prefs.linkgetPath }),
        getConfig({ binaryPath: prefs.linkgetPath }),
        runClean({ apply: false }, { binaryPath: prefs.linkgetPath }),
      ]);

      if (tasksRes.status === "fulfilled" && tasksRes.value.ok) {
        const enriched = await mapWithConcurrency(
          tasksRes.value.tasks || [],
          8,
          enrichTask,
        );
        setEntries(enriched);
      } else if (tasksRes.status === "fulfilled" && !tasksRes.value.ok) {
        setTasksError(
          normalizeLinkgetError(tasksRes.value.error, "Unable to Load Tasks")
            .message,
        );
      } else if (tasksRes.status === "rejected") {
        setTasksError(
          tasksRes.reason instanceof Error
            ? tasksRes.reason.message
            : String(tasksRes.reason),
        );
      }

      if (docRes.status === "fulfilled") {
        if (docRes.value.tools || docRes.value.version) {
          setDoctor(docRes.value);
        } else if (!docRes.value.ok) {
          setLoadError(
            normalizeLinkgetError(docRes.value.error, "Linkget Doctor Failed")
              .message,
          );
        }
      } else {
        setLoadError(
          docRes.reason instanceof Error
            ? docRes.reason.message
            : String(docRes.reason),
        );
      }

      if (cfgRes.status === "fulfilled" && cfgRes.value.ok) {
        setConfig(cfgRes.value);
      }
      if (
        cleanRes.status === "fulfilled" &&
        typeof cleanRes.value.eligible_count === "number"
      ) {
        setCleanPreview(cleanRes.value);
      }
    } finally {
      setIsLoading(false);
    }
  }, [prefs.linkgetPath]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleRetry = async (taskId: string) => {
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: `Retrying ${taskId}…`,
    });
    try {
      const res = await retryTask(taskId, { binaryPath: prefs.linkgetPath });
      if (res.ok) {
        toast.style = Toast.Style.Success;
        toast.title = "Retry Completed";
      } else {
        const failedItem = res.items?.find(
          (item) => item.status !== "completed" && item.error,
        );
        const err = normalizeLinkgetError(
          res.error || failedItem?.error || res.items?.[0]?.error,
          "Retry Failed",
        );
        toast.style = Toast.Style.Failure;
        toast.title = err.title;
        toast.message = err.message;
      }
      await refresh();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Retry Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

  const handleCancelRead = async (taskId: string) => {
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: `Cancelling ${taskId}…`,
    });
    try {
      const res = await cancelReadTask(taskId, {
        binaryPath: prefs.linkgetPath,
      });
      if (res.exitCode === 0) {
        toast.style = Toast.Style.Success;
        toast.title = "Cancellation Requested";
      } else {
        toast.style = Toast.Style.Failure;
        toast.title = "Cancel Failed";
        toast.message = res.stderr.trim() || res.stdout.trim();
      }
      await refresh();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Cancel Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

  const handleCheckUpgrade = async () => {
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Checking GitHub Releases…",
    });
    try {
      const res = await checkUpgrade({ binaryPath: prefs.linkgetPath });
      setUpgradeInfo(res);
      if (res.ok === false || res.error) {
        const err = normalizeLinkgetError(res.error, "Update Check Failed");
        toast.style = Toast.Style.Failure;
        toast.title = err.title;
        toast.message = err.message;
        return;
      }
      if (res.update_available) {
        toast.style = Toast.Style.Success;
        toast.title = `Update Available: v${res.latest}`;
        toast.message = res.upgrade_command;
        if (res.release_url) {
          toast.primaryAction = {
            title: "Open Release Page",
            onAction: () => {
              void open(res.release_url as string);
            },
          };
        }
      } else {
        toast.style = Toast.Style.Success;
        toast.title = "Linkget Is Up to Date";
        toast.message = `Current: v${res.current}`;
      }
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Update Check Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

  const handleSetArticleFolder = async () => {
    const targetFolder = resolveEffectiveArticleFolder(prefs, null);
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Saving Article Folder Default…",
    });
    try {
      await setArticleFolderConfig(targetFolder, {
        binaryPath: prefs.linkgetPath,
      });
      toast.style = Toast.Style.Success;
      toast.title = "Default Article Folder Updated";
      toast.message = targetFolder;
      await refresh();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Failed to Update Config";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

  const handleClearArticleFolder = async () => {
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Clearing CLI Article Folder Override…",
    });
    try {
      await clearArticleFolderConfig({ binaryPath: prefs.linkgetPath });
      toast.style = Toast.Style.Success;
      toast.title = "Cleared Article Folder Override";
      await refresh();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Failed to Clear Override";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

  const handleSetMediaDefault = async (mode: "photos" | "folder") => {
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Updating Default Destination…",
    });
    try {
      await setDefaultDestinationConfig(
        mode,
        mode === "folder"
          ? prefs.defaultMediaFolder || "~/Downloads"
          : undefined,
        { binaryPath: prefs.linkgetPath },
      );
      toast.style = Toast.Style.Success;
      toast.title = "Default Destination Updated";
      await refresh();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Failed to Update Config";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

  const handleRunCleanApply = async () => {
    if (!cleanPreview || cleanPreview.eligible_count === 0) {
      await showToast({
        style: Toast.Style.Success,
        title: "Nothing Eligible to Clean",
      });
      return;
    }

    const confirmed = await confirmAlert({
      title: `Remove ${cleanPreview.eligible_count} Temporary Stage(s)?`,
      message: `This will reclaim ${formatBytes(cleanPreview.reclaimable_bytes)} from old completed staging folders. Saved articles, user downloads, and active or Photos-unverified stages remain untouched.`,
      primaryAction: {
        title: "Clean Eligible Stages",
        style: Alert.ActionStyle.Destructive,
      },
    });
    if (!confirmed) {
      return;
    }

    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Cleaning Eligible Stages…",
    });
    try {
      const res = await runClean(
        { apply: true },
        { binaryPath: prefs.linkgetPath },
      );
      if (!res.ok && res.deleted_count === 0) {
        const err = normalizeLinkgetError(res.error, "Clean Failed");
        toast.style = Toast.Style.Failure;
        toast.title = err.title;
        toast.message = err.message;
      } else {
        toast.style = Toast.Style.Success;
        toast.title = `Removed ${res.deleted_count} Stage(s)`;
        toast.message =
          res.errors && res.errors.length > 0
            ? `Freed ${formatBytes(res.freed_bytes)} (${res.errors.length} receipt warning(s))`
            : `Freed ${formatBytes(res.freed_bytes)}`;
      }
      await refresh();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Clean Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

  const showTasks = filter !== "system";
  const showSystem = filter === "all" || filter === "system";

  const filteredTasks = showTasks
    ? entries.filter((entry) => {
        if (filter === "articles") return entry.isArticle;
        if (filter === "media")
          return entry.task.kind === "download" && !entry.isArticle;
        if (filter === "reads") return entry.task.kind === "read";
        if (filter === "attention") {
          return (
            entry.task.status !== "completed" &&
            entry.task.status !== "running" &&
            entry.task.status !== "queued"
          );
        }
        return true;
      })
    : [];

  const binaryPath = resolveLinkgetBinary(prefs.linkgetPath);
  const effectiveArticleFolder = resolveEffectiveArticleFolder(prefs, config);
  const tools = doctor?.tools ? Object.entries(doctor.tools) : [];
  const sessions = doctor?.sessions ? Object.entries(doctor.sessions) : [];

  const installMarkdown = [
    "# Linkget Installation",
    "",
    `- **CLI Version:** ${doctor?.version || "Unknown"}`,
    `- **Binary:** \`${binaryPath}\``,
    `- **Python:** ${doctor?.python || "Unknown"}`,
    upgradeInfo && upgradeInfo.ok !== false && !upgradeInfo.error
      ? `- **Latest Release:** ${upgradeInfo.latest} (${upgradeInfo.update_available ? "Update available" : "Up to date"})`
      : "",
    "",
    "## Saved Defaults",
    "",
    `- **Media Default:** ${formatDestinationSetting(config?.destination, "Apple Photos")}`,
    `- **Audio Default:** ${formatDestinationSetting(config?.audio_destination, "Not set (Raycast uses ~/Downloads)")}`,
    `- **Article Default:** ${formatDestinationSetting(config?.article_destination, `Not set (Raycast uses ${effectiveArticleFolder})`)}`,
    `- **Page Controller:** ${config?.page_controller?.browser || doctor?.page_controller?.browser || "auto"} (${config?.page_controller?.source || doctor?.page_controller?.source || "default"})`,
  ]
    .filter(Boolean)
    .join("\n");

  const cleanMarkdown = cleanPreview
    ? [
        "# Temporary Staging Cleanup (`linkget clean`)",
        "",
        `- **Cutoff:** older than \`${cleanPreview.older_than}\``,
        `- **Eligible to Remove:** ${cleanPreview.eligible_count} stage(s) (${formatBytes(cleanPreview.reclaimable_bytes)})`,
        `- **Protected & Kept:** ${cleanPreview.kept_count} stage(s)`,
        cleanPreview.errors && cleanPreview.errors.length > 0
          ? `- **Receipt Warnings:** ${cleanPreview.errors.length} receipt(s) could not be read and remain protected`
          : "",
        "",
        "Only verified, completed, temporary `linkget-*` staging folders older than the cutoff are eligible. User downloads, saved articles, active tasks, and unverified Photos receipts are always preserved.",
      ]
        .filter(Boolean)
        .join("\n")
    : "# Temporary Staging Cleanup\n\nLoading cleanup preview…";

  const commonBoardActions = (
    <ActionPanel.Section title="Extension">
      <Action
        title="Refresh Status Board"
        icon={Icon.ArrowClockwise}
        shortcut={Keyboard.Shortcut.Common.Refresh}
        onAction={() => void refresh()}
      />
      <Action
        title="Open Save Link"
        icon={Icon.Plus}
        shortcut={Keyboard.Shortcut.Common.New}
        onAction={() =>
          void launchCommand({
            name: "save-link",
            type: LaunchType.UserInitiated,
          })
        }
      />
      <Action
        title="Open Extension Preferences"
        icon={Icon.Gear}
        onAction={() => void openExtensionPreferences()}
      />
    </ActionPanel.Section>
  );

  const isEmptyAll = !showTasks || filteredTasks.length === 0;

  return (
    <List
      isLoading={isLoading}
      isShowingDetail
      navigationTitle="Linkget — Status Board"
      searchBarPlaceholder="Filter recent downloads, articles, engines, or sessions…"
      searchBarAccessory={
        <List.Dropdown
          tooltip="Filter Status Board"
          value={filter}
          onChange={(val) => setFilter(val as BoardFilter)}
        >
          <List.Dropdown.Item value="all" title="All (Downloads & System)" />
          <List.Dropdown.Item value="articles" title="Saved Articles" />
          <List.Dropdown.Item value="media" title="Media & Audio" />
          <List.Dropdown.Item value="reads" title="Reads & Transcripts" />
          <List.Dropdown.Item value="attention" title="Needs Attention" />
          <List.Dropdown.Item value="system" title="CLI, Engines & Sessions" />
        </List.Dropdown>
      }
    >
      {(loadError || tasksError) && !doctor && entries.length === 0 ? (
        <List.EmptyView
          icon={Icon.ExclamationMark}
          title="Linkget CLI Not Reachable"
          description={loadError || tasksError || undefined}
          actions={
            <ActionPanel>
              <Action
                title="Open Extension Preferences"
                icon={Icon.Gear}
                onAction={() => void openExtensionPreferences()}
              />
              <Action
                title="Retry"
                icon={Icon.ArrowClockwise}
                onAction={() => void refresh()}
              />
            </ActionPanel>
          }
        />
      ) : filter !== "all" && filter !== "system" && isEmptyAll ? (
        <List.EmptyView
          icon={tasksError ? Icon.ExclamationMark : Icon.Tray}
          title={
            tasksError
              ? "Unable to Load Linkget Tasks"
              : "No Matching Linkget Items"
          }
          description={
            tasksError ||
            "Use Save Link to archive an article or download media."
          }
          actions={
            <ActionPanel>
              <Action
                title="Open Save Link"
                icon={Icon.Plus}
                onAction={() =>
                  void launchCommand({
                    name: "save-link",
                    type: LaunchType.UserInitiated,
                  })
                }
              />
              <Action
                title="Refresh Status Board"
                icon={Icon.ArrowClockwise}
                shortcut={Keyboard.Shortcut.Common.Refresh}
                onAction={() => void refresh()}
              />
            </ActionPanel>
          }
        />
      ) : (
        <>
          {showTasks && tasksError && filteredTasks.length === 0 && (
            <List.Section title="Recent Downloads & Articles">
              <List.Item
                key="tasks-load-error"
                icon={{ source: Icon.ExclamationMark, tintColor: Color.Red }}
                title="Unable to Load Recent Tasks"
                subtitle={tasksError}
                detail={
                  <List.Item.Detail
                    markdown={`# Unable to Load Recent Tasks\n\n${tasksError}`}
                  />
                }
                actions={<ActionPanel>{commonBoardActions}</ActionPanel>}
              />
            </List.Section>
          )}
          {showTasks && filteredTasks.length > 0 && (
            <List.Section
              title={`Recent Downloads & Articles (${filteredTasks.length})`}
            >
              {filteredTasks.map((entry) => {
                const visual = statusVisual(entry);
                const meta = entry.articleMeta;
                const dlItem =
                  entry.task.kind === "download"
                    ? entry.task.items[0]
                    : undefined;
                const destFolder =
                  meta?.folderPath ||
                  dlItem?.destination ||
                  (entry.task.kind === "read"
                    ? entry.task.outputs[0]
                    : undefined) ||
                  (entry.task.kind === "download"
                    ? entry.task.destination
                    : undefined);

                const accessories: List.Item.Accessory[] = [];
                if (entry.isArticle) {
                  accessories.push({
                    tag: {
                      value: meta?.site || "Article",
                      color: entry.existsOnDisk
                        ? Color.Green
                        : Color.SecondaryText,
                    },
                  });
                } else if (entry.task.kind === "download" && entry.task.audio) {
                  accessories.push({
                    tag: {
                      value: entry.task.audio_format
                        ? entry.task.audio_format.toUpperCase()
                        : "Audio",
                      color: Color.Purple,
                    },
                  });
                } else if (entry.isPhotos) {
                  accessories.push({
                    tag: { value: "Photos", color: Color.Blue },
                  });
                } else if (entry.task.kind === "read") {
                  accessories.push({
                    tag: { value: "Read", color: Color.Yellow },
                  });
                }

                if (
                  !entry.existsOnDisk &&
                  !entry.isPhotos &&
                  entry.task.status === "completed"
                ) {
                  accessories.push({
                    text: "Moved/Deleted",
                    tooltip: "Original folder is no longer present on disk",
                  });
                }

                return (
                  <List.Item
                    key={`${entry.task.kind}-${entry.task.id}`}
                    icon={{ source: visual.icon, tintColor: visual.color }}
                    title={entry.displayTitle}
                    subtitle={entry.displaySubtitle}
                    accessories={accessories}
                    detail={
                      <List.Item.Detail
                        markdown={buildTaskDetailMarkdown(entry)}
                        metadata={<TaskDetailMetadata entry={entry} />}
                      />
                    }
                    actions={
                      <ActionPanel>
                        <ActionPanel.Section title="Open">
                          {meta && meta.exists && meta.htmlPath && (
                            <Action.Open
                              title="Open HTML Article in Browser"
                              icon={Icon.Globe}
                              target={meta.htmlPath}
                            />
                          )}
                          {meta && meta.exists && (
                            <Action
                              title="Read Full Article in Raycast"
                              icon={Icon.Book}
                              shortcut={{
                                modifiers: ["cmd", "shift"],
                                key: "r",
                              }}
                              onAction={() =>
                                push(<ArticleReaderDetail meta={meta} />)
                              }
                            />
                          )}
                          {!meta?.exists && entry.primaryPath && (
                            <Action.Open
                              title="Open Primary File"
                              icon={Icon.Document}
                              target={entry.primaryPath}
                            />
                          )}
                          {entry.isPhotos && (
                            <Action.Open
                              title="Open Apple Photos"
                              icon={Icon.Image}
                              target="/System/Applications/Photos.app"
                            />
                          )}
                          {entry.existsOnDisk &&
                            destFolder &&
                            !entry.isPhotos && (
                              <Action.ShowInFinder
                                title="Show in Finder"
                                path={entry.primaryPath || destFolder}
                                shortcut={Keyboard.Shortcut.Common.Open}
                              />
                            )}
                          {meta && meta.exists && meta.mdPath && (
                            <Action.Open
                              title="Open Markdown File"
                              icon={Icon.Document}
                              target={meta.mdPath}
                              shortcut={{ modifiers: ["cmd"], key: "m" }}
                            />
                          )}
                        </ActionPanel.Section>

                        <ActionPanel.Section title="Task Actions">
                          {entry.task.kind === "download" &&
                            entry.task.status !== "completed" && (
                              <Action
                                title="Retry Download"
                                icon={Icon.ArrowClockwise}
                                onAction={() => void handleRetry(entry.task.id)}
                              />
                            )}
                          {entry.task.kind === "read" &&
                            (entry.task.status === "running" ||
                              entry.task.status === "queued") && (
                              <Action
                                title="Cancel Read Task"
                                icon={Icon.Stop}
                                style={Action.Style.Destructive}
                                onAction={() =>
                                  void handleCancelRead(entry.task.id)
                                }
                              />
                            )}
                          {destFolder && !entry.isPhotos && (
                            <Action.CopyToClipboard
                              title="Copy Folder Path"
                              content={destFolder}
                              shortcut={Keyboard.Shortcut.Common.CopyPath}
                            />
                          )}
                          {meta?.canonicalUrl && (
                            <Action.CopyToClipboard
                              title="Copy Source URL"
                              content={meta.canonicalUrl}
                            />
                          )}
                        </ActionPanel.Section>

                        {commonBoardActions}
                      </ActionPanel>
                    }
                  />
                );
              })}
            </List.Section>
          )}

          {showSystem && (
            <>
              <List.Section title="System & Maintenance">
                <List.Item
                  key="system-cli-defaults"
                  icon={{
                    source: Icon.Terminal,
                    tintColor: doctor?.ok ? Color.Green : Color.Orange,
                  }}
                  title="Linkget CLI & Defaults"
                  subtitle={doctor?.version ? `v${doctor.version}` : binaryPath}
                  accessories={[
                    {
                      tag: {
                        value: doctor?.missing_tools?.length
                          ? `${doctor.missing_tools.length} missing`
                          : doctor?.ok
                            ? "Healthy"
                            : "Check Needed",
                        color:
                          doctor?.missing_tools?.length || !doctor?.ok
                            ? Color.Orange
                            : Color.Green,
                      },
                    },
                  ]}
                  detail={<List.Item.Detail markdown={installMarkdown} />}
                  actions={
                    <ActionPanel>
                      <ActionPanel.Section title="Maintenance">
                        <Action
                          title="Check for Updates"
                          icon={Icon.Download}
                          onAction={() => void handleCheckUpgrade()}
                        />
                        <Action
                          title={
                            prefs.defaultArticleFolder?.trim()
                              ? "Save Configured Article Folder Default"
                              : "Set Article Default to Downloads"
                          }
                          icon={Icon.Folder}
                          onAction={() => void handleSetArticleFolder()}
                        />
                        {config?.article_destination && (
                          <Action
                            title="Clear Article Folder Config Override"
                            icon={Icon.XMarkCircle}
                            onAction={() => void handleClearArticleFolder()}
                          />
                        )}
                        <Action
                          title="Set Media Default to Apple Photos"
                          icon={Icon.Image}
                          onAction={() => void handleSetMediaDefault("photos")}
                        />
                        <Action
                          title="Set Media Default to Folder"
                          icon={Icon.Folder}
                          onAction={() => void handleSetMediaDefault("folder")}
                        />
                      </ActionPanel.Section>
                      {commonBoardActions}
                    </ActionPanel>
                  }
                />

                <List.Item
                  key="system-stage-cleanup"
                  icon={{
                    source: Icon.Trash,
                    tintColor:
                      cleanPreview && cleanPreview.eligible_count > 0
                        ? Color.Orange
                        : Color.Green,
                  }}
                  title="Temporary Stage Cleanup"
                  subtitle={
                    cleanPreview
                      ? `${cleanPreview.eligible_count} eligible · ${formatBytes(cleanPreview.reclaimable_bytes)}`
                      : "Checking…"
                  }
                  accessories={
                    cleanPreview
                      ? [{ text: `${cleanPreview.kept_count} kept` }]
                      : undefined
                  }
                  detail={<List.Item.Detail markdown={cleanMarkdown} />}
                  actions={
                    <ActionPanel>
                      <ActionPanel.Section title="Cleanup">
                        <Action
                          title="Clean Eligible Temporary Stages"
                          icon={Icon.Trash}
                          style={Action.Style.Destructive}
                          onAction={() => void handleRunCleanApply()}
                        />
                      </ActionPanel.Section>
                      {commonBoardActions}
                    </ActionPanel>
                  }
                />
              </List.Section>

              {tools.length > 0 && (
                <List.Section title="Download Engines">
                  {tools.map(([name, info]) => (
                    <List.Item
                      key={`tool-${name}`}
                      icon={{
                        source: info.available
                          ? Icon.CheckCircle
                          : Icon.XMarkCircle,
                        tintColor: info.available ? Color.Green : Color.Red,
                      }}
                      title={name}
                      subtitle={info.version || undefined}
                      accessories={[
                        {
                          tag: {
                            value: info.available ? "Installed" : "Missing",
                            color: info.available ? Color.Green : Color.Red,
                          },
                        },
                      ]}
                      detail={
                        <List.Item.Detail
                          markdown={`# ${name}\n\n- **Available:** ${info.available ? "Yes" : "No"}\n- **Path:** ${info.path ? `\`${info.path}\`` : "Not found"}\n- **Version:** ${info.version || "Unknown"}`}
                        />
                      }
                      actions={
                        <ActionPanel>
                          {info.path && (
                            <Action.CopyToClipboard
                              title="Copy Binary Path"
                              content={info.path}
                            />
                          )}
                          {commonBoardActions}
                        </ActionPanel>
                      }
                    />
                  ))}
                </List.Section>
              )}

              {sessions.length > 0 && (
                <List.Section title="Site Sessions">
                  {sessions.map(([siteKey, session]) => {
                    const isSaved =
                      session.state === "saved" ||
                      session.state === "saved_unverified" ||
                      session.state === "valid" ||
                      session.state === "connected";
                    const statusLabel = isSaved
                      ? "Saved"
                      : session.state === "missing"
                        ? "Public Only"
                        : session.state.replace(/_/g, " ");
                    const statusColor = isSaved
                      ? Color.Blue
                      : session.state === "blocked" ||
                          session.state === "disconnected"
                        ? Color.Orange
                        : Color.SecondaryText;
                    return (
                      <List.Item
                        key={`session-${siteKey}`}
                        icon={{
                          source: isSaved ? Icon.PersonCircle : Icon.Globe,
                          tintColor: isSaved
                            ? Color.Green
                            : Color.SecondaryText,
                        }}
                        title={siteKey}
                        subtitle={session.domain}
                        accessories={[
                          {
                            tag: {
                              value: statusLabel,
                              color: statusColor,
                            },
                          },
                        ]}
                        detail={
                          <List.Item.Detail
                            markdown={`# ${siteKey} (${session.domain})\n\n- **State:** ${session.state}\n- **Detail:** ${session.detail || "None"}\n${session.modified ? `- **Modified:** ${session.modified}\n` : ""}\nRun \`linkget auth ${siteKey}\` in a terminal to connect or refresh login cookies.`}
                          />
                        }
                        actions={
                          <ActionPanel>
                            <Action.CopyToClipboard
                              title="Copy Auth Command"
                              content={`linkget auth ${siteKey}`}
                            />
                            {commonBoardActions}
                          </ActionPanel>
                        }
                      />
                    );
                  })}
                </List.Section>
              )}
            </>
          )}
        </>
      )}
    </List>
  );
}
