"""Public, stateless goal chains over the frozen product v7.

Energy Switch endpoints are jointly ranked; prompts recompute from their lawful
board references. Search opportunities never imply an unseen deck card exists.
"""
import time
from apps.api.expert_policies import grimmsnarl_exchange_v7 as previous
from packages.battle.h2_v4.policy import deficit,utility
from packages.battle.h2_v4.tempo import estimate,attack_value,ready_value
from packages.simulation.a2 import legal

PROFILE=previous.PROFILE
VERSION='h2-grimmsnarl-pre-pivot-v22'
MAIN,MID,BASIC=previous.MAIN,previous.MID,previous.BASIC
facts=previous.facts

def field(team):return team['active']+team['bench']
def address(card):return (card.get('boardRef') or card['ref']).split(':',1)[1]
def replace(own,updates):
    return {**own,**{z:[updates.get(c['ref'],c) for c in own[z]] for z in ('active','bench')}}
def promoted(own,card):return {**own,'active':[card],'bench':[c for c in field(own) if c['ref']!=card['ref']]}
def retreat_ready(own):
    a=(own['active'] or [{}])[0]
    return (bool(own['active']) and not own.get('turnFlags',{}).get('retreated')
            and not a.get('retreatBlocked') and a.get('specialCondition') not in ('ASLEEP','PARALYZED')
            and len(a.get('energy',[]))>=len(a.get('retreatCost',[])))

def ready_attack(card,own,other):
    """Public readiness; a pivot must leave the destination attack funded."""
    if card.get('attackBlocked') or card.get('specialCondition') in ('ASLEEP','PARALYZED'):return -100
    team=promoted(own,card);values=[]
    for attack in card.get('attacks',[]):
        cost=attack.get('cost',[])
        if card['name']=='Bloodmoon Ursaluna ex' and attack['name']=='Blood Moon':cost=['COLORLESS']*max(0,len(cost)-(6-other['prize_count']))
        if attack['name'] in card.get('limitedAttacks',[]) or deficit(cost,card.get('energy',[])):continue
        if other['active'] and facts.protected(other['active'][0],other,card,False):continue
        value=attack_value(attack,card,team,other)
        values.append(value*(.5 if card.get('specialCondition')=='CONFUSED' else 1))
    return max(values,default=-100)

def best_pivot(own,other):
    cs=[c for c in own['bench'] if ready_attack(c,own,other)>-100]
    return max(cs,key=lambda c:ready_attack(c,own,other)) if cs else None

def pivot_value(own,other,attacks_blocked=False):
    if attacks_blocked or own.get('turnFlags',{}).get('attacksBlocked'):return 0
    current=ready_attack(own['active'][0],own,other) if own['active'] else -100
    if not retreat_ready(own):return current
    target=best_pivot(own,other)
    if target is None:return current
    access=ready_attack(target,own,other)
    # Compare the best reachable attack before/after the complete transfer.
    # Making the active worse cannot create an artificial pivot improvement.
    return access if access>current+20 else current

def finish_bridge(own,other):
    """A visible winning or two-prize attack after one attachment and retreat.

    This returns a goal, not permission to execute a fabricated action. Each
    next step is checked against actual legal options/candidates below.
    """
    if not own['active'] or not other['active']:return None
    current=facts.exchange(own,other,other['active'][0]) if facts.shadow(own) else 0
    for c in field(own):
        n=c.get('energy',[]).count('DARK')
        if c['name']!=MAIN or n<1 or c.get('specialCondition') in ('CONFUSED','ASLEEP','PARALYZED'):continue
        if n==1 and own.get('turnFlags',{}).get('energyPlayed'):continue
        if c not in own['active'] and not retreat_ready(own):continue
        projected={**c,'energy':c.get('energy',[])+(['DARK'] if n==1 else [])}
        team=promoted(own,projected)
        points=facts.exchange(team,other,other['active'][0]) if facts.shadow(team) else 0
        if points>current and (points>=own['prize_count'] or points>=2):return c
    return None

def energy_role(card,own,other):
    """Value energy only through attacks, transfer counters or the Marnie line.

    Acquiring an evolution is deliberately a different task from powering it.
    A spare Darkness on a Water attack has no development bonus here.
    """
    n=card.get('energy',[]).count('DARK');score=0
    if card['name'] in (MAIN,MID,BASIC):
        score=(140 if card['name']==MAIN else 55) if n>=2 else (25 if n else 0)
    if card['name']=='Munkidori' and n:
        score=55+(previous.parent.moving_units(own)*10 if not card.get('abilityUsed') else 0)
    team=promoted(own,card)
    if card in own['active'] and facts.shadow(team):
        score+=220+300*previous.parent.attack_plan(team,other,[])['score']
    return score

def reachable_prizes(own,other,attacks_blocked=False):
    if attacks_blocked or own.get('turnFlags',{}).get('attacksBlocked'):return 0
    scores=[]
    for c in field(own):
        if c['name']!=MAIN or c.get('specialCondition') in ('ASLEEP','PARALYZED','CONFUSED'):continue
        if c in own['bench'] and not retreat_ready(own):continue
        scores.append(previous.parent.attack_plan(promoted(own,c),other,[])['score'])
    return max(scores,default=0)

def candy_goal(own):
    if own.get('turnFlags',{}).get('itemsBlocked') or own.get('turnFlags',{}).get('firstPersonalTurn'):return None
    if not any(c['name']=='Rare Candy' for c in own['hand']):return None
    eligible=[c for c in field(own) if c['name']==BASIC and not c.get('firstTurnPlayed',True)]
    return max(eligible,key=lambda c:(c.get('energy',[]).count('DARK'),c in own['active']),default=None)

def candy_chain_needed(own,other):
    """Spend a Candy/search slot on attack continuity, not another ex count.

    An established safe lead can keep a one-prize stage-one backup and the item
    for rebuilding after Devolution. No unseen enemy tool is assumed present.
    """
    established=[c for c in field(own) if c['name']==MAIN and c.get('energy',[]).count('DARK')>=2]
    return not established or front_in_danger(own,other)


