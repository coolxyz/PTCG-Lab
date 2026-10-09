"""Read-only snapshot audit: image bindings, local blobs and battle admission gaps."""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.request
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/01a11453-529f-74d0-b53f-41dfa039d5cb'
def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p, v):
    Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding='utf-8')
def natural(s):
    return [(0, int(x)) if x.isdecimal() else (1, x.casefold()) for x in re.split(r'(\d+)', s or '')]

rid = read(ROOT / '.catalog/sync/current.json')['releaseId']
base = ROOT / '.catalog/sync/releases' / rid
catalog = read(base / 'data/catalog/catalog.json')
commit = catalog['upstream']['commit']
normalized = read(ROOT / '.catalog/sync/snapshots' / commit / 'normalized.json')
rules = read(base / 'rulesets/cn-standard-2026-09-16.json')
scope = read(base / 'data/cardpool/battle-scope.json')
effects = {e['effectKey']: e for e in read(base / 'data/simulation/effects.json')['effects']}
with urllib.request.urlopen('http://127.0.0.1:8765/api/cards', timeout=60) as response:
    live = {c['printingId']: c for c in json.load(response)['cards']}
assert set(live) == {c['printingId'] for c in catalog['cards']}
products = {p['id']: p for p in catalog['products']}
issue_names = {'UNKNOWN_POKEMON_TYPE': '宝可梦种类枚举未识别', 'UNKNOWN_SPECIAL_CARD': '特殊卡类型未识别', 'UNKNOWN_ABILITY_COST': '招式费用未识别', 'UNKNOWN_TRAINER_TYPE': '训练家类型未识别'}

# Read all pinned image blobs through one Git process, with network lazy fetching
# disabled. PNG CRC/format checks + decoded pixels, limits and Git identity.
blobs = {c['imageBlob'] for c in normalized['cards'].values() if c.get('imageBlob')}
git = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=ROOT / '.catalog/upstream/PTCG-CHS-Datasets', stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'})
image_checks = read(OUT/'image-validation.json') if '--reuse-image-checks' in sys.argv else {}
if image_checks:
    previous = read(OUT/'audit-data.json')['summary']
    assert previous['commit'] == commit and set(image_checks) == blobs
try:
    for index, oid in enumerate(sorted(blobs - image_checks.keys())):
        git.stdin.write((oid + '\n').encode()); git.stdin.flush()
        header = git.stdout.readline().decode().strip().split()
        if len(header) != 3:
            image_checks[oid] = {'ok': False, 'reason': '本地 Git 对象缺失'}
            continue
        size = int(header[2]); data = git.stdout.read(size); assert git.stdout.read(1) == b'\n'
        try:
            assert len(data) == size and size <= 8*1024*1024
            assert hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + data).hexdigest() == oid
            with Image.open(io.BytesIO(data)) as image:
                assert image.format in ('PNG', 'JPEG', 'WEBP') and image.width*image.height <= 20_000_000
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                image_checks[oid] = {'ok': True, 'width': image.width, 'height': image.height}
        except Exception as exc:
            image_checks[oid] = {'ok': False, 'reason': '图像解码、大小或内容哈希校验失败', 'error': type(exc).__name__}
        if (index+1) % 2000 == 0:
            print(f'Checked images {index+1}/{len(blobs)}', flush=True)
finally:
    git.stdin.close(); git.wait(timeout=10)

supported_hashes = defaultdict(list)
for c in catalog['cards']:
    effect = effects.get(c.get('engineId'))
    if c.get('sourceVerified') and c.get('effectStatus') == 'verified' and effect and effect['status']=='experimental' and c['printingId'] in effect['printings']:
        for cid in c.get('upstreamFaces', {}):
            supported_hashes[normalized['cards'][cid]['ruleHash']].append(c['printingId'])

