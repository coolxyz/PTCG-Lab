"""Offline notice evidence for the mobile target/build closure, not license approval."""
from hashlib import sha256
import html
import json
from pathlib import Path,PurePosixPath
import tarfile
import tomllib

from scripts.packaging.common import new_output,record
from scripts.public.policy import safe_file

def relative(name):
    p=PurePosixPath(name)
    return bool(name) and not p.is_absolute() and p.as_posix()==name and '..' not in p.parts and '\\' not in name and ':' not in name

def supplement(root, package, fixed, declaration, archive):
    """Require both the locked archive and its own VCS identity before using texts."""
    manifest=Path(root)/'licenses/mobile/supplemental.json'
    if not manifest.exists():return None
    value=json.loads(safe_file(root,'licenses/mobile/supplemental.json').read_bytes())
    if value.get('schema')!=1:raise ValueError('Invalid supplemental license manifest')
    rows=[r for r in value['components'] if (r['name'],r['version'])==(package['name'],package['version'])]
    if not rows:return None
    if len(rows)!=1:raise ValueError('Duplicate supplemental license record')
    row=rows[0];prefix=package['name']+'-'+package['version']+'/'
    vcs=json.loads(archive.extractfile(prefix+'.cargo_vcs_info.json').read())
    if row['crateSha256']!=fixed['checksum'] or row['vcsCommit']!=vcs['git']['sha1'] or row['pathInVcs']!=vcs['path_in_vcs'] or row['licenseDeclared']!=declaration.get('license'):
        raise ValueError('Supplemental license identity differs from locked crate')
    texts=[]
    for file in row['files']:
        if not relative(file['path']):raise ValueError('Unsafe supplemental license path')
        raw=safe_file(Path(root)/'licenses/mobile',file['path']).read_bytes()
        if sha256(raw).hexdigest()!=file['sha256']:raise ValueError('Changed supplemental license text')
        texts.append((file['path'],raw))
    if not texts:raise ValueError('Empty supplemental license record')
    notice='\n'.join([package['name']+' '+package['version'],'Declared license: '+row['licenseDeclared'],'Upstream: '+str(row['repository']),'Authors (metadata, not a substituted copyright notice): '+', '.join(row['authors']),'Unmodified source code: '+row['sourceArchive'],'VCS commit: '+row['vcsCommit']])+'\n'
    return row,texts,notice.encode()