def optional_bench_ex(option,own,other):
    """Do not let inherited acquisition quotas force another ex under a safe lead.

    This filters only a legal bench evolution. Active evolution, establishment,
    threatened-lead replacement, and an already initiated Candy chain stay legal.
    """
    return (option['actionType']=='EvolvePokemonAction' and option.get('source')==MAIN
            and any(c['ref']==option.get('targetRef') for c in own['bench'])
            and bool(facts.shadow(own)) and not front_in_danger(own,other))


def transfer_plan(own,other,donor_ref=None,*,attacks_blocked=False):
    best=None
    for donor in field(own):
        if donor_ref is not None and address(donor)!=donor_ref:continue
        if 'DARK' not in donor.get('energy',[]):continue
        left=list(donor['energy']);left.remove('DARK');after_d={**donor,'energy':left}
        for recipient in field(own):
            if recipient['ref']==donor['ref']:continue
            after_r={**recipient,'energy':recipient.get('energy',[])+['DARK']}
            projected=replace(own,{donor['ref']:after_d,recipient['ref']:after_r})
            pivot_gain=pivot_value(projected,other,attacks_blocked)-pivot_value(own,other,attacks_blocked)
            if recipient['name']=='Munkidori' and (recipient.get('abilityUsed') or previous.parent.moving_units(own)==0) and pivot_gain<=0:continue
            before=energy_role(donor,own,other)+energy_role(recipient,own,other)
            after=energy_role(after_d,projected,other)+energy_role(after_r,projected,other)
            gain=after-before+pivot_gain
            # An energy reserve must yield to a visible winning ability chain.
            finishes=False
            marginal_prizes=0
            if recipient['name']=='Munkidori' and not recipient.get('energy'):
                ready_before=any(c['name']=='Munkidori' and 'DARK' in c.get('energy',[]) and not c.get('abilityUsed') for c in field(own))
                points_before=previous.parent.best_transfer(own,other,[])[0] if ready_before else previous.parent.attack_plan(own,other,[])['score']
                points_after=previous.parent.best_transfer(projected,other,[])[0]
                marginal_prizes=max(0,points_after-points_before)
                # Use the same per-prize scale already applied to funded front
                # attacks. Healing/energy roles alone miss a transfer + bench KO.
                gain+=300*marginal_prizes
                finishes=points_after>=own['prize_count']>points_before
                if finishes:gain+=10000
            attack_finish=reachable_prizes(projected,other,attacks_blocked)>=own['prize_count']>reachable_prizes(own,other,attacks_blocked)
            if attack_finish and not finishes:finishes=True;gain+=10000
            # A bench attacker must remain reachable after the donor paid.
            if recipient in own['bench'] and recipient['name']==MAIN and not retreat_ready(projected):
                gain-=40
            plan={'donor':address(donor),'recipient':address(recipient),'gain':gain,'finishes':finishes,'marginalPrizes':marginal_prizes,'pivotGain':pivot_gain}
            if best is None or gain>best['gain']:best=plan
    return best

def legacy_endpoints(own,other):
    """Exact v7 rank: BENCH-only choices use readiness, mixed zones utility."""
    def choose(cs):
        bench_only=all(c in own['bench'] for c in cs)
        return max(cs,key=lambda c:utility(c,own)+(ready_value(c,own,other,deficit)*4-10*len(c.get('retreatCost',[])) if bench_only else 0))
    donors=[c for c in field(own) if 'DARK' in c.get('energy',[])]
    if not donors or len(field(own))<2:return None
    donor=choose(donors);recipient=choose([c for c in field(own) if c['ref']!=donor['ref']])
    return donor,recipient

def transfer_projection(own,donor,recipient):
    energy=list(donor['energy']);energy.remove('DARK')
    return replace(own,{donor['ref']:{**donor,'energy':energy},recipient['ref']:{**recipient,'energy':recipient.get('energy',[])+['DARK']}})

def supported_transfer(plan,own,other):
    """Use the new ranking for an executable benefit, not every proxy gain."""
    if not plan or plan['gain']<=0:return False
    if plan['finishes'] or plan['marginalPrizes']>0:return True
    donor=next(c for c in field(own) if address(c)==plan['donor']);recipient=next(c for c in field(own) if address(c)==plan['recipient'])
    after=transfer_projection(own,donor,recipient);charged=next(c for c in field(after) if c['ref']==recipient['ref'])
    # A nonterminal pivot can cash in one prize while dismantling the funded
    # monkey relay. Keep the existing relay when that alternative is available;
    # one-step attack value does not price its later healing/counter transfers.
    endpoints=legacy_endpoints(own,other)
    if donor['name']=='Munkidori' and donor['energy'].count('DARK')==1 and recipient['name']!='Munkidori' and endpoints:
        _,relay=endpoints
        if relay['name']=='Munkidori' and not relay.get('energy') and not relay.get('abilityUsed') and previous.parent.moving_units(own)>0:
            target=best_pivot(after,other)
            winning=bool(target and retreat_ready(after) and previous.parent.attack_plan(promoted(after,target),other,[])['score']>=own['prize_count'])
            if not winning:return False
    if recipient['name']==MAIN and ready_attack(charged,after,other)>-100 and ready_attack(recipient,own,other)==-100:return True
    if recipient['name']=='Munkidori' and not recipient.get('abilityUsed') and not recipient.get('energy') and previous.parent.moving_units(own)>0 and donor['name']!=MAIN:
        if donor['name']!='Munkidori' or donor.get('abilityUsed') or donor['energy'].count('DARK')>1:return True
    target=best_pivot(after,other)
    if plan['pivotGain']>0 and target:
        if target['name']=='Budew':
            if endpoints:
                _,relay=endpoints
                if relay['name']=='Munkidori' and not relay.get('energy') and not relay.get('abilityUsed') and previous.parent.moving_units(own)>0:return False
            return True
        if target['name']==MAIN:
            return previous.parent.attack_plan(promoted(after,target),other,[])['score']>previous.parent.attack_plan(own,other,[])['score']
    return False

