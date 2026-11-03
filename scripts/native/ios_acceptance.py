"""Artifact-bound mobile acceptance ledger; compilation never passes runtime cases."""
from hashlib import sha256
import json
from pathlib import Path
from datetime import datetime,timezone
from scripts.packaging.common import record

CASES={
 'launch-offline':'首次启动、离线卡库与中文说明',
 'collection-reopen':'品相、备注、收藏保存及重开',
 'collection-csv':'CSV 预览、导入去重、冲突及撤销',
 'deck-freeze':'卡组编辑、冻结身份与重开',
 'file-provider':'系统文件选择、取消和 iCloud 提供器',
 'share-receiver':'卡组/收藏分享及接收方文件校验',
 'catalog-update':'资料预览、原子应用、失败保留及重开',
 'artwork-cache':'Raw/CDN、校验、退避、断网缓存和系统回收',
 'keychain':'真实设备 Keychain、拒绝访问和日志重开',
 'data-protection':'锁屏文件保护与解锁后读取',
 'background-recovery':'前后台、游标补漏、晚到帧和控制权',
 'termination-recovery':'系统终止后原 commandId、回执及会话轮换恢复',
 'phone-layout':'iPhone 安全区、旋转、键盘、触控与全屏',
 'tablet-layout':'iPad 布局、触控及分享 popover',
 'cross-device-match':'与桌面完整自然对局及本人回放',
 'upgrade-restore':'设备升级迁移、异常保留及备份恢复',
 'minimum-system':'最低系统与设备矩阵',
 'dependency-review':'目标依赖许可与最终分发材料审阅',
 'signed-device':'签名真机安装与权限',
}

def create(app_sha,source_sha,target):
    if target not in {'device','simulator'}:raise ValueError('Explicit iOS target required')
    for value in [app_sha,source_sha]:
        if len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Artifact/source hash required')
    return {'schema':1,'artifactSha256':app_sha,'sourceSha256':source_sha,'target':target,'runtimeAccepted':False,'releaseReady':False,'cases':{key:{'title':title,'status':'pending','evidence':[]} for key,title in CASES.items()}}

def update(path,case,status,evidence,*,system,device,operator,artifact=None):
    path=Path(path);value=json.loads(path.read_text())
    if value.get('schema')!=1 or set(value.get('cases',{}))!=set(CASES) or case not in CASES or status not in {'pass','fail','blocked','pending'}:raise ValueError('Invalid acceptance case')
    create(value['artifactSha256'],value['sourceSha256'],value['target'])
    if artifact is None or Path(artifact).is_symlink() or not Path(artifact).is_file() or sha256(Path(artifact).read_bytes()).hexdigest()!=value['artifactSha256']:raise ValueError('Current candidate archive must match the ledger')
    if not all(isinstance(s,str) and 1<=len(s)<=200 and '\x00' not in s for s in [system,device,operator]):raise ValueError('System, device class and operator required; do not use UDID or credentials')
    items=[]
    for item in evidence:
        p=Path(item)
        if p.is_symlink() or not p.is_file() or p.stat().st_size>30*1024*1024:raise ValueError('Actual bounded evidence file required')
        items.append({'name':p.name,'sha256':sha256(p.read_bytes()).hexdigest()})
    if status=='pass' and not items:raise ValueError('Pass requires recorded evidence')
    value['cases'][case].update(status=status,evidence=items,system=system,deviceClass=device,operator=operator,recordedAt=datetime.now(timezone.utc).isoformat())
    value['runtimeAccepted']=all(c['status']=='pass' for c in value['cases'].values())
    # Distribution remains separate from an operator's runtime record.
    value['releaseReady']=False
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
    return value

def write_matrix(output,app_sha,source_sha,target):
    value=create(app_sha,source_sha,target);record(Path(output)/'ACCEPTANCE.json',value)
    text='# iOS 候选程序人工验收\n\n构建/启动不是运行验收。请使用同一源摘要和候选包 SHA，逐项提供系统版本、设备类别、操作者与证据；不要记录凭据、UDID 或完整私有对局数据。\n\n'
    text+='| 项目 | 初始结果 |\n| --- | --- |\n'+''.join('| '+title+' | pending |\n' for title in CASES.values())
    text+='\n模拟器包只用于对应架构模拟器；未签名真机包不能直接安装到 iPhone/iPad。真实 Keychain、锁屏、文件提供器和自然对局必须另验，不能以合成测试补记。\n'
    (Path(output)/'验收步骤.md').write_text(text,encoding='utf8')
    return value
