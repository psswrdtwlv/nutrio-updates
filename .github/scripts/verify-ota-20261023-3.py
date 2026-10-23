"""Verify the staged/active public OTA, or a local archive before publication."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time
import urllib.request
import zipfile

VERSION='2026.10.23.3'
URL=f'https://psswrdtwlv.github.io/nutrio-updates/nutrio-{VERSION}.zip'
CORE_SHA='b682a3c5aa149cb306e456fec8bdc593e9aa6fcaeb6299833189a870ac32f1c3'


def download(url, limit):
    for attempt in range(24):
        try:
            req=urllib.request.Request(url,headers={'Cache-Control':'no-cache'})
            with urllib.request.urlopen(req,timeout=30) as r:
                assert r.status==200 and r.url==url
                data=r.read(limit+1);assert len(data)<=limit
            return data
        except Exception as e:
            print('Waiting for deployment',attempt+1,str(e),flush=True)
            if attempt==23:raise
            time.sleep(10)


def unpack(z, storage):
    packed=z.read(storage['file'].lstrip('/'))
    assert len(packed)==storage['bytes'] and hashlib.sha256(packed).hexdigest()==storage['sha256']
    assert 0<storage['decodedBytes']<128*1024*1024
    raw=subprocess.check_output(['node','-e',"let a=[];process.stdin.on('data',x=>a.push(x));process.stdin.on('end',()=>process.stdout.write(require('zlib').brotliDecompressSync(Buffer.concat(a))));"],input=packed)
    assert len(raw)==storage['decodedBytes'] and hashlib.sha256(raw).hexdigest()==storage['decodedSha256']
    rows=json.loads(raw);assert len(rows)==storage['products']
    return rows


def verify(data, sha):
    assert hashlib.sha256(data).hexdigest()==sha
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names=z.namelist()
        assert len(names)==len(set(names)) and z.testzip() is None and 'index.html' in names
        assert 'offline-foods.json' not in names
        assert all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts for n in names)
        assert not any(PurePosixPath(n).name.startswith('.env') or n.endswith('.map') or 'AiPreview-' in n for n in names)
        base=json.loads(z.read('offline-foods.meta.json'));world=json.loads(z.read('global-foods.meta.json'))
        assert base['products']==141815 and base['sha256']==base['storage']['decodedSha256']==CORE_SHA
        assert world['format']=='nutrio-global-v1' and world['baseProducts']==141815 and world['baseSha256']==CORE_SHA
        assert world['products']==860603 and world['totalProducts']==1002418 and len(world['parts'])==72
        assert world['license']=='Open Database License (ODbL) 1.0'
        core=unpack(z,base['storage']);ids={r[0] for r in core};assert len(ids)==141815
        codes=[r[3].zfill(14) for r in core if r[3]];assert len(codes)==104674 and len(set(codes))==104674
        codes=set(codes);count=len(core);del core
        for p in world['parts']:
            assert re.fullmatch(r'/global-catalog/[a-f0-9]{64}\.br',p['file']) and p['decodedBytes']<=8*1024*1024
            for r in unpack(z,p):
                assert len(r)==16 and isinstance(r[0],str) and re.fullmatch(r'[0-9]{1,24}',r[0])
                rid='nut:off-bulk:'+r[0];assert rid not in ids;ids.add(rid)
                assert all(type(v) in (int,float) and 0<=v for v in r[4:8])
                assert r[4]<=950 and max(r[5:8])<=100 and sum(r[5:8])<=105
                if r[3]:
                    assert r[3]==r[0];code=r[3].zfill(14);assert code not in codes;codes.add(code)
                count+=1
        assert count==len(ids)==1002418 and len(codes)==933249
        assert {n for n in names if n.startswith('global-catalog/')}=={p['file'].lstrip('/') for p in world['parts']}
        scripts=''.join(z.read(n).decode() for n in names if n.endswith('.js'))
        assert any(re.search(r'/globalCatalog\.worker-[^/]+\.js$',n) for n in names)
        assert 'nutrio.pro@yandex.ru' in scripts and '1.0.0 · 1 бета' in scripts
        assert 'NUTRIO_AI_KEY' not in scripts and 'AI · закрытый тест' not in scripts
        for key in ['themes.classic.title','themes.halloween.title','themes.stadium.title','themes.boxing.title','themes.athletics.title','nutrio.ota.reminder.v1','Update available']:
            assert key in scripts,key
        assert not re.search(r'icon:"[^"\\]+",label:\w+\("add\.action\.(?:kcal|protein)"\)',scripts)
        assert re.search(r'icon:"utensils",label:\w+\("add\.action\.recipe"\)',scripts)
        css=''.join(z.read(n).decode() for n in names if n.endswith('.css'))
        assert 'overflow-wrap:anywhere' in css
        assert any(n.endswith('.wasm') for n in names)
        return {'version':VERSION,'sha256':sha,'bytes':len(data),'installedWebBytes':sum(p.file_size for p in z.infolist()),
                'products':count,'usableBarcodes':len(codes),'globalPartitions':72,'archiveVerified':True}


def main():
    p=argparse.ArgumentParser();p.add_argument('--archive',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    sha=os.environ['NUTRIO_VERIFY_SHA'];assert re.fullmatch(r'[a-f0-9]{64}',sha)
    data=a.archive.read_bytes() if a.archive else download(URL,80*1024*1024)
    result=verify(data,sha)
    active=os.environ.get('NUTRIO_VERIFY_ACTIVE')=='true'
    if active:
        expected={'version':VERSION,'nativeVersion':'1','url':URL,'sha256':sha}
        for attempt in range(24):
            actual=json.loads(download('https://psswrdtwlv.github.io/nutrio-updates/manifest.json',16384))
            if actual==expected:break
            if attempt==23:raise AssertionError(actual)
            print('Waiting for active manifest',actual.get('version'),flush=True);time.sleep(10)
    result.update(publicArchiveVerified=not bool(a.archive),publicManifestVerified=active)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