def destructive_legacy_transfer(own,other,attacks_blocked=False):
    endpoints=legacy_endpoints(own,other)
    if not endpoints:return True
    donor,recipient=endpoints;after=transfer_projection(own,donor,recipient)
    strips_main=donor['name']==MAIN and donor['energy'].count('DARK')==2
    # The same energy is a main-attacker resource before evolution. Price the
    # reachable attack loss and a sole funded line, rather than its stage name.
    line=donor['name'] in (MAIN,MID,BASIC)
    line_loss=line and donor['energy'].count('DARK')==2
    access_loss=pivot_value(after,other,attacks_blocked)<pivot_value(own,other,attacks_blocked)
    strips_live_monkey=donor['name']=='Munkidori' and donor['energy'].count('DARK')==1 and not donor.get('abilityUsed') and previous.parent.moving_units(own)>0
    if not (strips_main or strips_live_monkey or line_loss or access_loss):return False
    monkey_use=recipient['name']=='Munkidori' and not recipient.get('abilityUsed') and 'DARK' not in recipient.get('energy',[]) and previous.parent.moving_units(own)>0
    charged=next(c for c in field(after) if c['ref']==recipient['ref'])
    real_ko=ready_attack(charged,after,other)>=400 and ready_attack(recipient,own,other)<400
    target=best_pivot(after,other)
    pivot=pivot_value(after,other)>pivot_value(own,other) and target and target['name'] in (MAIN,'Budew')
    return not (monkey_use or real_ko or pivot)

def successor_needed(own,other):
    """One established attacker still needs a second line after a nonfinal KO."""
    return (own['prize_count']>1 and other['prize_count']>1 and len(own['bench'])<5
            and sum(c['name'] in (MAIN,MID,BASIC) for c in field(own))==1
            and any(c['name']==MAIN and c.get('energy',[]).count('DARK')>=2 for c in field(own))
            and any(c['name'] in (MID,MAIN) for c in own['hand'])
            and not adds_gust_liability(own,other))

def front_in_danger(own,other):
    if not facts.shadow(own) or not other['active']:return False
    _,left=previous.after_shadow(own,other,other['active'][0])
    for enemy in left:
        team={**other,'active':[enemy],'bench':[c for c in left if c['ref']!=enemy['ref']],'hand':[{}]*other.get('hand_count',0)}
        if enemy.get('attackBlocked') or facts.protected(own['active'][0],own,enemy,False):continue
        for attack in enemy.get('attacks',[]):
            if attack['name'] not in enemy.get('limitedAttacks',[]) and deficit(attack.get('cost',[]),enemy.get('energy',[]))<=1:
                if estimate(attack,enemy,team,own)>=own['active'][0]['hp']:return True
    return False

def adds_gust_liability(own,other):
    risk=backup_exposure(own,other)
    if not risk['exposed']:return False
    # Compare the marginal prize route. A new one-prize Basic does not create
    # that route when a reachable existing bench Pokemon already concedes it.
    for victim in own['bench']:
        if victim.get('prize',1)!=1 or not facts.gust_reachable(victim,own):continue
        target={**own,'active':[victim],'bench':[c for c in field(own) if c['ref']!=victim['ref']]}
        for enemy in field(other):
            if enemy.get('attackBlocked'):continue
            team={**promoted(other,enemy),'hand':[{}]*other.get('hand_count',0)}
            if facts.protected(victim,target,enemy,False):continue
            for attack in enemy.get('attacks',[]):
                if attack['name'] not in enemy.get('limitedAttacks',[]) and deficit(attack.get('cost',[]),enemy.get('energy',[]))<=1:
                    if estimate(attack,enemy,team,target)>=victim['hp']:return False
    return True

def successor_preparation(own,other,options,selected):
    """Prepare with actual noncompeting actions before ending this turn.

    No assumed search result or deck acceleration is needed to keep the current
    attack available. Evolving an unfunded ex is excluded from this preparation.
    """
    if selected['actionType']!='AttackAction' or selected.get('attack',{}).get('name')!='Shadow Bullet' or not facts.shadow(own):return None
    if own['active'][0].get('specialCondition')=='CONFUSED':return None
    gusts=[o['source'] for o in options if o.get('source') in facts.GUSTS]
    if previous.parent.attack_plan(own,other,gusts)['score']>=own['prize_count']:return None
    for o in options:
        if o['actionType']!='EvolvePokemonAction':continue
        target=next((c for c in own['bench'] if c['ref']==o.get('targetRef')),None)
        if target and (o.get('source')==MID or (o.get('source')==MAIN and target.get('energy',[]).count('DARK')>=2 and candy_chain_needed(own,other))):
            return o
    goal=candy_goal(own)
    if goal and candy_chain_needed(own,other) and goal in own['bench'] and goal.get('energy',[]).count('DARK')>=2 and any(c['name']==MAIN for c in own['hand']):
        candy=next((o for o in options if o['actionType']=='UseItemAction' and o.get('source')=='Rare Candy'),None)
        if candy:return candy
    for o in options:
        if o['actionType']!='AttachEnergyAction' or o.get('source')!='Darkness Energy':continue
        target=next((c for c in own['bench'] if c['ref']==o.get('targetRef')),None)
        if target and target['name'] in (MAIN,MID,BASIC) and target.get('energy',[]).count('DARK')<2:
            # A hand energy also buys an escape after a gust. Do not spend its
            # flexibility on a Basic with no visible evolution while the lead is safe.
            if target['name']==MAIN or any(c['name'] in (MID,MAIN) for c in own['hand']) or front_in_danger(own,other):return o
    if not successor_needed(own,other):return None
    playable=next((o for o in options if o['actionType']=='PlayPokemonAction' and o.get('source')==BASIC),None)
    if playable:return playable
    if any(c['name']==BASIC for c in own['discard']):
        recovery=next((o for o in options if o['actionType']=='UseItemAction' and o.get('source')=='Night Stretcher'),None)
        if recovery:return recovery
    if front_in_danger(own,other):
        return next((o for o in options if o['actionType']=='UseStadiumAction' and o.get('source') in ('Spikemuth Gym','Artazon')),None)
    return None

def evolution_window(own):
    names={c['name'] for c in own['hand']}
    return (MAIN in names and not own.get('turnFlags',{}).get('firstPersonalTurn') and
            any(not c.get('firstTurnPlayed',True) and (c['name']==MID or (c['name']==BASIC and 'Rare Candy' in names and not own.get('turnFlags',{}).get('itemsBlocked'))) for c in field(own)))

