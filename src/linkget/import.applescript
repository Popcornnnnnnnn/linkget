on run argv
    set dateMode to item 1 of argv
    if dateMode is not "--keep-date" and dateMode is not "--date-now" then
        error "Unknown Photos date mode: " & dateMode
    end if
    set sourcePaths to items 2 thru -1 of argv
    set filesToImport to {}
    set newFileIndexes to {}
    set expectedAssets to 0
    set handledPairs to {}
    set sourceIndex to 0
    repeat with sourcePath in sourcePaths
        set sourceIndex to sourceIndex + 1
        set sourceFile to (POSIX file (contents of sourcePath))
        set currentName to (name of (info for sourceFile))
        set stem to my liveStem(currentName)
        if stem is "" then
            tell application "Photos"
                set existingCount to (count of (media items whose filename is currentName))
            end tell
            if existingCount is 0 then
                set end of filesToImport to sourceFile
                set end of newFileIndexes to sourceIndex
                set expectedAssets to expectedAssets + 1
            end if
        else if handledPairs does not contain stem then
            set end of handledPairs to stem
            set pairFiles to {}
            set pairIndexes to {}
            set imageName to ""
            set pairIndex to 0
            repeat with candidate in sourcePaths
                set pairIndex to pairIndex + 1
                set pairFile to POSIX file (contents of candidate)
                set candidateName to name of (info for pairFile)
                if my liveStem(candidateName) is stem then
                    set end of pairFiles to pairFile
                    set end of pairIndexes to pairIndex
                    if candidateName does not end with ".mov" then set imageName to candidateName
                end if
            end repeat
            if (count of pairFiles) is not 2 or imageName is "" then error "A retained Live Photo pair is incomplete."
            tell application "Photos"
                set existingCount to count of (media items whose filename is imageName)
            end tell
            if existingCount is 0 then
                set filesToImport to filesToImport & pairFiles
                set newFileIndexes to newFileIndexes & pairIndexes
                set expectedAssets to expectedAssets + 1
            end if
        end if
    end repeat

    if (count of filesToImport) > 0 then
        tell application "Photos"
            set importedItems to import filesToImport skip check duplicates false
        end tell
        if (count of importedItems) is not expectedAssets then
            error "Photos imported " & (count of importedItems) & " / " & expectedAssets & " expected assets; Live Photo pairing may not have been recognized."
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
    -- Identify new AND existing assets so duplicate imports also verify the
    -- library's own originals before Python removes any staged source.
    set resultText to resultText & linefeed
    set firstIdentifier to true
    repeat with sourcePath in sourcePaths
        set sourceFile to POSIX file (contents of sourcePath)
        set currentName to name of (info for sourceFile)
        set stem to my liveStem(currentName)
        if stem is not "" then
            repeat with candidate in sourcePaths
                set candidateFile to POSIX file (contents of candidate)
                set candidateName to name of (info for candidateFile)
                if my liveStem(candidateName) is stem and candidateName does not end with ".mov" then
                    set currentName to candidateName
                    exit repeat
                end if
            end repeat
        end if
        tell application "Photos"
            set matchingItems to media items whose filename is currentName
            if (count of matchingItems) is 0 then error "Photos could not identify the saved item: " & currentName
            set savedIdentifier to id of item 1 of matchingItems
        end tell
        if firstIdentifier then
            set firstIdentifier to false
        else
            set resultText to resultText & tab
        end if
        set resultText to resultText & savedIdentifier
    end repeat
    return resultText
end run

on liveStem(theName)
    if theName does not start with "linkget-live-" then return ""
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "."
    set stem to text item 1 of theName
    set AppleScript's text item delimiters to oldDelimiters
    return stem
end liveStem
