"""Synthetic bundle/graph fixtures, never evidence of an executable iOS app."""
from hashlib import sha256
import io,json,plistlib,struct,tarfile,zipfile
from pathlib import Path
import pytest
from scripts.packaging.ios_artifact import audit,archive,macho,IDENTIFIER,seal_simulator,simulator_identity
from scripts.packaging.ios_inventory import generate
from scripts.native.ios_acceptance import create,update,CASES
from scripts.native.ios_candidate import environment,simulator_project,simulator_environment
from scripts.native.ios_tools import checked,validate_spec

def binary(platform=7,minimum=16<<16,entitlements=None):
    command=struct.pack('<6I',0x32,24,platform,minimum,27<<16,0)
    if platform!=7 or entitlements is False:return struct.pack('<8I',0xfeedfacf,0x100000c,0,2,1,len(command),0,0)+command
    payload=plistlib.dumps(entitlements if entitlements is not None else {'application-identifier':'TESTSIM000.'+IDENTIFIER})
    segment=struct.pack('<II16sQQQQiiII',0x19,152,b'__TEXT',0,0,0,0,7,5,1,0)
    section=struct.pack('<16s16sQQIIIIIIII',b'__entitlements',b'__TEXT',0,len(payload),208,0,0,0,0,0,0,0)
    return struct.pack('<8I',0xfeedfacf,0x100000c,0,2,2,176,0,0)+command+segment+section+payload