def setup_capacity(own,other):
    return (evolution_window(own) and len(own['bench'])<5 and other['prize_count']>1
            and sum(c['name'] in (MAIN,MID,BASIC) for c in field(own))==1)

def reserve_machine(name,own,other):
    """Keep a machine for a concrete visible future use, not a global quota.

    Otherwise retain product hand management: spending an unused tool can thin
    a later shuffled hand. No specific future draw or hidden card is assumed.
    """
    if name=='Technical Machine: Evolution':
        paths={BASIC:MID,MID:MAIN,'Snorunt':'Froslass'}
        hand={c['name'] for c in own['hand']}
        return any(c['name'] in paths and paths[c['name']] not in hand for c in own['bench'])
    if name=='Technical Machine: Devolution':return any(c.get('evolved') for c in field(other))
    return False

def backup_exposure(own,other):
    """Conditional Counter Catcher exposure, not knowledge of an enemy hand.

    A newly played Basic cannot evolve this turn. Count the planned KO before
    testing the enemy's prize condition. Current attacks/one attachment and the
    public mature Morgrem -> Grimmsnarl possibility supply a risk scenario.
    """
    active=(own['active'] or [{}])[0];projected=own
    if active.get('name')==MID:
        option={'actionType':'EvolvePokemonAction','source':MAIN,'targetRef':active['ref']}
        projected=previous.parent.evolved_active(own,option) or own
    elif active.get('name')==BASIC and 'Rare Candy' in {c['name'] for c in own['hand']}:
        projected=previous.parent.evolved_active(own,{'actionType':'UseItemAction','source':'Rare Candy'}) or own
    points=previous.parent.attack_plan(projected,other,[])['score']
    remaining=own['prize_count']-points
    if remaining<=0 or other['prize_count']<=remaining:return {'exposed':False,'points':points}
    # A known card in our registered construction; no opponent deck access.
    victim={'name':BASIC,'hp':70,'weakness':['GRASS'],'stage':'BASIC','prize':1,'ref':'prospective-backup','energy':[]}
    target={**own,'active':[victim],'bench':field(own)}
    threat=0
    for enemy in field(other):
        if enemy.get('attackBlocked'):continue
        team={**promoted(other,enemy),'hand':[{}]*other.get('hand_count',0)}
        for attack in enemy.get('attacks',[]):
            if attack['name'] not in enemy.get('limitedAttacks',[]) and deficit(attack.get('cost',[]),enemy.get('energy',[]))<=1:
                threat=max(threat,estimate(attack,enemy,team,target))
        if enemy['name']==MID and not enemy.get('firstTurnPlayed',True) and 'DARK' in enemy.get('energy',[]):
            threat=max(threat,180)
    # Keep a body if it prevents an immediate loss of the only Pokemon. The
    # threat is an upper scenario, so use it as a veto only on optional setup.
    sole_front_dies=len(field(own))==1 and (projected['active'] or [{}])[0].get('hp',0)<=threat
    return {'exposed':threat>=70 and not sole_front_dies,'points':points,'conditionalDamage':threat,'opponentCatcherEligible':True}

def prepare_backup(own,other):return setup_capacity(own,other) and not backup_exposure(own,other)['exposed']

def promotion_transfer_target(own,other):
    target=best_pivot(own,other);n=previous.parent.moving_units(own)
    if not retreat_ready(own) or not target or target['name']!=MAIN or target.get('specialCondition')=='CONFUSED' or not other['active']:return None
    future=promoted(own,target);front=other['active'][0]
    if not n or not facts.shadow(future) or not all(c['hp']>n*10 for c in field(other)) or previous.parent.front_hit(future,other)<front['hp']:return None
    # Preserve extra bench KOs; otherwise put counters on the front that this
    # attack already removes, rather than gifting the enemy a healing donor.
    return max(field(other),key=lambda c:(previous.parent.transfer_score(future,other,c,n,[]),c['ref']==front['ref']))

def before_promotion(own,other,options,selected):
    """Heal before a ready attack, then preserve its useful retreat payment.

    Apply only when retreat is already the chosen legal action. For counter
    movement require the current enemy front to remain in place and already be
    within a funded bench Grimmsnarl's range. No cooperative promotion assumed.
    """
    if selected['actionType']!='RetreatAction' or not own['active']:return None
    active=own['active'][0];target=best_pivot(own,other)
    if not target:return None
    if promotion_transfer_target(own,other):
        monkey=next((o for o in options if o['actionType']=='UseAbilityAction' and o.get('source')=='Munkidori'),None)
        if monkey:return monkey
    cost=len(active.get('retreatCost',[]));energy=len(active.get('energy',[]))
    keep_monkey=active['name']=='Munkidori' and 'DARK' in active.get('energy',[]) and energy-cost==0<energy-max(0,cost-1)
    keep_attacker=active['name']==MAIN and energy-cost<2<=energy-max(0,cost-1)
    if keep_monkey or keep_attacker:
        return next((o for o in options if o['actionType']=='UseToolAction' and o.get('source')=='Rescue Board' and o.get('targetRef')==active['ref']),None)
    return None

def blocked_attack(dto):
    own=dto['observation']['self'];f=own.get('turnFlags',{})
    return bool(f.get('attacksBlocked') or (f.get('firstPersonalTurn') and (dto.get('startingPlayer') or '').lower()==own['id']))

def mobility_target(own,other):
    """A funded, actual bench attack; the retreat payment is planned separately."""
    if not own['active'] or own.get('turnFlags',{}).get('retreated'):return None
    a=own['active'][0]
    if a.get('retreatBlocked') or a.get('specialCondition') in ('ASLEEP','PARALYZED'):return None
    target=best_pivot(own,other)
    return target if target and ready_attack(target,own,other)>ready_attack(a,own,other)+20 else None

def machine_target(card):
    # A deck evolution into the ex cannot trigger its hand-entry ability.
    return card['name'] in (BASIC,'Snorunt') or (card['name']==MID and card.get('energy',[]).count('DARK')>=2)

