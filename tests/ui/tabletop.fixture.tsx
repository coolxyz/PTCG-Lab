import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Tabletop } from "../../packages/battle-ui/src/Tabletop";
import type { BattleView, CardInfo, EngineCard } from "../../packages/battle-ui/src/battle/types";
const hand = ["基础", "能量", "竞技场", "支援者", "选择特性", ...Array.from({length: 13}, (_,i) => `其他${i}`)]
  .map((name, i) => ({ name, id: name, ref: `test:self:hand:${i}` }));
const active: EngineCard = { name: "战斗宝可梦", ref: "test:self:active:0", hp: 100, maximumHp: 100 };
const side = { active: [active], bench: [], hand, hand_count: hand.length, deck_count: 40, prize_count: 6, discard: [], lost_zone: [] };
const original: BattleView = {
  matchId: "ui-fixture", stateVersion: 1, status: "active", phase: "turn", done: false, winner: null,
  observation: { turn: "PLAYER1", turn_number: 1, self: side, opponent: {...side, hand: null, active: [{...active, ref:"test:opponent:active:0"}]}, stadium: [], termination_reason: null },
  decision: { id: "decision-1", actor:"PLAYER1", kind:"options", min:1,max:1,hidden:false,candidates:[],options: [
    {id:"bench",actionType:"PlayPokemonAction",sourceRef:hand[0].ref},
    {id:"energy",actionType:"AttachEnergyAction",sourceRef:hand[1].ref,targetRef:active.ref},
    {id:"stadium",actionType:"PutStadiumAction",sourceRef:hand[2].ref},
    {id:"supporter",actionType:"UseSupporterAction",sourceRef:hand[3].ref},
    {id:"ability-1",label:"第一种特性",actionType:"UseAbilityAction",active_pokemonRef:hand[4].ref},
    {id:"ability-2",label:"第二种特性",actionType:"UseAbilityAction",active_pokemonRef:hand[4].ref},
  ]},
};
const cards = Object.fromEntries(hand.map(c => [c.name, { cnName:c.name, image:{url:"/test-card.png"} }])) as Record<string, CardInfo>;
function Fixture() {
 const [view, setView] = useState(original), [locked,setLocked]=useState(false), [selected,setSelected]=useState<string[]>([]),[played,setPlayed]=useState<string[]>([]);
 useEffect(() => {
   const update = (e: Event) => {
     const data = (e as CustomEvent).detail;
     setView({...original, ...data.view}); setLocked(!!data.locked); setSelected([]); setPlayed([]);
   };
   window.addEventListener("fixture",update); return () => window.removeEventListener("fixture",update);
 }, []);
 return <><Tabletop view={view} cards={cards} locked={locked} selected={selected} toggle={ref=>setSelected(old=>old.includes(ref)?old.filter(x=>x!==ref):old.length<(view.decision?.max||0)?[...old,ref]:old)} play={id=>setPlayed(old=>[...old,id])} describe={o=>o.label||o.id} animateEvents={false}/><output id="played">{JSON.stringify(played)}</output><output id="selected">{JSON.stringify(selected)}</output></>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
