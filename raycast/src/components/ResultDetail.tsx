import {
  Action,
  ActionPanel,
  Color,
  Detail,
  Icon,
  Keyboard,
  LaunchType,
  launchCommand,
  showToast,
  Toast,
  useNavigation,
} from "@raycast/api";
import fs from "node:fs";
import path from "node:path";
import { useEffect, useState } from "react";
import { pathToFileURL } from "node:url";
import {
  detectArticleArchive,
  formatBytes,
  readArticleLocalMeta,
  readArticleMarkdown,
} from "../lib/articles";
import { normalizeLinkgetError, retryTask } from "../lib/cli";
import { shortenHomePath, summarizeMediaFiles } from "../lib/task-format";
import type {
  ArticleLocalMeta,
  BatchDownloadResponse,
  DownloadResponse,
  ReadSubmitResponse,
} from "../lib/types";
import { ArticleReaderDetail } from "./ArticleReaderDetail";

export interface ResultDetailProps {
  kind: "download" | "read" | "batch" | "error";
  downloadResult?: DownloadResponse;
  readResult?: ReadSubmitResponse;
  batchResult?: BatchDownloadResponse;
  errorResult?: {
    title: string;
    message: string;
    nextStep?: string;
    category?: string;
    retryable?: boolean;
    taskId?: string | null;
  };
  binaryPath?: string;
}

function isMediaOrFileExisting(filePath: string): boolean {
  try {
    return fs.lstatSync(filePath).isFile();
  } catch {
    return false;
  }
}