def evolution_machine_goal(dto):
    own,other=dto['observation']['self'],dto['observation']['opponent']
    if blocked_attack(dto) or not own['active'] or facts.shadow(own):return False
    a=own['active'][0];names={c['name'] for c in own['hand']}
    if a.get('attackBlocked') or a.get('specialCondition') in ('CONFUSED','ASLEEP','PARALYZED'):return False
    if not any(c['name'] in (BASIC,MID) for c in own['bench']):return False
    if not ('Technical Machine: Evolution' in names or 'Technical Machine: Evolution' in a.get('tool',[])):return False
    if a.get('tool') and 'Technical Machine: Evolution' not in a['tool']:return False
    if not a.get('energy') and (own.get('turnFlags',{}).get('energyPlayed') or 'Darkness Energy' not in names):return False
    # The only manual attachment can instead activate real counter movement.
    # Preserve that option when the holder is empty; already powered holders
    # can evolve after using Adrena-Brain without competing for this resource.
    if 'Darkness Energy' in names and not own.get('turnFlags',{}).get('energyPlayed') and previous.parent.moving_units(own)>=2 and any(c['name']=='Munkidori' and not c.get('abilityUsed') and 'DARK' not in c.get('energy',[]) for c in field(own)):
        return False
    # Direct deck evolution does not trigger Punk Up. An unfunded stage one
    # alone is not an energy plan; keep it available for evolution from hand.
    if not any(machine_target(c) for c in own['bench']):return False
    # Keep the actual current main attack ahead of a setup attack.
    for c in field(own):
        if c['name']==MAIN and c.get('energy',[]).count('DARK')>=1:
            if c.get('energy',[]).count('DARK')>=2 or ('Darkness Energy' in names and not own.get('turnFlags',{}).get('energyPlayed')):
                if c in own['active'] or retreat_ready(own):return False
    return True

def pivot_terminal(own,other,target):
    if not target or target['name']!=MAIN or not other['active']:return False
    future=promoted(own,target)
    return bool(facts.shadow(future) and facts.exchange(future,other,other['active'][0])>=own['prize_count'])

def pre_pivot_preparation(dto,options):
    """Bank a known retreat attachment, then draw before a KO closes setup.

    Revalidated at every real prompt. The draw never promises a Board or a
    missing attacker. A visible terminal attack retains its immediate route.
    """
    own,other=dto['observation']['self'],dto['observation']['opponent']
    target=mobility_target(own,other)
    if blocked_attack(dto) or not target or target['name']!=MAIN or pivot_terminal(own,other,target):return None,None
    draw=next((o for name in ('Iono',"Lillie's Determination") for o in options if o['actionType']=='UseSupporterAction' and o.get('source')==name),None)
    a=own['active'][0]
    if draw and len(own['hand'])<=2 and len(a.get('retreatCost',[]))==len(a.get('energy',[]))+1:
        attach=next((o for o in options if o['actionType']=='AttachEnergyAction' and o.get('source')=='Darkness Energy' and o.get('targetRef')==a['ref']),None)
        if attach:return attach,'bank-retreat-energy-before-low-hand-draw'
    if draw and len(own['hand'])<=2:return draw,'draw-before-promotion-closes-preparation'
    # Finish an actual funded successor before promotion changes the safe-lead
    # filters and a front KO becomes the next high-ranked action.
    if sum(c['name']==MAIN and c.get('energy',[]).count('DARK')>=2 for c in field(own))<2:
        for o in options:
            c=next((c for c in own['bench'] if c['ref']==o.get('targetRef')),None)
            if o['actionType']=='EvolvePokemonAction' and o.get('source') in (MID,MAIN) and c and c.get('energy',[]).count('DARK')>=2:
                return o,'evolve-funded-successor-before-promotion'
        names={c['name'] for c in own['hand']}
        if any(c['name']==BASIC and not c.get('firstTurnPlayed',True) and c.get('energy',[]).count('DARK')>=2 for c in own['bench']) and MID not in names:
            search=next((o for o in options if o['actionType']=='UseItemAction' and o.get('source')=='Poké Pad'),None)
            if search:return search,'inspect-funded-successor-before-promotion'
    return None,None

def resource_chain_step(dto,options,selected):
    """Only actual next legal actions; no assumed search result or extra attachment."""
    own,other=dto['observation']['self'],dto['observation']['opponent']
    if blocked_attack(dto) or not own['active']:return None,None
    # Do not delay an available KO or an inherited immediate winning ability.
    if selected['actionType']=='AttackAction' and estimate(selected.get('attack',{}),own['active'][0],own,other)>=other['active'][0]['hp']:return None,None
    if selected['actionType']=='UseAbilityAction' and selected.get('source')=='Munkidori':return None,None
    if selected['actionType']=='EvolvePokemonAction' and selected.get('source')==MID and candy_goal(own) and MAIN not in {c['name'] for c in own['hand']}:
        goal=candy_goal(own)
        if selected.get('targetRef')==goal['ref']:
            # Inspect an actual legal search before closing this Basic's Candy
            # window with a stage-one evolution. Absence of a main in candidates
            # never licenses an invented result; subsequent steps revalidate.
            search=next((o for source,kind in (('Spikemuth Gym','UseStadiumAction'),('Ultra Ball','UseItemAction')) for o in options if o['actionType']==kind and o.get('source')==source),None)
            if search:return search,'inspect-main-search-before-closing-candy-window'
    if selected['actionType']=='EvolvePokemonAction' or (selected['actionType']=='UseItemAction' and selected.get('source')=='Rare Candy'):return None,None
    a=own['active'][0];target=mobility_target(own,other)
    if target and target['name']==MAIN:
        if not pivot_terminal(own,other,target) and (selected['actionType'] in ('UseStadiumAction','PutStadiumAction') or (selected['actionType']=='UseSupporterAction' and selected.get('source') in ('Arven','Iono',"Lillie's Determination"))):return None,None
        if retreat_ready(own):
            step=next((o for o in options if o['actionType']=='RetreatAction'),None)
            if step:return step,'promote-funded-main-before-resource-drift'
        cost=len(a.get('retreatCost',[]));energy=len(a.get('energy',[]))
        if not a.get('tool') and energy>=max(0,cost-1):
            board=next((o for o in options if o['actionType']=='UseToolAction' and o.get('source')=='Rescue Board' and o.get('targetRef')==a['ref']),None)
            if board:return board,'rescue-board-completes-main-pivot'
        if cost==energy+1:
            attach=next((o for o in options if o['actionType']=='AttachEnergyAction' and o.get('source')=='Darkness Energy' and o.get('targetRef')==a['ref']),None)
            if attach:return attach,'attach-pays-retreat-into-funded-main'
            if not own.get('turnFlags',{}).get('energyPlayed') and any(c['name']=='Darkness Energy' for c in own['discard']):
                recovery=next((o for o in options if o['actionType']=='UseItemAction' and o.get('source')=='Night Stretcher'),None)
                if recovery:return recovery,'recover-known-retreat-energy'
    mature_mid=any(c['name']==MID and not c.get('firstTurnPlayed',True) for c in field(own))
    if mature_mid and MAIN not in {c['name'] for c in own['hand']} and not facts.shadow(own):
        search=next((o for source,kind in (('Spikemuth Gym','UseStadiumAction'),('Ultra Ball','UseItemAction')) for o in options if o['actionType']==kind and o.get('source')==source),None)
        if search:return search,'inspect-hand-evolution-before-deck-evolution'
    if evolution_machine_goal(dto):
        if not a.get('energy'):
            step=next((o for o in options if o['actionType']=='AttachEnergyAction' and o.get('source')=='Darkness Energy' and o.get('targetRef')==a['ref']),None)
        elif 'Technical Machine: Evolution' not in a.get('tool',[]):
            step=next((o for o in options if o['actionType']=='UseToolAction' and o.get('source')=='Technical Machine: Evolution' and o.get('targetRef')==a['ref']),None)
        else:step=next((o for o in options if o['actionType']=='AttackAction' and o.get('attack',{}).get('name')=='Evolution'),None)
        if step:return step,'fund-and-execute-evolution-machine'
    # Keep a free retreat tool available for the active, rather than occupying
    # that slot with an unpowered machine. A real Budew attack can be the bridge.
    if target and not a.get('tool') and len(a.get('energy',[]))>=max(0,len(a.get('retreatCost',[]))-1):
        board=next((o for o in options if o['actionType']=='UseToolAction' and o.get('source')=='Rescue Board' and o.get('targetRef')==a['ref']),None)
        if board:return board,'reserve-active-tool-slot-for-executable-pivot'
    return None,None

