"""Install per-user plugin atomically. Existing plugin versions are backed up."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]

def install(destination):
    if sys.version_info < (3,9):raise RuntimeError('LLMLINK requires Python 3.9 or newer')
    destination=Path(destination).expanduser()
    if destination.is_symlink():raise ValueError('Plugin destination must not be a symbolic link')
    destination=destination.resolve()
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.name!='LLMLINK':raise ValueError('Destination must be a plugin folder named LLMLINK')
    stage=destination.parent.parent/('.LLMLINK-install-'+uuid.uuid4().hex)
    backup=None
    try:
        shutil.copytree(ROOT/'plugin'/'LLMLINK',stage,ignore=shutil.ignore_patterns('__pycache__','*.pyc','runtime.json'))
        (stage/'runtime.json').write_text(json.dumps({'python':sys.executable},indent=2)+'\n',encoding='utf-8')
        if destination.exists():
            backup=destination.parent/('LLMLINK-backup-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
            # Backups live outside the plugins folder, so Cinema cannot load duplicate command IDs.
            backup_root=destination.parent.parent/'LLMLINK-plugin-backups'
            backup_root.mkdir(exist_ok=True)
            backup=backup_root/backup.name
            destination.rename(backup)
        stage.rename(destination)
    except BaseException:
        if backup and backup.exists() and not destination.exists():backup.rename(destination)
        if stage.exists():shutil.rmtree(stage)
        raise
    return {'installed':str(destination),'backup':str(backup) if backup else None,'restart_required':True}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugins',help='Cinema 4D user plugins directory; use Preferences > Open Preferences Folder')
    args=parser.parse_args()
    if args.plugins:
        targets=[Path(args.plugins)]
    elif sys.platform=='darwin':
        targets=sorted((Path.home()/'Library/Preferences/Maxon').glob('Maxon Cinema 4D 2024_*/plugins'))
    else:
        parser.error('Specify --plugins with the Cinema 4D user plugins directory')
    if len(targets)!=1:parser.error('Cannot choose a Cinema 4D installation; specify --plugins')
    print(json.dumps(install(targets[0]/'LLMLINK'),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
