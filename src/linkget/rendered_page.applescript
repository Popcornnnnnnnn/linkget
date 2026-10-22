-- Content-only daily Chrome control. This script never enables permissions.
-- Each read owns one new tab; existing user tabs are never reused or closed.
on run argv
    set actionName to item 1 of argv
    if application "Google Chrome" is not running then error "Open daily Chrome first."
    tell application "Google Chrome"
        if actionName is "open" then
            if (count windows) is 0 then error "Open a window in daily Chrome first."
            set browserWindow to front window
            set previousID to id of active tab of browserWindow
            set temporaryTab to missing value
            try
                set temporaryTab to make new tab at end of tabs of browserWindow with properties {URL:item 2 of argv}
                -- Select only the owned tab to initialize background players.
                -- The app is not activated. Python closes it in a finally block.
                set active tab index of browserWindow to count of tabs of browserWindow
                return (id of browserWindow as text) & ":" & (id of temporaryTab as text) & ":" & (previousID as text)
            on error messageText number messageNumber
                if temporaryTab is not missing value then try
                    close temporaryTab
                end try
                error messageText number messageNumber
            end try
        end if
        -- Chrome versions expose IDs as either text or integer. Validate the
        -- supplied numeric identity, then compare a single canonical text type.
        set windowID to (item 2 of argv as integer) as text
        set tabID to (item 3 of argv as integer) as text
        set browserWindow to missing value
        set temporaryTab to missing value
        repeat with candidateWindow in windows
            if (id of candidateWindow as text) is windowID then
                set browserWindow to contents of candidateWindow
                repeat with candidateTab in tabs of browserWindow
                    if (id of candidateTab as text) is tabID then
                        set temporaryTab to contents of candidateTab
                        exit repeat
                    end if
                end repeat
                exit repeat
            end if
        end repeat
        if actionName is "read" then
            if temporaryTab is missing value then error "The owned reading tab was closed."
            return execute temporaryTab javascript (item 4 of argv)
        end if
        if actionName is "close" then
            if temporaryTab is missing value then return "closed"
            set previousID to (item 4 of argv as integer) as text
            set expectedURL to item 5 of argv
            set tabURL to URL of temporaryTab
            -- Do not close a tab the user has navigated to unrelated work.
            if tabURL is not expectedURL and tabURL does not start with (expectedURL & "?") then return "preserved_navigated_tab"
            if (id of active tab of browserWindow as text) is tabID then
                set candidateIndex to 0
                repeat with candidateTab in tabs of browserWindow
                    set candidateIndex to candidateIndex + 1
                    if (id of candidateTab as text) is previousID then
                        set active tab index of browserWindow to candidateIndex
                        exit repeat
                    end if
                end repeat
            end if
            close temporaryTab
            return "closed"
        end if
        error "Unsupported content reader action."
    end tell
end run