def fixture(tmp_path):
    app=tmp_path/'Fixture.app';app.mkdir()
    (app/'Fixture').write_bytes(binary());(app/'Fixture').chmod(0o755)
    (app/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':IDENTIFIER,'CFBundleExecutable':'Fixture','CFBundleSupportedPlatforms':['iPhoneSimulator'],'MinimumOSVersion':'16.0','CFBundleVersion':'1','CFBundleShortVersionString':'0.8.0'}))
    resources=app/'assets/catalog';resources.mkdir(parents=True)
    files={}
    for name in ['catalog.json','artwork.json']:
        raw=b'{}';(resources/name).write_bytes(raw);files[name]=sha256(raw).hexdigest()
    (resources/'manifest.json').write_text(json.dumps({'schema':1,'files':files}))
    notices=app/'assets/licenses';notices.mkdir();(notices/'LICENSE').write_text('Synthetic license')
    (notices/'INVENTORY.json').write_text(json.dumps({'files':{'LICENSE':sha256(b'Synthetic license').hexdigest()}}))
    return app

def test_bundle_fixture_is_target_bound_and_never_runtime_accepted(tmp_path):
    app=fixture(tmp_path);report=audit(app,'simulator')
    assert report['binary']['platform']==7 and report['runtimeExecution']=='pending' and not report['releaseReady']
    with pytest.raises(ValueError,match='mismatch'):audit(app,'device')
    result=archive(app,tmp_path/'packed','simulator');assert result['sha256']==sha256((tmp_path/'packed'/result['file']).read_bytes()).hexdigest()
    import zipfile
    with zipfile.ZipFile(tmp_path/'packed'/result['file']) as z:assert (z.getinfo('Fixture.app/Fixture').external_attr>>16)&0o111

def test_simulator_seals_final_resources_and_archive_keeps_seal(tmp_path,monkeypatch):
    from scripts.packaging import ios_artifact as module
    import subprocess
    app=fixture(tmp_path);calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if '--sign' in args:
            seal=app/'_CodeSignature';seal.mkdir();(seal/'CodeResources').write_bytes(b'synthetic-seal')
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(module.subprocess,'run',run)
    result=seal_simulator(app)
    assert result=={'target':'simulator','codeSeal':'ad-hoc','verified':True,'distributionSigned':False,'simulatedApplicationIdentifier':'TESTSIM000.'+IDENTIFIER,'defaultKeychainAccessGroup':'TESTSIM000.'+IDENTIFIER}
    assert calls==[['codesign','--force','--sign','-','--timestamp=none',str(app)],['codesign','--verify','--deep','--strict',str(app)]]
    packed=archive(app,tmp_path/'packed','simulator')
    with zipfile.ZipFile(tmp_path/'packed'/packed['file']) as z:assert z.read('Fixture.app/_CodeSignature/CodeResources')==b'synthetic-seal'

def test_simulator_seal_failure_is_fatal_and_device_input_is_rejected(tmp_path,monkeypatch):
    from scripts.packaging import ios_artifact as module
    import subprocess
    app=fixture(tmp_path)
    def run(args,**kwargs):
        if '--verify' in args:raise subprocess.CalledProcessError(1,args,stderr=b'invalid seal')
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(module.subprocess,'run',run)
    with pytest.raises(subprocess.CalledProcessError):seal_simulator(app)
    (app/'Fixture').write_bytes(binary(platform=2))
    with pytest.raises(ValueError,match='mismatch'):seal_simulator(app)

def test_simulator_identity_requires_own_linker_metadata(tmp_path):
    app=fixture(tmp_path)
    for value in [False,{'application-identifier':'TESTSIM000.foreign.app'},{'application-identifier':'TESTSIM000.'+IDENTIFIER,'keychain-access-groups':['foreign.group']}]:
        (app/'Fixture').write_bytes(binary(entitlements=value))
        with pytest.raises(ValueError,match='simulator Keychain'):simulator_identity(app)

def test_device_cannot_contain_simulated_identity(tmp_path):
    app=fixture(tmp_path);raw=bytearray(binary());struct.pack_into('<I',raw,40,2);(app/'Fixture').write_bytes(raw)
    path=app/'Info.plist';info=plistlib.loads(path.read_bytes());info['CFBundleSupportedPlatforms']=['iPhoneOS'];path.write_bytes(plistlib.dumps(info))
    with pytest.raises(ValueError,match='Simulator identity'):audit(app,'device')

def test_simulator_build_settings_keep_minimum_and_scope_signing_to_simulator():
    original={'targets':{'app':{'platform':'iOS','settings':{'base':{'OTHER_LDFLAGS':'keep'}}}}}
    result=simulator_project(original);settings=result['targets']['app']['settings']['base']
    assert settings['OTHER_LDFLAGS']=='keep' and settings['IPHONEOS_DEPLOYMENT_TARGET']=='16.0'
    assert settings['CODE_SIGN_IDENTITY[sdk=iphonesimulator*]']=='-'
    assert settings['DEVELOPMENT_TEAM[sdk=iphonesimulator*]']==''
    assert 'CODE_SIGNING_ALLOWED' not in settings and result['options']['deploymentTarget']['iOS']=='16.0'
    assert original['targets']['app']['settings']['base']=={'OTHER_LDFLAGS':'keep'}
    env={'CODE_SIGNING_ALLOWED':'NO','CODE_SIGN_IDENTITY':''};child=simulator_environment(env)
    assert child['CODE_SIGNING_ALLOWED']=='YES' and child['CODE_SIGN_IDENTITY']=='-' and env['CODE_SIGNING_ALLOWED']=='NO'

def test_real_simulator_seal_survives_archive_and_rejects_changed_resources(tmp_path):
    """Opt-in SDK artifact evidence; never an iOS UI or signing-team result."""
    import os,subprocess
    source=os.environ.get('PTCG_IOS_SIMULATOR_APP')
    if not source:pytest.skip('Set PTCG_IOS_SIMULATOR_APP to a real isolated candidate')
    app=Path(source);checked=audit(app,'simulator')
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)
    simulator_identity(app)
    packed=archive(app,tmp_path/'packed','simulator')
    output=tmp_path/'unpacked';output.mkdir()
    subprocess.run(['ditto','-x','-k',str(tmp_path/'packed'/packed['file']),str(output)],check=True,capture_output=True)
    restored=output/app.name
    subprocess.run(['codesign','--verify','--deep','--strict',str(restored)],check=True,capture_output=True)
    simulator_identity(restored)
    resource=restored/checked['resources']/'catalog.json';resource.write_bytes(resource.read_bytes()+b'\n')
    assert subprocess.run(['codesign','--verify','--deep','--strict',str(restored)],capture_output=True).returncode!=0

