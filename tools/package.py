"""Build installable source bundle without local history, credentials or test outputs."""
import hashlib
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.2.0'

def main():
    target=ROOT/'dist'
    target.mkdir(exist_ok=True)
    path=target/('LLMLINK-Cinema4D-'+VERSION+'-macOS.zip')
    prefix='LLMLINK-Cinema4D-'+VERSION
    paths=[ROOT/'README.md',ROOT/'README.ru.md',ROOT/'LICENSE',ROOT/'Install.command',ROOT/'tools/install.py']
    paths.extend(p for p in (ROOT/'plugin').rglob('*') if p.is_file() and p.suffix in ('.pyp','.py'))
    paths.extend(p for p in (ROOT/'docs').glob('*.md'))
    paths.extend(p for p in (ROOT/'examples').glob('*') if p.suffix in ('.c4d','.png'))
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(paths):
            archive.write(item,prefix+'/'+item.relative_to(ROOT).as_posix())
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    (target/'SHA256SUMS').write_text(digest+'  '+path.name+'\n')
    print(path)
    print('SHA256',digest)
if __name__=='__main__':main()