def search_chain_target(dto,cs):
    own,other=dto['observation']['self'],dto['observation']['opponent'];d=dto['decision'];source=d.get('source');names={c['name'] for c in own['hand']}
    # HAND selections here are trainer payment, never card acquisition.
    # Preserve all min/max cost constraints and the inherited protected payment.
    if 'CardPosition.HAND' in d.get('zones',[]):return None,None
    byname={c.get('card',{}).get('name'):c for c in cs}
    a=(own['active'] or [{}])[0];target=mobility_target(own,other)
    if source in ('Arven','Secret Box'):
        if target and not a.get('tool') and len(a.get('energy',[]))>=max(0,len(a.get('retreatCost',[]))-1) and 'Rescue Board' in byname:
            return byname['Rescue Board'],'search-retreat-tool-for-visible-attack'
        if not facts.shadow(own) and any(c['name'] in (BASIC,MID) for c in field(own)):
            mature_basic=any(c['name']==BASIC and not c.get('firstTurnPlayed',True) for c in field(own))
            if mature_basic and MAIN in names and 'Rare Candy' not in names and 'Rare Candy' in byname and not own.get('turnFlags',{}).get('itemsBlocked'):
                return byname['Rare Candy'],'search-candy-for-known-main'
            missing_main=(any(c['name']==MID for c in field(own)) or (MID in names and any(c['name']==BASIC for c in field(own)) and len(field(own))>1)) and MAIN not in names
            missing_mid=any(c['name']==BASIC for c in field(own)) and MID not in names and not (MAIN in names and 'Rare Candy' in names)
            # Before maturity, an extra evolution search competes with two
            # actual Basics. Gym already provides this line's search access.
            gym=any(c.get('name')=='Spikemuth Gym' for c in dto['observation'].get('stadium',[]))
            gym=gym or any(c['name']=='Spikemuth Gym' for c in own['hand'])
            mature_mid=any(c['name']==MID and not c.get('firstTurnPlayed',True) for c in field(own))
            can_evolve=not own.get('turnFlags',{}).get('firstPersonalTurn') and (mature_mid or mature_basic)
            if missing_mid and not mature_mid and can_evolve and not gym and not own.get('turnFlags',{}).get('itemsBlocked') and 'Poké Pad' in byname:return byname['Poké Pad'],'search-evolution-before-optional-basics'
        if 'Technical Machine: Evolution' in byname:
            projected={**dto,'observation':{**dto['observation'],'self':{**own,'hand':own['hand']+[{'name':'Technical Machine: Evolution'}]}}}
            if evolution_machine_goal(projected):return byname['Technical Machine: Evolution'],'search-machine-for-funded-setup-attack'
    if source=='Night Stretcher' and target and target['name']==MAIN and not own.get('turnFlags',{}).get('energyPlayed') and len(a.get('retreatCost',[]))==len(a.get('energy',[]))+1 and 'Darkness Energy' in byname:
        return byname['Darkness Energy'],'recover-energy-for-known-pivot'
    return None,None