@pytest.mark.parametrize('mutation',['user-data','links','wrong-id','wrong-minimum','resource-hash','notice-hash','extra-notice'])
def test_bundle_audit_rejects_payload_or_identity_changes(tmp_path,mutation):
    app=fixture(tmp_path)
    if mutation=='user-data':(app/'mobile.sqlite').write_bytes(b'private')
    elif mutation=='links':(app/'linked').symlink_to(app/'Fixture')
    elif mutation=='wrong-id':
        path=app/'Info.plist';value=plistlib.loads(path.read_bytes());value['CFBundleIdentifier']='other.app';path.write_bytes(plistlib.dumps(value))
    elif mutation=='wrong-minimum':(app/'Fixture').write_bytes(binary(minimum=27<<16))
    elif mutation=='resource-hash':(app/'assets/catalog/catalog.json').write_bytes(b'changed')
    elif mutation=='notice-hash':(app/'assets/licenses/LICENSE').write_text('changed')
    else:(app/'assets/licenses/unsealed').write_text('extra')
    with pytest.raises(ValueError):audit(app,'simulator')

def test_macho_rejects_truncation_and_mac_platform_is_not_ios(tmp_path):
    with pytest.raises(ValueError):macho(binary()[:40])
    app=fixture(tmp_path);(app/'Fixture').write_bytes(binary(platform=1))
    with pytest.raises(ValueError,match='mismatch'):audit(app,'simulator')

def test_runtime_ledger_requires_evidence_and_preserves_other_pending_cases(tmp_path):
    artifact=tmp_path/'candidate.app.zip';artifact.write_bytes(b'Synthetic archive only')
    ledger=tmp_path/'ACCEPTANCE.json';ledger.write_text(json.dumps(create(sha256(artifact.read_bytes()).hexdigest(),'b'*64,'simulator')))
    with pytest.raises(ValueError,match='evidence'):update(ledger,'launch-offline','pass',[],system='iOS 27.2',device='simulator',operator='tester',artifact=artifact)
    proof=tmp_path/'proof.txt';proof.write_text('Synthetic test evidence only')
    value=update(ledger,'launch-offline','pass',[proof],system='iOS 27.2',device='simulator',operator='tester',artifact=artifact)
    assert value['cases']['launch-offline']['status']=='pass'
    assert all(value['cases'][key]['status']=='pending' for key in CASES if key!='launch-offline')
    assert not value['runtimeAccepted'] and not value['releaseReady']
    artifact.write_bytes(b'Different candidate')
    with pytest.raises(ValueError,match='match the ledger'):update(ledger,'launch-offline','pass',[proof],system='iOS 27.2',device='simulator',operator='tester',artifact=artifact)

def test_build_environment_blocks_global_install_and_strips_signing_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('APPLE_CERTIFICATE','synthetic');monkeypatch.setenv('IOS_MOBILE_PROVISION','synthetic')
    env=environment(tmp_path,tmp_path/'rust',tmp_path/'cargo')
    assert 'APPLE_CERTIFICATE' not in env and 'IOS_MOBILE_PROVISION' not in env
    assert env['CARGO_NET_OFFLINE']=='true' and env['CODE_SIGNING_ALLOWED']=='NO'
    assert env['HOME']==__import__('os').environ['HOME']
    assert 'exit 86' in (tmp_path/'guard-bin/brew').read_text()

def test_build_cache_is_explicit_and_cannot_follow_links(tmp_path):
    work=tmp_path/'work';work.mkdir();cache=tmp_path/'cache';cache.mkdir()
    value=environment(work,tmp_path/'rust',tmp_path/'cargo',target_cache=cache)
    assert value['CARGO_TARGET_DIR']==str(cache)
    link=tmp_path/'linked';link.symlink_to(cache,target_is_directory=True)
    other=tmp_path/'other';other.mkdir()
    with pytest.raises(ValueError,match='links forbidden'):
        environment(other,tmp_path/'rust',tmp_path/'cargo',target_cache=link)

