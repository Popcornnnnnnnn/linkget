"""Explain existing retention evidence. This module never removes or owns files."""
from pathlib import Path

if __package__:
    from . import accounts, jobs, ownership, staging
    from .media import MEDIA, AUDIO
else:
    import accounts, jobs, ownership, staging
    from media import MEDIA, AUDIO


def reasons(row, active=False):
    if active:
        return ['Task is active; its files must remain available.']
    result=[]
    if row.get('status')!='completed':
        result.append('Retained for retry/recovery; the task is not complete.')
    if row.get('receipt'):
        result.append('Photos import receipt exists; managed-copy verification is not current in this view.')
    if row.get('protected_stages'):
        result.append('An earlier stage was protected after replacement or conflicting evidence.')
    if not row.get('stage_snapshots'):
        result.append('No complete creation-time stage record; legacy/uncertain files stay retained.')
    if not result:
        result.append('Some temporary files remained after saving; preview can check recorded ownership and current copies.')
    return result


def _preserved_file(path):
    # Never suggest removing media, exports, apps, installers or profile data.
    suffix=path.suffix.lower()
    if suffix in MEDIA|AUDIO|{'.dmg','.pkg','.zip','.tar','.gz','.tgz','.whl','.app','.db','.sqlite','.plist','.txt','.md','.srt','.vtt','.html','.json','.csv','.pdf','.xlsx'}:
        return True
    return any(part.endswith('.app') or 'profile' in part.lower() or part.lower()=='user-data' for part in path.parts)


def _protected(journal,index,root):
    protected=[jobs.directory().parent.resolve()]
    prefs=accounts.preferences()
    profile=prefs.get('profile')
    if isinstance(profile,str) and Path(profile).expanduser().is_absolute():
        protected.append(Path(profile).expanduser().resolve())
    for source in prefs.get('sources',{}).values():
        if isinstance(source,str):
            value=source.partition(':')[2]
            if value and Path(value).expanduser().is_absolute():protected.append(Path(value).expanduser().resolve())
    for other in jobs.all_journals():
        destination=other.data.get('options',{}).get('folder')
        if destination:protected.append(Path(destination).resolve())
        for number,row in enumerate(other.data['items'],1):
            if other.identifier==journal.identifier and number==index:continue
            protected.extend(Path(path).resolve() for path in staging.paths(row))
    return any(root==path or root in path.parents or path in root.parents for path in protected)


def preview(journal,index,active=False):
    row=journal.data['items'][index-1]
    try:
        paths=staging.paths(row)
    except (ValueError,TypeError):
        return [{'path':None,'classification':'unknown','reason':'Invalid retained-path metadata; no directory was inspected.'}]
    receipt=row.get('receipt') or {}
    if not isinstance(receipt,dict):
        return [{'path':None,'classification':'unknown','reason':'Invalid Photos receipt; no resource was inspected.'}]
    staged=receipt.get('staged',[])
    if not isinstance(staged,list):
        return [{'path':None,'classification':'unknown','reason':'Invalid staged-resource metadata; no resource was inspected.'}]
    paths+= [item['path'] for item in staged if isinstance(item,dict) and isinstance(item.get('path'),str)]
    entries=[]
    for name in dict.fromkeys(paths):
        path=Path(name)
        def keep(reason,classification='must keep'):
            entries.append({'path':str(path),'classification':classification,'reason':reason})
        if active or row.get('status')!='completed':
            keep('Active or unfinished task; needed for retry/recovery.');continue
        if receipt:
            keep('Photos managed-copy verification remains separate; this preview never opens Photos or runs recovery.');continue
        if path.is_symlink():
            keep('Symbolic link; not followed.');continue
        if not path.exists():
            keep('Recorded path is unavailable; no cleanup eligibility can be established.','unknown');continue
        try:
            root=staging._root(path)
            if str(root) in row.get('protected_stages',[]):
                keep('Protected stage after changed/conflicting file evidence.');continue
            if _protected(journal,index,root):
                keep('Overlaps a user destination, profile, task data or another task.');continue
            record=row.get('stage_snapshots',{}).get(str(root))
            if (not isinstance(record,dict) or record.get('created') is not True or
                    staging.identity(root)!=record.get('identity')):
                keep('Legacy/missing creation-time stage identity.','unknown');continue
            files,directories,identities=staging._inventory(root)
            file_ids=record.get('file_identities',{})
            directory_ids=record.get('directory_identities',{})
            if (any(record.get('files',{}).get(file)!=checksum or file_ids.get(file)!=identities[file] for file,checksum in files.items()) or
                    any(directory_ids.get(folder)!=staging.identity(root/folder) for folder in directories)):
                keep('Changed, replaced or newly added files/directories; retain for review.');continue
            copies=row.get('destination_files')
            if not isinstance(copies,list) or not copies:
                keep('No recorded saved-copy evidence; observation cannot establish a safe cleanup candidate.','unknown');continue
            destination=journal.data.get('options',{}).get('folder')
            if not isinstance(destination,str) or not Path(destination).is_absolute():
                keep('No scoped folder-copy evidence; Photos and unknown destinations remain protected.','unknown');continue
            copy_hashes=set()
            for item in copies:
                proof=ownership.FileProof.from_record(item)
                if not proof.path.is_relative_to(Path(destination).resolve()):
                    raise ValueError('Copy proof is outside the saved destination')
                proof.verify(fresh=True)
                if root not in proof.path.parents and proof.path!=root and not any(tuple(value[:2])==proof.identity[:2] for value in file_ids.values()):
                    copy_hashes.add(proof.sha256)
            if staging.identity(root)!=record['identity']:
                keep('Temporary folder identity changed during preview.');continue
            for file,checksum in files.items():
                target=root/file
                if _preserved_file(target):
                    entries.append({'path':str(target),'classification':'must keep','reason':'Media, exports, apps/installers and profile data are excluded from cleanup candidates.'})
                elif checksum not in copy_hashes:
                    entries.append({'path':str(target),'classification':'must keep','reason':'No current verified saved copy matches these bytes.'})
                else:
                    # Restore evidence only from the persisted creation snapshot;
                    # never call observe(), remember(), cleanup() or recover().
                    proof=ownership.FileProof.from_record({'path':str(target),'created':True,
                        'sha256':checksum,'file_identity':file_ids[file]}).verify(fresh=True)
                    entries.append({'path':str(proof.path),'classification':'candidate',
                        'reason':'Transient working file: persisted creation identity and current saved-copy bytes both match. Preview only.'})
            if not files:keep('Recorded stage is empty; no file cleanup candidate.','unknown')
        except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
            keep('Current ownership/copy evidence could not be validated; files stay retained.','unknown')
    return entries
