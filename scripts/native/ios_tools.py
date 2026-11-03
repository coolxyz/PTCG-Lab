"""Install locked iOS build tools into a new private directory, never globally."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile
from scripts.public.policy import safe_name

from scripts.packaging.common import new_output,record

LOCK=Path(__file__).with_name('ios-tools.lock.json')
SPEC="""s=Gem::Package.new(ARGV[0]).spec; puts JSON.generate({name:s.name,version:s.version.to_s,platform:s.platform.to_s,compatible:s.required_ruby_version.satisfied_by?(Gem::Version.new(RUBY_VERSION)),dependencies:s.dependencies.select{|d|d.type==:runtime}.map{|d|{name:d.name,requirements:d.requirement.to_s}}})"""

def checked(raw,digest):
    if len(digest)!=64 or sha256(raw).hexdigest()!=digest:
        raise ValueError('Build tool checksum mismatch')
    return raw

def validate_spec(spec,name,item):
    if (spec.get('name'),spec.get('version'),spec.get('platform'),spec.get('compatible'))!=(name,item['version'],'ruby',True):
        raise ValueError('Wrong or incompatible Ruby platform package')
    key=lambda d:(d['name'],d['requirements'])
    if sorted(spec['dependencies'],key=key)!=sorted(item['dependencies'],key=key):
        raise ValueError('Gem dependencies differ from lock')

def install(output,*,cache=None,offline=False):
    lock=json.loads(LOCK.read_bytes())
    ruby=subprocess.check_output(['/usr/bin/ruby','-e','print RUBY_VERSION'],text=True,timeout=10)
    if ruby!=lock['ruby']:raise ValueError('Private tool lock requires Ruby '+lock['ruby']+'; use a separately reviewed toolchain on other hosts')
    output=new_output(output);cache=Path(cache).absolute() if cache else output/'downloads'
    if cache.is_symlink():raise ValueError('Tool cache must not be a symlink')
    cache.mkdir(parents=True,exist_ok=True);bindir=output/'bin';bindir.mkdir()
    def download(name,item):
        path=cache/name
        if path.is_symlink():raise ValueError('Tool archive links forbidden')
        if path.exists():checked(path.read_bytes(),item['sha256']);return path
        if offline:raise ValueError('Missing offline tool archive: '+name)
        urls=[item['url']]
        if item['url'].startswith('https://rubygems.org/'):
            urls += [item['url'].replace('https://rubygems.org/','https://'+host+'/') for host in ['index.rubygems.org','api.rubygems.org']]
        error=None
        for url in urls:
            try:
                with urllib.request.urlopen(url,timeout=25) as response:raw=response.read(40*1024*1024+1)
                if len(raw)>40*1024*1024:raise ValueError('Oversized tool archive')
                checked(raw,item['sha256']);path.write_bytes(raw);return path
            except (OSError,ValueError) as e:error=e
        raise RuntimeError('Unable to fetch verified tool '+name) from error
    env={**os.environ,'GEM_HOME':str(output/'gems'),'GEM_PATH':str(output/'gems'),'MAKEFLAGS':'-j2','COCOAPODS_DISABLE_STATS':'true'}
    for name,item in lock['gems'].items():
        path=download(name+'-'+item['version']+'.gem',item)
        spec=json.loads(subprocess.check_output(['/usr/bin/ruby','-rjson','-rrubygems/package','-e',SPEC,str(path)],text=True,env=env,timeout=15))
        validate_spec(spec,name,item)
        with (output/'install.log').open('a') as log:
            subprocess.run(['/usr/bin/gem','install',str(path),'--local','--ignore-dependencies','--no-document','--install-dir',str(output/'gems'),'--bindir',str(bindir)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    path=download('xcodegen.zip',lock['xcodegen'])
    with zipfile.ZipFile(path) as z:
        names=[i for i in z.infolist() if i.filename.endswith('/bin/xcodegen') and not i.is_dir()]
        if len(names)!=1 or names[0].file_size>40*1024*1024:raise ValueError('Unexpected XcodeGen archive')
        (bindir/'xcodegen').write_bytes(z.read(names[0]));(bindir/'xcodegen').chmod(0o755)
        # XcodeGen resolves platform/build presets relative to its executable.
        for item in z.infolist():
            if item.is_dir() or not item.filename.startswith('xcodegen/share/'):continue
            name=item.filename.removeprefix('xcodegen/')
            if not safe_name(name) or item.file_size>2*1024*1024 or (item.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe XcodeGen preset')
            dest=output/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(item))
    pod=subprocess.check_output([str(bindir/'pod'),'--version'],env=env,text=True,timeout=20).strip()
    xcodegen=subprocess.check_output([str(bindir/'xcodegen'),'--version'],env=env,text=True,timeout=10).strip()
    if pod!=lock['gems']['cocoapods']['version'] or xcodegen!='Version: '+lock['xcodegen']['version']:raise ValueError('Installed tool version differs from lock')
    report={'schema':1,'cocoaPods':pod,'xcodeGen':xcodegen,'ruby':ruby,'lockSha256':sha256(LOCK.read_bytes()).hexdigest(),'globalEnvironmentChanged':False}
    record(output/'TOOLS.json',report);return report