def generate(root,cargo_home,metadata,output):
    root,cargo_home=Path(root),Path(cargo_home);output=new_output(output)
    lock=tomllib.loads(safe_file(root,'apps/mobile/src-tauri/Cargo.lock').read_text())
    locked={(p['name'],p['version']):p for p in lock['package']}
    nodes={n['id']:n for n in metadata['resolve']['nodes']};pending=[metadata['resolve']['root']];selected=set()
    while pending:
        key=pending.pop()
        if key in selected:continue
        selected.add(key);pending.extend(d['pkg'] for d in nodes[key]['deps'])
    components=[];evidence={}
    def component(kind,name,version,scope,license,source=None):
        key=sha256((kind+'\0'+name+'\0'+version).encode()).hexdigest()[:24]
        item={'id':key,'kind':kind,'name':name,'version':version,'scope':scope,'licenseDeclared':license or 'NOASSERTION','source':source,'licenseFiles':[]}
        components.append(item);return item
    def add(item,name,raw):
        if not relative(name) or len(raw)>2*1024*1024:raise ValueError('Invalid license evidence')
        name='texts/'+item['id']+'/'+name
        destination=output/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
        evidence[name]=sha256(raw).hexdigest();item['licenseFiles'].append(name)
    for package in sorted(metadata['packages'],key=lambda p:(p['name'],p['version'])):
        if package['id'] not in selected:continue
        fixed=locked.get((package['name'],package['version']))
        if not fixed:raise ValueError('Cargo graph differs from lock')
        if package['source'] is None:
            item=component('cargo',package['name'],package['version'],'mobile-target-and-host-build-closure','MIT')
            add(item,'LICENSE',safe_file(root,'LICENSE').read_bytes());continue
        if not package['source'].startswith('registry+') or not fixed.get('checksum'):raise ValueError('Unpinned mobile dependency')
        archives=list((cargo_home/'registry/cache').glob('*/'+package['name']+'-'+package['version']+'.crate'))
        if len(archives)!=1:raise ValueError('Locked crate archive unavailable: '+package['name'])
        raw=archives[0].read_bytes()
        if sha256(raw).hexdigest()!=fixed['checksum']:raise ValueError('Crate checksum differs from lock')
        prefix=package['name']+'-'+package['version']+'/'
        with tarfile.open(archives[0]) as t:
            declaration=tomllib.loads(t.extractfile(prefix+'Cargo.toml').read().decode())['package']
            item=component('cargo',package['name'],package['version'],'mobile-target-and-host-build-closure',declaration.get('license'),{'registry':package['source'],'sha256':fixed['checksum']})
            names=[]
            for entry in t.getmembers():
                name=entry.name.removeprefix(prefix)
                if not entry.name.startswith(prefix) or not relative(name):continue
                if entry.isfile() and (('/' not in name and name.lower().startswith(('license','licence','copying','notice','copyright','unlicense'))) or name==declaration.get('license-file')):names.append(name)
            if not names:
                names=[e.name.removeprefix(prefix) for e in t.getmembers() if e.isfile() and e.name.startswith(prefix) and relative(e.name.removeprefix(prefix)) and PurePosixPath(e.name).name.lower() in {'license','license.txt','copying','copying.txt'}][:10]
            for name in sorted(set(names)):add(item,name,t.extractfile(prefix+name).read())
            extra=supplement(root,package,fixed,declaration,t)
            if extra:
                row,texts,notice=extra
                for name,raw in texts:add(item,'supplemental/'+PurePosixPath(name).name,raw)
                add(item,'UPSTREAM-NOTICE.txt',notice)
                item['supplementalEvidence']={k:row[k] for k in ('vcsCommit','sourceArchive','files')}
    npm_lock=json.loads(safe_file(root,'package-lock.json').read_bytes())['packages'];seen=set()
    def npm(name):
        if name in seen:return
        seen.add(name)
        if not relative(name):raise ValueError('Unsafe npm package')
        locked=npm_lock.get('node_modules/'+name)
        if not locked:raise ValueError('Missing npm lock entry')
        folder=locked.get('resolved') if locked.get('link') else 'node_modules/'+name
        if not relative(folder):raise ValueError('Invalid npm source')
        workspace=bool(locked.get('link'))
        if workspace:locked=npm_lock.get(folder,{})
        base=root/folder;meta=json.loads(safe_file(base,'package.json').read_bytes())
        if meta['name']!=name or meta['version']!=locked['version']:raise ValueError('Wrong npm version')
        item=component('npm',name,meta['version'],'declared-frontend-runtime-closure',meta.get('license'),{'integrity':locked.get('integrity')})
        paths=[p for p in base.iterdir() if p.is_file() and p.name.lower().startswith(('license','copying','notice'))]
        if not paths and workspace:add(item,'LICENSE',safe_file(root,'LICENSE').read_bytes());item['licenseDeclared']='MIT'
        for path in sorted(paths):add(item,path.name,safe_file(base,path.name).read_bytes())
        for dependency in sorted(meta.get('dependencies',{})):npm(dependency)
    mobile=json.loads(safe_file(root,'apps/mobile/package.json').read_bytes())
    for name in sorted(mobile['dependencies']):npm(name)
    item=component('project','PTCG Lab mobile',mobile['version'],'project-and-own-swift-bridge','MIT')
    for name in ['LICENSE','THIRD_PARTY_NOTICES.md']:add(item,name,safe_file(root,name).read_bytes())
    missing=[{'id':c['id'],'name':c['name'],'version':c['version'],'licenseDeclared':c['licenseDeclared']} for c in components if not c['licenseFiles'] or c['licenseDeclared']=='NOASSERTION']
    report={'schema':1,'scope':'Conservative target Rust + host build dependency graph and declared frontend runtime dependencies; not proof of exact linked components or license approval. Apple SDK is not redistributed.','components':components,'missingEvidence':missing,'evidenceComplete':not missing,'files':evidence}
    record(output/'INVENTORY.json',report)
    rows=[]
    for c in components:
        links=' '.join('<a href="'+html.escape(p,quote=True)+'">许可原文</a>' for p in c['licenseFiles']) or '原文缺失，待审'
        rows.append('<tr><td>'+html.escape(c['name'])+'</td><td>'+html.escape(c['version'])+'</td><td>'+html.escape(c['licenseDeclared'])+'</td><td>'+links+'</td></tr>')
    (output/'THIRD-PARTY.html').write_text('<!doctype html><meta charset="utf-8"><title>PTCG Lab 移动依赖许可</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;color:#352b49}td,th{padding:10px;border-bottom:1px solid #e4dcf0;text-align:left}</style><h1>移动依赖与许可原文</h1><p>'+html.escape(report['scope'])+'</p><p>缺少证据：'+str(len(missing))+'</p><table><tr><th>组件</th><th>版本</th><th>声明许可</th><th>证据</th></tr>'+''.join(rows)+'</table>',encoding='utf8')
    # Seal all public notice files; the inventory does not hash itself.
    report['files']['THIRD-PARTY.html']=sha256((output/'THIRD-PARTY.html').read_bytes()).hexdigest()
    (output/'INVENTORY.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report
