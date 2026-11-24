import {
  Action,
  ActionPanel,
  Color,
  getPreferenceValues,
  Icon,
  Keyboard,
  LaunchType,
  launchCommand,
  List,
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
  listTasks,
  normalizeLinkgetError,
  retryTask,
} from "./lib/cli";
import type {
  ArticleLocalMeta,
  DownloadTask,
  ExtensionPreferences,
  ReadTask,
  TaskEntry,
} from "./lib/types";

type TaskFilter = "all" | "articles" | "media" | "reads" | "attention";

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
  const allSavedFiles = task.items.flatMap((row) => row.saved_files || []);
  const savedFiles = allSavedFiles;
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

function buildDetailMarkdown(entry: EnrichedTask): string {
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

function DetailMetadataBlock({ entry }: { entry: EnrichedTask }) {
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

export default function BrowseTasksCommand() {
  const prefs = getPreferenceValues<ExtensionPreferences>();
  const { push } = useNavigation();

  const [entries, setEntries] = useState<EnrichedTask[]>([]);
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadTasks = useCallback(async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const res = await listTasks(
        { limit: 50 },
        { binaryPath: prefs.linkgetPath },
      );
      if (!res.ok) {
        const err = normalizeLinkgetError(res.error, "Unable to Load Tasks");
        setErrorMsg(err.message);
        setEntries([]);
        return;
      }
      const enriched = await mapWithConcurrency(res.tasks || [], 8, enrichTask);
      setEntries(enriched);
    } catch (error) {
      setErrorMsg(error instanceof Error ? error.message : String(error));
    } finally {
      setIsLoading(false);
    }
  }, [prefs.linkgetPath]);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

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
      await loadTasks();
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
      await loadTasks();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Cancel Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

  const filtered = entries.filter((entry) => {
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
  });

  return (
    <List
      isLoading={isLoading}
      isShowingDetail
      searchBarPlaceholder="Filter saved articles, downloads, and read tasks…"
      searchBarAccessory={
        <List.Dropdown
          tooltip="Filter Tasks"
          value={filter}
          onChange={(val) => setFilter(val as TaskFilter)}
        >
          <List.Dropdown.Item value="all" title="All Tasks" />
          <List.Dropdown.Item value="articles" title="Articles" />
          <List.Dropdown.Item value="media" title="Media & Audio" />
          <List.Dropdown.Item value="reads" title="Reads & Transcripts" />
          <List.Dropdown.Item value="attention" title="Needs Attention" />
        </List.Dropdown>
      }
    >
      {errorMsg ? (
        <List.EmptyView
          icon={Icon.ExclamationMark}
          title="Could Not Load Linkget Tasks"
          description={errorMsg}
          actions={
            <ActionPanel>
              <Action
                title="Retry"
                icon={Icon.ArrowClockwise}
                onAction={() => void loadTasks()}
              />
            </ActionPanel>
          }
        />
      ) : filtered.length === 0 ? (
        <List.EmptyView
          icon={Icon.Tray}
          title="No Matching Linkget Tasks"
          description="Save a link, article, or transcript with Save Link."
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
                title="Refresh Tasks"
                icon={Icon.ArrowClockwise}
                shortcut={Keyboard.Shortcut.Common.Refresh}
                onAction={() => void loadTasks()}
              />
            </ActionPanel>
          }
        />
      ) : (
        filtered.map((entry) => {
          const visual = statusVisual(entry);
          const meta = entry.articleMeta;
          const dlItem =
            entry.task.kind === "download" ? entry.task.items[0] : undefined;
          const destFolder =
            meta?.folderPath ||
            dlItem?.destination ||
            (entry.task.kind === "read" ? entry.task.outputs[0] : undefined) ||
            (entry.task.kind === "download"
              ? entry.task.destination
              : undefined);

          const accessories: List.Item.Accessory[] = [];
          if (entry.isArticle) {
            accessories.push({
              tag: {
                value: meta?.site || "Article",
                color: entry.existsOnDisk ? Color.Green : Color.SecondaryText,
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
                  markdown={buildDetailMarkdown(entry)}
                  metadata={<DetailMetadataBlock entry={entry} />}
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
                        shortcut={{ modifiers: ["cmd", "shift"], key: "r" }}
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
                    {entry.existsOnDisk && destFolder && !entry.isPhotos && (
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
                          onAction={() => void handleCancelRead(entry.task.id)}
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

                  <ActionPanel.Section title="Extension">
                    <Action
                      title="Refresh Tasks"
                      icon={Icon.ArrowClockwise}
                      shortcut={Keyboard.Shortcut.Common.Refresh}
                      onAction={() => void loadTasks()}
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
                  </ActionPanel.Section>
                </ActionPanel>
              }
            />
          );
        })
      )}
    </List>
  );
}
