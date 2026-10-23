"""Actor-only structured prompt labels; no arbitrary engine diagnostic forwarding."""


def describe(game, decision):
    phase = game.env.phase
    if phase == "active":
        return "选择起始战斗宝可梦"
    if phase in ("bench", "bonus_bench"):
        return "选择要放入备战区的基础宝可梦"
    actions = game.actions
    if actions.hidden:
        return "选择奖赏卡（不公开卡牌身份）"
    prompt = game.info.get("prompt")
    source = getattr(getattr(prompt, "source", None), "name", "")
    if source == "Munkidori":
        sides = {c.get("side") for c in decision["candidates"]}
        if sides == {"self"}:
            return "选择我方要移出伤害指示物的宝可梦，随后选择移动数量"
        if sides == {"opponent"}:
            return "选择对方接收伤害指示物的宝可梦"
    tips = getattr(prompt, "tips", "").lower()
    if source == "Tatsugiri":
        if tips == "tatsugiri_inspect_empty":
            return f"米立龙：查看牌库顶 {len(actions.candidates)} 张牌。本次没有支援者，确认后将这些牌洗回牌库；本回合特性已使用。"
        if tips == "tatsugiri_inspect_found":
            return f"米立龙：查看牌库顶 {len(actions.candidates)} 张牌。确认后可选择其中一张支援者加入手牌，其余牌洗回牌库。"
        return "米立龙：选择至多一张支援者，公开后加入手牌；也可以不选，然后洗牌。"
    if tips.startswith('choose energy cards (') and 'units available in selected cards' in tips:
        import re
        units=re.search(r'\((\d+)/(\d+) units',tips)
        if units:
            return f'选择要弃置的能量卡：已选卡牌可提供 {units[1]} / {units[2]} 个能量；满足数量后可结束选择'
    # Match known templates, not their raw text. All outputs below are static.
    if "knocked out" in tips:
        return "战斗宝可梦昏厥，请从备战区选择新的战斗宝可梦"
    if "retreat" in tips and "energy" in tips:
        return "选择弃置的能量以支付撤退费用"
    if source == "Gholdengo ex":
        return "从手牌弃置基本能量，每张造成 50 点伤害"
    if source == "Ciphermaniac's Codebreaking":
        return (
            "选择放在牌库最上方的一张牌"
            if game.env.gamestate.pending_cards
            else "选择两张牌，随后决定置顶顺序"
        )
    zones = {str(getattr(c, "cardPosition", "")) for c in actions.candidates}
    if source == "Mew ex" and all(type(c).__name__=='Attack' for c in actions.candidates):
        return "选择要复制的招式"
    if zones == {"CardPosition.HAND"}:
        return "从手牌选择卡牌，按当前卡牌效果支付费用或处理手牌"
    if zones == {"CardPosition.DISCARD"}:
        return "从弃牌区选择要回收的卡牌"
    if zones == {"CardPosition.LEFT"}:
        return "从可查看的牌库卡牌中选择（不显示牌库顺序）"
    if zones and zones <= {"CardPosition.ACTIVE", "CardPosition.BENCH"}:
        return "选择场上的目标宝可梦；同名目标按区域与位置区分"
    return "选择当前效果要求的卡牌；确认后继续下一步裁定"
