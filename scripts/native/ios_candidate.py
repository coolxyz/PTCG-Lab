"""Isolated iOS build with local simulator seals; no account or deployment."""
from pathlib import Path
from hashlib import sha256
import json,os,shutil,subprocess,sys,zipfile

from scripts.packaging.common import ROOT,new_output,record,provenance
from scripts.public.export_source import export_source
from scripts.packaging.mobile_assets import build as resources
from scripts.packaging.ios_inventory import generate as notices
from scripts.packaging.ios_artifact import audit,archive,seal_simulator
from scripts.native.ios_acceptance import write_matrix

def simulator_project(spec):
    spec=json.loads(json.dumps(spec))
    spec.setdefault('options',{})['deploymentTarget']={'iOS':'16.0'}
    for target in spec['targets'].values():
        if target.get('platform')!='iOS':raise ValueError('Only the generated iOS project is supported')
        target['deploymentTarget']='16.0'
        target.setdefault('settings',{}).setdefault('base',{}).update({'IPHONEOS_DEPLOYMENT_TARGET':'16.0','CODE_SIGN_IDENTITY[sdk=iphonesimulator*]':'-','CODE_SIGN_STYLE[sdk=iphonesimulator*]':'Manual','DEVELOPMENT_TEAM[sdk=iphonesimulator*]':'','CODE_SIGNING_ALLOWED[sdk=iphonesimulator*]':'YES'})
    return spec

def simulator_environment(env):
    return {**env,'CODE_SIGNING_ALLOWED':'YES','CODE_SIGN_IDENTITY':'-','DEVELOPMENT_TEAM':'','CODE_SIGN_STYLE':'Manual'}

def environment(work,rust_bin,cargo_home,tools=None,gems=None,target_cache=None):
    work,rust_bin,cargo_home=Path(work).absolute(),Path(rust_bin).absolute(),Path(cargo_home).absolute()
    guard=work/'guard-bin';guard.mkdir();brew=guard/'brew';brew.write_text('#!/bin/sh\nprintf "%s\\n" "Global tool installation disabled" >&2\nexit 86\n');brew.chmod(0o700)
    # CLI init checks this device-log command even for SDK-only builds.
    # An explicit failing guard prevents both global installation and device access;
    # it is never represented as a working libimobiledevice installation.
    device_log=guard/'idevicesyslog';device_log.write_text('#!/bin/sh\nprintf "%s\\n" "Device operations disabled in unsigned SDK build" >&2\nexit 86\n');device_log.chmod(0o700)
    paths=[str(guard),str(rust_bin)]
    if tools:paths.append(str(Path(tools).absolute()))
    env=dict(os.environ)
    for key in list(env):
        if key.startswith(('APPLE_','IOS_CERTIFICATE','IOS_MOBILE_PROVISION')):env.pop(key)
    env.update(PATH=os.pathsep.join(paths+[env.get('PATH','')]),RUSTC=str(rust_bin/'rustc'),CARGO_HOME=str(cargo_home),CARGO_TARGET_DIR=str(work/'target'),CARGO_BUILD_JOBS='2',CARGO_NET_OFFLINE='true',CI='true',COCOAPODS_DISABLE_STATS='true',CP_HOME_DIR=str(work/'cocoapods'),CLANG_MODULE_CACHE_PATH=str(work/'cache/clang'),SWIFT_MODULECACHE_PATH=str(work/'cache/swift'),SWIFTPM_MODULECACHE_OVERRIDE=str(work/'cache/swiftpm'),XDG_CACHE_HOME=str(work/'cache'),IPHONEOS_DEPLOYMENT_TARGET='16.0',CODE_SIGNING_ALLOWED='NO',CODE_SIGNING_REQUIRED='NO',CODE_SIGN_IDENTITY='')
    if gems:env.update(GEM_HOME=str(Path(gems).absolute()),GEM_PATH=str(Path(gems).absolute()))
    if target_cache:
        target=Path(target_cache).absolute()
        if any(p.is_symlink() for p in (target,*target.parents)):raise ValueError('Target cache links forbidden')
        env['CARGO_TARGET_DIR']=str(target)
    return env

