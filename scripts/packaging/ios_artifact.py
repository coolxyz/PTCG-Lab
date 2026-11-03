"""Inspect an actual unsigned iOS .app; never substitute SDK checks for a bundle."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import plistlib
import struct
import subprocess
import re
import zipfile

from scripts.packaging.common import record, new_output
from scripts.public.policy import safe_file

IDENTIFIER = "org.ptcglab.mobile.preview"
PLATFORMS = {"device": 2, "simulator": 7}


def macho(raw):
    if len(raw) < 32 or raw[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("A thin arm64 Mach-O is required")
    _, cpu, _, kind, count, size, _, _ = struct.unpack_from("<8I", raw)
    if cpu != 0x100000C or kind not in {2, 6} or count > 4096 or size > len(raw)-32:
        raise ValueError("Invalid arm64 Mach-O header")
    cursor, end, build = 32, 32+size, None
    dependencies, rpaths, simulated = [], [], None
    for _ in range(count):
        if cursor+8 > end: raise ValueError("Truncated Mach-O")
        command, length = struct.unpack_from("<II", raw, cursor)
        if length < 8 or length % 4 or cursor+length > end: raise ValueError("Invalid load command")
        if command == 0x32:
            if length < 24 or build is not None: raise ValueError("Invalid platform command")
            platform, minimum, sdk, tools = struct.unpack_from("<4I", raw, cursor+8)
            if length != 24+tools*8: raise ValueError("Invalid build tools")
            decode = lambda n: f"{n>>16}.{(n>>8)&255}.{n&255}"
            build = {"platform": platform, "minimumOS": decode(minimum), "sdk": decode(sdk)}
        elif command == 0x19:
            if length < 72:raise ValueError('Invalid segment')
            sections=struct.unpack_from('<I',raw,cursor+64)[0]
            if length!=72+sections*80:raise ValueError('Invalid section count')
            for index in range(sections):
                section=cursor+72+index*80
                if raw[section:section+16].split(b'\0',1)[0]==b'__entitlements' and raw[section+16:section+32].split(b'\0',1)[0]==b'__TEXT':
                    size=struct.unpack_from('<Q',raw,section+40)[0];offset=struct.unpack_from('<I',raw,section+48)[0]
                    if simulated is not None or not 0<size<=65536 or offset<end or offset+size>len(raw):raise ValueError('Invalid simulated entitlements')
                    try:simulated=plistlib.loads(raw[offset:offset+size])
                    except Exception as error:raise ValueError('Invalid simulated entitlements') from error
                    if not isinstance(simulated,dict):raise ValueError('Invalid simulated entitlements')
        elif command in {0xC,0x18|0x80000000,0x1F|0x80000000,0x23|0x80000000,0x20,0x1C|0x80000000}:
            if length < 12: raise ValueError("Invalid loader command")
            offset = struct.unpack_from("<I",raw,cursor+8)[0]
            if offset < 12 or offset >= length: raise ValueError("Invalid loader offset")
            text = raw[cursor+offset:cursor+length]
            if b"\0" not in text: raise ValueError("Unterminated loader path")
            text = text.split(b"\0",1)[0].decode("utf8")
            (rpaths if command == 0x8000001C else dependencies).append(text)
        cursor += length
    if cursor != end or build is None: raise ValueError("Missing iOS platform identity")
    return {**build,"architecture":"arm64","fileType":kind,"dependencies":dependencies,"rpaths":rpaths,**({'simulatedEntitlements':simulated} if simulated is not None else {})}


def simulator_identity(app):
    app=Path(app);info=plistlib.loads((app/'Info.plist').read_bytes())
    entitlements=macho(safe_file(app,info['CFBundleExecutable']).read_bytes()).get('simulatedEntitlements',{})
    identity=entitlements.get('application-identifier','')
    if not isinstance(identity,str) or not re.fullmatch(r'[A-Z0-9]{10}\.'+re.escape(IDENTIFIER),identity) or not set(entitlements)<={'application-identifier','keychain-access-groups'} or entitlements.get('keychain-access-groups',[identity])!=[identity]:
        raise ValueError('Missing or foreign Xcode simulator Keychain identity')
    return identity


def audit(app, target, *, expected_resources=None, require_notices=True):
    app = Path(app).absolute()
    if target not in PLATFORMS or app.suffix != ".app" or not app.is_dir(): raise ValueError("Actual .app and explicit target required")
    if any(p.is_symlink() for p in (app,*app.parents,*app.rglob("*"))): raise ValueError("Bundle links forbidden")
    info = plistlib.loads(safe_file(app,"Info.plist").read_bytes())
    executable = info.get("CFBundleExecutable","")
    if info.get("CFBundleIdentifier") != IDENTIFIER or not executable or PurePosixPath(executable).name != executable or ":" in executable or "\\" in executable:
        raise ValueError("Wrong mobile bundle identity")
    binary = macho(safe_file(app,executable).read_bytes())
    supported = "iPhoneOS" if target == "device" else "iPhoneSimulator"
    if binary["platform"] != PLATFORMS[target] or info.get("CFBundleSupportedPlatforms") != [supported] or binary["fileType"] != 2:
        raise ValueError("Device/simulator identity mismatch")
    if target=='device' and 'simulatedEntitlements' in binary:raise ValueError('Simulator identity in device artifact')
    if tuple(map(int,binary["minimumOS"].split('.'))) > (16,0,0): raise ValueError("Binary exceeds declared iOS 16 minimum")
    if str(info.get("MinimumOSVersion")) not in {"16.0","16.0.0"}: raise ValueError("Unexpected minimum OS declaration")
    forbidden = {"runtime","python","engine","workers","credentials","profiles","node_modules",".git","data"}
    files, binaries = {}, []
    for path in sorted(app.rglob("*")):
        if not path.is_file(): continue
        name = path.relative_to(app).as_posix()
        if forbidden.intersection(p.lower() for p in path.relative_to(app).parts) or path.suffix.lower() in {".py",".pyc",".sqlite",".sealed",".pem",".key",".p12",".mobileprovision",".onnx"} or path.name.startswith('.env'):
            raise ValueError("Runtime, user data or credentials in mobile bundle")
        raw = path.read_bytes(); files[name] = sha256(raw).hexdigest()
        if raw[:4] == b"\xcf\xfa\xed\xfe":
            item=macho(raw)
            if item['platform']!=PLATFORMS[target]:raise ValueError('Foreign embedded Mach-O')
            for dependency in item['dependencies']:
                if '..' in PurePosixPath(dependency).parts or not dependency.startswith(('/System/Library/','/usr/lib/','@rpath/','@loader_path/','@executable_path/')):
                    raise ValueError('Development-machine dynamic dependency')
            for rpath in item['rpaths']:
                if '..' in PurePosixPath(rpath).parts or not rpath.startswith(('@executable_path/','@loader_path/','/usr/lib/swift')):raise ValueError('Development-machine rpath')
            binaries.append({'path':name,**item})
    # Tauri's iOS resource_dir is the bundle's assets directory.
    roots=[p.parent for p in app.rglob('manifest.json') if p.parent.name=='catalog']
    if len(roots)!=1:raise ValueError('Unique catalog resources required')
    resource_root=roots[0];manifest=json.loads(safe_file(resource_root,'manifest.json').read_bytes())
    if manifest.get('schema')!=1 or set(manifest.get('files',{}))!={'catalog.json','artwork.json'}:raise ValueError('Invalid display manifest')
    for name,digest in manifest['files'].items():
        raw=safe_file(resource_root,name).read_bytes()
        if sha256(raw).hexdigest()!=digest:raise ValueError('Changed display resources')
        if expected_resources and raw!=safe_file(expected_resources,name).read_bytes():raise ValueError('Unexpected display resource provenance')
    if expected_resources and safe_file(resource_root,'manifest.json').read_bytes()!=safe_file(expected_resources,'manifest.json').read_bytes():raise ValueError('Changed display manifest')
    notices=resource_root.parent/'licenses'
    if require_notices:
        inventory=json.loads(safe_file(notices,'INVENTORY.json').read_bytes())
        actual={p.relative_to(notices).as_posix() for p in notices.rglob('*') if p.is_file()}
        if actual!=set(inventory['files'])|{'INVENTORY.json'}:raise ValueError('Unexpected notice payload')
        for name,digest in inventory['files'].items():
            if sha256(safe_file(notices,name).read_bytes()).hexdigest()!=digest:raise ValueError('Changed dependency notice')
    return {'schema':1,'target':target,'identifier':IDENTIFIER,'version':info.get('CFBundleShortVersionString'),'buildNumber':info.get('CFBundleVersion'),'binary':binary,'binaries':binaries,'files':files,'resources':resource_root.relative_to(app).as_posix(),'containsPython':False,'containsRuleExecutor':False,'runtimeExecution':'pending','releaseReady':False}


def seal_simulator(app, *, expected_resources=None):
    """Reseal a validated simulator bundle without a distribution identity.

    --no-sign builds can retain a cached executable's resource seal while the
    bundle resources change. Installation may succeed but launchd then rejects
    it. Seal the final resources, including nested code, before archiving.
    """
    app=Path(app).absolute()
    checked=audit(app,'simulator',expected_resources=expected_resources)
    identity=simulator_identity(app)
    executable=plistlib.loads((app/'Info.plist').read_bytes())['CFBundleExecutable']
    for item in sorted(checked['binaries'],key=lambda item:len(PurePosixPath(item['path']).parts),reverse=True):
        if item['path']!=executable:
            subprocess.run(['codesign','--force','--sign','-','--timestamp=none',str(app/item['path'])],check=True,capture_output=True)
    subprocess.run(['codesign','--force','--sign','-','--timestamp=none',str(app)],check=True,capture_output=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)
    return {'target':'simulator','codeSeal':'ad-hoc','verified':True,'distributionSigned':False,'simulatedApplicationIdentifier':identity,'defaultKeychainAccessGroup':identity}


def archive(app, output, target, *, expected_resources=None):
    app,output=Path(app).absolute(),Path(output).absolute()
    if output.is_relative_to(app) or app.is_relative_to(output):raise ValueError('Separate app/archive paths required')
    checked=audit(app,target,expected_resources=expected_resources)
    output=new_output(output);app=Path(app)
    name=f'PTCG-Lab-ios-{target}.app.zip';archive_path=output/name
    with zipfile.ZipFile(archive_path,'x',zipfile.ZIP_DEFLATED) as z:
        for relative in checked['files']:
            source=safe_file(app,relative);info=zipfile.ZipInfo(app.name+'/'+relative);info.compress_type=zipfile.ZIP_DEFLATED
            # Preserve executable bits for installation on another build host.
            info.external_attr=(0o100000 | (source.stat().st_mode & 0o777))<<16
            z.writestr(info,source.read_bytes())
    record(output/'BUNDLE-AUDIT.json',checked)
    checksum=sha256(archive_path.read_bytes()).hexdigest()
    (output/(name+'.sha256')).write_text(checksum+'  '+name+'\n')
    return {'file':name,'sha256':checksum,'audit':checked}