gaps, unsupported, uncertain = [], [], []
environment_counts = Counter()
target_total = supported_total = 0
for c in catalog['cards']:
    pid = c['printingId']; upids = sorted(c['upstreamFaces'], key=int)
    sources = [normalized['cards'][cid] for cid in upids]
    issues = sorted({issue for s in sources for issue in s['issues']})
    source_basic = any(s['face']['category']=='能量' and s['face']['energyType']=='基本能量' and s['face']['type'] in {'GRASS','FIRE','WATER','LIGHTNING','PSYCHIC','FIGHTING','DARK','METAL'} for s in sources)
    mapped_basic = c.get('basicEnergyType') in scope['basicEnergyTypes']
    marks = sorted({s['mark'] for s in sources if s.get('mark')} | ({c['mark']} if c.get('mark') else set()))
    in_marks = bool(set(marks) & set(rules['allowedMarks']) & set(scope['allowedMarks']))
    date = c.get('releasedAt')
    if not date:
        env = '发售日期待核实'
    elif date > catalog['asOf']:
        env = '尚未发售'
    elif pid in rules.get('bannedPrintings', []):
        env = '已禁用'
    elif in_marks or source_basic or mapped_basic:
        env = '当前环境目标'
    elif any(s['face']['category']=='能量' and s['face']['energyType']=='基本能量' for s in sources):
        env = '环境外'
    elif not marks:
        env = '标记待核实'
    else:
        env = '环境外'
    environment_counts[env] += 1
    pack_names = [products[p]['name'] for p in c.get('productIds', [])]
    row = {'卡牌ID': pid, '卡名': c['cnName'], '所属卡包': '；'.join(pack_names), '编号': c.get('printedNumber') or c.get('collectorNumber') or '', '卡牌类型': c['category'], '标记': '/'.join(marks) or '无标记', '发售日期': date or '', '环境分类': env, '上游版本ID': ','.join(upids), '上游版本数': len(upids), '来源异常': '；'.join(issue_names.get(i,i) for i in issues), '来源链接': f'https://github.com/duanxr/PTCG-CHS-Datasets/blob/{commit}/ptcg_chs_infos.json'}
    ui_image = live[pid]['image']
    reasons = []
    valid_sources = [s for s in sources if image_checks.get(s.get('imageBlob'), {}).get('ok')]
    if not ui_image.get('url'):
        reasons.append('本地图像完好但未绑定到卡牌' if valid_sources else '无可用上游图片')
    elif ui_image['url'].startswith('/api/sync/images/'):
        image_id = ui_image['url'].rsplit('/',1)[1]
        chosen = normalized['cards'].get(image_id)
        if not chosen or not image_checks.get(chosen.get('imageBlob'),{}).get('ok'):
            reasons.append('已绑定图片无法从本地缓存解码')
    elif ui_image['url'].startswith('/uploaded-card-images/'):
        path = ROOT / 'var/app-card-images' / Path(ui_image['url']).name
        try:
            with Image.open(path) as im: im.verify()
        except Exception: reasons.append('用户上传图片文件缺失或无效')
    else:
        reasons.append('图片地址不是当前本地来源，需复核')
    if reasons:
        sample = valid_sources[0] if valid_sources else sources[0]
        gaps.append({**row, '缺图原因': '；'.join(reasons), '本地候选图': len(valid_sources), '候选图路径': sample.get('imagePath') or '', '候选图接口': f'http://127.0.0.1:8765/api/sync/images/{commit}/{sample["id"]}', '建议处理': '将卡图绑定与规则枚举校验分离，独立校验后绑定' if valid_sources and issues else '核对来源记录和卡图绑定'})
    if env in ('标记待核实','发售日期待核实'):
        uncertain.append({**row, '待核实原因': env, '当前支持标记': c.get('effectStatus','')})
    if env != '当前环境目标':
        continue
    target_total += 1
    blockers = []
    effect = effects.get(c.get('engineId'))
    if not c.get('sourceVerified'): blockers.append('印刷资料未核验')
    if c.get('effectStatus') != 'verified': blockers.append('卡牌效果未核验')
    if not effect: blockers.append('无可用效果实现映射')
    elif effect.get('status') != 'experimental' or pid not in effect['printings']: blockers.append('效果发布未接纳该印刷')
    if source_basic and not mapped_basic: blockers.append('基本能量类型字段未映射')
    if not mapped_basic and c.get('mark') not in scope['allowedMarks']: blockers.append('本地环境标记未正确映射')
    if c.get('category')=='宝可梦' and c.get('isBasicPokemon') is None: blockers.append('进化阶段未核实')
    if not blockers:
        supported_total += 1
        continue
    reuse = sorted({p for s in sources for p in supported_hashes[s['ruleHash']] if p != pid})
    category = '基本能量映射缺失' if source_basic else '相同源规则已有支持，待映射验收' if reuse else '来源字段或身份需核实' if issues else '效果实现或规则验收缺失'
    face_text = []
    for s in sources[:1]:
        face = s['face']
        face_text += [a['name']+'：'+a.get('text','') for a in face['abilities']]
        face_text += [a['name']+' '+str(a.get('damage',''))+'：'+a.get('text','') for a in face['attacks']]
        face_text += [str(s.get('desc','')) for s in face['skills']]
        if face.get('ruleText'): face_text.append(face['ruleText'])
    unsupported.append({**row, '缺口分类': category, '未支持原因': '；'.join(blockers), '当前效果ID': c.get('engineId',''), '可复核的同规则身份': '；'.join(reuse), '规则哈希': '；'.join(sorted({s['ruleHash'] for s in sources})), '规则原文': '\n'.join(face_text)})

for rows in (gaps,unsupported,uncertain):
    rows.sort(key=lambda x:(x['所属卡包'], natural(x['编号']),x['卡牌ID']))
assert target_total == supported_total + len(unsupported)
summary = {'releaseId': rid, 'commit': commit, 'asOf': catalog['asOf'], 'catalogVersion': catalog['version'], 'catalogCards':len(catalog['cards']), 'sourceVariants':len(normalized['cards']), 'uniqueImageBlobsChecked':len(image_checks), 'invalidImageBlobs':sum(not v['ok'] for v in image_checks.values()), 'imageGaps':len(gaps), 'targetTotal':target_total, 'targetSupported':supported_total, 'battleGaps':len(unsupported), 'environmentCounts':dict(environment_counts), 'gapReasons':dict(Counter(r['缺口分类'] for r in unsupported)), 'imageReasons':dict(Counter(r['缺图原因'] for r in gaps)), 'uncertainCards':len(uncertain), 'overlap':len({r['卡牌ID'] for r in gaps}&{r['卡牌ID'] for r in unsupported})}
save(OUT/'audit-data.json', {'summary':summary,'images':gaps,'battle':unsupported,'uncertain':uncertain})
save(OUT/'image-validation.json', image_checks)
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
