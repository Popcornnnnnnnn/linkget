import {
  Action,
  ActionPanel,
  Alert,
  Color,
  confirmAlert,
  getPreferenceValues,
  Icon,
  Keyboard,
  List,
  open,
  openExtensionPreferences,
  showToast,
  Toast,
} from "@raycast/api";
import { useCallback, useEffect, useState } from "react";
import { formatBytes } from "./lib/articles";
import {
  checkUpgrade,
  clearArticleFolderConfig,
  getConfig,
  getDoctor,
  normalizeLinkgetError,
  resolveEffectiveArticleFolder,
  resolveLinkgetBinary,
  runClean,
  setArticleFolderConfig,
  setDefaultDestinationConfig,
} from "./lib/cli";
import type {
  CleanResponse,
  ConfigResponse,
  DoctorResponse,
  ExtensionPreferences,
  UpgradeCheckResponse,
} from "./lib/types";

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

export default function StatusAndToolsCommand() {
  const prefs = getPreferenceValues<ExtensionPreferences>();

  const [doctor, setDoctor] = useState<DoctorResponse | null>(null);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [cleanPreview, setCleanPreview] = useState<CleanResponse | null>(null);
  const [upgradeInfo, setUpgradeInfo] = useState<UpgradeCheckResponse | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const [docRes, cfgRes, cleanRes] = await Promise.allSettled([
        getDoctor({ binaryPath: prefs.linkgetPath }),
        getConfig({ binaryPath: prefs.linkgetPath }),
        runClean(
          { apply: false },
          {
            binaryPath: prefs.linkgetPath,
          },
        ),
      ]);

      if (docRes.status === "fulfilled" && docRes.value.ok) {
        setDoctor(docRes.value);
      } else if (docRes.status === "rejected") {
        setLoadError(
          docRes.reason instanceof Error
            ? docRes.reason.message
            : String(docRes.reason),
        );
      }
      if (cfgRes.status === "fulfilled" && cfgRes.value.ok) {
        setConfig(cfgRes.value);
      }
      if (cleanRes.status === "fulfilled" && cleanRes.value.ok) {
        setCleanPreview(cleanRes.value);
      }
    } finally {
      setIsLoading(false);
    }
  }, [prefs.linkgetPath]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
      toast.style = Toast.Style.Success;
      toast.title = `Removed ${res.deleted_count} Stage(s)`;
      toast.message = `Freed ${formatBytes(res.freed_bytes)}`;
      await refresh();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Clean Failed";
      toast.message = error instanceof Error ? error.message : String(error);
    }
  };

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
        "",
        "Only verified, completed, temporary `linkget-*` staging folders older than the cutoff are eligible. User downloads, saved articles, active tasks, and unverified Photos receipts are always preserved.",
      ].join("\n")
    : "# Temporary Staging Cleanup\n\nLoading cleanup preview…";

  return (
    <List
      isLoading={isLoading}
      isShowingDetail
      navigationTitle="Linkget Status & Maintenance"
      searchBarPlaceholder="Search engines, sessions, or maintenance items…"
    >
      {loadError && !doctor ? (
        <List.EmptyView
          icon={Icon.ExclamationMark}
          title="Linkget CLI Not Reachable"
          description={loadError}
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
      ) : (
        <>
          <List.Section title="Installation & Configuration">
            <List.Item
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
                      : "Healthy",
                    color: doctor?.missing_tools?.length
                      ? Color.Orange
                      : Color.Green,
                  },
                },
              ]}
              detail={<List.Item.Detail markdown={installMarkdown} />}
              actions={
                <ActionPanel>
                  <Action
                    title="Check for Updates"
                    icon={Icon.Download}
                    onAction={() => void handleCheckUpgrade()}
                  />
                  <Action
                    title="Save Effective Article Folder to Config"
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
                  <Action
                    title="Open Extension Preferences"
                    icon={Icon.Gear}
                    onAction={() => void openExtensionPreferences()}
                  />
                  <Action
                    title="Refresh Status"
                    icon={Icon.ArrowClockwise}
                    shortcut={Keyboard.Shortcut.Common.Refresh}
                    onAction={() => void refresh()}
                  />
                </ActionPanel>
              }
            />

            <List.Item
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
                  <Action
                    title="Clean Eligible Temporary Stages"
                    icon={Icon.Trash}
                    style={Action.Style.Destructive}
                    onAction={() => void handleRunCleanApply()}
                  />
                  <Action
                    title="Refresh Status"
                    icon={Icon.ArrowClockwise}
                    shortcut={Keyboard.Shortcut.Common.Refresh}
                    onAction={() => void refresh()}
                  />
                </ActionPanel>
              }
            />
          </List.Section>

          {tools.length > 0 && (
            <List.Section title="Download Engines">
              {tools.map(([name, info]) => (
                <List.Item
                  key={name}
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
                      <Action
                        title="Refresh Status"
                        icon={Icon.ArrowClockwise}
                        shortcut={Keyboard.Shortcut.Common.Refresh}
                        onAction={() => void refresh()}
                      />
                    </ActionPanel>
                  }
                />
              ))}
            </List.Section>
          )}

          {sessions.length > 0 && (
            <List.Section title="Site Sessions">
              {sessions.map(([siteKey, session]) => {
                const isSaved = session.state !== "missing";
                return (
                  <List.Item
                    key={siteKey}
                    icon={{
                      source: isSaved ? Icon.PersonCircle : Icon.Globe,
                      tintColor: isSaved ? Color.Green : Color.SecondaryText,
                    }}
                    title={siteKey}
                    subtitle={session.domain}
                    accessories={[
                      {
                        tag: {
                          value: isSaved ? "Saved" : "Public Only",
                          color: isSaved ? Color.Blue : Color.SecondaryText,
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
                        <Action
                          title="Refresh Status"
                          icon={Icon.ArrowClockwise}
                          shortcut={Keyboard.Shortcut.Common.Refresh}
                          onAction={() => void refresh()}
                        />
                      </ActionPanel>
                    }
                  />
                );
              })}
            </List.Section>
          )}
        </>
      )}
    </List>
  );
}
