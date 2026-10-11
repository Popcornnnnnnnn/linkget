on run argv
    set postURL to item 1 of argv
    set readScript to item 2 of argv
    if application "Google Chrome" is not running then error "Open daily Chrome first."
    tell application "Google Chrome"
        if (count windows) is 0 then error "Open a window in daily Chrome first."
        set browserWindow to front window
        set previousIndex to active tab index of browserWindow
        set temporaryTab to missing value
        set createdTab to false
        repeat with candidateWindow in windows
            repeat with candidateTab in tabs of candidateWindow
                set candidateURL to URL of candidateTab
                if candidateURL is postURL or candidateURL starts with (postURL & "?") then
                    set temporaryTab to contents of candidateTab
                    exit repeat
                end if
            end repeat
            if temporaryTab is not missing value then exit repeat
        end repeat
        if temporaryTab is missing value then
            set temporaryTab to make new tab at end of tabs of browserWindow with properties {URL:postURL}
            set createdTab to true
            -- Chrome defers a new inactive player's initialization. Select it
            -- without activating the app, then restore the user's tab.
            set active tab index of browserWindow to count of tabs of browserWindow
        end if
        try
            repeat 110 times
                set mediaJSON to execute temporaryTab javascript readScript
                if mediaJSON is not "null" and mediaJSON is not "" then
                    if createdTab then
                        if id of active tab of browserWindow is id of temporaryTab then set active tab index of browserWindow to previousIndex
                        close temporaryTab
                    end if
                    return mediaJSON
                end if
                delay 0.3
            end repeat
            error "Chrome did not expose a complete media set. Check that the post loads normally; its gallery may be incomplete or use an unsupported layout."
        on error messageText number messageNumber
            try
                if createdTab then
                    if id of active tab of browserWindow is id of temporaryTab then set active tab index of browserWindow to previousIndex
                    close temporaryTab
                end if
            end try
            error messageText number messageNumber
        end try
    end tell
end run