def candidate(assets,work,output,*,rust_bin,cargo_home,tools=None,gems=None,target_cache=None,targets=('simulator',),root=ROOT):
    if not targets or any(t not in {'simulator','device'} for t in targets) or len(set(targets))!=len(targets):raise ValueError('Explicit unique targets required')
    assets,rust_bin,cargo_home=Path(assets).absolute(),Path(rust_bin).absolute(),Path(cargo_home).absolute()
    work,output=new_output(work),new_output(output);root=Path(root).absolute()
    if work.is_relative_to(output) or output.is_relative_to(work):raise ValueError('Separate work/output directories required')
    if target_cache and (Path(target_cache).absolute().is_relative_to(output) or output.is_relative_to(Path(target_cache).absolute())):raise ValueError('Target cache must be separate from release output')
    env=environment(work,rust_bin,cargo_home,tools,gems,target_cache);source=provenance();phase='preflight';completed=[]
    def command(name,args,cwd,*,capture=False,timeout=None,local_simulator=False):
        nonlocal phase
        phase=name;record_path=work/'PROGRESS.json';record_path.write_text(json.dumps({'phase':phase,'completed':completed})+'\n')
        with (work/(name+'.log')).open('w') as log:
            result=subprocess.run([str(a) for a in args],cwd=cwd,env=simulator_environment(env) if local_simulator else env,stdout=subprocess.PIPE if capture else log,stderr=log,text=True,timeout=timeout)
        if result.returncode:raise RuntimeError(name+' failed; see '+name+'.log')
        completed.append(name);return result.stdout
    try:
        # simctl can silently run global Xcode first-launch installation. Refuse
        # before invoking Tauri; the operator must finish Xcode setup explicitly.
        command('xcode-ready',['xcodebuild','-checkFirstLaunchStatus'],root,timeout=10)
        for name in ['node','pod','xcodegen','xcodebuild']:
            if not shutil.which(name,path=env['PATH']):raise ValueError('Missing isolated build tool: '+name)
        version=command('rust-version',[Path(rust_bin)/'rustc','--version'],root,capture=True,timeout=10)
        if not version.startswith('rustc 1.90.0 '):raise ValueError('Use the pinned Rust 1.90.0 toolchain')
        export_source(work/'source.zip',root=root)
        with zipfile.ZipFile(work/'source.zip') as z:z.extractall(work/'source')
        checkout=work/'source/PTCG-Lab';mobile=checkout/'apps/mobile'
        command('npm-install',['npm','ci','--ignore-scripts','--offline','--no-audit','--no-fund','--logs-dir',work/'npm-logs'],checkout)
        command('frontend',['npm','run','mobile:build'],checkout)
        resources(assets,work/'resources',root=checkout)
        cargo=Path(rust_bin)/'cargo';metadata=command('cargo-metadata',[cargo,'metadata','--locked','--offline','--format-version','1','--filter-platform','aarch64-apple-ios','--manifest-path',mobile/'src-tauri/Cargo.toml'],checkout,capture=True)
        phase='inventory';inventory=notices(checkout,cargo_home,json.loads(metadata),work/'licenses');completed.append('inventory')
        # Tauri requires a nonempty team even with --no-sign. This sentinel is
        # only for unsigned SDK builds; it is not an Apple account/team.
        config=work/'tauri.ios.conf.json';record(config,{'build':{'frontendDist':str(mobile/'dist')},'bundle':{'iOS':{'developmentTeam':'0000000000'},'resources':{str(work/'resources')+'/':'catalog/',str(work/'licenses')+'/':'licenses/'}}})
        # Use the package script so generated Xcode phases retain the npm runner.
        # Calling tauri.js with node directly generates an invalid `node tauri`.
        cli=['npm','run','tauri','--']
        command('project',[*cli,'ios','init','--ci','--skip-targets-install','--config',config],mobile)
        if 'simulator' in targets:
            apple=mobile/'src-tauri/gen/apple';project=apple/'project.yml'
            spec=json.loads(command('simulator-project-spec',['xcodegen','dump','--type','json','--no-env','--spec',project],apple,capture=True))
            project.write_text(json.dumps(simulator_project(spec),indent=2)+'\n')
            command('simulator-project',['xcodegen','generate','--spec',project],apple)
        seals=[]
        for target in targets:
            command('build-'+target,[*cli,'ios','build','--ci','--debug',*(['--no-sign'] if target=='device' else []),'--archive-only','--target','aarch64-sim' if target=='simulator' else 'aarch64','--config',config],mobile,local_simulator=target=='simulator')
            candidates=[]
            for app in (mobile/'src-tauri/gen/apple').rglob('*.app'):
                try:audit(app,target,expected_resources=work/'resources');candidates.append(app)
                except (ValueError,FileNotFoundError):pass
            if not candidates:raise ValueError('Build produced no audited '+target+' .app')
            # Copies of the same bundle may exist in DerivedData and xcarchive.
            app=max(candidates,key=lambda p:p.stat().st_mtime)
            if target=='simulator':
                phase='seal-simulator'
                seals.append(seal_simulator(app,expected_resources=work/'resources'))
                completed.append(phase)
            packed=archive(app,output/target,target,expected_resources=work/'resources')
            write_matrix(output/target,packed['sha256'],source['publicSourceSha256'],target)
        shutil.copytree(work/'licenses',output/'licenses')
        report={'schema':1,'status':'unsigned_candidates_built_runtime_pending','source':source,'targets':list(targets),'completed':completed,'codeSeals':seals,'distributionSigned':False,'licenseEvidenceComplete':inventory['evidenceComplete'],'licenseMissingCount':len(inventory['missingEvidence']),'signed':False,'runtimeAccepted':False,'releaseReady':False,'servicesChanged':False,'deviceOperations':'disabled'}
        record(output/'BUILD-REPORT.json',report)
        return report
    except Exception as error:
        record(output/'BUILD-REPORT.json',{'schema':1,'status':'build_incomplete','phase':phase,'error':str(error),'source':source,'completed':completed,'signed':False,'runtimeAccepted':False,'releaseReady':False,'servicesChanged':False,'deviceOperations':'disabled'})
        raise
