"""Explicit user-selected references; no automatic project scanning."""
import base64
import hashlib
import shutil
import uuid
from pathlib import Path

IMAGE_TYPES = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}
TEXT_TYPES = {'.txt','.md','.py','.json','.csv','.yaml','.yml','.toml','.obj','.mtl','.svg','.xml','.glsl','.osl'}


def collect(paths, directory):
    if not isinstance(paths,list) or len(paths)>6:
        raise ValueError('Attach up to six files per message')
    directory=Path(directory);directory.mkdir(exist_ok=True,parents=True,mode=0o700)
    output=[];total=0
    for value in paths:
        if not isinstance(value,str):raise ValueError('Invalid attachment path')
        source=Path(value).expanduser().resolve(strict=True)
        if not source.is_file():raise ValueError('Attachment is not a file')
        suffix=source.suffix.lower();size=source.stat().st_size
        item={'name':source.name,'source':str(source),'size':size}
        if suffix in IMAGE_TYPES:
            if size>8*1024*1024:raise ValueError('Image limit is 8 MiB per file')
            total+=size
            if total>20*1024*1024:raise ValueError('Image attachments exceed 20 MiB')
            with source.open('rb') as stream:head=stream.read(16)
            valid=(suffix=='.png' and head.startswith(b'\x89PNG\r\n\x1a\n') or
                   suffix in ('.jpg','.jpeg') and head.startswith(b'\xff\xd8\xff') or
                   suffix=='.webp' and head[:4]==b'RIFF' and head[8:12]==b'WEBP')
            if not valid:raise ValueError('Image file signature does not match its extension')
            destination=directory/(uuid.uuid4().hex+suffix)
            shutil.copyfile(source,destination);destination.chmod(0o600)
            item.update(kind='image',path=str(destination),mime=IMAGE_TYPES[suffix],
                        sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
        elif suffix in TEXT_TYPES:
            if size>256*1024:raise ValueError('Text attachment limit is 256 KiB')
            item.update(kind='text',text=source.read_text(encoding='utf-8'))
        else:
            item.update(kind='reference',note='Local asset reference. Contents are not uploaded. Import/read through an approved Cinema 4D operation if needed.')
        output.append(item)
    return output


def image_bytes(item):
    path=Path(item['path'])
    with path.open('rb') as stream:
        data=stream.read(8*1024*1024+1)
    if len(data)>8*1024*1024 or hashlib.sha256(data).hexdigest()!=item['sha256']:
        raise ValueError('Attached image changed; attach it again')
    return data


def image_data(item):
    data=image_bytes(item)
    return base64.b64encode(data).decode('ascii')