def decide(dto,*,entries,environment):
    if previous.deck_hash(entries)!=PROFILE['deckHash'] or environment!=PROFILE['environmentHash']:
        return previous.decide(dto,entries=entries,environment=environment)
    start=time.perf_counter();own,other=dto['observation']['self'],dto['observation']['opponent'];d=dto['decision'];reason='unchanged-v7';work=dto
    blocked=own.get('turnFlags',{}).get('attacksBlocked') or (own.get('turnFlags',{}).get('firstPersonalTurn') and (dto.get('startingPlayer') or '').lower()==own['id'])
    if d['kind']=='options':
        transfer=transfer_plan(own,other,attacks_blocked=blocked) if any(o.get('source')=='Energy Switch' for o in d['options']) else None
        options=[o for o in d['options'] if not optional_bench_ex(o,own,other) and not(o['actionType']=='UseItemAction' and o.get('source')=='Energy Switch' and not supported_transfer(transfer,own,other) and destructive_legacy_transfer(own,other,blocked))
                 and not(o['actionType']=='UseToolAction' and reserve_machine(o.get('source'),own,other) and blocked)
                 and not(not any(machine_target(c) for c in own['bench']) and ((o['actionType']=='UseToolAction' and o.get('source')=='Technical Machine: Evolution' and own['active'] and o.get('targetRef')==own['active'][0]['ref']) or (o['actionType']=='AttackAction' and o.get('attack',{}).get('name')=='Evolution')))]
        draw_available=any(o['actionType']=='UseSupporterAction' and o.get('source') in ('Arven','Iono',"Lillie's Determination") for o in options)
        ko_available=any(o['actionType']=='AttackAction' and estimate(o.get('attack',{}),own['active'][0],own,other)>=other['active'][0]['hp'] for o in options) if own['active'] and other['active'] else False
        monkey_ko=any(c['name']=='Munkidori' and 'DARK' in c.get('energy',[]) and not c.get('abilityUsed') for c in field(own)) and previous.parent.best_transfer(own,other,[])[0]>0
        if draw_available and not ko_available and not facts.shadow(own) and not monkey_ko:
            options=[o for o in options if not(o['actionType']=='UseSupporterAction' and o.get('source')=="Boss's Orders")]
        if len(options)!=len(d['options']):work={**dto,'decision':{**d,'options':options}};reason='reject-unproductive-transfer-or-blocked-machine'
    cmd,detail=previous.decide(work,entries=entries,environment=environment)
    if detail.get('route')!=previous.VERSION:return cmd,detail
    bridge=finish_bridge(own,other)
    if d['kind']=='selection' and not d.get('hidden'):
        cs=d['candidates'];target=None
        if d.get('source')=='Energy Switch' and cs and all(c.get('side')=='self' for c in cs):
            addresses={address(c) for c in cs};energized={address(c) for c in field(own) if 'DARK' in c.get('energy',[])}
            if addresses==energized:
                plan=transfer_plan(own,other,attacks_blocked=blocked);endpoint=plan['donor'] if plan else None
            else:
                missing={address(c) for c in field(own)}-addresses
                # A fallback donor must retain its fallback recipient. Do not
                # start a different plan midway through the same item chain.
                plan=transfer_plan(own,other,attacks_blocked=blocked)
                if len(missing)!=1 or not plan or plan['donor']!=next(iter(missing)):plan=None
                endpoint=plan['recipient'] if plan else None
            target=next((c for c in cs if address(c)==endpoint),None) if supported_transfer(plan,own,other) else None
            if target:reason='joint-energy-transfer-endpoint'
        if bridge:
            missing=bridge.get('energy',[]).count('DARK')<2
            wanted=('Night Stretcher' if d.get('source')=='Arven' else 'Darkness Energy' if d.get('source')=='Night Stretcher' else None) if missing else None
            if wanted and any(c['name']=='Darkness Energy' for c in own['discard']):
                target=next((c for c in cs if c.get('card',{}).get('name')==wanted),target)
                if target:reason='search-prize-energy-chain'
            if cs and all(c.get('side')=='self' and c.get('position')=='CardPosition.BENCH' for c in cs) and not d.get('source'):
                target=next((c for c in cs if address(c)==address(bridge)),target)
                if target:reason='promote-last-prize-attacker'
        if d.get('source')=='Spikemuth Gym' and prepare_backup(own,other):
            target=next((c for c in cs if c.get('card',{}).get('name')==BASIC),target)
            if target:reason='search-backup-before-punk-up'
        if successor_needed(own,other) and d.get('source') in ('Spikemuth Gym','Artazon','Night Stretcher'):
            backup=next((c for c in cs if c.get('card',{}).get('name')==BASIC),None)
            if backup:target=backup;reason='acquire-visible-successor-basic'
        if successor_needed(own,other) and d.get('source')=='Arven' and any(c['name']==BASIC for c in own['discard']) and not any(c['name'] in (BASIC,'Night Stretcher') for c in own['hand']):
            recovery=next((c for c in cs if c.get('card',{}).get('name')=='Night Stretcher'),None)
            if recovery:target=recovery;reason='recover-known-successor-instead-of-speculative-search'
        if bridge is None and candy_goal(own) and candy_chain_needed(own,other) and d.get('source') in ('Spikemuth Gym','Poké Pad','Ultra Ball','Night Stretcher') and 'CardPosition.HAND' not in d.get('zones',[]) and not any(c['name']==MAIN for c in own['hand']):
            main=next((c for c in cs if c.get('card',{}).get('name')==MAIN),None)
            if main:target=main;reason='search-main-for-visible-candy-chain'
        if d.get('source')=='Munkidori' and cs and all(c.get('side')=='opponent' for c in cs):
            units=previous.parent.moving_units(own)
            best=max(field(other),key=lambda c:previous.parent.transfer_score(own,other,c,units,[]))
            planned=best if previous.parent.transfer_score(own,other,best,units,[])>=own['prize_count'] else promotion_transfer_target(own,other)
            if planned:
                target=next((c for c in cs if (c.get('boardRef') or c['ref'])==planned['ref']),target)
                if target:reason='transfer-target-includes-following-promotion-attack'
        if bridge is None and d.get('source') in ('Spikemuth Gym','Poké Pad','Ultra Ball','Night Stretcher') and 'CardPosition.HAND' not in d.get('zones',[]) and MAIN not in {c['name'] for c in own['hand']} and any(c['name']==MID and not c.get('firstTurnPlayed',True) for c in field(own)) and not facts.shadow(own):
            main=next((c for c in cs if c.get('card',{}).get('name')==MAIN),None)
            if main:target=main;reason='search-main-for-mature-hand-evolution'
        # The retrieval prompt must execute the same funded successor goal
        # that made an evolution search useful; a full bench cannot use another
        # optional Basic. Never assume the card exists outside real candidates.
        if d.get('source')=='Poké Pad' and 'CardPosition.HAND' not in d.get('zones',[]) and MID not in {c['name'] for c in own['hand']} and any(c['name']==MAIN and c.get('energy',[]).count('DARK')>=2 for c in field(own)) and any(c['name']==BASIC and not c.get('firstTurnPlayed',True) and c.get('energy',[]).count('DARK')>=2 for c in field(own)):
            mid=next((c for c in cs if c.get('card',{}).get('name')==MID),None)
            if mid:target=mid;reason='retrieve-funded-successor-evolution'
        chain_target,chain_reason=search_chain_target(dto,cs)
        if chain_target and bridge is None:target=chain_target;reason=chain_reason
        if dto.get('phase')=='active' and not d.get('source') and not own['active'] and d.get('zones')==['CardPosition.HAND']:
            picked=next((c for c in cs if c['ref'] in cmd['choice'].get('selectedRefs',[])),None)
            if picked and picked.get('card',{}).get('name') in ('Bloodmoon Ursaluna ex','Snorunt'):
                replacement=next((c for c in cs if c.get('card',{}).get('name')==BASIC),None)
                if replacement is None and picked['card']['name']=='Bloodmoon Ursaluna ex':replacement=next((c for c in cs if c.get('card',{}).get('name')=='Munkidori'),None)
                names={c['name'] for c in own['hand']}
                accessible=bool(names & {'Rescue Board','Technical Machine: Evolution'}) or (replacement is not None and replacement.get('card',{}).get('name')=='Munkidori' and 'Darkness Energy' in names)
                if replacement and accessible:target=replacement;reason='opening-active-preserves-evolution-and-retreat-access'
        if target:cmd={**cmd,'choice':{'selectedRefs':[target['ref']]}}
        if d.get('source')=='Technical Machine: Evolution' and cs and all(c.get('side')=='self' and c.get('position')=='CardPosition.BENCH' for c in cs):
            useful={address(c) for c in own['bench'] if machine_target(c)}
            valid=[c for c in cs if address(c) in useful]
            chosen=[c for c in valid if c['ref'] in cmd['choice'].get('selectedRefs',[])]
            count=min(d['max'],len(valid))
            if count>=d['min'] and valid:
                selected=(chosen+[c for c in valid if c not in chosen])[:count]
                cmd={**cmd,'choice':{'selectedRefs':[c['ref'] for c in selected]}};reason='machine-target-preserves-hand-entry-energy'
    elif d['kind']=='options':
        options=work['decision']['options'];selected=next(o for o in options if o['id']==cmd['choice']['optionId'])
        terminal=(selected['actionType']=='AttackAction' and own['active'] and own['active'][0].get('specialCondition')!='CONFUSED' and facts.shadow(own) and facts.exchange(own,other,other['active'][0])>=own['prize_count'])
        step=None
        monkey=next((o for o in options if o['actionType']=='UseAbilityAction' and o.get('source')=='Munkidori'),None)
        if monkey and not terminal and previous.parent.best_transfer(own,other,[])[0]>=own['prize_count']:
            step=monkey;reason='execute-visible-winning-transfer-before-promotion'
        preparation=before_promotion(own,other,options,selected)
        if preparation and not terminal and step is None:
            step=preparation;reason='heal-and-preserve-energy-before-promotion'
        if bridge and not terminal and step is None:
            if bridge.get('energy',[]).count('DARK')<2:
                step=next((o for o in options if o['actionType']=='AttachEnergyAction' and o.get('source')=='Darkness Energy' and o.get('targetRef')==bridge['ref']),None)
                if step is None and any(c['name']=='Darkness Energy' for c in own['discard']):
                    step=next((o for o in options if o['actionType']=='UseItemAction' and o.get('source')=='Night Stretcher'),None)
                    if step is None and not own.get('turnFlags',{}).get('itemsBlocked'):
                        step=next((o for o in options if o['actionType']=='UseSupporterAction' and o.get('source')=='Arven'),None)
            elif bridge in own['bench']:
                step=next((o for o in options if o['actionType']=='RetreatAction'),None)
            if step:reason='execute-prize-energy-chain'
        if step is None and not terminal:
            winning=transfer_plan(own,other,attacks_blocked=blocked) if any(o.get('source')=='Energy Switch' for o in options) else None
            adds_before_attack=winning and winning['gain']>0 and winning['marginalPrizes']>0 and selected['actionType']=='AttackAction'
            if winning and (winning['finishes'] or adds_before_attack):
                step=next((o for o in options if o['actionType']=='UseItemAction' and o.get('source')=='Energy Switch'),None)
                if step:reason='energy-transfer-completes-winning-ability' if winning['finishes'] else 'energy-transfer-adds-executable-prize'
        if step is None and not terminal and prepare_backup(own,other):
            evolving=selected['actionType']=='EvolvePokemonAction' and selected.get('source')==MAIN
            evolving |= selected['actionType']=='UseItemAction' and selected.get('source')=='Rare Candy'
            playable=next((o for o in options if o['actionType']=='PlayPokemonAction' and o.get('source')==BASIC),None)
            if playable:step=playable
            elif evolving:step=next((o for o in options if o['actionType']=='UseStadiumAction' and o.get('source')=='Spikemuth Gym'),None)
            if step:reason='bench-backup-before-punk-up'
        if step is None and not terminal:
            step=successor_preparation(own,other,options,selected)
            if step:reason='prepare-successor-before-nonterminal-attack'
        goal=candy_goal(own)
        if step is None and not terminal and goal and candy_chain_needed(own,other) and any(c['name']==MAIN for c in own['hand']) and selected['actionType']=='EvolvePokemonAction' and selected.get('source')==MID:
            step=next((o for o in options if o['actionType']=='UseItemAction' and o.get('source')=='Rare Candy'),None)
            if step:reason='complete-visible-candy-chain-before-stage-one-evolution'
        if step is None and not terminal:
            preparation,preparation_reason=pre_pivot_preparation(dto,options)
            if preparation:step=preparation;reason=preparation_reason
        if step is None and not terminal:
            chain_step,chain_reason=resource_chain_step(dto,options,selected)
            if chain_step:step=chain_step;reason=chain_reason
        if step:cmd={**cmd,'choice':{'optionId':step['id']}}
    assert legal(dto,cmd)
    return cmd,{**detail,'route':VERSION,'reason':reason,'aiMilliseconds':(time.perf_counter()-start)*1000}

# Preserve helper exports used by the other four product experts.
deck_hash=previous.deck_hash
environment_hash=previous.environment_hash
plan=previous.plan
resource=previous.resource
energy_value=previous.energy_value
expert=previous.expert
LINE=previous.LINE