def test_mobile_inventory_reads_verified_crate_archive_and_never_copies_cache(tmp_path):
    root=tmp_path/'source';(root/'apps/mobile/src-tauri').mkdir(parents=True);(root/'node_modules/react').mkdir(parents=True)
    (root/'LICENSE').write_text('Project MIT');(root/'THIRD_PARTY_NOTICES.md').write_text('Project notices')
    (root/'apps/mobile/package.json').write_text(json.dumps({'version':'0.8.0','dependencies':{'@ptcg/shared':'0.8.0'}}))
    (root/'packages/shared').mkdir(parents=True)
    (root/'packages/shared/package.json').write_text(json.dumps({'name':'@ptcg/shared','version':'0.8.0','dependencies':{'react':'1.0.0'}}))
    (root/'node_modules/react/package.json').write_text(json.dumps({'name':'react','version':'1.0.0','license':'MIT'}));(root/'node_modules/react/LICENSE').write_text('React MIT')
    (root/'package-lock.json').write_text(json.dumps({'packages':{'node_modules/react':{'version':'1.0.0','integrity':'synthetic'},'node_modules/@ptcg/shared':{'link':True,'resolved':'packages/shared'},'packages/shared':{'version':'0.8.0'}}}))
    cache=tmp_path/'cargo/registry/cache/example';cache.mkdir(parents=True);crate=cache/'example-1.0.0.crate'
    with tarfile.open(crate,'w:gz') as t:
        for name,raw in {'Cargo.toml':b'[package]\nlicense="MIT"\n','LICENCE':b'Example MIT'}.items():
            info=tarfile.TarInfo('example-1.0.0/'+name);info.size=len(raw);t.addfile(info,io.BytesIO(raw))
    digest=sha256(crate.read_bytes()).hexdigest()
    (root/'apps/mobile/src-tauri/Cargo.lock').write_text('[[package]]\nname="example"\nversion="1.0.0"\nchecksum="'+digest+'"\n')
    metadata={'packages':[{'id':'registry-example','name':'example','version':'1.0.0','source':'registry+https://example.invalid'}],'resolve':{'root':'registry-example','nodes':[{'id':'registry-example','deps':[]}]}}
    value=generate(root,tmp_path/'cargo',metadata,tmp_path/'notices')
    assert value['evidenceComplete'] and len(value['components'])==4
    assert all('registry/cache' not in p for p in value['files'])
    crate.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='checksum'):generate(root,tmp_path/'cargo',metadata,tmp_path/'rejected')

def test_tools_reject_bad_checksum_java_archive_and_dependency_drift():
    with pytest.raises(ValueError,match='checksum'):checked(b'tampered','a'*64)
    item={'version':'1.0.0','dependencies':[]}
    spec={'name':'example','version':'1.0.0','platform':'java','compatible':True,'dependencies':[]}
    with pytest.raises(ValueError,match='platform'):validate_spec(spec,'example',item)
    spec['platform']='ruby';spec['dependencies']=[{'name':'unlocked','requirements':'>= 0'}]
    with pytest.raises(ValueError,match='dependencies'):validate_spec(spec,'example',item)

def test_candidate_refuses_uninitialized_xcode_before_cli_or_source_export(tmp_path,monkeypatch):
    from scripts.native import ios_candidate as module
    import subprocess
    calls=[]
    def run(args,**kw):
        calls.append(args)
        return subprocess.CompletedProcess(args,69)
    monkeypatch.setattr(module.subprocess,'run',run)
    monkeypatch.setattr(module,'provenance',lambda:{'publicSourceSha256':'a'*64})
    with pytest.raises(RuntimeError,match='xcode-ready'):
        module.candidate(tmp_path/'assets',tmp_path/'work',tmp_path/'out',rust_bin=tmp_path/'rust',cargo_home=tmp_path/'cargo')
    assert calls==[['xcodebuild','-checkFirstLaunchStatus']]
    assert not (tmp_path/'work/source.zip').exists()
    assert json.loads((tmp_path/'out/BUILD-REPORT.json').read_text())['runtimeAccepted'] is False

@pytest.mark.parametrize('location',['Developer/Applications/Simulator.app','Applications/Simulator.app','Applications/DeviceHub.app'])
def test_doctor_finds_both_xcode_simulator_layouts(tmp_path,monkeypatch,location):
    from scripts.native import ios
    import subprocess
    contents=tmp_path/'Xcode.app/Contents';developer=contents/'Developer'
    (contents/'MacOS').mkdir(parents=True);(contents/'MacOS/Xcode').write_bytes(b'fixture')
    (developer/'Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS27.2.sdk').mkdir(parents=True)
    (contents/location).mkdir(parents=True)
    monkeypatch.setenv('DEVELOPER_DIR',str(developer))
    monkeypatch.setattr(ios.shutil,'which',lambda name:'/private-tools/'+name)
    monkeypatch.setattr(ios.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,0))
    result=ios.doctor()
    assert result['simulatorAppAvailable'] and result['iosProjectPrerequisitesReady']
    assert result['runtimeExecution']=='not checked'
