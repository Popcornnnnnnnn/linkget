on run argv
    set dateMode to item 1 of argv
    if dateMode is not "--keep-date" and dateMode is not "--date-now" then
        error "Unknown Photos date mode: " & dateMode
    end if
    set sourcePaths to items 2 thru -1 of argv
    set groups to my groupSources(sourcePaths)
    set groupKeys to item 1 of groups
    set groupNames to item 2 of groups
    set fileGroups to item 3 of groups
    set indexGroups to item 4 of groups
    set sourceGroups to item 5 of groups
    set filesToImport to {}
    set newFileIndexes to {}
    set newGroups to {}
    set savedIdentifiers to {}
    repeat with groupIndex from 1 to count of groupKeys
        set currentName to item groupIndex of groupNames
        -- Reuse the identifier from this single lookup for existing assets.
        set savedIdentifier to my existingIdentifier(currentName)
        set end of savedIdentifiers to savedIdentifier
        if savedIdentifier is "" then
            set filesToImport to filesToImport & item groupIndex of fileGroups
            set newFileIndexes to newFileIndexes & item groupIndex of indexGroups
            set end of newGroups to groupIndex
        end if
    end repeat

    if (count of filesToImport) > 0 then
        set importedItems to my importFiles(filesToImport)
        if (count of importedItems) is not (count of newGroups) then
            error "Photos imported " & (count of importedItems) & " / " & (count of newGroups) & " expected assets; Live Photo pairing may not have been recognized."
        end if
        set importedAt to current date
        repeat with importedItem in importedItems
            set identity to my importedIdentity(contents of importedItem, dateMode, importedAt)
            -- Photos may return items in a different order. Match returned
            -- filenames to groups; never assume ordering or re-query Photos.
            set groupIndex to my indexOf(groupKeys, my groupKey(item 1 of identity))
            if groupIndex is 0 then error "Photos returned an unexpected imported filename."
            if newGroups does not contain groupIndex or item groupIndex of savedIdentifiers is not "" then
                error "Photos returned an unexpected or repeated imported item."
            end if
            set item groupIndex of savedIdentifiers to item 2 of identity
        end repeat
    end if
    set newCount to count of filesToImport
    set skippedCount to (count of sourcePaths) - newCount
    set resultText to (newCount as text) & ":" & (skippedCount as text)
    repeat with fileIndex in newFileIndexes
        set resultText to resultText & ":" & ((contents of fileIndex) as text)
    end repeat
    -- Preserve the resource-based protocol: both Live Photo components get
    -- their shared asset ID, so Python verifies both managed originals.
    set resultText to resultText & linefeed
    set firstIdentifier to true
    repeat with groupIndex in sourceGroups
        set savedIdentifier to item (contents of groupIndex) of savedIdentifiers
        if savedIdentifier is "" then error "Photos could not identify every saved item."
        if firstIdentifier then
            set firstIdentifier to false
        else
            set resultText to resultText & tab
        end if
        set resultText to resultText & savedIdentifier
    end repeat
    return resultText
end run

on groupSources(sourcePaths)
    set groupKeys to {}
    set groupNames to {}
    set fileGroups to {}
    set indexGroups to {}
    set sourceGroups to {}
    set sourceIndex to 0
    repeat with sourcePath in sourcePaths
        set sourceIndex to sourceIndex + 1
        set sourceFile to POSIX file (contents of sourcePath)
        set currentName to name of (info for sourceFile)
        set sourceKey to my groupKey(currentName)
        set groupIndex to my indexOf(groupKeys, sourceKey)
        if groupIndex is 0 then
            set end of groupKeys to sourceKey
            set end of groupNames to ""
            set end of fileGroups to {}
            set end of indexGroups to {}
            set groupIndex to count of groupKeys
        end if
        set item groupIndex of fileGroups to (item groupIndex of fileGroups) & {sourceFile}
        set item groupIndex of indexGroups to (item groupIndex of indexGroups) & {sourceIndex}
        if my liveStem(currentName) is "" or currentName does not end with ".mov" then
            if item groupIndex of groupNames is not "" then error "An import group has repeated pictures."
            set item groupIndex of groupNames to currentName
        end if
        set end of sourceGroups to groupIndex
    end repeat
    repeat with groupIndex from 1 to count of groupKeys
        if item groupIndex of groupNames is "" then error "A retained Live Photo pair is incomplete."
        if my liveStem(item groupIndex of groupNames) is not "" and (count of item groupIndex of fileGroups) is not 2 then
            error "A retained Live Photo pair is incomplete."
        end if
    end repeat
    return {groupKeys, groupNames, fileGroups, indexGroups, sourceGroups}
end groupSources

on existingIdentifier(currentName)
    tell application "Photos"
        set matchingItems to media items whose filename is currentName
        if (count of matchingItems) is 0 then return ""
        return id of item 1 of matchingItems
    end tell
end existingIdentifier

on importFiles(sourceFiles)
    tell application "Photos"
        return import sourceFiles skip check duplicates false
    end tell
end importFiles

on importedIdentity(importedItem, dateMode, importedAt)
    tell application "Photos"
        if dateMode is "--date-now" then set date of importedItem to importedAt
        return {filename of importedItem, id of importedItem}
    end tell
end importedIdentity

on indexOf(values, wanted)
    repeat with valueIndex from 1 to count of values
        if item valueIndex of values is wanted then return valueIndex
    end repeat
    return 0
end indexOf

on groupKey(theName)
    set stem to my liveStem(theName)
    if stem is "" then return theName
    return stem
end groupKey

on liveStem(theName)
    if theName does not start with "linkget-live-" then return ""
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "."
    set stem to text item 1 of theName
    set AppleScript's text item delimiters to oldDelimiters
    return stem
end liveStem
