on run argv
    set dateMode to item 1 of argv
    if dateMode is not "--keep-date" and dateMode is not "--date-now" then
        error "Unknown Photos date mode: " & dateMode
    end if
    set sourcePaths to items 2 thru -1 of argv
    set filesToImport to {}
    set newFileIndexes to {}
    set sourceIndex to 0
    repeat with sourcePath in sourcePaths
        set sourceIndex to sourceIndex + 1
        set sourceFile to (POSIX file (contents of sourcePath))
        set currentName to (name of (info for sourceFile))
        tell application "Photos"
            set existingCount to (count of (media items whose filename is currentName))
        end tell
        if existingCount is 0 then
            set end of filesToImport to sourceFile
            set end of newFileIndexes to sourceIndex
        end if
    end repeat

    if (count of filesToImport) > 0 then
        tell application "Photos"
            set importedItems to import filesToImport skip check duplicates false
        end tell
        if (count of importedItems) is not (count of filesToImport) then
            error "Photos imported only " & (count of importedItems) & " / " & (count of filesToImport) & " new items."
        end if
        if dateMode is "--date-now" then
            set importedAt to current date
            tell application "Photos"
                repeat with importedItem in importedItems
                    set date of importedItem to importedAt
                end repeat
            end tell
        end if
    end if
    set newCount to (count of filesToImport)
    set skippedCount to (count of sourcePaths) - newCount
    set resultText to (newCount as text) & ":" & (skippedCount as text)
    repeat with fileIndex in newFileIndexes
        set resultText to resultText & ":" & ((contents of fileIndex) as text)
    end repeat
    return resultText
end run
