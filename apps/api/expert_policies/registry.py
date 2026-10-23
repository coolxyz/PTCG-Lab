"""Exact own-cardlist routing, immutable match pins and fail-closed rollout."""
from collections import Counter
from functools import lru_cache
import hashlib,json,time
from pathlib import Path
from packages.battle import h2
from packages.simulation.a2 import legal
from packages.collection.domain import require
from apps.api.expert_policies import grimmsnarl,dragapult,joltik,zoroark,gardevoir
POLICIES={p.PROFILE['presetId']:p for p in (grimmsnarl,dragapult,joltik,zoroark,gardevoir)}
NAMES={'h2-gardevoir-ex':'沙奈朵专属 v5','h2-n-zoroark-ex':'N 索罗亚克专属 v3','h2-marnie-grimmsnarl':'长毛巨魔专属 v22','h2-dragapult-dusknoir':'多龙专属 v9','h2-joltik-box':'电电虫专属 v1'}

def bundle_hash():
    folder=Path(__file__).parent
    files=sorted(p for p in folder.iterdir() if p.suffix in ('.py','.json'))
    # Include the service integration itself, which controls routing and disclosure.
    files.append(folder.parent/'expert_service.py')
    return hashlib.sha256(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},sort_keys=True).encode()).hexdigest()

@lru_cache(maxsize=1)
def identity():return bundle_hash(),grimmsnarl.environment_hash()

def rollout_path(db_path):return Path(db_path).parent/'h2-experts-rollout.json'

def select(entries,db_path):
    try:
        config=json.loads(rollout_path(db_path).read_text())
    except FileNotFoundError:
        config=json.loads((Path(__file__).parent.parent/'h2-rollout.json').read_text())
    except (OSError,ValueError):return None
    if not isinstance(config,dict) or not isinstance(config.get('enabled'),list):return None
    bundle,env=identity()
    if config.get('bundleHash')!=bundle:return None
    for key in config.get('enabled',[]):
        p=POLICIES.get(key)
        if p and env==p.PROFILE['environmentHash'] and grimmsnarl.deck_hash(entries)==p.PROFILE['deckHash']:
            return {'id':key,'version':p.VERSION,'deckHash':p.PROFILE['deckHash'],'environmentHash':env,'bundleHash':bundle}
    return None

def line_counts(lines):
    counts=Counter()
    for line in lines:
        quantity,name=line.split(' ',1)
        counts[name]+=int(quantity)
    return counts

def validate(pin,lines):
    from packages.battle.service import MatchService
    p=POLICIES.get(pin.get('id'));bundle,env=identity()
    require(p is not None and pin=={'id':pin['id'],'version':p.VERSION,'deckHash':p.PROFILE['deckHash'],'environmentHash':env,'bundleHash':bundle},'H2_EXPERT_MISMATCH','专属策略版本不匹配，请保留回放并开始新对局',409)
    require(env==p.PROFILE['environmentHash'] and line_counts(lines)==line_counts(MatchService._lines(p.PROFILE['entries'])),'H2_EXPERT_DECK_MISMATCH','专属策略卡表或环境不匹配',409)
    return p

def public(pin):
    return {'id':pin['id'],'version':pin['version'],'name':NAMES.get(pin['id'],pin['version'])} if pin else {'id':'original-h2','version':h2.VERSION,'name':'原 H2'}

def decide(dto,pin):
    p=POLICIES[pin['id']];start=time.perf_counter();reason=None
    try:
        cmd,detail=p.decide(dto,entries=p.PROFILE['entries'],environment=pin['environmentHash'])
        if detail.get('fallback') or not legal(dto,cmd):raise ValueError('invalid')
        if time.perf_counter()-start>1:raise TimeoutError('budget')
    except Exception as exc:
        reason='expert-budget' if isinstance(exc,TimeoutError) else 'expert-error'
        cmd,detail=h2.decide(dto)
    # Never forward expert plans, hand names, candidate scores or exception text.
    return cmd,{'fallback':reason is not None,'fallbackReason':reason,'aiMilliseconds':round((time.perf_counter()-start)*1000,3),'expertVersion':p.VERSION}
