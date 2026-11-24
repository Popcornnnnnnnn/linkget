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
} from "../lib/articles";
import { normalizeLinkgetError, retryTask } from "../lib/cli";
import type {
  ArticleLocalMeta,
  DownloadResponse,
  ReadSubmitResponse,
} from "../lib/types";
import { ArticleReaderDetail } from "./ArticleReaderDetail";

export interface ResultDetailProps {
  kind: "download" | "read" | "error";
  downloadResult?: DownloadResponse;
  readResult?: ReadSubmitResponse;
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
  const [downloadRes, setDownloadRes] = useState<DownloadResponse | undefined>(
    props.downloadResult,
  );
  const [errorRes, setErrorRes] = useState(props.errorResult);
  const [kind, setKind] = useState(props.kind);
  const [isRetrying, setIsRetrying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadArticle() {
      if (kind !== "download" || !downloadRes) {
        setArticleMeta(null);
        return;
      }
      const archive = detectArticleArchive(
        downloadRes.saved_files || [],
        downloadRes.article_folder || downloadRes.destination,
      );
      if (!archive) {
        setArticleMeta(null);
        return;
      }
      const meta = await readArticleLocalMeta(archive);
      if (!cancelled) {
        setArticleMeta(meta);
      }
    }
    void loadArticle();
    return () => {
      cancelled = true;
    };
  }, [kind, downloadRes]);

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

  const dl = downloadRes;
  const savedFiles = dl?.saved_files || [];
  const primaryFile = savedFiles.find((file) => isMediaOrFileExisting(file));
  const dest = dl?.article_folder || dl?.destination || "Photos";
  const isPhotos = dest === "Photos";

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

  const fileRows =
    dl?.files && dl.files.length > 0
      ? dl.files.map((f) => {
          const name = f.path ? path.basename(f.path) : f.file || "file";
          return `- \`${name}\`${f.bytes ? ` (${formatBytes(f.bytes)})` : ""}`;
        })
      : savedFiles.map((f) => `- \`${path.basename(f)}\``);

  const summaryMd = [
    `# ${dl?.audio_mode ? "Audio Saved" : "Download Completed"}`,
    "",
    dl?.site ? `**Site:** ${dl.site}` : "",
    `**Destination:** ${dest}`,
    fileRows.length > 0
      ? `\n## Saved Files (${fileRows.length})\n\n${fileRows.join("\n")}`
      : "",
  ]
    .filter(Boolean)
    .join("\n");

  const folderExists = !isPhotos && fs.existsSync(dest);

  return (
    <Detail
      navigationTitle="Saved"
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
            text={`${dl?.saved_count ?? savedFiles.length} item(s)`}
          />
          <Detail.Metadata.Label title="Destination" text={dest} />
          {dl?.task_id && (
            <Detail.Metadata.Label title="Task ID" text={dl.task_id} />
          )}
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          <ActionPanel.Section title="Open">
            {primaryFile && (
              <Action.Open
                title="Open Primary File"
                icon={Icon.Document}
                target={primaryFile}
              />
            )}
            {isPhotos && (
              <Action.Open
                title="Open Apple Photos"
                icon={Icon.Image}
                target="/System/Applications/Photos.app"
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
