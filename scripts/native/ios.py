"""Prepare private iOS candidates; no distribution identity or deployment."""
import argparse
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0,str(ROOT))


def prepare(assets,output):
    from scripts.packaging.common import new_output,record
    from scripts.packaging.mobile_assets import build
    assets,output=Path(assets).resolve(),Path(output).absolute()
    if output.resolve().is_relative_to(assets) or assets.is_relative_to(output.resolve()):
        raise ValueError("Resource source and output must be separate directories")
    output=new_output(output)
    report=build(assets,output/"resources")
    config=output/"tauri.ios.conf.json"
    record(config,{
        "build":{"frontendDist":str(ROOT/"apps/mobile/dist")},
        "bundle":{"resources":{str(output/"resources")+"/":"catalog/"}},
    })
    record(output/"PREPARED.json",{**report,"nativeExecution":"pending","signed":False,"releaseReady":False})
    return {**report,"configuration":str(config)}


def doctor(runtime=False):
    selected=os.environ.get("DEVELOPER_DIR")
    if not selected:
        try:
            selected=subprocess.run(["xcode-select","-p"],check=True,capture_output=True,text=True,timeout=5).stdout.strip()
        except (OSError,subprocess.SubprocessError):selected=None
    developer=Path(selected) if selected else None
    checks={
        "xcode":bool(developer and (developer.parent/"MacOS/Xcode").is_file()),
        "iosSDK":bool(developer and list((developer/"Platforms/iPhoneOS.platform/Developer/SDKs").glob("iPhoneOS*.sdk"))),
        "cocoaPods":shutil.which("pod") is not None,
        "xcodeGen":shutil.which("xcodegen") is not None,
        "simulatorApp":bool(developer and any(p.is_dir() for p in (developer/"Applications/Simulator.app",developer.parent/"Applications/Simulator.app",developer.parent/"Applications/DeviceHub.app"))),
        "node":shutil.which("node") is not None,
    }
    first_launch=False
    if checks['xcode']:
        try:first_launch=subprocess.run([str(developer/'usr/bin/xcodebuild'),'-checkFirstLaunchStatus'],capture_output=True,timeout=8).returncode==0
        except (OSError,subprocess.SubprocessError):pass
    checks['xcodeFirstLaunchComplete']=first_launch
    report={"checks":checks,"iosProjectPrerequisitesReady":all(checks[k] for k in ("xcode","iosSDK","cocoaPods","xcodeGen","node","xcodeFirstLaunchComplete")),
            "simulatorAppAvailable":checks["simulatorApp"],"runtimeExecution":"not checked","deviceSigning":"not checked",
            "note":"Build SDK, runtime availability, signing and actual execution are separate gates; SDK compilation is not device acceptance."}
    if runtime and not first_launch:
        report['simulatorRuntimes']='not queried: Xcode first launch incomplete; no automatic initialization'
    elif runtime:
        try:
            result=subprocess.run(["xcrun","simctl","list","runtimes","--json"],capture_output=True,text=True,timeout=10)
            if result.returncode==0:
                rows=json.loads(result.stdout).get('runtimes',[])
                report['simulatorRuntimes']=[{'name':r.get('name'),'version':r.get('version'),'available':r.get('isAvailable',False)} for r in rows]
            else:report['simulatorRuntimes']='query failed'
        except (OSError,subprocess.SubprocessError,ValueError):report['simulatorRuntimes']='query unavailable or timed out'
    return report



if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    command=sub.add_parser("prepare")
    command.add_argument("--assets",required=True,type=Path)
    command.add_argument("--output",required=True,type=Path)
    command=sub.add_parser("doctor")
    command.add_argument("--runtime",action="store_true")
    command=sub.add_parser("tools")
    command.add_argument("--output",required=True,type=Path)
    command.add_argument("--cache",type=Path)
    command.add_argument("--offline",action="store_true")
    command=sub.add_parser("bundle")
    command.add_argument("--resources",required=True,type=Path)
    command.add_argument("--output",required=True,type=Path)
    command=sub.add_parser("candidate")
    command.add_argument("--assets",required=True,type=Path)
    command.add_argument("--work",required=True,type=Path)
    command.add_argument("--output",required=True,type=Path)
    command.add_argument("--rust-bin",required=True,type=Path)
    command.add_argument("--cargo-home",required=True,type=Path)
    command.add_argument("--tools-bin",type=Path)
    command.add_argument("--gems",type=Path)
    command.add_argument("--target-cache",type=Path)
    command.add_argument("--target",choices=["simulator","device"],action="append")
    command=sub.add_parser("record")
    command.add_argument("--ledger",required=True,type=Path)
    command.add_argument("--artifact",required=True,type=Path)
    command.add_argument("--case",required=True)
    command.add_argument("--result",required=True,choices=['pass','fail','blocked','pending'])
    command.add_argument("--evidence",action='append',type=Path,default=[])
    command.add_argument("--system",required=True)
    command.add_argument("--device-class",required=True)
    command.add_argument("--operator",required=True)
    args=parser.parse_args()
    if args.command=='doctor':result=doctor(args.runtime)
    else:
        if sys.version_info<(3,14):parser.error('iOS packaging requires Python 3.14+')
        if args.command=='prepare':result=prepare(args.assets,args.output)
        elif args.command=='tools':
            from scripts.native.ios_tools import install
            result=install(args.output,cache=args.cache,offline=args.offline)
        elif args.command=='bundle':
            from scripts.packaging.mobile_assets import bundle
            result=bundle(args.resources,args.output)
        elif args.command=='candidate':
            from scripts.native.ios_candidate import candidate
            result=candidate(args.assets,args.work,args.output,rust_bin=args.rust_bin,cargo_home=args.cargo_home,tools=args.tools_bin,gems=args.gems,target_cache=args.target_cache,targets=args.target or ['simulator'])
        else:
            from scripts.native.ios_acceptance import update
            result=update(args.ledger,args.case,args.result,args.evidence,system=args.system,device=args.device_class,operator=args.operator,artifact=args.artifact)
    print(json.dumps(result,ensure_ascii=False))
