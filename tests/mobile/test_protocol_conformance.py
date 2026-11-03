"""Differential checks against the current shared Python validators, not a rule engine."""
from copy import deepcopy
import json,os,subprocess
from pathlib import Path
import pytest
from ptcg_online_client.contracts import checked,ClientError

ROOT=Path(__file__).resolve().parents[2]


def test_rust_decoder_matches_current_shared_contracts():
    binary=os.environ.get('PTCG_MOBILE_PROTOCOL_PROBE')
    if not binary:pytest.skip('Build protocol_probe and set PTCG_MOBILE_PROTOCOL_PROBE')
    corpus=json.loads((ROOT/'tests/synthetic/protocol/v2.json').read_text())
    cases=deepcopy(corpus)
    for row in corpus:
        for field,value in [('unexpectedField',True),('protocolVersion','1.0')]:
            changed=deepcopy(row);changed['value'][field]=value;cases.append(changed)
    original=next(row for row in corpus if row['kind']=='MatchSnapshot' and row['value']['viewerSeat']=='PLAYER1')
    for path,value in [(['matchSeq'],True),(['matchSeq'],1.0),(['decision','actor'],'PLAYER2'),(['observation','self','handCount'],5),(['observation','opponent','hand'],[{'cardId':'secret','name':'hidden'}])]:
        changed=deepcopy(original);parent=changed['value']
        for key in path[:-1]:parent=parent[key]
        parent[path[-1]]=value;cases.append(changed)
    hidden=next(row for row in corpus if row['kind']=='MatchSnapshot' and row['value']['decision'] and row['value']['decision']['kind']=='selection')
    changed=deepcopy(hidden);changed['value']['decision']['candidates'][0]['card']={'cardId':'secret','name':'hidden'};cases.append(changed)
    changed=deepcopy(hidden);changed['value']['decision']['max']=999;cases.append(changed)
    deck=next(row for row in corpus if row['kind']=='DeckSubmission')
    changed=deepcopy(deck);changed['value']['entries'][0]['quantity']=59;cases.append(changed)
    changed=deepcopy(deck);changed['value']['entries']*=2;cases.append(changed)
    replay=next(row for row in corpus if row['kind']=='ReplayPage')
    changed=deepcopy(replay);changed['value']['frames']*=2;cases.append(changed)
    caps=next(row for row in corpus if row['kind']=='Capabilities')
    changed=deepcopy(caps);changed['value']['ruleReleaseIds']=[];cases.append(changed)
    tokens={'protocolVersion':'2.0','serverId':'synthetic','userId':'1'*32,'deviceId':'2'*32,'sessionId':'3'*32,'tokenType':'Bearer','accessToken':'atk_'+'a'*43,'refreshToken':'rtk_'+'b'*43,'accessExpiresAt':'2099-01-01T00:00:00Z','refreshExpiresAt':'2099-02-01T00:00:00Z'}
    cases.append({'kind':'Tokens','value':tokens})
    changed=deepcopy(tokens);changed['accessToken']='invalid';cases.append({'kind':'Tokens','value':changed})
    expected=[]
    for row in cases:
        try:checked(row['kind'],row['value']);expected.append(True)
        except ClientError:expected.append(False)
    process=subprocess.run([binary],input=''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in cases),capture_output=True,text=True,timeout=30,check=True)
    actual=[json.loads(line)['valid'] for line in process.stdout.splitlines()]
    assert len(actual)==len(expected)
    assert actual==expected