export function ResultDetail(props: ResultDetailProps) {
  const { push } = useNavigation();
  const [articleMeta, setArticleMeta] = useState<ArticleLocalMeta | null>(null);
  const [articleMdContent, setArticleMdContent] = useState<string | null>(null);
  const [downloadRes, setDownloadRes] = useState<DownloadResponse | undefined>(
    props.downloadResult,
  );
  const [batchRes, setBatchRes] = useState<BatchDownloadResponse | undefined>(
    props.batchResult,
  );
  const [errorRes, setErrorRes] = useState(props.errorResult);
  const [kind, setKind] = useState(props.kind);
  const [isRetrying, setIsRetrying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadArticle() {
      if (kind !== "download" || !downloadRes) {
        setArticleMeta(null);
        setArticleMdContent(null);
        return;
      }
      const archive = detectArticleArchive(
        downloadRes.saved_files || [],
        downloadRes.article_folder || downloadRes.destination,
      );
      if (!archive) {
        setArticleMeta(null);
        setArticleMdContent(null);
        return;
      }
      const meta = await readArticleLocalMeta(archive);
      if (!cancelled) {
        setArticleMeta(meta);
        setArticleMdContent(null);
      }
      if (meta.exists && meta.mdPath) {
        try {
          const mdRes = await readArticleMarkdown(meta.mdPath, 512 * 1024);
          if (!cancelled && !mdRes.truncated) {
            setArticleMdContent(mdRes.markdown);
          }
        } catch {
          // ignore unreadable markdown
        }
      }
    }
    void loadArticle();
    return () => {
      cancelled = true;
    };
  }, [kind, downloadRes]);

  const handleRetryBatch = async (taskId: string) => {
    setIsRetrying(true);
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: `Retrying failed items in ${taskId}…`,
    });
    try {
      const res = await retryTask(taskId, { binaryPath: props.binaryPath });
      const retriedMap = new Map<number, DownloadResponse>();
      for (const item of res.items || []) {
        if (typeof item.item === "number") {
          retriedMap.set(item.item, item);
        }
      }
      const prevItems = batchRes?.items || [];
      const mergedItems =
        prevItems.length > 0
          ? prevItems.map((item) =>
              typeof item.item === "number" && retriedMap.has(item.item)
                ? { ...item, ...retriedMap.get(item.item) }
                : item,
            )
          : res.items || [];
      const completedCount = mergedItems.filter(
        (item) => item.status === "completed",
      ).length;
      const failedCount = mergedItems.length - completedCount;
      setBatchRes({
        ...(batchRes || {}),
        ok: failedCount === 0 && Boolean(res.ok),
        task_id: res.task_id ?? taskId,
        completed_count: completedCount,
        failed_count: failedCount,
        items: mergedItems,
        error: res.error,
      });
      if (res.ok && failedCount === 0) {
        toast.style = Toast.Style.Success;
        toast.title = "All Batch Items Completed";
      } else {
        const errInfo = normalizeLinkgetError(
          res.error ||
            mergedItems.find((item) => item.status !== "completed")?.error,
          "Some Items Still Failed",
        );
        toast.style = Toast.Style.Failure;
        toast.title = errInfo.title;
        toast.message = errInfo.message;
      }
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Retry Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    } finally {
      setIsRetrying(false);
    }
  };

  const handleRetryTask = async (taskId: string) => {
    setIsRetrying(true);
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: `Retrying task ${taskId}…`,
    });
    try {
      const res = await retryTask(taskId, { binaryPath: props.binaryPath });
      const firstItem = res.items?.[0];
      if (res.ok && firstItem) {
        toast.style = Toast.Style.Success;
        toast.title = "Retry Completed";
        setDownloadRes({
          ...firstItem,
          task_id: res.task_id ?? taskId,
          ok: true,
        });
        setErrorRes(undefined);
        setKind("download");
        return;
      }
      const firstFailed = res.items?.find(
        (item) => item.status !== "completed" && item.error,
      );
      const errInfo = normalizeLinkgetError(
        res.error || firstFailed?.error || firstItem?.error,
        "Retry Failed",
      );
      toast.style = Toast.Style.Failure;
      toast.title = errInfo.title;
      toast.message = errInfo.message;
      setErrorRes({
        ...errInfo,
        taskId: res.task_id ?? taskId,
      });
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Retry Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    } finally {
      setIsRetrying(false);
    }
  };

  if (kind === "error" && errorRes) {
    const mdLines = [`# ${errorRes.title}`, "", errorRes.message];
    if (errorRes.nextStep) {
      mdLines.push("", "## Next Step", "", errorRes.nextStep);
    }
    return (
      <Detail
        isLoading={isRetrying}
        navigationTitle={errorRes.title}
        markdown={mdLines.join("\n")}
        metadata={
          <Detail.Metadata>
            <Detail.Metadata.TagList title="Status">
              <Detail.Metadata.TagList.Item text="Failed" color={Color.Red} />
              {errorRes.category && (
                <Detail.Metadata.TagList.Item
                  text={errorRes.category}
                  color={Color.SecondaryText}
                />
              )}
            </Detail.Metadata.TagList>
            {errorRes.taskId && (
              <Detail.Metadata.Label title="Task ID" text={errorRes.taskId} />
            )}
          </Detail.Metadata>
        }
        actions={
          <ActionPanel>
            {errorRes.retryable && errorRes.taskId && (
              <Action
                title="Retry Download"
                icon={Icon.ArrowClockwise}
                onAction={() => void handleRetryTask(errorRes.taskId as string)}
              />
            )}
            <Action
              title="Open Status Board"
              icon={Icon.List}
              onAction={() =>
                void launchCommand({
                  name: "status-board",
                  type: LaunchType.UserInitiated,
                })
              }
            />
            <Action.CopyToClipboard
              title="Copy Error Details"
              content={[errorRes.title, errorRes.message, errorRes.nextStep]
                .filter(Boolean)
                .join("\n\n")}
            />
          </ActionPanel>
        }
      />
    );
  }

  if (kind === "read" && props.readResult) {
    const read = props.readResult;
    const md = [
      "# Background Read Task Queued",
      "",
      `Task \`${read.task_id || "unknown"}\` is now **${read.status || "queued"}**.`,
      "",
      "Check **Status Board** to inspect progress, open outputs, or cancel the job.",
    ].join("\n");
    return (
      <Detail
        navigationTitle="Read Task Submitted"
        markdown={md}
        metadata={
          <Detail.Metadata>
            <Detail.Metadata.Label
              title="Task ID"
              text={read.task_id || "Unknown"}
            />
            <Detail.Metadata.Label
              title="Status"
              text={read.status || "queued"}
            />
            {read.kind && (
              <Detail.Metadata.Label title="Kind" text={read.kind} />
            )}
          </Detail.Metadata>
        }
        actions={
          <ActionPanel>
            <Action
              title="Open Status Board"
              icon={Icon.List}
              onAction={() =>
                void launchCommand({
                  name: "status-board",
                  type: LaunchType.UserInitiated,
                })
              }
            />
            {read.task_id && (
              <Action.CopyToClipboard
                title="Copy Task ID"
                content={read.task_id}
              />
            )}
          </ActionPanel>
        }
      />
    );
  }

  if (kind === "batch" && batchRes) {
    const items = batchRes.items || [];
    const completedCount =
      batchRes.completed_count ??
      items.filter((item) => item.status === "completed").length;
    const failedCount = batchRes.failed_count ?? items.length - completedCount;
    const itemLines = items.map((item, idx) => {
      const num = item.item ?? idx + 1;
      const siteLabel = item.site || "Unknown";
      if (item.status === "completed") {
        const targetText = item.article_folder
          ? `Archived to \`${shortenHomePath(item.article_folder)}\``
          : item.article_photo
            ? `${item.article_photo === "duplicate" ? "Already in Apple Photos" : "Imported to Apple Photos"}${item.title ? ` (${item.title})` : ""}`
            : item.destination === "Photos"
              ? `Saved ${item.saved_count ?? 1} item(s) to Apple Photos`
              : `Saved ${item.saved_count ?? item.saved_files?.length ?? 0} file(s) to \`${shortenHomePath(item.destination || "folder")}\``;
        return `- **#${num} · Completed** · ${siteLabel} — ${targetText}`;
      }
      const err = normalizeLinkgetError(item.error, "Failed");
      return `- **#${num} · ${err.title}** · ${siteLabel} — ${err.message}`;
    });
    const firstDestFolder = items
      .map((item) => item.article_folder || item.destination)
      .find(
        (destPath) =>
          destPath && destPath !== "Photos" && fs.existsSync(destPath),
      );
    const md = [
      `# Batch ${failedCount === 0 ? "Completed" : "Finished with Errors"}`,
      "",
      `**Completed:** ${completedCount}/${items.length} · **Failed:** ${failedCount}`,
      "",
      ...itemLines,
    ].join("\n");
    return (
      <Detail
        isLoading={isRetrying}
        navigationTitle={`Batch (${completedCount}/${items.length})`}
        markdown={md}
        metadata={
          <Detail.Metadata>
            <Detail.Metadata.TagList title="Status">
              <Detail.Metadata.TagList.Item
                text={failedCount === 0 ? "Completed" : `${failedCount} Failed`}
                color={failedCount === 0 ? Color.Green : Color.Orange}
              />
              <Detail.Metadata.TagList.Item
                text={`${completedCount}/${items.length} OK`}
                color={Color.Blue}
              />
            </Detail.Metadata.TagList>
            {batchRes.task_id && (
              <Detail.Metadata.Label title="Task ID" text={batchRes.task_id} />
            )}
          </Detail.Metadata>
        }
        actions={
          <ActionPanel>
            {failedCount > 0 && batchRes.task_id && (
              <Action
                title="Retry Failed Items"
                icon={Icon.ArrowClockwise}
                onAction={() =>
                  void handleRetryBatch(batchRes.task_id as string)
                }
              />
            )}
            <Action
              title="Open Status Board"
              icon={Icon.List}
              onAction={() =>
                void launchCommand({
                  name: "status-board",
                  type: LaunchType.UserInitiated,
                })
              }
            />
            {firstDestFolder && (
              <Action.ShowInFinder
                title="Show Destination in Finder"
                path={firstDestFolder}
                shortcut={Keyboard.Shortcut.Common.Open}
              />
            )}
            {batchRes.task_id && (
              <Action.CopyToClipboard
                title="Copy Task ID"
                content={batchRes.task_id}
              />
            )}
          </ActionPanel>
        }
      />
    );
  }

  const dl = downloadRes;
  const savedFiles = dl?.saved_files || [];
  const dest = dl?.article_folder || dl?.destination || "Photos";
  const isPhotos = dest === "Photos";
  const primaryFile = isPhotos
    ? undefined
    : savedFiles.find((file) => isMediaOrFileExisting(file));

  if (articleMeta && articleMeta.exists) {
    const title = articleMeta.title || dl?.title || "Article Archived";
    const safeAlt = title.replace(/[[\]]/g, "");
    const parts: string[] = [];
    if (articleMeta.coverPath) {
      parts.push(`![${safeAlt}](${pathToFileURL(articleMeta.coverPath).href})`);
    }
    parts.push(`# ${title}`);
    if (articleMeta.excerpt) {
      parts.push(articleMeta.excerpt);
    }
    return (
      <Detail
        navigationTitle={title}
        markdown={parts.join("\n\n")}
        metadata={
          <Detail.Metadata>
            <Detail.Metadata.TagList title="Result">
              <Detail.Metadata.TagList.Item
                text="Article Archived"
                color={Color.Green}
              />
              {dl?.site && (
                <Detail.Metadata.TagList.Item
                  text={dl.site}
                  color={Color.Blue}
                />
              )}
            </Detail.Metadata.TagList>
            {(articleMeta.author || dl?.author) && (
              <Detail.Metadata.Label
                title="Author"
                text={(articleMeta.author || dl?.author) as string}
              />
            )}
            <Detail.Metadata.Label
              title="Assets"
              text={`${dl?.assets_downloaded ?? articleMeta.assetsDownloaded ?? articleMeta.assetCount} downloaded${dl?.assets_failed ? `, ${dl.assets_failed} failed` : ""}`}
            />
            <Detail.Metadata.Label
              title="Saved Files"
              text={`${dl?.saved_count ?? savedFiles.length} files`}
            />
            <Detail.Metadata.Label
              title="Folder"
              text={articleMeta.folderPath}
            />
          </Detail.Metadata>
        }
        actions={
          <ActionPanel>
            <ActionPanel.Section title="Open Article">
              {articleMeta.htmlPath && (
                <Action.Open
                  title="Open HTML Article in Browser"
                  icon={Icon.Globe}
                  target={articleMeta.htmlPath}
                />
              )}
              {articleMeta.mdPath && (
                <Action
                  title="Read Full Article in Raycast"
                  icon={Icon.Book}
                  shortcut={{ modifiers: ["cmd", "shift"], key: "r" }}
                  onAction={() =>
                    push(<ArticleReaderDetail meta={articleMeta} />)
                  }
                />
              )}
              <Action.ShowInFinder
                title="Show Archive Folder in Finder"
                path={articleMeta.folderPath}
                shortcut={Keyboard.Shortcut.Common.Open}
              />
              {articleMeta.mdPath && (
                <Action.Open
                  title="Open Markdown File"
                  icon={Icon.Document}
                  target={articleMeta.mdPath}
                  shortcut={{ modifiers: ["cmd"], key: "m" }}
                />
              )}
            </ActionPanel.Section>
            <ActionPanel.Section title="Other">
              {articleMdContent && (
                <Action.CopyToClipboard
                  title="Copy Article Markdown"
                  content={articleMdContent}
                  shortcut={Keyboard.Shortcut.Common.Copy}
                />
              )}
              <Action.CopyToClipboard
                title="Copy Archive Folder Path"
                content={articleMeta.folderPath}
                shortcut={Keyboard.Shortcut.Common.CopyPath}
              />
              <Action
                title="Open Status Board"
                icon={Icon.List}
                onAction={() =>
                  void launchCommand({
                    name: "status-board",
                    type: LaunchType.UserInitiated,
                  })
                }
              />
            </ActionPanel.Section>
          </ActionPanel>
        }
      />
    );
  }

  const photosSummary = isPhotos
    ? summarizeMediaFiles(savedFiles, Boolean(dl?.audio_mode))
    : null;

  const fileRows = isPhotos
    ? []
    : dl?.files && dl.files.length > 0
      ? dl.files.map((f) => {
          const name = f.path ? path.basename(f.path) : f.file || "file";
          return `- \`${name}\`${f.bytes ? ` (${formatBytes(f.bytes)})` : ""}`;
        })
      : savedFiles.map((f) => `- \`${path.basename(f)}\``);

  const summaryMd = dl?.article_photo
    ? [
        `# ${dl.title || "X Article Saved to Apple Photos"}`,
        "",
        dl.author ? `**Author:** ${dl.author}` : "",
        `**Status:** ${dl.article_photo === "duplicate" ? "Already in Apple Photos" : "Imported to Apple Photos"}`,
        typeof dl.assets_downloaded === "number"
          ? `**Assets:** ${dl.assets_downloaded} downloaded${dl.assets_failed ? `, ${dl.assets_failed} failed` : ""}`
          : "",
      ]
        .filter(Boolean)
        .join("\n")
    : isPhotos
      ? [
          `# ${photosSummary?.label || "Saved to Apple Photos"}`,
          "",
          dl?.site ? `**Source:** ${dl.site}` : "",
          photosSummary && photosSummary.extensions.length > 0
            ? `Imported **${photosSummary.label.toLowerCase()}** (${photosSummary.extensions.map((ext) => `\`${ext}\``).join(", ")}) into **Apple Photos**.`
            : "Imported into **Apple Photos**.",
        ]
          .filter(Boolean)
          .join("\n")
      : [
          `# ${dl?.audio_mode ? "Audio Saved" : "Download Completed"}`,
          "",
          dl?.site ? `**Site:** ${dl.site}` : "",
          `**Destination:** \`${shortenHomePath(dest)}\``,
          fileRows.length > 0
            ? `\n## Saved Files (${fileRows.length})\n\n${fileRows.join("\n")}`
            : "",
        ]
          .filter(Boolean)
          .join("\n");

  const folderExists = !isPhotos && fs.existsSync(dest);

  return (
    <Detail
      navigationTitle={isPhotos ? "Saved to Photos" : "Saved"}
      markdown={summaryMd}
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.TagList title="Status">
            <Detail.Metadata.TagList.Item
              text={dl?.status || "completed"}
              color={Color.Green}
            />
            {dl?.site && (
              <Detail.Metadata.TagList.Item text={dl.site} color={Color.Blue} />
            )}
            {dl?.audio_mode && (
              <Detail.Metadata.TagList.Item text="Audio" color={Color.Purple} />
            )}
          </Detail.Metadata.TagList>
          <Detail.Metadata.Label
            title="Saved"
            text={
              isPhotos
                ? photosSummary?.label || `${dl?.saved_count ?? 1} item(s)`
                : `${dl?.saved_count ?? savedFiles.length} item(s)`
            }
          />
          <Detail.Metadata.Label
            title="Destination"
            text={isPhotos ? "Apple Photos" : shortenHomePath(dest)}
          />
          {dl?.task_id && (
            <Detail.Metadata.Label title="Task ID" text={dl.task_id} />
          )}
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          <ActionPanel.Section title="Open">
            {isPhotos && (
              <Action.Open
                title="Open Apple Photos"
                icon={Icon.Image}
                target="/System/Applications/Photos.app"
              />
            )}
            {!isPhotos && primaryFile && (
              <Action.Open
                title="Open Primary File"
                icon={Icon.Document}
                target={primaryFile}
              />
            )}
            {folderExists && (
              <Action.ShowInFinder
                title="Show Destination in Finder"
                path={primaryFile || dest}
                shortcut={Keyboard.Shortcut.Common.Open}
              />
            )}
          </ActionPanel.Section>
          <ActionPanel.Section title="Other">
            {!isPhotos && (
              <Action.CopyToClipboard
                title="Copy Destination Path"
                content={dest}
                shortcut={Keyboard.Shortcut.Common.CopyPath}
              />
            )}
            <Action
              title="Open Status Board"
              icon={Icon.List}
              onAction={() =>
                void launchCommand({
                  name: "status-board",
                  type: LaunchType.UserInitiated,
                })
              }
            />
          </ActionPanel.Section>
        </ActionPanel>
      }
    />
  );
}
